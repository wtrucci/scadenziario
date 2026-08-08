"""
CRUD routes for services (servizi).

A service is a recurring contract: it is valid from data_inizio to data_fine and
billed every cadenza_mesi months. The actual billable dates ("occorrenze") are
computed elsewhere (app/services/occorrenze.py); these routes only manage the
contract record.

All routes require an authenticated user (require_login).
Delete uses HTMX hx-delete; all other writes use standard HTML form POST.
"""
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db, utcnow
from app.dependencies import require_login
from app.models.cliente import Cliente
from app.models.enums import TipoServizio
from app.models.override_importo import OverrideImporto
from app.models.servizio import Servizio
from app.models.utente import Utente
from app.routes.dashboard import _contesto_risultati
from app.services import filtri
from app.services.occorrenze import (
    ETICHETTE_STATO_CONTRATTO,
    STATI_CONTRATTO,
    calcola_data_fine,
    data_fine_effettiva,
    occorrenze_nel_periodo,
    stato_contratto,
)
from app.services.periodi import etichetta_cadenza
from app.templating import templates

router = APIRouter(prefix="/servizi")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_or_404(db: Session, servizio_id: int) -> Servizio:
    s = db.get(Servizio, servizio_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Servizio non trovato")
    return s


def _clienti_attivi(db: Session) -> list[Cliente]:
    return db.scalars(select(Cliente).where(Cliente.attivo.is_(True)).order_by(Cliente.nome)).all()


def _form_choices(db: Session) -> dict:
    """Returns the dropdown data needed to render the service form."""
    return {
        "clienti_attivi": _clienti_attivi(db),
        "tipi_servizio": list(TipoServizio),
        "referenti_esistenti": filtri.referenti_disponibili(db),
        "descrizioni_esistenti": filtri.descrizioni_disponibili(db),
    }


def _valori_da_servizio(s: Servizio) -> dict:
    return {
        "cliente_id": str(s.cliente_id),
        "descrizione": s.descrizione,
        "tipo": s.tipo.value,
        "data_inizio": s.data_inizio.isoformat(),
        "durata_mesi": str(s.durata_mesi) if s.durata_mesi is not None else "",
        "rinnovo_automatico": s.rinnovo_automatico,
        "cadenza_mesi": str(s.cadenza_mesi),
        "importo": str(s.importo),
        "quantita": str(s.quantita),
        "valuta": s.valuta,
        "preavviso_giorni": str(s.preavviso_giorni),
        "disdetto": s.disdetto,
        "referente": s.referente or "",
        "note": s.note or "",
    }


def _valori_da_form(**kwargs) -> dict:
    """Build the valori dict from raw form strings (for error repopulation)."""
    return {k: (v or "") for k, v in kwargs.items()}


def _valida(
    *,
    cliente_id_raw: str,
    descrizione: str,
    tipo_raw: str,
    data_inizio_raw: str,
    durata_mesi_raw: str,
    cadenza_mesi_raw: str,
    importo_raw: str,
    quantita_raw: str,
    valuta: str,
    preavviso_giorni_raw: str,
    db: Session,
) -> tuple[list[str], dict]:
    """
    Validate form fields. Returns (errori, parsed_values).
    parsed_values contains typed objects only for fields that passed validation.
    errori is empty when all fields are valid.
    """
    errori: list[str] = []
    parsed: dict = {}

    # cliente_id
    try:
        cid = int(cliente_id_raw)
        cliente = db.get(Cliente, cid)
        if cliente is None or not cliente.attivo:
            errori.append("Seleziona un cliente valido e attivo.")
        else:
            parsed["cliente_id"] = cid
    except (ValueError, TypeError):
        errori.append("Seleziona un cliente.")

    # descrizione
    desc = descrizione.strip()
    if not desc:
        errori.append("La descrizione è obbligatoria.")
    elif len(desc) > 300:
        errori.append("La descrizione non può superare i 300 caratteri.")
    else:
        parsed["descrizione"] = desc

    # tipo
    try:
        parsed["tipo"] = TipoServizio(tipo_raw)
    except ValueError:
        errori.append("Seleziona un tipo valido.")

    # data_inizio
    data_inizio = None
    try:
        data_inizio = date.fromisoformat(data_inizio_raw)
        parsed["data_inizio"] = data_inizio
    except (ValueError, TypeError):
        errori.append("Inserisci una data di inizio valida.")

    # durata_mesi — data_fine is derived from it (data_inizio + durata_mesi),
    # never entered directly (see Servizio docstring).
    try:
        durata_mesi = int(durata_mesi_raw)
        if durata_mesi < 1:
            errori.append("La durata del contratto deve essere di almeno 1 mese.")
        else:
            parsed["durata_mesi"] = durata_mesi
            if data_inizio is not None:
                parsed["data_fine"] = calcola_data_fine(data_inizio, durata_mesi)
    except (ValueError, TypeError):
        errori.append("Durata contratto non valida: inserisci un numero intero di mesi (es. 12).")

    # cadenza_mesi
    try:
        cadenza = int(cadenza_mesi_raw)
        if cadenza < 1:
            errori.append("La cadenza deve essere di almeno 1 mese.")
        else:
            parsed["cadenza_mesi"] = cadenza
    except (ValueError, TypeError):
        errori.append("Cadenza non valida: inserisci un numero intero di mesi (es. 12).")

    # importo
    try:
        importo = Decimal(importo_raw.replace(",", "."))
        if importo < 0:
            errori.append("L'importo non può essere negativo.")
        else:
            parsed["importo"] = importo
    except (InvalidOperation, AttributeError):
        errori.append("Importo non valido: usa un numero decimale (es. 99.90).")

    # quantita
    try:
        q = int(quantita_raw)
        if q < 1:
            errori.append("La quantità deve essere almeno 1.")
        else:
            parsed["quantita"] = q
    except (ValueError, TypeError):
        errori.append("Quantità non valida: inserisci un numero intero.")

    # valuta
    v = valuta.strip().upper()
    if not v:
        errori.append("La valuta è obbligatoria.")
    else:
        parsed["valuta"] = v

    # preavviso_giorni
    try:
        pg = int(preavviso_giorni_raw)
        if pg < 0:
            errori.append("Il preavviso non può essere negativo.")
        else:
            parsed["preavviso_giorni"] = pg
    except (ValueError, TypeError):
        errori.append("Preavviso non valido: inserisci un numero intero.")

    return errori, parsed


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("")
def lista_servizi(
    request: Request,
    cliente: str | None = None,
    referente: str | None = None,
    stato: str | None = None,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    # Normalise the raw query params into typed/validated filter values.
    cliente_id = int(cliente) if (cliente and cliente.isdigit()) else None
    referente_val = referente.strip() if referente and referente.strip() else None
    stato_val = stato if stato in STATI_CONTRATTO else None

    # cliente/referente are SQL conditions on service columns; the contract
    # state is NOT a column (only disdetto is — see stato_contratto), so it is
    # filtered in Python after computing it per row, same pattern as the
    # dashboard's occurrence-level state filter.
    query = (
        select(Servizio)
        .options(joinedload(Servizio.cliente))
        .order_by(Servizio.data_inizio)
    )
    if cliente_id is not None:
        query = query.where(Servizio.cliente_id == cliente_id)
    if referente_val:
        query = query.where(Servizio.referente == referente_val)

    servizi = db.scalars(query).all()
    oggi = date.today()
    # Each row carries a human-readable cadence label (mensile/trimestrale/...),
    # the EFFECTIVE end date (so an auto-renewing contract shows its current,
    # rolled-forward period instead of the originally stored one), and the
    # computed contract state.
    righe = [
        (s, etichetta_cadenza(s.cadenza_mesi), data_fine_effettiva(s, riferimento=oggi),
         stato_contratto(s, oggi=oggi))
        for s in servizi
    ]
    if stato_val is not None:
        righe = [r for r in righe if r[3] == stato_val]

    contesto = {
        "user": user,
        "righe": righe,
        # Current filter values, to pre-populate the form (e.g. on a bookmarked URL).
        "filtri": {
            "cliente": cliente_id,
            "referente": referente_val or "",
            "stato": stato_val or "",
        },
        "clienti": filtri.clienti_disponibili(db),
        "referenti": filtri.referenti_disponibili(db),
        "stati": STATI_CONTRATTO,
        "etichette_stato": ETICHETTE_STATO_CONTRATTO,
    }

    # HTMX request (filter change): swap only the results region. A normal page
    # load or a bookmarked URL gets the whole page, with filters already applied
    # and the form pre-populated from the querystring.
    template = (
        "servizi/_risultati.html"
        if request.headers.get("HX-Request")
        else "servizi/lista.html"
    )
    return templates.TemplateResponse(request, template, contesto)


@router.get("/nuovo")
def nuovo_form(
    request: Request,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    return templates.TemplateResponse(
        request,
        "servizi/form.html",
        {
            "user": user,
            "titolo": "Nuovo servizio",
            "action": "/servizi",
            "valori": {
                "cliente_id": "",
                "descrizione": "",
                "tipo": TipoServizio.abbonamento.value,
                "data_inizio": "",
                "durata_mesi": "12",
                "rinnovo_automatico": False,
                "cadenza_mesi": "12",
                "importo": "",
                "quantita": "1",
                "valuta": "EUR",
                "preavviso_giorni": "30",
                "disdetto": False,
                "referente": "",
                "note": "",
            },
            "errori": [],
            **_form_choices(db),
        },
    )


@router.post("")
def crea_servizio(
    request: Request,
    cliente_id: str = Form(""),
    descrizione: str = Form(""),
    tipo: str = Form(""),
    data_inizio: str = Form(""),
    durata_mesi: str = Form(""),
    rinnovo_automatico: str | None = Form(None),  # checkbox: present when checked
    cadenza_mesi: str = Form(""),
    importo: str = Form(""),
    quantita: str = Form("1"),
    valuta: str = Form("EUR"),
    preavviso_giorni: str = Form("30"),
    disdetto: str | None = Form(None),  # checkbox: present when checked
    referente: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    is_rinnovo = rinnovo_automatico is not None
    is_disdetto = disdetto is not None
    valori = _valori_da_form(
        cliente_id=cliente_id, descrizione=descrizione, tipo=tipo,
        data_inizio=data_inizio, durata_mesi=durata_mesi, cadenza_mesi=cadenza_mesi,
        importo=importo, quantita=quantita, valuta=valuta,
        preavviso_giorni=preavviso_giorni,
        referente=referente, note=note,
    )
    valori["rinnovo_automatico"] = is_rinnovo
    valori["disdetto"] = is_disdetto
    errori, parsed = _valida(
        cliente_id_raw=cliente_id, descrizione=descrizione, tipo_raw=tipo,
        data_inizio_raw=data_inizio, durata_mesi_raw=durata_mesi, cadenza_mesi_raw=cadenza_mesi,
        importo_raw=importo, quantita_raw=quantita, valuta=valuta,
        preavviso_giorni_raw=preavviso_giorni, db=db,
    )
    if errori:
        return templates.TemplateResponse(
            request, "servizi/form.html",
            {"user": user, "titolo": "Nuovo servizio", "action": "/servizi",
             "valori": valori, "errori": errori, **_form_choices(db)},
            status_code=422,
        )
    db.add(Servizio(
        referente=referente.strip() or None,
        note=note.strip() or None,
        rinnovo_automatico=is_rinnovo,
        disdetto=is_disdetto,
        **parsed,
    ))
    db.commit()
    return RedirectResponse(url="/servizi", status_code=303)


@router.get("/{servizio_id}/modifica")
def modifica_form(
    request: Request,
    servizio_id: int,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    s = _get_or_404(db, servizio_id)
    # If the linked customer is inactive, still include it so the saved value
    # is visible, and mark it as such in the dropdown.
    choices = _form_choices(db)
    if s.cliente not in choices["clienti_attivi"]:
        choices["clienti_attivi"] = [s.cliente] + list(choices["clienti_attivi"])
    return templates.TemplateResponse(
        request,
        "servizi/form.html",
        {
            "user": user,
            "titolo": f"Modifica — {s.descrizione}",
            "action": f"/servizi/{servizio_id}/modifica",
            "servizio": s,
            "valori": _valori_da_servizio(s),
            "etichetta_stato_attuale": ETICHETTE_STATO_CONTRATTO[stato_contratto(s)],
            "errori": [],
            **choices,
        },
    )


@router.post("/{servizio_id}/modifica")
def aggiorna_servizio(
    request: Request,
    servizio_id: int,
    cliente_id: str = Form(""),
    descrizione: str = Form(""),
    tipo: str = Form(""),
    data_inizio: str = Form(""),
    durata_mesi: str = Form(""),
    rinnovo_automatico: str | None = Form(None),  # checkbox: present when checked
    cadenza_mesi: str = Form(""),
    importo: str = Form(""),
    quantita: str = Form("1"),
    valuta: str = Form("EUR"),
    preavviso_giorni: str = Form("30"),
    disdetto: str | None = Form(None),  # checkbox: present when checked
    referente: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    s = _get_or_404(db, servizio_id)
    is_rinnovo = rinnovo_automatico is not None
    is_disdetto = disdetto is not None
    valori = _valori_da_form(
        cliente_id=cliente_id, descrizione=descrizione, tipo=tipo,
        data_inizio=data_inizio, durata_mesi=durata_mesi, cadenza_mesi=cadenza_mesi,
        importo=importo, quantita=quantita, valuta=valuta,
        preavviso_giorni=preavviso_giorni,
        referente=referente, note=note,
    )
    valori["rinnovo_automatico"] = is_rinnovo
    valori["disdetto"] = is_disdetto
    errori, parsed = _valida(
        cliente_id_raw=cliente_id, descrizione=descrizione, tipo_raw=tipo,
        data_inizio_raw=data_inizio, durata_mesi_raw=durata_mesi, cadenza_mesi_raw=cadenza_mesi,
        importo_raw=importo, quantita_raw=quantita, valuta=valuta,
        preavviso_giorni_raw=preavviso_giorni, db=db,
    )
    if errori:
        choices = _form_choices(db)
        if s.cliente not in choices["clienti_attivi"]:
            choices["clienti_attivi"] = [s.cliente] + list(choices["clienti_attivi"])
        return templates.TemplateResponse(
            request, "servizi/form.html",
            {"user": user, "titolo": f"Modifica — {s.descrizione}",
             "action": f"/servizi/{servizio_id}/modifica",
             "servizio": s, "valori": valori, "errori": errori,
             "etichetta_stato_attuale": ETICHETTE_STATO_CONTRATTO[stato_contratto(s)],
             **choices},
            status_code=422,
        )
    for field, value in parsed.items():
        setattr(s, field, value)
    s.referente = referente.strip() or None
    s.note = note.strip() or None
    s.rinnovo_automatico = is_rinnovo
    s.disdetto = is_disdetto
    db.commit()
    return RedirectResponse(url="/servizi", status_code=303)


@router.delete("/{servizio_id}")
def elimina_servizio(
    servizio_id: int,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    s = _get_or_404(db, servizio_id)
    db.delete(s)
    db.commit()
    return Response(status_code=200)


@router.post("/{servizio_id}/occorrenze/{data_occorrenza}/fatturato")
def toggle_fatturato(
    request: Request,
    servizio_id: int,
    data_occorrenza: date,
    mese: str | None = Form(None),
    cliente: str | None = Form(None),
    referente: str | None = Form(None),
    stato_filtro: str | None = Form(None, alias="stato"),
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    """
    Toggle the "fatturato" flag for one occurrence (HTMX).

    Occurrences are not stored, so the flag lives on a per-occurrence state row
    (OverrideImporto). We accept the date only if it is a real occurrence of the
    service, to avoid creating state rows on bogus dates.

    Billing freezes the price: on fatturato=True we snapshot the current
    effective price/quantity (override-aware) into the row, so a later change to
    the service price does not alter what was already billed. On un-billing we
    clear that snapshot ONLY if it was not a deliberate manual override.

    The toggle button is only used on the dashboard (see
    servizi/_toggle_fatturato.html), which sends the currently active month and
    filters along (hx-vals/hx-include). We re-render the whole results region
    with them — not just the button — because the new fatturato state can (a)
    change the summary card counts and (b) make the row itself disappear from
    an active stato filter (e.g. filtering "Da fatturare" and billing it).
    """
    s = _get_or_404(db, servizio_id)

    # Guard: the date must be an actual occurrence of this service. Reuse the
    # computed occurrence as the source of the effective price/quantity.
    occorrenze = occorrenze_nel_periodo(s, data_occorrenza, data_occorrenza)
    if not occorrenze:
        raise HTTPException(status_code=404, detail="Occorrenza non trovata")
    occ = occorrenze[0]

    stato = db.scalars(
        select(OverrideImporto)
        .where(OverrideImporto.servizio_id == servizio_id)
        .where(OverrideImporto.data_occorrenza == data_occorrenza)
    ).first()

    if stato is None:
        # No state row yet: create one carrying only the fatturato flag.
        stato = OverrideImporto(servizio_id=servizio_id, data_occorrenza=data_occorrenza)
        db.add(stato)

    stato.fatturato = not stato.fatturato
    if stato.fatturato:
        # Billing ON: freeze the effective price/quantity at this moment.
        stato.importo = occ.importo
        stato.quantita = occ.quantita
        stato.fatturato_il = utcnow()
    else:
        # Billing OFF (undo): drop the timestamp, and clear the snapshot so the
        # occurrence tracks the service price again — but only if this is not a
        # deliberate manual override, which must be preserved.
        stato.fatturato_il = None
        if not stato.override_manuale:
            stato.importo = None
            stato.quantita = None
    db.commit()

    contesto = _contesto_risultati(db, mese, cliente, referente, stato_filtro)
    return templates.TemplateResponse(request, "dashboard/_risultati.html", contesto)
