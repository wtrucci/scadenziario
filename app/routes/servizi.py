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
from sqlalchemy.orm import Session

from app.database import get_db, utcnow
from app.dependencies import require_login
from app.models.cliente import Cliente
from app.models.enums import StatoServizio, TipoServizio
from app.models.override_importo import OverrideImporto
from app.models.servizio import Servizio
from app.models.utente import Utente
from app.services.occorrenze import occorrenze_nel_periodo
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
        "stati": list(StatoServizio),
    }


def _valori_da_servizio(s: Servizio) -> dict:
    return {
        "cliente_id": str(s.cliente_id),
        "descrizione": s.descrizione,
        "tipo": s.tipo.value,
        "data_inizio": s.data_inizio.isoformat(),
        "data_fine": s.data_fine.isoformat(),
        "cadenza_mesi": str(s.cadenza_mesi),
        "importo": str(s.importo),
        "quantita": str(s.quantita),
        "valuta": s.valuta,
        "preavviso_giorni": str(s.preavviso_giorni),
        "stato": s.stato.value,
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
    data_fine_raw: str,
    cadenza_mesi_raw: str,
    importo_raw: str,
    quantita_raw: str,
    valuta: str,
    preavviso_giorni_raw: str,
    stato_raw: str,
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

    # data_inizio / data_fine
    data_inizio = data_fine = None
    try:
        data_inizio = date.fromisoformat(data_inizio_raw)
        parsed["data_inizio"] = data_inizio
    except (ValueError, TypeError):
        errori.append("Inserisci una data di inizio valida.")
    try:
        data_fine = date.fromisoformat(data_fine_raw)
        parsed["data_fine"] = data_fine
    except (ValueError, TypeError):
        errori.append("Inserisci una data di fine valida.")
    # Cross-field check: the contract cannot end before it starts.
    if data_inizio is not None and data_fine is not None and data_fine < data_inizio:
        errori.append("La data di fine non può essere precedente alla data di inizio.")

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

    # stato
    try:
        parsed["stato"] = StatoServizio(stato_raw)
    except ValueError:
        errori.append("Seleziona uno stato valido.")

    return errori, parsed


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("")
def lista_servizi(
    request: Request,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    servizi = db.scalars(select(Servizio).order_by(Servizio.data_inizio)).all()
    # Each row carries a human-readable cadence label (mensile/trimestrale/...).
    righe = [(s, etichetta_cadenza(s.cadenza_mesi)) for s in servizi]
    return templates.TemplateResponse(
        request, "servizi/lista.html", {"user": user, "righe": righe}
    )


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
                "data_fine": "",
                "cadenza_mesi": "12",
                "importo": "",
                "quantita": "1",
                "valuta": "EUR",
                "preavviso_giorni": "30",
                "stato": StatoServizio.attivo.value,
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
    data_fine: str = Form(""),
    cadenza_mesi: str = Form(""),
    importo: str = Form(""),
    quantita: str = Form("1"),
    valuta: str = Form("EUR"),
    preavviso_giorni: str = Form("30"),
    stato: str = Form(""),
    referente: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    valori = _valori_da_form(
        cliente_id=cliente_id, descrizione=descrizione, tipo=tipo,
        data_inizio=data_inizio, data_fine=data_fine, cadenza_mesi=cadenza_mesi,
        importo=importo, quantita=quantita, valuta=valuta,
        preavviso_giorni=preavviso_giorni, stato=stato,
        referente=referente, note=note,
    )
    errori, parsed = _valida(
        cliente_id_raw=cliente_id, descrizione=descrizione, tipo_raw=tipo,
        data_inizio_raw=data_inizio, data_fine_raw=data_fine, cadenza_mesi_raw=cadenza_mesi,
        importo_raw=importo, quantita_raw=quantita, valuta=valuta,
        preavviso_giorni_raw=preavviso_giorni, stato_raw=stato, db=db,
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
    data_fine: str = Form(""),
    cadenza_mesi: str = Form(""),
    importo: str = Form(""),
    quantita: str = Form("1"),
    valuta: str = Form("EUR"),
    preavviso_giorni: str = Form("30"),
    stato: str = Form(""),
    referente: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    s = _get_or_404(db, servizio_id)
    valori = _valori_da_form(
        cliente_id=cliente_id, descrizione=descrizione, tipo=tipo,
        data_inizio=data_inizio, data_fine=data_fine, cadenza_mesi=cadenza_mesi,
        importo=importo, quantita=quantita, valuta=valuta,
        preavviso_giorni=preavviso_giorni, stato=stato,
        referente=referente, note=note,
    )
    errori, parsed = _valida(
        cliente_id_raw=cliente_id, descrizione=descrizione, tipo_raw=tipo,
        data_inizio_raw=data_inizio, data_fine_raw=data_fine, cadenza_mesi_raw=cadenza_mesi,
        importo_raw=importo, quantita_raw=quantita, valuta=valuta,
        preavviso_giorni_raw=preavviso_giorni, stato_raw=stato, db=db,
    )
    if errori:
        choices = _form_choices(db)
        if s.cliente not in choices["clienti_attivi"]:
            choices["clienti_attivi"] = [s.cliente] + list(choices["clienti_attivi"])
        return templates.TemplateResponse(
            request, "servizi/form.html",
            {"user": user, "titolo": f"Modifica — {s.descrizione}",
             "action": f"/servizi/{servizio_id}/modifica",
             "servizio": s, "valori": valori, "errori": errori, **choices},
            status_code=422,
        )
    for field, value in parsed.items():
        setattr(s, field, value)
    s.referente = referente.strip() or None
    s.note = note.strip() or None
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
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    """
    Toggle the "fatturato" flag for one occurrence (HTMX).

    Occurrences are not stored, so the flag lives on a per-occurrence state row
    (OverrideImporto). This creates the row if missing, or updates it, leaving
    any price/quantity correction untouched. We accept the date only if it is a
    real occurrence of the service, to avoid creating state rows on bogus dates.
    Returns the refreshed toggle button partial.
    """
    s = _get_or_404(db, servizio_id)

    # Guard: the date must be an actual occurrence of this service.
    if not occorrenze_nel_periodo(s, data_occorrenza, data_occorrenza):
        raise HTTPException(status_code=404, detail="Occorrenza non trovata")

    stato = db.scalars(
        select(OverrideImporto)
        .where(OverrideImporto.servizio_id == servizio_id)
        .where(OverrideImporto.data_occorrenza == data_occorrenza)
    ).first()

    if stato is None:
        # No state row yet: create one carrying only the fatturato flag.
        stato = OverrideImporto(servizio_id=servizio_id, data_occorrenza=data_occorrenza)
        db.add(stato)

    # Flip the flag; record/clear the timestamp accordingly.
    stato.fatturato = not stato.fatturato
    stato.fatturato_il = utcnow() if stato.fatturato else None
    db.commit()

    return templates.TemplateResponse(
        request,
        "servizi/_toggle_fatturato.html",
        {
            "servizio_id": servizio_id,
            "data_occorrenza": data_occorrenza,
            "fatturato": stato.fatturato,
        },
    )
