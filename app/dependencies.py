"""
Authentication dependencies for protecting routes.

- get_current_user: loads the logged-in user from the session, or None.
- require_login: ensures a valid session, otherwise redirects to /login.
- require_admin: builds on require_login and additionally requires the admin role.

We deliberately reload the user from the database on every request (rather than
trusting data stored in the cookie) so that disabling an account or changing a
role takes effect immediately.
"""
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.enums import RuoloUtente
from app.models.utente import Utente


class NotAuthenticatedError(Exception):
    """Raised when a protected route is accessed without a valid session.

    Handled in app.main by redirecting to /login.
    """


class NotAuthorizedError(Exception):
    """Raised when a logged-in user lacks the required role (e.g. admin)."""


def get_current_user(request: Request, db: Session = Depends(get_db)) -> Utente | None:
    """Return the logged-in, active user, or None if there is no valid session."""
    user_id = request.session.get("user_id")
    if user_id is None:
        return None

    user = db.get(Utente, user_id)
    # Treat a disabled or deleted account as not logged in.
    if user is None or not user.attivo:
        return None
    return user


def require_login(user: Utente | None = Depends(get_current_user)) -> Utente:
    """Dependency for routes that require any authenticated user."""
    if user is None:
        raise NotAuthenticatedError()
    return user


def require_admin(user: Utente = Depends(require_login)) -> Utente:
    """Dependency for routes that require the admin role."""
    if user.ruolo is not RuoloUtente.admin:
        raise NotAuthorizedError()
    return user
