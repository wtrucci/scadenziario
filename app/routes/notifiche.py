"""
Notification log: what the scheduler has sent, and what failed.

NotificaLog was written but never read back: when Telegram refused a message
the only trace was in the container log. This page is the read side — it
answers "did the customer's warning actually go out?" without an SSH session.

Read-only on purpose: a log you can edit is not a log.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, contains_eager

from app.config import settings
from app.database import get_db
from app.dependencies import require_login
from app.models.cliente import Cliente
from app.models.notifica_log import NotificaLog
from app.models.servizio import Servizio
from app.models.utente import Utente
from app.services.notifiche import invia_telegram
from app.templating import templates

router = APIRouter(prefix="/notifiche")

# The log grows one row per notification for good: a page showing everything
# would get slower every month for no benefit, since what you look for is
# always recent. Older entries stay in the database, reachable from the
# "fallite" filter, which is the only reason to go far back.
LIMITE_RIGHE = 300

ESITI_FILTRABILI = ("inviate", "fallite")


def _in_locale(momento: datetime) -> datetime:
    """Show a stored timestamp in the configured timezone.

    Timestamps are written in UTC (see database.utcnow), which is right, but
    "sent at 21:40" when you sent it at 23:40 reads as a bug.

    SQLite has no timezone-aware type: DateTime(timezone=True) stores the text
    and hands back a NAIVE datetime, which astimezone() would then take for
    local time — turning the conversion into a no-op on a machine already set
    to TZ, and into a wrong time everywhere else. So a naive value is stamped
    as UTC first, which is what wrote it. On PostgreSQL the value comes back
    aware and this branch simply does not trigger.
    """
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(ZoneInfo(settings.TZ))


@router.get("")
def lista_notifiche(
    request: Request,
    esito: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    esito_val = esito if esito in ESITI_FILTRABILI else None
    ricerca = (q or "").strip()

    query = (
        select(NotificaLog)
        .join(NotificaLog.servizio)
        .join(Servizio.cliente)
        .options(contains_eager(NotificaLog.servizio).contains_eager(Servizio.cliente))
        .order_by(NotificaLog.inviata_il.desc())
    )
    if esito_val is not None:
        query = query.where(NotificaLog.esito.is_(esito_val == "inviate"))
    for parola in ricerca.split():
        schema = f"%{parola}%"
        query = query.where(
            or_(
                Cliente.nome.ilike(schema),
                Servizio.descrizione.ilike(schema),
                Servizio.referente.ilike(schema),
                NotificaLog.dettaglio.ilike(schema),
            )
        )

    righe = list(db.scalars(query.limit(LIMITE_RIGHE)))

    # Counts over the WHOLE log, not the page: the point of the "fallite" card
    # is to tell you there are failures even when none is recent enough to
    # show up in the latest 300.
    totali = dict(
        db.execute(select(NotificaLog.esito, func.count()).group_by(NotificaLog.esito)).all()
    )
    conteggi = {
        "inviate": totali.get(True, 0),
        "fallite": totali.get(False, 0),
    }

    def link(st: str) -> str:
        """Card link: applies its filter, or clears it when already active —
        the same behaviour as the dashboard summary cards."""
        parti = []
        if st != esito_val:
            parti.append(f"esito={st}")
        if ricerca:
            parti.append(f"q={ricerca}")
        return "/notifiche" + ("?" + "&".join(parti) if parti else "")

    contesto = {
        "user": user,
        "righe": righe,
        "conteggi": conteggi,
        "link_esiti": {st: link(st) for st in ESITI_FILTRABILI},
        "filtri": {"esito": esito_val or "", "q": ricerca},
        "in_locale": _in_locale,
        "limite": LIMITE_RIGHE,
    }
    template = (
        "notifiche/_risultati.html"
        if request.headers.get("HX-Request")
        else "notifiche/lista.html"
    )
    return templates.TemplateResponse(request, template, contesto)


@router.post("/test")
def invia_prova(
    request: Request,
    user: Utente = Depends(require_login),
):
    """Send a test message through the real Telegram configuration (HTMX).

    The only other way to find out that the token or chat id is wrong is to
    wait for a real deadline and notice that no message arrived. This goes
    through exactly the same function the scheduler uses, so a success here
    means the scheduler's messages will get through too.

    Not written to NotificaLog: that log is about services (every row points
    to one), and a test would inflate the Inviate/Fallite counts with
    something that is not a notification.
    """
    adesso = _in_locale(datetime.now(timezone.utc)).strftime("%d/%m/%Y %H:%M")
    testo = (
        "✅ Scadenziario — messaggio di prova.\n"
        "Se lo stai leggendo, la configurazione Telegram funziona.\n"
        f"Inviato da {user.username} il {adesso} (v{settings.APP_VERSION})."
    )
    esito, dettaglio = invia_telegram(testo)
    return templates.TemplateResponse(
        request,
        "notifiche/_esito_test.html",
        {"esito": esito, "dettaglio": dettaglio, "adesso": adesso},
    )
