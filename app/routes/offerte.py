"""
Renewal offers: contracts whose renewal needs an offer sent to the customer.

- ``GET  /offerte``                          the list (search + state cards)
- ``POST /offerte/{id}/{data_rinnovo}/inviata`` mark / un-mark the offer as sent
- ``GET  /offerte/contatore``                 the number next to "Offerte" in
  the sidebar, loaded by HTMX from base.html

Which renewals appear and how urgent they are is decided in
app/services/offerte.py; this module only reads and writes.
"""
from __future__ import annotations

import json
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager, selectinload

from app.database import get_db
from app.dependencies import require_login
from app.models.cliente import Cliente
from app.models.offerta_rinnovo import OffertaRinnovo
from app.models.servizio import Servizio
from app.models.utente import Utente
from app.routes.notifiche import _in_locale
from app.services import offerte
from app.services.filtri import condizioni_ricerca
from app.templating import templates

router = APIRouter(prefix="/offerte")

# Card filters. "urgenti" are the ones the sidebar counts (late or within
# GIORNI_URGENZA days); "da_inviare" is every offer not sent yet.
FILTRI_STATO = ("urgenti", "da_inviare", "inviate")

# Tells the sidebar counter to reload (see base.html) after a change.
EVENTO_AGGIORNAMENTO = json.dumps({"offerteAggiornate": True})


def _candidati(db: Session, ricerca: str = "") -> list[Servizio]:
    """Contracts that can have a renewal proposal, with what the calculation needs.

    Filtering here what SQL can filter keeps the occurrence engine away from
    contracts that would be thrown out anyway.
    """
    query = (
        select(Servizio)
        .join(Servizio.cliente)
        .options(
            contains_eager(Servizio.cliente),
            selectinload(Servizio.override_importi),
            selectinload(Servizio.offerte_rinnovo),
        )
        .where(Servizio.rinnovo_automatico.is_(False), Servizio.disdetto.is_(False))
    )
    for condizione in condizioni_ricerca(ricerca):
        query = query.where(condizione)
    return list(db.scalars(query).unique())


def _passa_filtro(riga: offerte.RigaOfferta, stato: str | None) -> bool:
    if stato == "urgenti":
        return riga.da_sollecitare
    if stato == "da_inviare":
        return not riga.inviata
    if stato == "inviate":
        return riga.inviata
    return True


def _contesto(db: Session, stato: str | None, q: str | None) -> dict:
    stato_val = stato if stato in FILTRI_STATO else None
    ricerca = (q or "").strip()

    tutte = offerte.righe_offerte(_candidati(db, ricerca))
    conteggi = {
        "urgenti": sum(r.da_sollecitare for r in tutte),
        "da_inviare": sum(not r.inviata for r in tutte),
        "inviate": sum(r.inviata for r in tutte),
    }

    def url(stato_link: str | None) -> str:
        parametri = {}
        if stato_link:
            parametri["stato"] = stato_link
        if ricerca:
            parametri["q"] = ricerca
        return "/offerte" + ("?" + urlencode(parametri) if parametri else "")

    return {
        "righe": [r for r in tutte if _passa_filtro(r, stato_val)],
        "conteggi": conteggi,
        # A card applies its filter, or clears it when already active — the
        # same behaviour as the dashboard summary cards.
        "link_stati": {st: url(None if st == stato_val else st) for st in FILTRI_STATO},
        "filtri": {"stato": stato_val or "", "q": ricerca},
        # Where the edit form brings you back to: this page, as you left it.
        "ritorno": url(stato_val),
        "in_locale": _in_locale,
        "giorni_orizzonte": offerte.GIORNI_ORIZZONTE,
        "giorni_urgenza": offerte.GIORNI_URGENZA,
    }


@router.get("")
def lista_offerte(
    request: Request,
    stato: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    contesto = {"user": user, **_contesto(db, stato, q)}
    template = (
        "offerte/_risultati.html"
        if request.headers.get("HX-Request")
        else "offerte/lista.html"
    )
    return templates.TemplateResponse(request, template, contesto)


@router.post("/{servizio_id}/{data_rinnovo}/inviata")
def toggle_inviata(
    request: Request,
    servizio_id: int,
    data_rinnovo: date,
    stato: str | None = Form(None),
    q: str | None = Form(None),
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    """Mark the offer for one renewal as sent, or take it back (HTMX).

    The date must be the renewal the contract is actually proposing, so rows
    are never written for dates the page could not have shown. The whole
    results region is re-rendered — the cards change, and an active filter
    may drop the row — and the sidebar counter is told to refresh.
    """
    servizio = db.get(Servizio, servizio_id)
    if servizio is None:
        raise HTTPException(status_code=404, detail="Servizio non trovato")
    rinnovo = offerte.rinnovo_da_proporre(servizio, data_rinnovo, date.today())
    if rinnovo is None or rinnovo.data_occorrenza != data_rinnovo:
        raise HTTPException(status_code=404, detail="Rinnovo non trovato")

    esistente = db.scalar(
        select(OffertaRinnovo).where(
            OffertaRinnovo.servizio_id == servizio_id,
            OffertaRinnovo.data_rinnovo == data_rinnovo,
        )
    )
    if esistente is not None:
        db.delete(esistente)
    else:
        db.add(OffertaRinnovo(servizio_id=servizio_id, data_rinnovo=data_rinnovo))
    db.commit()

    risposta = templates.TemplateResponse(
        request, "offerte/_risultati.html", {"user": user, **_contesto(db, stato, q)}
    )
    risposta.headers["HX-Trigger"] = EVENTO_AGGIORNAMENTO
    return risposta


@router.get("/contatore", response_class=HTMLResponse)
def contatore(
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    """How many offers are late or due within GIORNI_URGENZA days.

    Loaded separately by the sidebar (see base.html) instead of being computed
    for every page: no route has to remember to pass it, and a slow count
    never holds up the page. Empty when there is nothing to chase, so the
    badge disappears.
    """
    n = sum(r.da_sollecitare for r in offerte.righe_offerte(_candidati(db)))
    return str(n) if n else ""
