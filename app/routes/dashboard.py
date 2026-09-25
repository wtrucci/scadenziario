"""
Dashboard and billing-summary routes.

- ``GET /``                  occurrences falling in the selected month
- ``GET /riepilogo``         billing summary for the month (active services only)
- ``GET /riepilogo/export``  CSV export of the same summary
- ``GET /riepilogo/export/pdf-mese``  the same summary as one PDF, all customers

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

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_login
from app.models.utente import Utente
from app.services import filtri, periodi, riepilogo
from app.services.occorrenze import ETICHETTE_STATO_CONTRATTO, stato_contratto
from app.services.pdf import genera_pdf_cliente, genera_pdf_mese
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
    oggi = date.today()
    corrente = date(oggi.year, oggi.month, 1)
    return {
        "etichetta": periodi.etichetta_mese(primo),
        "chiave": periodi.chiave_mese(primo),
        "prec": {"chiave": periodi.chiave_mese(prec), "etichetta": periodi.etichetta_mese(prec)},
        "succ": {"chiave": periodi.chiave_mese(succ), "etichetta": periodi.etichetta_mese(succ)},
        # Wandering a few months ahead is normal; walking back one month at a
        # time is not. "e_corrente" lets the selector hide the shortcut when it
        # would do nothing.
        "corrente": {"chiave": periodi.chiave_mese(corrente),
                     "etichetta": periodi.etichetta_mese(corrente)},
        "e_corrente": primo == corrente,
    }


def _decimale_it(valore: Decimal) -> str:
    """Format a Decimal with a comma decimal separator (Italian Excel locale)."""
    return f"{valore:.2f}".replace(".", ",")


def _contesto_risultati(
    db: Session,
    mese: str | None,
    cliente: str | None,
    referente: str | None,
    stato: str | None,
) -> dict:
    """Build everything ``dashboard/_risultati.html`` needs: month nav, table
    rows, summary cards and the active-filter values.

    Shared by the dashboard route and the "fatturato" toggle endpoint (see
    app/routes/servizi.py) — toggling an occurrence can change which rows
    match the active stato filter and always changes the summary counts, so
    that endpoint re-renders this same region instead of only the button.
    """
    primo = periodi.parse_mese(mese)

    # Normalise the raw query params into typed/validated filter values.
    cliente_id = int(cliente) if (cliente and cliente.isdigit()) else None
    referente_val = referente.strip() if referente and referente.strip() else None
    stato_val = stato if stato in STATI_OCCORRENZA_FILTRABILI else None

    # cliente/referente are SQL filters (service columns); the visual state is
    # filtered in Python because it is computed by the engine, not a column.
    righe_mese = riepilogo.occorrenze_del_mese(
        db, primo, cliente_id=cliente_id, referente=referente_val
    )
    righe = riepilogo.filtra_per_stato_visivo(righe_mese, stato_val)

    # Summary card counts, computed from the cliente/referente-filtered month
    # BEFORE the stato filter, so the cards always show the full breakdown even
    # while the table below is narrowed to one state.
    # The cards are NOT mutually exclusive (explicit user request):
    # "da_fatturare" counts EVERY not-yet-billed occurrence of the month
    # (cancelled contracts excluded — same population the riepilogo page
    # lists), while "in_scadenza" counts the subset inside its warning window,
    # so one occurrence can appear in both cards at once. This differs from
    # the per-ROW stato_visivo, which stays exclusive by precedence (the row
    # badge shows the single most urgent state).
    conteggi = {"da_fatturare": 0, "in_scadenza": 0, "fatturato": 0}
    totale_da_fatturare = Decimal("0")
    for riga in righe_mese:
        occ = riga.occorrenza
        if occ.fatturato:
            conteggi["fatturato"] += 1
            continue
        if riga.servizio.disdetto:
            continue  # never nag to invoice a cancelled contract
        conteggi["da_fatturare"] += 1
        totale_da_fatturare += occ.totale
        if occ.stato_visivo == "in_scadenza":
            conteggi["in_scadenza"] += 1

    # Contract-level state (Attivo/In scadenza/Scaduto/Disdetto) is computed,
    # not a column — one lookup per distinct service, reused by the template
    # for the "Stato servizio" badge.
    oggi = date.today()
    stati_servizi = {
        riga.servizio.id: stato_contratto(riga.servizio, oggi=oggi) for riga in righe
    }

    # Where each summary card points. A card is the count of exactly one
    # filter, so clicking it applies that filter; clicking the one already
    # active clears it, which is the only way back that does not require
    # hunting for the funnel icon. cliente/referente are carried along, so the
    # cards narrow the current view instead of resetting it.
    link_stati = {
        st: "/?mese=" + periodi.chiave_mese(primo) + _filtri_querystring(
            cliente_id, referente_val, None if st == stato_val else st
        )
        for st in STATI_OCCORRENZA_FILTRABILI
    }

    return {
        "nav": _navigazione_mese(primo),
        "righe": righe,
        "conteggi": conteggi,
        "link_stati": link_stati,
        "totale_da_fatturare": totale_da_fatturare,
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
    }


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
    contesto = _contesto_risultati(db, mese, cliente, referente, stato)
    contesto["user"] = user
    contesto["clienti"] = filtri.clienti_disponibili(db)
    contesto["referenti"] = filtri.referenti_disponibili(db)
    contesto["stati_occorrenza"] = STATI_OCCORRENZA_FILTRABILI

    # HTMX request (filter change): swap only the results region. A normal page
    # load or a bookmarked URL gets the whole page, with filters already applied
    # and the form pre-populated from the querystring.
    template = (
        "dashboard/_risultati.html"
        if request.headers.get("HX-Request")
        else "dashboard/index.html"
    )
    return templates.TemplateResponse(request, template, contesto)


def _contesto_riepilogo(db: Session, mese: str | None, cliente_id: int | None = None) -> dict:
    """Build everything ``riepilogo/_risultati.html`` needs.

    An occurrence already marked "fatturato" is dropped entirely, not just
    greyed out: this page is "what's left to invoice", so a row must
    disappear from it the moment it (or the equivalent toggle on the
    dashboard) marks it billed — mirroring how billing an occurrence can drop
    it out of the dashboard's own "da_fatturare" filter. Shared by the GET
    route and the toggle/bulk-toggle endpoints in app/routes/servizi.py.
    """
    primo = periodi.parse_mese(mese)
    righe = riepilogo.occorrenze_del_mese(
        db, primo, escludi_disdetti=True, cliente_id=cliente_id
    )
    righe = [r for r in righe if r.occorrenza.stato_visivo != "fatturato"]
    gruppi = riepilogo.raggruppa_per_cliente(righe)
    return {
        "nav": _navigazione_mese(primo),
        "gruppi": gruppi,
        "totale": riepilogo.totale_complessivo(gruppi),
    }


@router.get("/riepilogo")
def riepilogo_mese(
    request: Request,
    mese: str | None = None,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    contesto = _contesto_riepilogo(db, mese)
    contesto["user"] = user

    template = (
        "riepilogo/_risultati.html"
        if request.headers.get("HX-Request")
        else "riepilogo/index.html"
    )
    return templates.TemplateResponse(request, template, contesto)


@router.get("/riepilogo/export")
def riepilogo_export(
    mese: str | None = None,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    primo = periodi.parse_mese(mese)
    righe = riepilogo.occorrenze_del_mese(db, primo, escludi_disdetti=True)
    # Same "still to invoice" scope as the riepilogo page itself (see
    # _contesto_riepilogo): an already-billed occurrence has nothing to do in
    # a billing-recap export.
    righe = [r for r in righe if r.occorrenza.stato_visivo != "fatturato"]
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


@router.get("/riepilogo/export/pdf-mese")
def riepilogo_export_pdf_mese(
    mese: str | None = None,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    """The whole month as one PDF: summary per customer, then the detail.

    Same scope as the page and the CSV (_contesto_riepilogo: cancelled
    contracts and already-billed occurrences out), so the three always show
    the same total. Internal, like the CSV: it lists every customer's
    figures, whereas the per-customer PDF is the one meant for the customer.
    """
    contesto = _contesto_riepilogo(db, mese)
    primo = periodi.parse_mese(mese)
    contenuto = genera_pdf_mese(
        contesto["gruppi"], contesto["nav"]["etichetta"], contesto["totale"], date.today()
    )
    return Response(
        content=contenuto,
        media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="riepilogo_{periodi.chiave_mese(primo)}.pdf"'},
    )


@router.get("/riepilogo/export/pdf")
def riepilogo_export_pdf(
    cliente_id: int,
    mese: str | None = None,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    """PDF recap of one customer's occurrences for the month — meant to be
    handed to that customer, unlike the all-customers CSV export."""
    primo = periodi.parse_mese(mese)
    righe = riepilogo.occorrenze_del_mese(
        db, primo, escludi_disdetti=True, cliente_id=cliente_id
    )
    righe = [r for r in righe if r.occorrenza.stato_visivo != "fatturato"]
    gruppi = riepilogo.raggruppa_per_cliente(righe)
    if not gruppi:
        raise HTTPException(status_code=404, detail="Nessuna occorrenza per questo cliente nel mese selezionato")

    contenuto = genera_pdf_cliente(gruppi[0], periodi.etichetta_mese(primo))
    nome_cliente = "".join(c if c.isalnum() else "_" for c in gruppi[0].cliente.nome)
    nome_file = f"riepilogo_{nome_cliente}_{periodi.chiave_mese(primo)}.pdf"
    return Response(
        content=contenuto,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nome_file}"'},
    )
