"""SQLite TTL cache for bounded, normalized live-search tool outputs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_query(query: str) -> str:
    """Normalize a query for deterministic cache keys without changing tool input."""
    return " ".join(str(query).strip().lower().split())


def provider_config_fingerprint(provider_config: dict[str, Any]) -> str:
    """Hash non-sensitive configuration so a provider change invalidates cached data."""
    canonical = json.dumps(provider_config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_cache_key(
    *,
    tool_name: str,
    query: str,
    provider_config: dict[str, Any],
    language: str,
    max_results: int,
    tool_version: str,
) -> str:
    """Hash every parameter that can change a live-search result contract."""
    payload = {
        "tool_name": tool_name,
        "query": normalize_query(query),
        "provider_config_fingerprint": provider_config_fingerprint(provider_config),
        "language": language,
        "max_results": max_results,
        "tool_version": tool_version,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"search_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True)
class SearchCacheEntry:
    """A cached tool payload and its immutable creation/expiry metadata."""

    payload: dict[str, Any]
    created_at: str
    expires_at: str


class SQLiteSearchCache:
    """Owns only the ``search_cache`` table inside the research SQLite database."""

    def __init__(self, database_path: str | Path, ttl_seconds: int):
        self.database_path = Path(database_path)
        self.ttl_seconds = ttl_seconds
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS search_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )

    def get(self, cache_key: str, *, now: datetime | None = None) -> SearchCacheEntry | None:
        now = now or _utc_now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json, created_at, expires_at FROM search_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if row is None:
                return None
            try:
                expires_at = datetime.fromisoformat(row["expires_at"])
                payload = json.loads(row["payload_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                connection.execute("DELETE FROM search_cache WHERE cache_key = ?", (cache_key,))
                return None
            if expires_at <= now:
                connection.execute("DELETE FROM search_cache WHERE cache_key = ?", (cache_key,))
                return None
            if not isinstance(payload, dict):
                connection.execute("DELETE FROM search_cache WHERE cache_key = ?", (cache_key,))
                return None
            return SearchCacheEntry(payload=payload, created_at=row["created_at"], expires_at=row["expires_at"])

    def put(self, cache_key: str, payload: dict[str, Any], *, now: datetime | None = None) -> SearchCacheEntry:
        now = now or _utc_now()
        created_at = now.isoformat()
        expires_at = (now + timedelta(seconds=self.ttl_seconds)).isoformat()
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO search_cache(cache_key, payload_json, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (cache_key, serialized, created_at, expires_at),
            )
        return SearchCacheEntry(payload=payload, created_at=created_at, expires_at=expires_at)

    def delete(self, cache_key: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM search_cache WHERE cache_key = ?", (cache_key,))
