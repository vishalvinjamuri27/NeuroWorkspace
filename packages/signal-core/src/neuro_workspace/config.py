"""Validated runtime configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings shared by API and workers."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "test", "staging", "production"] = "development"
    database_url: str = "sqlite:///./data/neuroworkspace.db"
    # SQLAlchemy's un-set defaults (pool_size=5, max_overflow=10 -> 15
    # connections per engine) were measured to start timing out under
    # scripts/bench_job_concurrency.py at 16 concurrent DB-touching threads
    # sharing one engine -- exactly the shape of one API replica serving
    # concurrent requests (see docs/PERFORMANCE.md, "Connection pool
    # behavior"). Each API replica and each worker process gets its own
    # engine/pool, so this is a per-process ceiling, not a global one --
    # replica_count * (db_pool_size + db_max_overflow) must stay under
    # Postgres's own max_connections (100 by default in this stack).
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout_seconds: int = 30
    redis_url: str = "redis://localhost:6379/0"
    object_store_backend: Literal["local", "s3"] = "local"
    object_store_root: Path = Path("data/objects")
    s3_endpoint_url: str | None = None
    s3_public_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: SecretStr | None = None
    s3_bucket: str = "neuroworkspace"
    session_secret: SecretStr = Field(default=SecretStr("development-only-secret-change-me"))
    session_cookie_name: str = "nw_session"
    session_ttl_seconds: int = 8 * 60 * 60
    dev_auth_enabled: bool = True
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    api_public_url: str = "http://localhost:8000"
    web_public_url: str = "http://localhost:3000"
    cors_origins: str = "http://localhost:3000"
    alpha_admin_emails: str = ""
    worker_eager: bool = True
    max_upload_bytes: int = 20 * 1024 * 1024 * 1024
    max_upload_files: int = 16
    max_waveform_channels: int = 256
    max_waveform_pixels: int = 4096
    max_waveform_points: int = 1_500_000
    waveform_cache_seconds: int = 300
    feature_flags: str = ""
    log_level: str = "INFO"
    build_version: str = "development"
    require_demo_ready: bool = False

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def enabled_features(self) -> frozenset[str]:
        return frozenset(
            flag.strip().casefold() for flag in self.feature_flags.split(",") if flag.strip()
        )

    @property
    def alpha_admin_email_set(self) -> frozenset[str]:
        return frozenset(
            email.strip().casefold()
            for email in self.alpha_admin_emails.split(",")
            if email.strip()
        )

    def feature_enabled(self, flag: str) -> bool:
        return flag.casefold() in self.enabled_features

    @model_validator(mode="after")
    def validate_production_safety(self) -> Settings:
        if self.environment == "production":
            if self.dev_auth_enabled:
                raise ValueError("DEV_AUTH_ENABLED must be false in production")
            if not self.oidc_issuer or not self.oidc_audience:
                raise ValueError("OIDC_ISSUER and OIDC_AUDIENCE are required in production")
            if not self.oidc_client_id:
                raise ValueError("OIDC_CLIENT_ID is required in production")
            if not self.oidc_issuer.startswith("https://"):
                raise ValueError("OIDC_ISSUER must use HTTPS in production")
            if len(self.session_secret.get_secret_value()) < 32:
                raise ValueError("SESSION_SECRET must contain at least 32 characters")
            if self.object_store_backend != "s3":
                raise ValueError("production requires private S3-compatible object storage")
            if not self.require_demo_ready:
                raise ValueError("REQUIRE_DEMO_READY must be true in production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide validated settings object."""

    return Settings()
