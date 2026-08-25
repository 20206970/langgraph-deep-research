import pytest

from src.config import Config


def _clear_document_environment(monkeypatch):
    for name in (
        "APP_ENV",
        "AUTH_JWT_SECRET",
        "AUTH_JWT_ALGORITHM",
        "AUTH_ACCESS_TOKEN_MINUTES",
        "DOCUMENT_STORAGE_ROOT",
        "DOCUMENT_CHROMA_PERSIST_DIR",
        "DOCUMENT_CHROMA_COLLECTION",
        "DOCUMENT_MAX_FILE_BYTES",
        "DOCUMENT_USER_QUOTA_BYTES",
        "DOCUMENT_JOB_LEASE_SECONDS",
        "DOCUMENT_JOB_MAX_ATTEMPTS",
        "DOCUMENT_PURGE_RETENTION_DAYS",
        "DOCUMENT_CONVERTER",
        "DOCUMENT_DOCLING_OCR_ENABLED",
        "DOCUMENT_MARKITDOWN_FALLBACK_ENABLED",
        "DOCUMENT_STAGE_TIMEOUT_SECONDS",
        "DOCUMENT_PARENT_TARGET_TOKENS",
        "DOCUMENT_CHILD_OVERLAP_RATIO",
        "DOCUMENT_VECTOR_TOP_K",
        "DOCUMENT_BM25_TOP_K",
        "DOCUMENT_RRF_K",
        "DOCUMENT_PARENT_CANDIDATE_K",
        "DOCUMENT_NEIGHBOR_WINDOW",
        "DOCUMENT_VLM_PROVIDER",
        "DOCUMENT_VLM_API_KEY",
        "DOCUMENT_VLM_BASE_URL",
        "DOCUMENT_VLM_MODEL",
        "DOCUMENT_VLM_MAX_TOKENS",
        "DOCUMENT_VLM_TIMEOUT_SECONDS",
        "RERANKER_PROVIDER",
        "RERANKER_MODEL",
        "RERANKER_DEVICE",
        "RERANKER_BATCH_SIZE",
        "RERANKER_MAX_LENGTH",
        "RERANKER_TOP_K",
    ):
        monkeypatch.delenv(name, raising=False)


def test_document_config_defaults_are_isolated_from_long_term_memory(monkeypatch):
    _clear_document_environment(monkeypatch)

    config = Config.from_env()

    assert config.documents.max_file_bytes == 50 * 1024 * 1024
    assert config.documents.user_quota_bytes == 500 * 1024 * 1024
    assert config.documents.chroma_persist_dir != config.memory.long_term_persist_dir
    assert config.documents.chroma_collection == "user_documents"
    assert config.documents.docling_ocr_enabled is False
    assert config.documents.child_overlap_ratio == pytest.approx(0.12)
    assert config.document_vlm.is_configured is False
    assert config.reranker.model == "BAAI/bge-reranker-v2-m3"


def test_document_environment_overrides_parse_all_model_roles(monkeypatch):
    _clear_document_environment(monkeypatch)
    monkeypatch.setenv("DOCUMENT_STORAGE_ROOT", "D:/private-documents")
    monkeypatch.setenv("DOCUMENT_MAX_FILE_BYTES", "1048576")
    monkeypatch.setenv("DOCUMENT_DOCLING_OCR_ENABLED", "true")
    monkeypatch.setenv("DOCUMENT_CHILD_OVERLAP_RATIO", "0.15")
    monkeypatch.setenv("DOCUMENT_VLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("DOCUMENT_VLM_MODEL", "gpt-4o")
    monkeypatch.setenv("DOCUMENT_VLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("RERANKER_DEVICE", "cuda:1")
    monkeypatch.setenv("RERANKER_TOP_K", "12")
    monkeypatch.setenv("DOCUMENT_RRF_K", "80")

    config = Config.from_env()

    assert config.documents.storage_root == "D:/private-documents"
    assert config.documents.max_file_bytes == 1_048_576
    assert config.documents.docling_ocr_enabled is True
    assert config.documents.child_overlap_ratio == pytest.approx(0.15)
    assert config.document_vlm.is_configured is True
    assert config.document_vlm.model == "gpt-4o"
    assert config.reranker.device == "cuda:1"
    assert config.reranker.top_k == 12
    assert config.document_retrieval.rrf_k == 80


def test_production_rejects_missing_or_development_jwt_secret(monkeypatch):
    _clear_document_environment(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(ValueError, match="AUTH_JWT_SECRET"):
        Config.from_env()

    monkeypatch.setenv("AUTH_JWT_SECRET", "development-only-change-me")
    with pytest.raises(ValueError, match="AUTH_JWT_SECRET"):
        Config.from_env()

    monkeypatch.setenv("AUTH_JWT_SECRET", "a-strong-production-secret")
    assert Config.from_env().auth.jwt_secret == "a-strong-production-secret"


def test_vlm_does_not_inherit_the_text_generation_model(monkeypatch):
    _clear_document_environment(monkeypatch)
    monkeypatch.setenv("OPENAI_MODEL", "text-model")

    config = Config.from_env()

    assert config.llm.model == "text-model"
    assert config.document_vlm.model == ""
    assert config.document_vlm.is_configured is False
