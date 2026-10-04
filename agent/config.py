"""Central configuration.

All runtime configuration is read once from the environment (and an optional
``.env`` file) into a single immutable :class:`Settings` object. Keeping config
in one place is what makes eval runs reproducible: a run's config captures the
model routing, thresholds, and rubric version, so results can be traced back to
exact settings.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core ---
    environment: str = "local"
    log_level: str = "INFO"

    # --- Datastores ---
    database_url: str = "postgresql+psycopg://triage:triage@localhost:5432/triage"
    redis_url: str = "redis://localhost:6379/0"

    # --- LLM ---
    anthropic_api_key: str | None = None
    model_classify: str = "claude-3-5-haiku-latest"
    model_decide: str = "claude-3-5-sonnet-latest"
    model_draft: str = "claude-3-5-sonnet-latest"
    model_judge: str = "claude-3-5-sonnet-latest"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # --- MCP ---
    mcp_transport: str = "stdio"
    mcp_ticketing_url: str = "http://localhost:8801/mcp"
    mcp_kb_url: str = "http://localhost:8802/mcp"
    mcp_customer_url: str = "http://localhost:8803/mcp"
    mcp_policy_url: str = "http://localhost:8804/mcp"
    mcp_ticketing_token: str = "dev-ticketing-token"
    mcp_kb_token: str = "dev-kb-token"
    mcp_customer_token: str = "dev-customer-token"
    mcp_policy_token: str = "dev-policy-token"

    # --- Safety thresholds ---
    confidence_escalate_threshold: float = 0.65

    # --- Eval ---
    eval_db_url: str = "sqlite:///./evals_results.db"
    judge_rubric_version: str = "v1"

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""

    return Settings()
