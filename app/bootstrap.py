"""
First-run bootstrap: create the initial admin account.

Called once at application startup. If the users table is empty, it creates an
admin from FIRST_ADMIN_USERNAME / FIRST_ADMIN_PASSWORD. If users already exist,
it does nothing. If the env vars are missing on an empty database, it logs a
clear warning and skips creation (the app still starts, but nobody can log in
until an admin is created).
"""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.enums import RuoloUtente
from app.models.utente import Utente
from app.security import hash_password

logger = logging.getLogger("scadenziario.bootstrap")


def create_first_admin(db: Session) -> None:
    """Create the initial admin user if no users exist yet."""
    existing_user = db.scalar(select(Utente).limit(1))
    if existing_user is not None:
        # Users already exist: never touch them.
        return

    if not settings.FIRST_ADMIN_USERNAME or not settings.FIRST_ADMIN_PASSWORD:
        logger.warning(
            "No users found and FIRST_ADMIN_USERNAME/FIRST_ADMIN_PASSWORD are not set. "
            "No admin account was created. Set these variables and restart to bootstrap "
            "the first admin."
        )
        return

    admin = Utente(
        username=settings.FIRST_ADMIN_USERNAME,
        password_hash=hash_password(settings.FIRST_ADMIN_PASSWORD),
        ruolo=RuoloUtente.admin,
        attivo=True,
    )
    db.add(admin)
    db.commit()
    logger.info("Created initial admin user %r.", settings.FIRST_ADMIN_USERNAME)
