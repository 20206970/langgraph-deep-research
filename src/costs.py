"""Usage extraction and explicit, configuration-backed LLM cost estimation."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.config import ModelPricing


def _messages(response: Any) -> Iterable[Any]:
    if isinstance(response, dict):
        return response.get("messages", []) or []
    messages = getattr(response, "messages", None)
    return messages or []


def _normalize_usage(raw_usage: Any) -> dict[str, int]:
    if not isinstance(raw_usage, dict):
        return {}
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "total_tokens": ("total_tokens",),
    }
    normalized: dict[str, int] = {}
    for key, candidates in aliases.items():
        for candidate in candidates:
            value = raw_usage.get(candidate)
            if isinstance(value, int) and value >= 0:
                normalized[key] = value
                break
    if "total_tokens" not in normalized and {"input_tokens", "output_tokens"} <= normalized.keys():
        normalized["total_tokens"] = normalized["input_tokens"] + normalized["output_tokens"]
    return normalized


def extract_token_usage(response: Any) -> dict[str, int]:
    """Aggregate provider usage exposed by AI messages without requiring a provider SDK."""
    total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    found = False
    seen: set[int] = set()
    for message in _messages(response):
        if id(message) in seen:
            continue
        seen.add(id(message))
        usage = getattr(message, "usage_metadata", None)
        if not isinstance(usage, dict):
            response_metadata = getattr(message, "response_metadata", {}) or {}
            usage = response_metadata.get("token_usage") if isinstance(response_metadata, dict) else None
        normalized = _normalize_usage(usage)
        if not normalized:
            continue
        found = True
        for key, value in normalized.items():
            total[key] += value
    return total if found else {}


def estimate_cost(
    token_usage: dict[str, int],
    model: str,
    pricing: dict[str, ModelPricing],
) -> tuple[float | None, str]:
    """Estimate cost only with both usage and an explicit price entry."""
    if not token_usage:
        return None, "unavailable"
    price = pricing.get(model)
    if price is None:
        return None, "unavailable"
    input_tokens = token_usage.get("input_tokens", 0)
    output_tokens = token_usage.get("output_tokens", 0)
    return (
        round(
            input_tokens * price.input_per_million / 1_000_000
            + output_tokens * price.output_per_million / 1_000_000,
            12,
        ),
        "estimated",
    )
