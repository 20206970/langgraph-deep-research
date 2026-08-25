"""CLI host for a separately deployed document-ingestion worker."""

from __future__ import annotations

import argparse
import importlib
import socket
from typing import Any

from src.config import get_config

from .jobs import DocumentWorker, IngestionHandler
from .repository import DocumentRepository


def _load_handler(reference: str) -> IngestionHandler:
    module_name, separator, attribute = reference.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("handler must use the module:function format")
    handler: Any = getattr(importlib.import_module(module_name), attribute)
    if not callable(handler):
        raise ValueError("configured ingestion handler must be callable")
    return handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Run private document ingestion jobs")
    parser.add_argument("--handler", required=True, help="P2.3 processor in module:function form")
    parser.add_argument("--once", action="store_true", help="Process at most one leased job")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()

    config = get_config()
    worker = DocumentWorker(
        DocumentRepository(config.storage.sqlite_path, config.documents),
        _load_handler(args.handler),
        worker_id=f"worker_{socket.gethostname()}_{__import__('os').getpid()}",
    )
    if args.once:
        print(worker.run_once().status)
        return
    worker.run_forever(poll_seconds=max(args.poll_seconds, 0.1))


if __name__ == "__main__":
    main()
