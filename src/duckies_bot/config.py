"""Environment-backed application settings."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str
    tarkov_api_url: str = "https://json.tarkov.dev"
    deadlock_api_url: str = "https://api.deadlock-api.com"
    deadlock_api_key: str | None = None
    deadlock_live_events_url: str = "http://127.0.0.1:3000"
    deadlock_scout_template_path: str = "data/deadlock_scout_template.json"
    database_path: str = "data/duckies.sqlite3"
    companion_api_host: str = "127.0.0.1"
    companion_api_port: int = 8080
    companion_public_url: str | None = None
    http_timeout_seconds: float = 30.0
    discord_guild_id: int | None = None

    def __post_init__(self) -> None:
        if not self.discord_token.strip():
            raise ValueError("discord_token cannot be blank")
        if not self.tarkov_api_url.strip():
            raise ValueError("tarkov_api_url cannot be blank")
        if not self.deadlock_api_url.strip():
            raise ValueError("deadlock_api_url cannot be blank")
        if not self.deadlock_live_events_url.strip():
            raise ValueError("deadlock_live_events_url cannot be blank")
        if not self.deadlock_scout_template_path.strip():
            raise ValueError("deadlock_scout_template_path cannot be blank")
        if not self.database_path.strip():
            raise ValueError("database_path cannot be blank")
        if not self.companion_api_host.strip():
            raise ValueError("companion_api_host cannot be blank")
        if not 1 <= self.companion_api_port <= 65535:
            raise ValueError("companion_api_port must be between 1 and 65535")
        if self.companion_public_url is not None and not self.companion_public_url.startswith(
            "https://"
        ):
            raise ValueError("companion_public_url must use https")
        if self.http_timeout_seconds <= 0:
            raise ValueError("http_timeout_seconds must be greater than zero")
        if self.discord_guild_id is not None and self.discord_guild_id <= 0:
            raise ValueError("discord_guild_id must be positive")


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_TOKEN is missing from the environment or .env file")

    try:
        timeout = float(os.getenv("HTTP_TIMEOUT_SECONDS", "30"))
    except ValueError as exc:
        raise RuntimeError("HTTP_TIMEOUT_SECONDS must be numeric") from exc

    try:
        companion_port = int(os.getenv("COMPANION_API_PORT", "8080"))
    except ValueError as exc:
        raise RuntimeError("COMPANION_API_PORT must be an integer") from exc

    guild_text = os.getenv("DISCORD_GUILD_ID", "").strip()
    try:
        guild_id = int(guild_text) if guild_text else None
    except ValueError as exc:
        raise RuntimeError("DISCORD_GUILD_ID must be an integer") from exc

    return Settings(
        discord_token=token,
        tarkov_api_url=os.getenv("TARKOV_API_URL", "https://json.tarkov.dev").strip(),
        deadlock_api_url=os.getenv(
            "DEADLOCK_API_URL", "https://api.deadlock-api.com"
        ).strip(),
        deadlock_api_key=os.getenv("DEADLOCK_API_KEY", "").strip() or None,
        deadlock_live_events_url=os.getenv(
            "DEADLOCK_LIVE_EVENTS_URL", "http://127.0.0.1:3000"
        ).strip(),
        deadlock_scout_template_path=os.getenv(
            "DEADLOCK_SCOUT_TEMPLATE_PATH", "data/deadlock_scout_template.json"
        ).strip(),
        database_path=os.getenv("DATABASE_PATH", "data/duckies.sqlite3").strip(),
        companion_api_host=os.getenv("COMPANION_API_HOST", "127.0.0.1").strip(),
        companion_api_port=companion_port,
        companion_public_url=os.getenv("COMPANION_PUBLIC_URL", "").strip().rstrip("/")
        or None,
        http_timeout_seconds=timeout,
        discord_guild_id=guild_id,
    )
