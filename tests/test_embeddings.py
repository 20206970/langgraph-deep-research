import sys
from types import SimpleNamespace

from src import config as config_module
from src.config import Config, EmbeddingsConfig
from src.memory import long_term


def test_embeddings_default_to_local_bge_m3(monkeypatch):
    for key in (
        "EMBEDDINGS_PROVIDER",
        "EMBEDDINGS_MODEL",
        "EMBEDDINGS_DEVICE",
        "EMBEDDINGS_BATCH_SIZE",
        "EMBEDDINGS_MAX_LENGTH",
        "EMBEDDINGS_NORMALIZE",
        "CHROMA_PERSIST_DIR",
    ):
        monkeypatch.delenv(key, raising=False)

    config = Config.from_env()

    assert config.embeddings.provider == "huggingface"
    assert config.embeddings.model == "BAAI/bge-m3"
    assert config.embeddings.batch_size == 8
    assert config.embeddings.max_length == 1024
    assert config.memory.long_term_persist_dir == "./chroma_data_bge_m3"


def test_legacy_dashscope_environment_migrates_to_bge_m3(monkeypatch):
    monkeypatch.delenv("EMBEDDINGS_PROVIDER", raising=False)
    monkeypatch.setenv("EMBEDDINGS_MODEL", "text-embedding-v1")
    monkeypatch.setenv("CHROMA_PERSIST_DIR", "./chroma_data")

    config = Config.from_env()

    assert config.embeddings.provider == "huggingface"
    assert config.embeddings.model == "BAAI/bge-m3"
    assert config.memory.long_term_persist_dir == "./chroma_data_bge_m3"


def test_huggingface_embeddings_receive_bge_runtime_settings(monkeypatch):
    captured = {}

    class FakeHuggingFaceEmbeddings:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self._client = SimpleNamespace(max_seq_length=None)

    config = Config(
        embeddings=EmbeddingsConfig(
            provider="huggingface",
            model="BAAI/bge-m3",
            device="cuda:1",
            batch_size=12,
            max_length=1024,
            normalize_embeddings=True,
        )
    )
    monkeypatch.setattr(config_module, "get_config", lambda: config)
    monkeypatch.setitem(
        sys.modules,
        "langchain_huggingface",
        SimpleNamespace(HuggingFaceEmbeddings=FakeHuggingFaceEmbeddings),
    )

    embeddings = long_term._get_embeddings_model()

    assert captured["model_name"] == "BAAI/bge-m3"
    assert captured["model_kwargs"] == {"device": "cuda:1"}
    assert captured["encode_kwargs"] == {"batch_size": 12, "normalize_embeddings": True}
    assert embeddings._client.max_seq_length == 1024
