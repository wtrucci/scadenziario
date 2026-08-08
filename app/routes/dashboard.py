"""
Dashboard and billing-summary routes.

- ``GET /``                  occurrences falling in the selected month
- ``GET /riepilogo``         billing summary for the month (active services only)
- ``GET /riepilogo/export``  CSV export of the same summary

The month is chosen via the ``?mese=YYYY-MM`` query string and defaults to the
current month. Occurrences come from ``app/services/riepilogo.py``, which builds
on the occurrence engine. All routes require an authenticated user.
"""
from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_login
from app.models.utente import Utente
from app.services import filtri, periodi, riepilogo
from app.services.occorrenze import ETICHETTE_STATO_CONTRATTO, stato_contratto
from app.templating import templates

router = APIRouter()

# The per-occurrence visual states the dashboard lets you filter by. "normale"
# is intentionally not offered (it just means "nothing special").
STATI_OCCORRENZA_FILTRABILI = ("da_fatturare", "in_scadenza", "fatturato")


def _filtri_querystring(cliente_id: int | None, referente: str | None, stato: str | None) -> str:
    """Encode the active filters (excluding ``mese``) as a querystring fragment.

    Returns "" when no filter is active, or "&key=value..." otherwise, so it can
    be appended after ``?mese=...`` in the month-navigation links (this is how
    filters are preserved while moving between months).
    """
    attivi = {}
    if cliente_id is not None:
        attivi["cliente"] = cliente_id
    if referente:
        attivi["referente"] = referente
    if stato:
        attivi["stato"] = stato
    return ("&" + urlencode(attivi)) if attivi else ""


def _navigazione_mese(primo: date) -> dict:
    """Build the data the month selector needs: current month plus the keys and
    labels for the previous and next months."""
    prec = periodi.mese_precedente(primo)
    succ = periodi.mese_successivo(primo)
    return {
        "etichetta": periodi.etichetta_mese(primo),
        "chiave": periodi.chiave_mese(primo),
        "prec": {"chiave": periodi.chiave_mese(prec), "etichetta": periodi.etichetta_mese(prec)},
        "succ": {"chiave": periodi.chiave_mese(succ), "etichetta": periodi.etichetta_mese(succ)},
    }


def _decimale_it(valore: Decimal) -> str:
    """Format a Decimal with a comma decimal separator (Italian Excel locale)."""
    return f"{valore:.2f}".replace(".", ",")


@router.get("/")
def dashboard(
    request: Request,
    mese: str | None = None,
    cliente: str | None = None,
    referente: str | None = None,
    stato: str | None = None,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    primo = periodi.parse_mese(mese)

    # Normalise the raw query params into typed/validated filter values.
    cliente_id = int(cliente) if (cliente and cliente.isdigit()) else None
    referente_val = referente.strip() if referente and referente.strip() else None
    stato_val = stato if stato in STATI_OCCORRENZA_FILTRABILI else None

    # cliente/referente are SQL filters (service columns); the visual state is
    # filtered in Python because it is computed by the engine, not a column.
    righe = riepilogo.occorrenze_del_mese(
        db, primo, cliente_id=cliente_id, referente=referente_val
    )
    righe = riepilogo.filtra_per_stato_visivo(righe, stato_val)

    # Contract-level state (Attivo/In scadenza/Scaduto/Disdetto) is computed,
    # not a column — one lookup per distinct service, reused by the template
    # for the "Stato servizio" badge.
    oggi = date.today()
    stati_servizi = {
        riga.servizio.id: stato_contratto(riga.servizio, oggi=oggi) for riga in righe
    }

    contesto = {
        "user": user,
        "nav": _navigazione_mese(primo),
        "righe": righe,
        "stati_servizi": stati_servizi,
        "etichette_stato": ETICHETTE_STATO_CONTRATTO,
        # Querystring of active filters, appended to the month-nav links so they
        # are preserved when navigating between months.
        "filtri_qs": _filtri_querystring(cliente_id, referente_val, stato_val),
        # Current filter values, to pre-populate the form (e.g. on a bookmarked URL).
        "filtri": {
            "cliente": cliente_id,
            "referente": referente_val or "",
            "stato": stato_val or "",
        },
        "clienti": filtri.clienti_disponibili(db),
        "referenti": filtri.referenti_disponibili(db),
        "stati_occorrenza": STATI_OCCORRENZA_FILTRABILI,
    }

    # HTMX request (filter change): swap only the results region. A normal page
    # load or a bookmarked URL gets the whole page, with filters already applied
    # and the form pre-populated from the querystring.
    template = (
        "dashboard/_risultati.html"
        if request.headers.get("HX-Request")
        else "dashboard/index.html"
    )
    return templates.TemplateResponse(request, template, contesto)


@router.get("/riepilogo")
def riepilogo_mese(
    request: Request,
    mese: str | None = None,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    primo = periodi.parse_mese(mese)
    righe = riepilogo.occorrenze_del_mese(db, primo, escludi_disdetti=True)
    gruppi = riepilogo.raggruppa_per_cliente(righe)
    totale = riepilogo.totale_complessivo(gruppi)
    return templates.TemplateResponse(
        request,
        "riepilogo/index.html",
        {"user": user, "nav": _navigazione_mese(primo), "gruppi": gruppi, "totale": totale},
    )


@router.get("/riepilogo/export")
def riepilogo_export(
    mese: str | None = None,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    primo = periodi.parse_mese(mese)
    righe = riepilogo.occorrenze_del_mese(db, primo, escludi_disdetti=True)
    oggi = date.today()

    buffer = io.StringIO()
    # Semicolon delimiter: Italian Excel uses ';' as the list separator, so
    # numbers written with a comma decimal separator land in their own cells.
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([
        "Cliente", "Descrizione", "Tipo", "Scadenza", "Quantità",
        "Importo unitario", "Totale", "Referente", "Stato",
    ])
    for riga in righe:
        s = riga.servizio
        occ = riga.occorrenza
        writer.writerow([
            s.cliente.nome,
            s.descrizione,
            s.tipo.value,
            occ.data_occorrenza.strftime("%d/%m/%Y"),
            occ.quantita,
            _decimale_it(occ.importo),
            _decimale_it(occ.totale),
            s.referente or "",
            ETICHETTE_STATO_CONTRATTO[stato_contratto(s, oggi=oggi)],
        ])

    # utf-8-sig prepends the BOM Excel needs to open accented text correctly.
    contenuto = buffer.getvalue().encode("utf-8-sig")
    nome_file = f"riepilogo_{periodi.chiave_mese(primo)}.csv"
    return Response(
        content=contenuto,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome_file}"'},
    )
