"""
Application entrypoint: builds the FastAPI app, wires up middleware, static
files, templates, exception handlers and the startup bootstrap.
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.status import HTTP_303_SEE_OTHER

from app.bootstrap import create_first_admin
from app.config import settings
from app.database import SessionLocal
from app.dependencies import (
    NotAuthenticatedError,
    NotAuthorizedError,
)
from app.routes import auth, clienti, dashboard, servizi
from app.scheduler import avvia_scheduler, ferma_scheduler
from app.templating import templates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scadenziario")

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run once at startup: create the first admin if needed."""
    if not settings.SECRET_KEY:
        # Without a secret key, session cookies cannot be signed safely.
        raise RuntimeError(
            "SECRET_KEY is not set. Copy .env.example to .env and set a value "
            '(e.g. python -c "import secrets; print(secrets.token_hex(32))").'
        )

    db = SessionLocal()
    try:
        create_first_admin(db)
    finally:
        db.close()

    avvia_scheduler()
    yield
    ferma_scheduler()


app = FastAPI(title="Scadenziario", version=settings.APP_VERSION, lifespan=lifespan)

# Signs the session cookie with SECRET_KEY (itsdangerous under the hood).
# We store only the user id inside; everything else is reloaded from the DB.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    https_only=settings.SESSION_HTTPS_ONLY,
    same_site="lax",
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.exception_handler(NotAuthenticatedError)
async def not_authenticated_handler(request: Request, exc: NotAuthenticatedError):
    """Send unauthenticated users to the login page.

    For HTMX requests we use the HX-Redirect header so the browser performs a
    full redirect instead of swapping the login page into a fragment.
    """
    if request.headers.get("HX-Request") == "true":
        response = Response(status_code=204)
        response.headers["HX-Redirect"] = "/login"
        return response
    return RedirectResponse(url="/login", status_code=HTTP_303_SEE_OTHER)


@app.exception_handler(NotAuthorizedError)
async def not_authorized_handler(request: Request, exc: NotAuthorizedError):
    """Logged-in but lacking the required role: render a 403 page."""
    return templates.TemplateResponse(
        request, "errors/403.html", {}, status_code=403
    )


app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(clienti.router)
app.include_router(servizi.router)
