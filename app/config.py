"""
Central place to read configuration from environment variables.

All configuration comes from the environment (see .env.example). This module
loads the .env file once and exposes a single `settings` object so the rest of
the code never touches os.environ directly.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    """Read a boolean env var. Accepts true/1/yes (case-insensitive) as True."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"true", "1", "yes", "on"}


class Settings:
    # Secret used to sign session cookies. No safe default: must be set explicitly.
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "")

    # Enables debug behaviour during development only.
    DEBUG: bool = _get_bool("DEBUG", False)

    # Send the session cookie only over HTTPS. Keep False for local http dev,
    # set True in production behind TLS.
    SESSION_HTTPS_ONLY: bool = _get_bool("SESSION_HTTPS_ONLY", False)

    DATABASE_URL: str = os.environ.get("DATABASE_URL", "sqlite:///./data/scadenziario.db")

    # First-admin bootstrap (used only when the users table is empty).
    FIRST_ADMIN_USERNAME: str = os.environ.get("FIRST_ADMIN_USERNAME", "")
    FIRST_ADMIN_PASSWORD: str = os.environ.get("FIRST_ADMIN_PASSWORD", "")


settings = Settings()
