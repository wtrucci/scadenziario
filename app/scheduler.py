"""
Background scheduler.

Runs in-process via APScheduler (see CLAUDE.md — no Celery/Redis). Two jobs:

1. expiration notifications, on a fixed interval;
2. a nightly database backup (see app/services/backup.py).

Started/stopped from the FastAPI lifespan in app/main.py.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.database import SessionLocal
from app.services import backup
from app.services.notifiche import invia_notifiche_scadenza, invia_telegram

logger = logging.getLogger("scadenziario.scheduler")

scheduler = BackgroundScheduler(timezone=settings.TZ)


def _job_notifiche() -> None:
    db = SessionLocal()
    try:
        inviate = invia_notifiche_scadenza(db)
        if inviate:
            logger.info("Controllo scadenze: %s notifica/e elaborata/e.", inviate)
    finally:
        db.close()


def _job_backup() -> None:
    """Take the nightly snapshot; announce only failures.

    A backup that silently stops working is worse than none, because you find
    out when you need it. Success stays in the log; a failure goes to Telegram,
    which is quiet enough (a couple of messages a week) that an alert there
    gets noticed.
    """
    try:
        backup.esegui_backup(settings.DATABASE_URL, settings.BACKUP_DIR, settings.BACKUP_KEEP)
    except backup.BackupFallito as exc:
        logger.error("Backup fallito: %s", exc)
        invia_telegram(f"⚠️ Backup del database NON riuscito\n{exc}")


def avvia_scheduler() -> None:
    """Register and start the notification job. Safe to call even when
    Telegram is not configured yet: the job simply logs and skips sending
    (see invia_telegram)."""
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        logger.warning(
            "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID non configurati: lo scheduler "
            "notifiche parte comunque ma non invierà nulla finché non sono impostati."
        )
    scheduler.add_job(
        _job_notifiche,
        "interval",
        minutes=settings.NOTIFICATION_CHECK_INTERVAL_MINUTES,
        id="controllo_scadenze",
        replace_existing=True,
    )
    if settings.BACKUP_ENABLED:
        scheduler.add_job(
            _job_backup,
            "cron",
            hour=settings.BACKUP_HOUR,
            minute=0,
            id="backup_database",
            replace_existing=True,
        )
        logger.info(
            "Backup notturno attivo: ore %02d:00, %s copie in %s",
            settings.BACKUP_HOUR, settings.BACKUP_KEEP, settings.BACKUP_DIR,
        )
    scheduler.start()


def ferma_scheduler() -> None:
    scheduler.shutdown(wait=False)
