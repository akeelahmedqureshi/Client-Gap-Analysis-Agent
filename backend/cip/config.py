"""Application settings, loaded from environment variables (prefix ``CIP_``)."""

from functools import lru_cache
from typing import Literal

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
    # Where analyses execute:
    #   inline – asyncio tasks inside the API process (single API process only; no extra services)
    #   celery – Celery workers via Redis (multiple API processes/workers; needs `pip install .[worker]`)
    run_executor: Literal["inline", "celery"] = "inline"
    # Redis for the Celery broker, distributed run locks and shared login rate limits.
    redis_url: str | None = None
    max_concurrent_runs: int = 3          # inline executor: analyses running at once (others queue)
    run_lock_ttl_seconds: int = 300        # renewed while a run executes; expires if a worker dies
    celery_visibility_timeout_seconds: int = 6 * 3600

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
    max_competitors: int = 10           # Top-N competitive landscape (BRS 7.5)
    deep_competitors: int = 3           # of which deep-analysed (BRS 7.7)
    competitor_light_pages: int = 2     # pages fetched to verify and rank a candidate
    competitor_deep_pages: int = 10     # pages crawled per deep-analysed competitor
    # Security review: passive website checks + dependency lookups against OSV.dev (public vulnerability
    # database; only package names/versions are sent). Set false to keep dependency lists in-house.
    security_review_enabled: bool = True
    osv_enabled: bool = True
    # App-store analysis: public App Store / Google Play listings and Apple customer reviews
    # (review authors are never stored). Storefront country for Apple lookups.
    app_store_enabled: bool = True
    app_store_country: str = "us"
    app_store_competitor_reviews: int = 3   # competitors whose App Store reviews are analysed
    # UX deep dive: passive accessibility / mobile / speed / conversion checks of public pages.
    # Browser checks (contrast, phone layout, page speed) need the [browser] extra; static checks always run.
    ux_review_enabled: bool = True
    ux_browser_checks: bool = True
    ux_max_pages: int = 4                  # client pages audited (home + key pages); competitors: homepage
    # Headless-browser rendering for JavaScript-heavy sites (needs `pip install .[browser]` + a Chromium):
    #   auto   – render only pages that look script-rendered (default)
    #   always – render every HTML page (slower, most complete)
    #   never  – plain HTTP only
    browser_rendering: Literal["auto", "always", "never"] = "auto"
    browser_executable: str | None = None   # path to a Chromium binary; default: Playwright's own
    browser_max_pages: int = 3              # concurrent browser pages

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

    # --- Monitoring & alerts ----------------------------------------------
    # Scheduler loop inside each API process (safe with several processes: due monitors are claimed
    # atomically). Disable on all but one deployment if you prefer, or set false to pause monitoring.
    monitor_scheduler_enabled: bool = True
    monitor_poll_seconds: int = 60
    # Base URL of the web UI, used for links in alert notifications.
    app_base_url: str = "http://localhost:5173"
    # Email notifications (optional). Without smtp_host, email channels are skipped.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "alerts@localhost"
    smtp_starttls: bool = True
    notify_timeout_seconds: float = 10.0

    # --- Usage & budgets (0 = unlimited) ---------------------------------------
    run_llm_token_budget: int = 0
    agent_llm_token_budget: int = 0
    run_web_request_budget: int = 0
    # Used when the provider does not report a cost (OpenRouter does): USD per million tokens / per search.
    llm_price_input_per_million: float = 0.0
    llm_price_output_per_million: float = 0.0
    search_cost_per_query: float = 0.0

    # --- Quality ------------------------------------------------------------
    # Research freshness (BRS 28): sources up to N days old are fresh, older than the stale limit are stale.
    freshness_fresh_days: int = 30
    freshness_stale_days: int = 180

    # --- Prioritization ---------------------------------------------------
    roadmap_top_n: int = 10
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openrouter_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
