"""SQLite persistence for immutable plan versions and durable research runs."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from langgraph.checkpoint.sqlite import SqliteSaver

from src.events import EventType, ResearchEvent
from src.state import ResearchRun, RunStatus, TaskPlan, TaskStatus, utc_now


class RepositoryError(RuntimeError):
    """Base error for repository operations that map to an API conflict or not found response."""


class NotFoundError(RepositoryError):
    """Raised when a requested persisted artifact is absent."""


class InvalidStateTransitionError(RepositoryError):
    """Raised when an immutable plan or run state would be overwritten."""


class SQLiteRepository:
    """Own business tables and LangGraph checkpoint tables in one SQLite database file."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._checkpoint_connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self._checkpoint_connection.execute("PRAGMA journal_mode=WAL")
        self._checkpoint_connection.execute("PRAGMA foreign_keys=ON")
        self._checkpointer = SqliteSaver(self._checkpoint_connection)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT NOT NULL,
                    plan_version INTEGER NOT NULL,
                    topic TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (plan_id, plan_version)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL UNIQUE,
                    plan_id TEXT NOT NULL,
                    plan_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (plan_id, plan_version) REFERENCES plans(plan_id, plan_version)
                );
                CREATE TABLE IF NOT EXISTS task_runs (
                    run_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, task_id, attempt),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS report_versions (
                    report_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (report_id),
                    UNIQUE (run_id, version),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS event_logs (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    task_id TEXT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    @property
    def checkpointer(self) -> SqliteSaver:
        return self._checkpointer

    def close(self) -> None:
        self._checkpoint_connection.close()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _load_json(value: str) -> dict[str, Any]:
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise RepositoryError("persisted payload must be a JSON object")
        return parsed

    def _event(self, connection: sqlite3.Connection, event_type: str, payload: dict[str, Any], run_id: str | None = None, task_id: str | None = None) -> None:
        connection.execute(
            "INSERT INTO event_logs(run_id, task_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, task_id, event_type, self._json(payload), utc_now()),
        )

    def append_event(self, event: ResearchEvent) -> None:
        """Persist a structured event while keeping compatibility with the integer PK."""
        payload = event.model_dump(mode="json")
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO event_logs(run_id, task_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    event.run_id,
                    event.task_id,
                    event.type.value,
                    self._json(payload),
                    event.timestamp,
                ),
            )

    def list_events(self, run_id: str, *, task_id: str | None = None) -> list[ResearchEvent]:
        """Read structured events in insertion order; legacy rows are ignored."""
        with self._connection() as connection:
            if task_id is None:
                rows = connection.execute(
                    "SELECT payload_json FROM event_logs WHERE run_id = ? ORDER BY event_id",
                    (run_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT payload_json FROM event_logs WHERE run_id = ? AND task_id = ? ORDER BY event_id",
                    (run_id, task_id),
                ).fetchall()
        events: list[ResearchEvent] = []
        for row in rows:
            try:
                events.append(ResearchEvent.model_validate(self._load_json(row["payload_json"])))
            except Exception:
                continue
        return events

    def create_plan(self, plan: TaskPlan) -> dict[str, Any]:
        if plan.parse_status.value == "rejected":
            raise InvalidStateTransitionError("rejected plans cannot be persisted for confirmation")
        payload = plan.model_dump(mode="json")
        created_at = utc_now()
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO plans(plan_id, plan_version, topic, payload_json, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (plan.plan_id, plan.plan_version, plan.topic, self._json(payload), RunStatus.PLANNED.value, created_at),
            )
            self._event(connection, "plan_created", {"plan_id": plan.plan_id, "plan_version": plan.plan_version})
        return self.get_plan(plan.plan_id, plan.plan_version)

    def get_plan(self, plan_id: str, plan_version: int) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json, status, created_at FROM plans WHERE plan_id = ? AND plan_version = ?",
                (plan_id, plan_version),
            ).fetchone()
        if row is None:
            raise NotFoundError("plan version not found")
        return {"plan": self._load_json(row["payload_json"]), "status": row["status"], "created_at": row["created_at"]}

    def update_plan(self, plan_id: str, plan_version: int, plan: TaskPlan) -> dict[str, Any]:
        current = self.get_plan(plan_id, plan_version)
        if current["status"] != RunStatus.PLANNED.value:
            raise InvalidStateTransitionError("confirmed or cancelled plans cannot be modified")
        with self._connection() as connection:
            latest_version = connection.execute(
                "SELECT MAX(plan_version) AS version FROM plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()["version"]
            next_version = int(latest_version) + 1
            next_plan = plan.model_copy(update={"plan_id": plan_id, "plan_version": next_version})
            payload = next_plan.model_dump(mode="json")
            created_at = utc_now()
            connection.execute(
                "INSERT INTO plans(plan_id, plan_version, topic, payload_json, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (plan_id, next_version, next_plan.topic, self._json(payload), RunStatus.PLANNED.value, created_at),
            )
            self._event(connection, "plan_version_created", {"plan_id": plan_id, "plan_version": next_version})
        return self.get_plan(plan_id, next_version)

    def confirm_plan(self, plan_id: str, plan_version: int) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT status FROM plans WHERE plan_id = ? AND plan_version = ?", (plan_id, plan_version)
            ).fetchone()
            if row is None:
                raise NotFoundError("plan version not found")
            if row["status"] == RunStatus.CONFIRMED.value:
                return self.get_plan(plan_id, plan_version)
            if row["status"] != RunStatus.PLANNED.value:
                raise InvalidStateTransitionError("only planned plans can be confirmed")
            connection.execute(
                "UPDATE plans SET status = ? WHERE plan_id = ? AND plan_version = ?",
                (RunStatus.CONFIRMED.value, plan_id, plan_version),
            )
            self._event(connection, "plan_confirmed", {"plan_id": plan_id, "plan_version": plan_version})
        return self.get_plan(plan_id, plan_version)

    def cancel_plan(self, plan_id: str, plan_version: int) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT status FROM plans WHERE plan_id = ? AND plan_version = ?", (plan_id, plan_version)
            ).fetchone()
            if row is None:
                raise NotFoundError("plan version not found")
            if row["status"] != RunStatus.PLANNED.value:
                raise InvalidStateTransitionError("only planned plans can be cancelled")
            connection.execute(
                "UPDATE plans SET status = ? WHERE plan_id = ? AND plan_version = ?",
                (RunStatus.CANCELLED.value, plan_id, plan_version),
            )
            self._event(connection, "plan_cancelled", {"plan_id": plan_id, "plan_version": plan_version})
        return self.get_plan(plan_id, plan_version)

    def create_run(self, plan_id: str, plan_version: int) -> dict[str, Any]:
        plan_record = self.get_plan(plan_id, plan_version)
        if plan_record["status"] != RunStatus.CONFIRMED.value:
            raise InvalidStateTransitionError("runs require a confirmed plan version")
        plan = TaskPlan.model_validate(plan_record["plan"])
        run = ResearchRun(
            plan_id=plan.plan_id,
            plan_version=plan.plan_version,
            topic=plan.topic,
            status=RunStatus.CONFIRMED,
        )
        payload = run.model_dump(mode="json")
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO runs(run_id, thread_id, plan_id, plan_version, status, payload_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run.run_id, run.thread_id, plan.plan_id, plan.plan_version, run.status.value, self._json(payload), run.created_at, run.updated_at),
            )
            for task in plan.tasks:
                task_payload = {"task": task.model_dump(mode="json"), "task_result": {"task_id": task.task_id, "status": TaskStatus.CONFIRMED.value, "attempts": 0}}
                connection.execute(
                    "INSERT INTO task_runs(run_id, task_id, attempt, payload_json, status, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (run.run_id, task.task_id, 0, self._json(task_payload), TaskStatus.CONFIRMED.value, utc_now()),
                )
            self._event(connection, "run_created", {"plan_id": plan.plan_id, "plan_version": plan.plan_version}, run.run_id)
        self.append_event(
            ResearchEvent(
                run_id=run.run_id,
                type=EventType.PLAN_CONFIRMED,
                payload={"plan_version": plan.plan_version},
            )
        )
        return self.get_run(run.run_id)

    def _get_run_row(self, run_id: str) -> sqlite3.Row:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise NotFoundError("run not found")
        return row

    def mark_run_running(self, run_id: str) -> dict[str, Any]:
        row = self._get_run_row(run_id)
        if row["status"] not in {RunStatus.CONFIRMED.value, RunStatus.RUNNING.value}:
            raise InvalidStateTransitionError("only confirmed or interrupted running runs can execute")
        payload = self._load_json(row["payload_json"])
        payload.update({"status": RunStatus.RUNNING.value, "updated_at": utc_now()})
        with self._connection() as connection:
            connection.execute(
                "UPDATE runs SET status = ?, payload_json = ?, updated_at = ? WHERE run_id = ?",
                (RunStatus.RUNNING.value, self._json(payload), payload["updated_at"], run_id),
            )
            self._event(connection, "run_started", {}, run_id)
        return self.get_run(run_id)

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        row = self._get_run_row(run_id)
        if row["status"] not in {RunStatus.CONFIRMED.value, RunStatus.RUNNING.value}:
            raise InvalidStateTransitionError("only confirmed or running runs can be cancelled")
        payload = self._load_json(row["payload_json"])
        payload.update({"status": RunStatus.CANCELLED.value, "updated_at": utc_now()})
        with self._connection() as connection:
            connection.execute(
                "UPDATE runs SET status = ?, payload_json = ?, updated_at = ? WHERE run_id = ?",
                (RunStatus.CANCELLED.value, self._json(payload), payload["updated_at"], run_id),
            )
            task_rows = connection.execute(
                "SELECT task_id, attempt, payload_json, status FROM task_runs WHERE run_id = ?",
                (run_id,),
            ).fetchall()
            for task_row in task_rows:
                if task_row["status"] in {
                    TaskStatus.SUCCEEDED.value,
                    TaskStatus.FAILED.value,
                    TaskStatus.CANCELLED.value,
                }:
                    continue
                task_payload = self._load_json(task_row["payload_json"])
                task_result = dict(task_payload.get("task_result") or {})
                task_result.update(
                    {
                        "task_id": task_row["task_id"],
                        "status": TaskStatus.CANCELLED.value,
                        "attempts": int(task_result.get("attempts") or task_row["attempt"]),
                        "error_code": "RUN_CANCELLED",
                        "error_message": "运行已取消。",
                    }
                )
                task_payload["task_result"] = task_result
                connection.execute(
                    "UPDATE task_runs SET payload_json = ?, status = ?, updated_at = ? "
                    "WHERE run_id = ? AND task_id = ? AND attempt = ?",
                    (
                        self._json(task_payload),
                        TaskStatus.CANCELLED.value,
                        utc_now(),
                        run_id,
                        task_row["task_id"],
                        task_row["attempt"],
                    ),
                )
            self._event(connection, "run_cancelled", {}, run_id)
        return self.get_run(run_id)

    def _latest_task_payloads(self, run_id: str) -> dict[str, dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT task_id, payload_json FROM task_runs WHERE run_id = ? ORDER BY task_id, attempt DESC", (run_id,)
            ).fetchall()
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            latest.setdefault(row["task_id"], self._load_json(row["payload_json"]))
        return latest

    def execution_state(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        latest = self._latest_task_payloads(run_id)
        task_results = {task_id: payload["task_result"] for task_id, payload in latest.items() if payload.get("task_result")}
        sources: dict[str, dict[str, Any]] = {}
        task_source_refs: dict[str, list[dict[str, Any]]] = {}
        for task_id, payload in latest.items():
            sources.update(payload.get("sources") or {})
            refs = payload.get("task_source_refs") or []
            if refs:
                task_source_refs[task_id] = refs
        return {
            "topic": run["plan"]["topic"],
            "plan": run["plan"],
            "run": run["run"],
            "confirmed_plan": True,
            "task_results": task_results,
            "sources": sources,
            "task_source_refs": task_source_refs,
        }

    def persist_graph_result(self, run_id: str, result: dict[str, Any]) -> dict[str, Any]:
        graph_run = ResearchRun.model_validate(result.get("run") or {})
        if graph_run.run_id != run_id:
            raise RepositoryError("graph result run_id does not match persisted run")
        task_results = result.get("task_results") or {}
        sources = result.get("sources") or {}
        source_refs = result.get("task_source_refs") or {}
        report_artifact = dict(result.get("report_artifact") or {})
        with self._connection() as connection:
            row = connection.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise NotFoundError("run not found")
            if row["status"] == RunStatus.CANCELLED.value:
                # A synchronous worker can finish after another request cancelled the run.
                # The cancellation is terminal, so its state must never be overwritten.
                self._event(connection, "run_result_discarded", {"status": graph_run.status.value}, run_id)
                return self.get_run(run_id)
            connection.execute(
                "UPDATE runs SET status = ?, payload_json = ?, updated_at = ? WHERE run_id = ?",
                (graph_run.status.value, self._json(graph_run.model_dump(mode="json")), graph_run.updated_at, run_id),
            )
            for task_id, task_result in task_results.items():
                attempt = int(task_result.get("attempts") or 1)
                used_source_ids = set(task_result.get("source_ids") or [])
                for claim in task_result.get("claims") or []:
                    used_source_ids.update(claim.get("source_ids") or [])
                task_payload = {
                    "task_result": task_result,
                    "sources": {source_id: sources[source_id] for source_id in used_source_ids if source_id in sources},
                    "task_source_refs": list(source_refs.get(task_id) or []),
                }
                connection.execute(
                    "INSERT OR REPLACE INTO task_runs(run_id, task_id, attempt, payload_json, status, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (run_id, task_id, attempt, self._json(task_payload), str(task_result.get("status") or TaskStatus.FAILED.value), utc_now()),
                )
            if report_artifact:
                version = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM report_versions WHERE run_id = ?", (run_id,)
                ).fetchone()["version"]
                report_artifact["report_version"] = version
                connection.execute(
                    "INSERT INTO report_versions(report_id, run_id, version, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (report_artifact["report_id"], run_id, version, self._json(report_artifact), report_artifact.get("created_at") or utc_now()),
                )
            self._event(connection, "run_finished", {"status": graph_run.status.value}, run_id)
        return self.get_run(run_id)

    def prepare_task_retry(self, run_id: str, task_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        latest = self._latest_task_payloads(run_id).get(task_id)
        if latest is None:
            raise NotFoundError("task run not found")
        task_result = latest.get("task_result") or {}
        if task_result.get("status") != TaskStatus.FAILED.value:
            raise InvalidStateTransitionError("only failed tasks can be retried")
        if run["run"]["status"] not in {RunStatus.FAILED.value, RunStatus.CONFIRMED.value}:
            raise InvalidStateTransitionError("only failed or confirmed runs can retry a task")
        row = self._get_run_row(run_id)
        payload = self._load_json(row["payload_json"])
        payload.update({"status": RunStatus.CONFIRMED.value, "updated_at": utc_now()})
        with self._connection() as connection:
            connection.execute(
                "UPDATE runs SET status = ?, payload_json = ?, updated_at = ? WHERE run_id = ?",
                (RunStatus.CONFIRMED.value, self._json(payload), payload["updated_at"], run_id),
            )
            self._event(connection, "task_retry_requested", {"attempt": int(task_result.get("attempts") or 0) + 1}, run_id, task_id)
        state = self.execution_state(run_id)
        state["retry_task_id"] = task_id
        return state

    def get_run(self, run_id: str) -> dict[str, Any]:
        row = self._get_run_row(run_id)
        plan = self.get_plan(row["plan_id"], row["plan_version"])
        latest = self._latest_task_payloads(run_id)
        with self._connection() as connection:
            reports = connection.execute(
                "SELECT payload_json FROM report_versions WHERE run_id = ? ORDER BY version", (run_id,)
            ).fetchall()
        task_runs = []
        for task_id, payload in latest.items():
            result = payload.get("task_result") or {}
            task_runs.append(
                {
                    "task_id": task_id,
                    "attempt": int(result.get("attempts") or 0),
                    "status": result.get("status") or TaskStatus.CONFIRMED.value,
                    "payload": payload,
                }
            )
        task_runs.sort(key=lambda item: item["task_id"])
        return {
            "run": self._load_json(row["payload_json"]),
            "plan": plan["plan"],
            "plan_status": plan["status"],
            "task_runs": task_runs,
            "report_versions": [self._load_json(row["payload_json"]) for row in reports],
        }
