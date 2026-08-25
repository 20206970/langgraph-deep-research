import json

import httpx
import pytest

from src.config import DocumentVLMConfig
from src.documents.conversion import ExtractedImage
from src.documents.vision import (
    HuggingFaceVisionProvider,
    OpenAICompatibleVisionProvider,
    VisionProviderError,
    build_vision_provider,
    enrich_image,
)


def _payload(description: str = "A line chart shows improving accuracy"):
    return {
        "description": description,
        "caption_completion": "Evaluation curves",
        "entities": ["accuracy"],
        "trends": ["increases"],
        "uncertainty": "Exact values are not legible.",
    }


def test_no_document_vlm_configuration_returns_no_provider_without_text_model_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "text-only-model")
    monkeypatch.setenv("OPENAI_API_KEY", "text-only-key")

    assert build_vision_provider(DocumentVLMConfig()) is None


def test_openai_compatible_provider_uses_only_document_vlm_configuration(tmp_path):
    image = tmp_path / "figure.png"
    image.write_bytes(b"png-data")
    observed: dict[str, object] = {}

    def responder(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["headers"] = dict(request.headers)
        observed["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(_payload())}}]})

    config = DocumentVLMConfig(
        provider="openai-compatible",
        api_key="vision-key",
        base_url="https://vision.example/v1",
        model="vision-model",
        max_tokens=321,
    )
    provider = OpenAICompatibleVisionProvider(config, client=httpx.Client(transport=httpx.MockTransport(responder)))

    result = provider.describe(image, caption="Figure 1")

    assert result.description.startswith("A line chart")
    assert observed["url"] == "https://vision.example/v1/chat/completions"
    assert observed["body"]["model"] == "vision-model"
    assert observed["body"]["max_tokens"] == 321
    assert observed["headers"]["authorization"] == "Bearer vision-key"


def test_huggingface_provider_and_enrichment_require_valid_structured_json(tmp_path):
    image_path = tmp_path / "figure.png"
    image_path.write_bytes(b"png-data")
    config = DocumentVLMConfig(provider="huggingface", model="local-vision")
    provider = HuggingFaceVisionProvider(config, runner=lambda *_args, **_kwargs: [{"generated_text": json.dumps(_payload())}])
    image = ExtractedImage(image_id="img_1", content=b"png-data", caption="Figure 1")

    enriched = enrich_image(provider, image, image_path=image_path, config=config)

    assert enriched.status == "succeeded"
    assert enriched.metadata["is_visual_enhancement"] is True
    assert enriched.metadata["model"] == "local-vision"

    invalid = HuggingFaceVisionProvider(config, runner=lambda *_args, **_kwargs: [{"generated_text": "not JSON"}])
    with pytest.raises(VisionProviderError, match="valid JSON"):
        invalid.describe(image_path)
