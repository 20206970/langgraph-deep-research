"""Configuration management for LangGraph Deep Research"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

# Load .env file
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path, override=True)


class SearchConfig(BaseModel):
    """Search backend configuration"""
    api: str = Field(default="tavily", description="Search API backend")
    tavily_api_key: Optional[str] = Field(default=None, description="Tavily API key")


class LLMConfig(BaseModel):
    """LLM configuration"""
    provider: str = Field(default="openai", description="LLM provider")
    api_key: str = Field(default="", description="API key")
    base_url: str = Field(default="https://api.openai.com/v1", description="Base URL")
    model: str = Field(default="gpt-4", description="Model name")
    temperature: float = Field(default=0.0, description="Temperature")


class EmbeddingsConfig(BaseModel):
    """Embeddings configuration, independent from the LLM provider."""

    provider: str = Field(default="huggingface", description="huggingface, dashscope, or openai")
    api_key: str = Field(default="", description="API key for embeddings")
    base_url: str = Field(default="https://api.openai.com/v1", description="Base URL for embeddings")
    model: str = Field(default="BAAI/bge-m3", description="Embeddings model")
    device: str = Field(default="auto", description="auto, cpu, cuda, or cuda:<index>")
    batch_size: int = Field(default=8, ge=1, description="Embedding batch size")
    max_length: int = Field(default=1024, ge=1, le=8192, description="Maximum tokens per embedding")
    normalize_embeddings: bool = Field(default=True, description="L2-normalize embedding vectors")


class MemoryConfig(BaseModel):
    """Memory configuration"""
    short_term_max_tokens: int = Field(default=2000, description="Short-term memory max tokens")
    long_term_persist_dir: str = Field(default="./chroma_data", description="ChromaDB persist directory")
    long_term_k: int = Field(default=3, description="Number of memories to retrieve")


class StorageConfig(BaseModel):
    """Durable storage used by plan versions, runs, and graph checkpoints."""

    sqlite_path: str = Field(default="./research.db", description="SQLite database path")


class Config(BaseSettings):
    """Main configuration"""
    search: SearchConfig = Field(default_factory=SearchConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embeddings: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)

    class Config:
        env_prefix = ""
        case_sensitive = False

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables"""
        configured_embeddings_provider = os.getenv("EMBEDDINGS_PROVIDER")
        embeddings_provider = (configured_embeddings_provider or "huggingface").lower()
        default_embeddings_model = "BAAI/bge-m3" if embeddings_provider == "huggingface" else "text-embedding-3-small"
        default_chroma_directory = "./chroma_data_bge_m3"
        return cls(
            search=SearchConfig(
                api=os.getenv("SEARCH_API", "tavily"),
                tavily_api_key=os.getenv("TAVILY_API_KEY"),
            ),
            llm=LLMConfig(
                provider=os.getenv("LLM_PROVIDER", "openai"),
                api_key=os.getenv("OPENAI_API_KEY", ""),
                base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                model=os.getenv("OPENAI_MODEL", "gpt-4"),
            ),
            embeddings=EmbeddingsConfig(
                provider=embeddings_provider,
                api_key=os.getenv("EMBEDDINGS_API_KEY", os.getenv("OPENAI_API_KEY", "")),
                base_url=os.getenv("EMBEDDINGS_BASE_URL", os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")),
                # Existing configs had no provider field and pointed EMBEDDINGS_MODEL at DashScope.
                # Treat them as a deliberate migration to the new local default.
                model=(
                    os.getenv("EMBEDDINGS_MODEL", default_embeddings_model)
                    if configured_embeddings_provider
                    else default_embeddings_model
                ),
                device=os.getenv("EMBEDDINGS_DEVICE", "auto"),
                batch_size=int(os.getenv("EMBEDDINGS_BATCH_SIZE", "8")),
                max_length=int(os.getenv("EMBEDDINGS_MAX_LENGTH", "1024")),
                normalize_embeddings=os.getenv("EMBEDDINGS_NORMALIZE", "true").lower() in {"1", "true", "yes", "on"},
            ),
            memory=MemoryConfig(
                short_term_max_tokens=int(os.getenv("SHORT_TERM_MAX_TOKENS", "2000")),
                long_term_persist_dir=(
                    os.getenv("CHROMA_PERSIST_DIR", default_chroma_directory)
                    if configured_embeddings_provider
                    else default_chroma_directory
                ),
                long_term_k=int(os.getenv("LONG_TERM_MEMORY_K", "3")),
            ),
            storage=StorageConfig(sqlite_path=os.getenv("RESEARCH_DB_PATH", "./research.db")),
        )


@lru_cache()
def get_config() -> Config:
    """Get cached configuration"""
    return Config.from_env()
