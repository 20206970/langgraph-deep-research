"""Optional VLM adapters kept strictly separate from the research text model settings."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from src.config import DocumentVLMConfig

from .conversion import ExtractedImage


class VisionProviderError(RuntimeError):
    """A VLM response was unavailable, malformed, or unusable for a visual enhancement."""


class VisionDescription(BaseModel):
    """Schema stored as visual metadata; it is never represented as PDF source text."""

    description: str = Field(min_length=1, max_length=8_000)
    caption_completion: str | None = Field(default=None, max_length=2_000)
    entities: list[str] = Field(default_factory=list, max_length=100)
    trends: list[str] = Field(default_factory=list, max_length=100)
    uncertainty: str = Field(default="", max_length=2_000)


class VisionProvider(Protocol):
    def describe(self, image_path: Path, *, caption: str | None = None) -> VisionDescription:
        """Return a validated visual description or raise ``VisionProviderError``."""


_VISION_PROMPT = """Analyze this figure from a user-provided research paper. Return JSON only with:
description (required factual visual summary), caption_completion (optional), entities (array),
trends (array), and uncertainty (what cannot be determined). This is a visual enhancement, not
a quotation from the paper; do not invent values, axes, or claims that are not visible."""
_PROMPT_FINGERPRINT = hashlib.sha256(_VISION_PROMPT.encode("utf-8")).hexdigest()[:16]


def vision_prompt_fingerprint() -> str:
    return _PROMPT_FINGERPRINT


def _mime_type(image_path: Path) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(image_path.suffix.lower(), "image/png")


def _parse_json_response(value: Any) -> VisionDescription:
    if isinstance(value, dict):
        payload = value
    elif isinstance(value, str):
        text = value.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else ""
            text = text.rsplit("```", 1)[0].strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise VisionProviderError("VLM response was not valid JSON") from error
    else:
        raise VisionProviderError("VLM response did not contain structured content")
    try:
        return VisionDescription.model_validate(payload)
    except ValidationError as error:
        raise VisionProviderError("VLM response did not match the visual schema") from error


class OpenAICompatibleVisionProvider:
    """OpenAI-compatible multimodal chat endpoint. It never reads text-model environment variables."""

    def __init__(self, config: DocumentVLMConfig, *, client: httpx.Client | None = None):
        self.config = config
        self._client = client or httpx.Client(timeout=config.timeout_seconds)

    def describe(self, image_path: Path, *, caption: str | None = None) -> VisionDescription:
        try:
            encoded_image = base64.b64encode(image_path.read_bytes()).decode("ascii")
        except OSError as error:
            raise VisionProviderError("extracted image is unavailable") from error
        context = f"\nExisting extracted caption context: {caption}" if caption else ""
        payload = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _VISION_PROMPT + context},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{_mime_type(image_path)};base64,{encoded_image}"},
                        },
                    ],
                }
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        endpoint = self.config.base_url.rstrip("/") + "/chat/completions"
        try:
            response = self._client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
            response_payload = response.json()
            content = response_payload["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            raise VisionProviderError("OpenAI-compatible VLM request failed") from error
        return _parse_json_response(content)


class HuggingFaceVisionProvider:
    """Local HuggingFace adapter for models exposing an image-to-text pipeline."""

    def __init__(
        self,
        config: DocumentVLMConfig,
        *,
        runner: Callable[..., Any] | None = None,
    ):
        self.config = config
        self._runner = runner

    def _pipeline(self) -> Callable[..., Any]:
        if self._runner is not None:
            return self._runner
        try:
            from transformers import pipeline
        except ImportError as error:
            raise VisionProviderError("transformers is required for the HuggingFace VLM provider") from error
        device = 0 if self.config.provider.lower().endswith("cuda") else -1
        self._runner = pipeline("image-to-text", model=self.config.model, device=device)
        return self._runner

    def describe(self, image_path: Path, *, caption: str | None = None) -> VisionDescription:
        try:
            result = self._pipeline()(str(image_path), max_new_tokens=self.config.max_tokens)
            if isinstance(result, list) and result:
                result = result[0]
            content = result.get("generated_text") if isinstance(result, dict) else result
        except VisionProviderError:
            raise
        except Exception as error:
            raise VisionProviderError("HuggingFace VLM request failed") from error
        return _parse_json_response(content)


def build_vision_provider(config: DocumentVLMConfig) -> VisionProvider | None:
    """Create only an explicitly configured provider; there is no OPENAI_MODEL fallback path."""

    if not config.is_configured:
        return None
    provider = config.provider.strip().lower()
    if provider in {"openai", "openai-compatible", "gpt"}:
        return OpenAICompatibleVisionProvider(config)
    if provider in {"huggingface", "hf", "huggingface-cuda"}:
        return HuggingFaceVisionProvider(config)
    raise VisionProviderError("unsupported document VLM provider")


@dataclass(frozen=True)
class VisionEnrichment:
    """Outcome for one image. Failure is local to that image and never fails text ingestion."""

    image: ExtractedImage
    status: str
    metadata: dict[str, Any]


def enrich_image(provider: VisionProvider, image: ExtractedImage, *, image_path: Path, config: DocumentVLMConfig) -> VisionEnrichment:
    """Call a provider and attach only structured, non-source metadata to the image record."""

    description = provider.describe(image_path, caption=image.caption)
    metadata = {
        **description.model_dump(mode="json"),
        "provider": config.provider,
        "model": config.model,
        "prompt_fingerprint": vision_prompt_fingerprint(),
        "is_visual_enhancement": True,
    }
    return VisionEnrichment(image=image, status="succeeded", metadata=metadata)
