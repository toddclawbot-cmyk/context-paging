"""Application settings loaded from environment."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Runtime configuration. Read once at process start."""
    database_url: str
    redis_url: str
    jwt_secret: str
    jwt_ttl_seconds: int = 3600
    rate_limit_per_min: int = 60
    log_level: str = "INFO"
    enable_metrics: bool = True
    sentry_dsn: str | None = None


def load_settings() -> Settings:
    """Load settings from environment, with safe defaults for non-prod."""
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "postgres://localhost/myshop"),
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        jwt_secret=os.environ.get("JWT_SECRET", "dev-secret-do-not-use-in-prod"),
        jwt_ttl_seconds=int(os.environ.get("JWT_TTL_SECONDS", "3600")),
        rate_limit_per_min=int(os.environ.get("RATE_LIMIT_PER_MIN", "60")),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        enable_metrics=os.environ.get("ENABLE_METRICS", "1") == "1",
        sentry_dsn=os.environ.get("SENTRY_DSN") or None,
    )
