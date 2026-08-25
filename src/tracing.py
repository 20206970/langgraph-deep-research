"""Optional LangSmith callback construction with local failure isolation."""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from src.config import TracingConfig
from src.events import redact_payload


def _redact_trace_value(value: Any, patterns: list[str]) -> Any:
    """Keep trace structure while removing private content and credential-like values."""
    redacted = redact_payload(value, max_depth=5)

    def apply_patterns(item: Any) -> Any:
        if isinstance(item, str):
            for pattern in patterns:
                try:
                    item = re.sub(pattern, "[REDACTED_PATTERN]", item)
                except re.error:
                    continue
            return item
        if isinstance(item, dict):
            return {key: apply_patterns(child) for key, child in item.items()}
        if isinstance(item, list):
            return [apply_patterns(child) for child in item]
        return item

    return apply_patterns(redacted)


def build_trace_callbacks(
    config: TracingConfig,
    metadata: dict[str, str],
    *,
    force_hide_content: bool = False,
) -> list[Any]:
    """Build a LangChain tracer only when explicitly configured and usable."""
    if not config.enabled or not config.api_key:
        return []

    try:
        from langsmith import Client
        from langchain_core.tracers import LangChainTracer

        redactor = lambda value: _redact_trace_value(value, config.redact_patterns)

        client_kwargs: dict[str, Any] = {
            "api_key": config.api_key,
            "tracing_sampling_rate": config.sample_rate,
            # A private-document run can place source chunks in agent tool messages.
            # Even an explicitly permissive general tracing configuration must not
            # export those chunks to LangSmith.
            "hide_inputs": True if force_hide_content or not config.capture_content else redactor,
            "hide_outputs": True if force_hide_content or not config.capture_content else redactor,
            "hide_metadata": redactor,
        }
        if config.endpoint:
            client_kwargs["api_url"] = config.endpoint
        client = Client(**client_kwargs)
        tracer = LangChainTracer(
            project_name=config.project,
            client=client,
            metadata={**metadata, "redacted": "true"},
            tags=["langgraph-deep-research"],
        )
        return [tracer]
    except Exception as error:
        logger.warning("LangSmith tracing disabled for this run: {}", str(error)[:300])
        return []
