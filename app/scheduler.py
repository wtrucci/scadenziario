"""
Background scheduler for expiration notifications.

Runs in-process via APScheduler (see CLAUDE.md — no Celery/Redis). A single
job checks, on a fixed interval, which occurrences just entered their warning
window and sends the configured notifications. Started/stopped from the
FastAPI lifespan in app/main.py.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.database import SessionLocal
from app.services.notifiche import invia_notifiche_scadenza

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
    scheduler.start()


def ferma_scheduler() -> None:
    scheduler.shutdown(wait=False)
