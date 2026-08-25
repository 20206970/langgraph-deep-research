"""Configuration management for LangGraph Deep Research"""

import os
import json
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


class ModelRoleConfig(BaseModel):
    """Per-role generation settings; an empty model falls back to ``OPENAI_MODEL``."""

    model: str = Field(default="")
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1_200, ge=1)


class ModelPricing(BaseModel):
    """Explicit model pricing in currency units per one million tokens."""

    input_per_million: float = Field(ge=0.0)
    output_per_million: float = Field(ge=0.0)


class ModelRoutingConfig(BaseModel):
    """Role-to-model configuration and optional explicit price table."""

    router: ModelRoleConfig = Field(default_factory=lambda: ModelRoleConfig(max_tokens=256))
    planner: ModelRoleConfig = Field(default_factory=lambda: ModelRoleConfig(temperature=0.1, max_tokens=1_200))
    summarizer: ModelRoleConfig = Field(default_factory=lambda: ModelRoleConfig(max_tokens=4_096))
    reporter: ModelRoleConfig = Field(default_factory=lambda: ModelRoleConfig(temperature=0.1, max_tokens=4_096))
    repair: ModelRoleConfig = Field(default_factory=lambda: ModelRoleConfig(max_tokens=1_200))
    judge: ModelRoleConfig = Field(default_factory=lambda: ModelRoleConfig(max_tokens=1_200))
    pricing: dict[str, ModelPricing] = Field(default_factory=dict)

    def for_role(self, role: str) -> ModelRoleConfig:
        if role == "default":
            return ModelRoleConfig()
        try:
            return getattr(self, role)
        except AttributeError as error:
            raise ValueError(f"unknown LLM role: {role}") from error


class SearchCacheConfig(BaseModel):
    """SQLite TTL cache settings for live web and paper search only."""

    enabled: bool = Field(default=True)
    ttl_seconds: int = Field(default=86_400, ge=1)
    language: str = Field(default="auto", min_length=1, max_length=32)
    tool_version: str = Field(default="p1.3", min_length=1, max_length=100)


class RunBudgetConfig(BaseModel):
    """Default bounds applied when a new persisted or compatibility run starts."""

    max_tasks: int = Field(default=5, ge=1, le=7)
    max_search_attempts: int = Field(default=3, ge=1, le=10)
    max_format_repairs: int = Field(default=1, ge=0, le=3)
    max_total_tokens: int | None = Field(default=None, ge=1)
    max_estimated_cost: float | None = Field(default=None, gt=0.0)
    max_elapsed_seconds: int = Field(default=300, ge=1)


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


class TracingConfig(BaseModel):
    """Optional LangSmith tracing with conservative content capture defaults."""

    enabled: bool = Field(default=False)
    endpoint: str = Field(default="")
    api_key: str = Field(default="")
    project: str = Field(default="langgraph-deep-research-dev")
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    capture_content: bool = Field(default=False)
    retention_days: int = Field(default=14, ge=1)
    redact_patterns: list[str] = Field(default_factory=list)


class AuthConfig(BaseModel):
    """Authentication settings. The development default is rejected in production."""

    environment: str = Field(default="development", min_length=1, max_length=32)
    jwt_secret: str = Field(default="development-only-change-me-32-bytes", min_length=32)
    jwt_algorithm: str = Field(default="HS256", min_length=1, max_length=32)
    access_token_minutes: int = Field(default=480, ge=1, le=43_200)

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


class DocumentConfig(BaseModel):
    """Private document storage, ingestion, and chunking settings."""

    storage_root: str = Field(default="./document_data", min_length=1)
    chroma_persist_dir: str = Field(default="./chroma_data_documents", min_length=1)
    chroma_collection: str = Field(default="user_documents", min_length=1, max_length=128)
    max_file_bytes: int = Field(default=50 * 1024 * 1024, ge=1)
    user_quota_bytes: int = Field(default=500 * 1024 * 1024, ge=1)
    job_lease_seconds: int = Field(default=600, ge=1)
    job_max_attempts: int = Field(default=3, ge=1, le=10)
    purge_retention_days: int = Field(default=30, ge=1, le=365)
    converter: str = Field(default="docling", min_length=1, max_length=64)
    docling_ocr_enabled: bool = False
    markitdown_fallback_enabled: bool = True
    stage_timeout_seconds: int = Field(default=600, ge=1)
    parent_target_tokens: int = Field(default=500, ge=400, le=600)
    child_overlap_ratio: float = Field(default=0.12, ge=0.10, le=0.15)


class DocumentVLMConfig(BaseModel):
    """Optional vision model configuration, isolated from text generation settings."""

    provider: str = Field(default="", max_length=64)
    api_key: str = Field(default="")
    base_url: str = Field(default="")
    model: str = Field(default="", max_length=256)
    max_tokens: int = Field(default=1_024, ge=1, le=16_384)
    timeout_seconds: int = Field(default=60, ge=1, le=3_600)

    @property
    def is_configured(self) -> bool:
        return bool(self.provider.strip() and self.model.strip())


