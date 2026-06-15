"""
Authentication routes: login form, login submit, logout.

Login errors are deliberately generic ("Credenziali non valide") and we never
reveal whether it was the username or the password that was wrong. We also run
a dummy password check when the username is unknown to keep response timing
constant (see app.security.dummy_verify).
"""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.utente import Utente
from app.security import dummy_verify, verify_password
from app.templating import templates

router = APIRouter()

GENERIC_LOGIN_ERROR = "Credenziali non valide."


@router.get("/login")
def login_form(request: Request):
    """Show the login form. If already logged in, go straight to the dashboard."""
    if request.session.get("user_id") is not None:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "auth/login.html", {"error": None})


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """Verify credentials and start a session on success."""
    user = db.scalar(select(Utente).where(Utente.username == username))

    if user is None:
        # Unknown username: run a dummy verify so timing matches the real path.
        dummy_verify()
        return _login_error(request)

    if not user.attivo or not verify_password(password, user.password_hash):
        return _login_error(request)

    # Success: store only the user id in the signed session cookie.
    request.session["user_id"] = user.id
    return RedirectResponse(url="/", status_code=303)


@router.get("/logout")
def logout(request: Request):
    """Destroy the session and return to the login page."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


def _login_error(request: Request):
    """Re-render the login form with a generic error (HTTP 401)."""
    return templates.TemplateResponse(
        request,
        "auth/login.html",
        {"error": GENERIC_LOGIN_ERROR},
        status_code=401,
    )
