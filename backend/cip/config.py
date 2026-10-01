"""Application settings, loaded from environment variables (prefix ``CIP_``)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CIP_", env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str = "sqlite+aiosqlite:///./cip.db"
    # Object storage for raw CSVs / reports. Local directory for now (S3-compatible store later).
    storage_dir: str = "./storage"
    # Allow self-service sign-up (creates a new organization). Disable in production.
    allow_registration: bool = True
    # Restart analyses that were interrupted (crash/deploy) when the API starts.
    resume_runs_on_startup: bool = True

    # --- Auth -------------------------------------------------------------
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60 * 8
    # Brute-force protection
    login_max_failures: int = 5            # consecutive failures before the account is locked
    login_lockout_minutes: int = 15
    login_ip_limit: int = 20               # login/register attempts per IP per window
    login_ip_window_seconds: int = 300

    # Fernet key (urlsafe base64, 32 bytes) used to encrypt OAuth tokens at rest.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_encryption_key: str | None = None

    # --- LLM (OpenRouter, OpenAI-compatible API) -------------------------
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openai/gpt-6-luna"
    openrouter_app_name: str = "Client Intelligence Platform"
    openrouter_site_url: str = "http://localhost:5173"
    llm_temperature: float = 0.2
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 3

    # --- Research ---------------------------------------------------------
    # Web search provider: "tavily", "brave" or "none".
    search_provider: str = "none"
    tavily_api_key: str | None = None
    brave_api_key: str | None = None
    crawler_max_pages: int = 15
    crawler_timeout_seconds: float = 20.0
    crawler_user_agent: str = "CIPResearchBot/0.1 (+https://example.com/bot)"
    max_competitors: int = 5

    # --- Source control OAuth --------------------------------------------
    github_client_id: str | None = None
    github_client_secret: str | None = None
    github_api_url: str = "https://api.github.com"
    gitlab_client_id: str | None = None
    gitlab_client_secret: str | None = None
    gitlab_url: str = "https://gitlab.com"
    oauth_redirect_base: str = "http://localhost:8000"

    # --- Repository scanning ---------------------------------------------
    repo_max_files_fetched: int = 40
    repo_max_file_bytes: int = 200_000
    large_repo_file_threshold: int = 5_000

    # --- Prioritization ---------------------------------------------------
    roadmap_top_n: int = 10
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openrouter_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