class RerankerConfig(BaseModel):
    """Parent-chunk reranker settings independent from the embeddings model."""

    provider: str = Field(default="flagembedding", min_length=1, max_length=64)
    model: str = Field(default="BAAI/bge-reranker-v2-m3", min_length=1, max_length=256)
    device: str = Field(default="auto", min_length=1, max_length=64)
    batch_size: int = Field(default=4, ge=1, le=256)
    max_length: int = Field(default=512, ge=1, le=8_192)
    top_k: int = Field(default=8, ge=1, le=100)


class DocumentRetrievalConfig(BaseModel):
    """Candidate and fusion bounds for user-document retrieval."""

    vector_top_k: int = Field(default=30, ge=1, le=200)
    bm25_top_k: int = Field(default=30, ge=1, le=200)
    rrf_k: int = Field(default=60, ge=1, le=1_000)
    parent_candidate_k: int = Field(default=16, ge=1, le=100)
    neighbor_window: int = Field(default=1, ge=0, le=5)


class Config(BaseSettings):
    """Main configuration"""
    search: SearchConfig = Field(default_factory=SearchConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embeddings: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    tracing: TracingConfig = Field(default_factory=TracingConfig)
    routing: ModelRoutingConfig = Field(default_factory=ModelRoutingConfig)
    search_cache: SearchCacheConfig = Field(default_factory=SearchCacheConfig)
    budget: RunBudgetConfig = Field(default_factory=RunBudgetConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    documents: DocumentConfig = Field(default_factory=DocumentConfig)
    document_vlm: DocumentVLMConfig = Field(default_factory=DocumentVLMConfig)
    reranker: RerankerConfig = Field(default_factory=RerankerConfig)
    document_retrieval: DocumentRetrievalConfig = Field(default_factory=DocumentRetrievalConfig)

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
        role_defaults = {
            "router": (0.0, 256),
            "planner": (0.1, 1_200),
            "summarizer": (0.0, 4_096),
            "reporter": (0.1, 4_096),
            "repair": (0.0, 1_200),
            "judge": (0.0, 1_200),
        }

        def role_config(role: str) -> ModelRoleConfig:
            temperature, max_tokens = role_defaults[role]
            prefix = role.upper()
            return ModelRoleConfig(
                model=os.getenv(f"{prefix}_MODEL", "").strip(),
                temperature=float(os.getenv(f"{prefix}_TEMPERATURE", str(temperature))),
                max_tokens=int(os.getenv(f"{prefix}_MAX_TOKENS", str(max_tokens))),
            )

        raw_pricing = os.getenv("MODEL_PRICING_JSON", "").strip()
        try:
            pricing_payload = json.loads(raw_pricing) if raw_pricing else {}
        except json.JSONDecodeError as error:
            raise ValueError("MODEL_PRICING_JSON must be a JSON object") from error
        if not isinstance(pricing_payload, dict):
            raise ValueError("MODEL_PRICING_JSON must be a JSON object")

        def optional_int(name: str) -> int | None:
            value = os.getenv(name, "").strip()
            return int(value) if value and value != "0" else None

        def optional_float(name: str) -> float | None:
            value = os.getenv(name, "").strip()
            return float(value) if value and value != "0" else None

        def env_bool(name: str, default: bool) -> bool:
            value = os.getenv(name)
            if value is None:
                return default
            return value.strip().lower() in {"1", "true", "yes", "on"}

        environment = os.getenv("APP_ENV", "development").strip().lower() or "development"
        configured_jwt_secret = os.getenv("AUTH_JWT_SECRET", "").strip()
        development_jwt_secret = AuthConfig.model_fields["jwt_secret"].default
        if environment in {"production", "prod"} and (
            not configured_jwt_secret
            or configured_jwt_secret == development_jwt_secret
            or len(configured_jwt_secret) < 32
        ):
            raise ValueError("AUTH_JWT_SECRET must be a non-development value of at least 32 characters in production")

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
            tracing=TracingConfig(
                enabled=os.getenv("LANGSMITH_TRACING", "false").lower() in {"1", "true", "yes", "on"},
                endpoint=os.getenv("LANGSMITH_ENDPOINT", ""),
                api_key=os.getenv("LANGSMITH_API_KEY", ""),
                project=os.getenv("LANGSMITH_PROJECT", "langgraph-deep-research-dev"),
                sample_rate=float(os.getenv("LANGSMITH_SAMPLE_RATE", "1.0")),
                capture_content=os.getenv("LANGSMITH_CAPTURE_CONTENT", "false").lower()
                in {"1", "true", "yes", "on"},
                retention_days=int(os.getenv("LANGSMITH_RETENTION_DAYS", "14")),
                redact_patterns=[
                    pattern.strip()
                    for pattern in os.getenv("LANGSMITH_REDACT_PATTERNS", "").split(",")
                    if pattern.strip()
                ],
            ),
            routing=ModelRoutingConfig(
                router=role_config("router"),
                planner=role_config("planner"),
                summarizer=role_config("summarizer"),
                reporter=role_config("reporter"),
                repair=role_config("repair"),
                judge=role_config("judge"),
                pricing=pricing_payload,
            ),
            search_cache=SearchCacheConfig(
                enabled=os.getenv("SEARCH_CACHE_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
                ttl_seconds=int(os.getenv("SEARCH_CACHE_TTL_SECONDS", "86400")),
                language=os.getenv("SEARCH_LANGUAGE", "auto"),
                tool_version=os.getenv("SEARCH_TOOL_VERSION", "p1.3"),
            ),
            budget=RunBudgetConfig(
                max_tasks=int(os.getenv("RUN_MAX_TASKS", "5")),
                max_search_attempts=int(os.getenv("RUN_MAX_SEARCH_ATTEMPTS", "3")),
                max_format_repairs=int(os.getenv("RUN_MAX_FORMAT_REPAIRS", "1")),
                max_total_tokens=optional_int("RUN_MAX_TOTAL_TOKENS"),
                max_estimated_cost=optional_float("RUN_MAX_ESTIMATED_COST"),
                max_elapsed_seconds=int(os.getenv("RUN_MAX_ELAPSED_SECONDS", "300")),
            ),
            auth=AuthConfig(
                environment=environment,
                jwt_secret=configured_jwt_secret or development_jwt_secret,
                jwt_algorithm=os.getenv("AUTH_JWT_ALGORITHM", "HS256"),
                access_token_minutes=int(os.getenv("AUTH_ACCESS_TOKEN_MINUTES", "480")),
            ),
            documents=DocumentConfig(
                storage_root=os.getenv("DOCUMENT_STORAGE_ROOT", "./document_data"),
                chroma_persist_dir=os.getenv("DOCUMENT_CHROMA_PERSIST_DIR", "./chroma_data_documents"),
                chroma_collection=os.getenv("DOCUMENT_CHROMA_COLLECTION", "user_documents"),
                max_file_bytes=int(os.getenv("DOCUMENT_MAX_FILE_BYTES", str(50 * 1024 * 1024))),
                user_quota_bytes=int(os.getenv("DOCUMENT_USER_QUOTA_BYTES", str(500 * 1024 * 1024))),
                job_lease_seconds=int(os.getenv("DOCUMENT_JOB_LEASE_SECONDS", "600")),
                job_max_attempts=int(os.getenv("DOCUMENT_JOB_MAX_ATTEMPTS", "3")),
                purge_retention_days=int(os.getenv("DOCUMENT_PURGE_RETENTION_DAYS", "30")),
                converter=os.getenv("DOCUMENT_CONVERTER", "docling"),
                docling_ocr_enabled=env_bool("DOCUMENT_DOCLING_OCR_ENABLED", False),
                markitdown_fallback_enabled=env_bool("DOCUMENT_MARKITDOWN_FALLBACK_ENABLED", True),
                stage_timeout_seconds=int(os.getenv("DOCUMENT_STAGE_TIMEOUT_SECONDS", "600")),
                parent_target_tokens=int(os.getenv("DOCUMENT_PARENT_TARGET_TOKENS", "500")),
                child_overlap_ratio=float(os.getenv("DOCUMENT_CHILD_OVERLAP_RATIO", "0.12")),
            ),
            document_vlm=DocumentVLMConfig(
                provider=os.getenv("DOCUMENT_VLM_PROVIDER", ""),
                api_key=os.getenv("DOCUMENT_VLM_API_KEY", ""),
                base_url=os.getenv("DOCUMENT_VLM_BASE_URL", ""),
                model=os.getenv("DOCUMENT_VLM_MODEL", ""),
                max_tokens=int(os.getenv("DOCUMENT_VLM_MAX_TOKENS", "1024")),
                timeout_seconds=int(os.getenv("DOCUMENT_VLM_TIMEOUT_SECONDS", "60")),
            ),
            reranker=RerankerConfig(
                provider=os.getenv("RERANKER_PROVIDER", "flagembedding"),
                model=os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"),
                device=os.getenv("RERANKER_DEVICE", "auto"),
                batch_size=int(os.getenv("RERANKER_BATCH_SIZE", "4")),
                max_length=int(os.getenv("RERANKER_MAX_LENGTH", "512")),
                top_k=int(os.getenv("RERANKER_TOP_K", "8")),
            ),
            document_retrieval=DocumentRetrievalConfig(
                vector_top_k=int(os.getenv("DOCUMENT_VECTOR_TOP_K", "30")),
                bm25_top_k=int(os.getenv("DOCUMENT_BM25_TOP_K", "30")),
                rrf_k=int(os.getenv("DOCUMENT_RRF_K", "60")),
                parent_candidate_k=int(os.getenv("DOCUMENT_PARENT_CANDIDATE_K", "16")),
                neighbor_window=int(os.getenv("DOCUMENT_NEIGHBOR_WINDOW", "1")),
            ),
        )


@lru_cache()
def get_config() -> Config:
    """Get cached configuration"""
    return Config.from_env()
