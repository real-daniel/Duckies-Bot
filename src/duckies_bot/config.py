"""Environment-backed application settings."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str
    tarkov_api_url: str = "https://json.tarkov.dev"
    http_timeout_seconds: float = 30.0
    discord_guild_id: int | None = None

    def __post_init__(self) -> None:
        if not self.discord_token.strip():
            raise ValueError("discord_token cannot be blank")
        if not self.tarkov_api_url.strip():
            raise ValueError("tarkov_api_url cannot be blank")
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

    guild_text = os.getenv("DISCORD_GUILD_ID", "").strip()
    try:
        guild_id = int(guild_text) if guild_text else None
    except ValueError as exc:
        raise RuntimeError("DISCORD_GUILD_ID must be an integer") from exc

    return Settings(
        discord_token=token,
        tarkov_api_url=os.getenv("TARKOV_API_URL", "https://json.tarkov.dev").strip(),
        http_timeout_seconds=timeout,
        discord_guild_id=guild_id,
    )
