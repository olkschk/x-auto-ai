"""Configuration loader. Reads .env and exposes typed accessors."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _required(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise RuntimeError(f"Required env var {key} is not set. See .env.example")
    return value


def _optional(key: str, default: str) -> str:
    return os.environ.get(key, default).strip() or default


def _bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str
    llm_model: str
    x_auth_token: str
    x_headless: bool
    mongo_uri: str
    mongo_db: str
    telegram_bot_token: str
    telegram_chat_id: str
    monitor_interval_seconds: int
    tweet_char_limit: int
    autoreply_host: str
    autoreply_port: int
    log_level: str

    @classmethod
    def load(cls) -> "Config":
        return cls(
            anthropic_api_key=_required("ANTHROPIC_API_KEY"),
            llm_model=_optional("LLM_MODEL", "claude-haiku-4-5-20251001"),
            x_auth_token=_required("X_AUTH_TOKEN"),
            x_headless=_bool("X_HEADLESS", default=False),
            mongo_uri=_optional("MONGO_URI", "mongodb://localhost:27017"),
            mongo_db=_optional("MONGO_DB", "twitter"),
            telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_required("TELEGRAM_CHAT_ID"),
            monitor_interval_seconds=int(_optional("MONITOR_INTERVAL_SECONDS", "120")),
            tweet_char_limit=int(_optional("TWEET_CHAR_LIMIT", "280")),
            autoreply_host=_optional("AUTOREPLY_HOST", "127.0.0.1"),
            autoreply_port=int(_optional("AUTOREPLY_PORT", "8765")),
            log_level=_optional("LOG_LEVEL", "INFO"),
        )


def load_config_lenient() -> Config:
    """Load config tolerating missing X_AUTH_TOKEN / Telegram (for autoreply server)."""
    return Config(
        anthropic_api_key=_required("ANTHROPIC_API_KEY"),
        llm_model=_optional("LLM_MODEL", "claude-haiku-4-5-20251001"),
        x_auth_token=_optional("X_AUTH_TOKEN", ""),
        x_headless=_bool("X_HEADLESS", default=False),
        mongo_uri=_optional("MONGO_URI", "mongodb://localhost:27017"),
        mongo_db=_optional("MONGO_DB", "twitter"),
        telegram_bot_token=_optional("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=_optional("TELEGRAM_CHAT_ID", ""),
        monitor_interval_seconds=int(_optional("MONITOR_INTERVAL_SECONDS", "120")),
        tweet_char_limit=int(_optional("TWEET_CHAR_LIMIT", "280")),
        autoreply_host=_optional("AUTOREPLY_HOST", "127.0.0.1"),
        autoreply_port=int(_optional("AUTOREPLY_PORT", "8765")),
        log_level=_optional("LOG_LEVEL", "INFO"),
    )
