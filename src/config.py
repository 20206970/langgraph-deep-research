"""Configuration management for LangGraph Deep Research"""

import os
from functools import lru_cache
from typing import Optional

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


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


class MemoryConfig(BaseModel):
    """Memory configuration"""
    short_term_max_tokens: int = Field(default=2000, description="Short-term memory max tokens")
    long_term_persist_dir: str = Field(default="./chroma_data", description="ChromaDB persist directory")
    long_term_k: int = Field(default=3, description="Number of memories to retrieve")


class Config(BaseSettings):
    """Main configuration"""
    search: SearchConfig = Field(default_factory=SearchConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)

    class Config:
        env_prefix = ""
        case_sensitive = False

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables"""
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
            memory=MemoryConfig(
                short_term_max_tokens=int(os.getenv("SHORT_TERM_MAX_TOKENS", "2000")),
                long_term_persist_dir=os.getenv("CHROMA_PERSIST_DIR", "./chroma_data"),
                long_term_k=int(os.getenv("LONG_TERM_K", "3")),
            ),
        )


@lru_cache()
def get_config() -> Config:
    """Get cached configuration"""
    return Config.from_env()