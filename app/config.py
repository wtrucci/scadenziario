"""
Central place to read configuration from environment variables.

All configuration comes from the environment (see .env.example). This module
loads the .env file once and exposes a single `settings` object so the rest of
the code never touches os.environ directly.
"""
import os

from dotenv import load_dotenv

from app import __version__

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    """Read a boolean env var. Accepts true/1/yes (case-insensitive) as True."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"true", "1", "yes", "on"}


class Settings:
    # Version shown in the interface. Normally the one baked into the package;
    # APP_VERSION can override it so a build can stamp something more precise
    # (a commit, a pre-release) without editing the source.
    APP_VERSION: str = os.environ.get("APP_VERSION", "").strip() or __version__

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

    # Telegram Bot API credentials for expiration notifications. Left empty in
    # dev/test: the scheduler logs a warning and skips sending instead of
    # crashing when they are not set (see app/scheduler.py).
    TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")

    # How often the scheduler checks for upcoming expirations.
    NOTIFICATION_CHECK_INTERVAL_MINUTES: int = int(
        os.environ.get("NOTIFICATION_CHECK_INTERVAL_MINUTES", "60")
    )

    TZ: str = os.environ.get("TZ", "Europe/Rome")

    # Nightly database backup (see app/services/backup.py). The directory sits
    # inside the mounted data volume by default, so backups travel with the
    # database; copying them off-site is left to the host (rsync, cloud client),
    # deliberately outside this app.
    BACKUP_ENABLED: bool = _get_bool("BACKUP_ENABLED", True)
    BACKUP_DIR: str = os.environ.get("BACKUP_DIR", "./data/backup")
    # Hour of day (0-23, in TZ) the backup job runs.
    BACKUP_HOUR: int = int(os.environ.get("BACKUP_HOUR", "3"))
    # How many daily copies to keep. The limit is not disk space (the database
    # is tiny) but how far back you can go on noticing a mistake late — and a
    # billing mistake typically surfaces at month end.
    BACKUP_KEEP: int = int(os.environ.get("BACKUP_KEEP", "30"))


settings = Settings()
