'''
Author: lihuan
Date: 2026-08-17 12:15:44
LastEditors: lihuan
LastEditTime: 2026-08-17 12:49:20
Email: 17719495105@163.com

Application settings — single source of truth for runtime configuration.
'''

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration, loaded from environment variables / ``.env``.

    See ``.env.example`` for the full list with defaults.
    """

    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[2] / ".env", extra="ignore")

    app_name: str = "agent-backend"
    env: str = "dev"
    debug: bool = False
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"

    # --- PostgreSQL (app data + langgraph checkpointer + pgvector) ---
    database_dsn: str = ""
    db_pool_min: int = 1
    db_pool_max: int = 10

    # --- JWT ---
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 10080  # 7 days

    # --- RustFS (S3-compatible object storage) ---
    rustfs_endpoint: str = ""
    rustfs_access_key: str = ""
    rustfs_secret_key: str = ""
    rustfs_region: str = "us-east-1"
    rustfs_bucket: str = "agent-files"
    rustfs_presign_expire_seconds: int = 3600

    # --- OpenSandbox pool ---
    opensandbox_domain: str = ""
    opensandbox_api_key: str = ""
    opensandbox_image: str = "opensandbox/code-interpreter:v1.1.0"
    opensandbox_max_idle: int = 3
    opensandbox_use_server_proxy: bool = True  # required on macOS
    sandbox_ttl_seconds: int = 600  # aligns with the server's default TTL
    sandbox_execute_timeout: int = 120  # per-command timeout (seconds)

    # --- LLM (OpenAI-compatible) ---
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.7
    llm_supports_vision: bool = False

    # --- Embedding (independent OpenAI-compatible endpoint) ---
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "bge-m3"
    embedding_dimensions: int = 1024  # must match chunks.embedding vector(N)
    embedding_batch_size: int = 16

    # --- Agent ---
    agent_todos_enabled: bool = False  # TodoListMiddleware is opt-in in 0.7.x
    # Concurrent generations per user (multiple conversations running at
    # once). Each conversation is still single-flight; the limiter only
    # bounds the user's total in-flight producers (each holds a sandbox).
    max_concurrent_agents_per_user: int = 4

    # --- Zhipu Web Search ---
    zhipu_api_key: str = ""

    # --- RAG ---
    rag_chunk_size: int = 800
    rag_chunk_overlap: int = 120
    rag_max_results: int = 5
    rag_max_file_bytes: int = 50 * 1024 * 1024

    # --- CORS ---
    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
