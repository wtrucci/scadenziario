"""
Dashboard and billing-summary routes.

- ``GET /``                  services expiring in the selected month
- ``GET /riepilogo``         billing summary for the month (active services only)
- ``GET /riepilogo/export``  CSV export of the same summary

The month is chosen via the ``?mese=YYYY-MM`` query string and defaults to the
current month. See ``app/services/periodi.py`` and ``app/services/riepilogo.py``.
All routes require an authenticated user.
"""
from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_login
from app.models.utente import Utente
from app.services import periodi, riepilogo
from app.services.scadenze import classe_scadenza
from app.templating import templates

router = APIRouter()


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
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    primo = periodi.parse_mese(mese)
    servizi = riepilogo.servizi_del_mese(db, primo)
    oggi = date.today()
    righe = [(s, classe_scadenza(s, oggi)) for s in servizi]
    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        {"user": user, "nav": _navigazione_mese(primo), "righe": righe},
    )


@router.get("/riepilogo")
def riepilogo_mese(
    request: Request,
    mese: str | None = None,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    primo = periodi.parse_mese(mese)
    servizi = riepilogo.servizi_del_mese(db, primo, solo_attivi=True)
    gruppi = riepilogo.raggruppa_per_cliente(servizi)
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
    servizi = riepilogo.servizi_del_mese(db, primo, solo_attivi=True)

    buffer = io.StringIO()
    # Semicolon delimiter: Italian Excel uses ';' as the list separator, so
    # numbers written with a comma decimal separator land in their own cells.
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([
        "Cliente", "Descrizione", "Tipo", "Scadenza", "Quantità",
        "Importo unitario", "Totale", "Referente", "Stato",
    ])
    for s in servizi:
        writer.writerow([
            s.cliente.nome,
            s.descrizione,
            s.tipo.value,
            s.data_scadenza.strftime("%d/%m/%Y"),
            s.quantita,
            _decimale_it(s.importo),
            _decimale_it(s.totale),
            s.referente or "",
            s.stato.value,
        ])

    # utf-8-sig prepends the BOM Excel needs to open accented text correctly.
    contenuto = buffer.getvalue().encode("utf-8-sig")
    nome_file = f"riepilogo_{periodi.chiave_mese(primo)}.csv"
    return Response(
        content=contenuto,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome_file}"'},
    )
