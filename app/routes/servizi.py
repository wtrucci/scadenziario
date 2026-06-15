"""
CRUD routes for services (servizi).

All routes require an authenticated user (require_login).
Delete uses HTMX hx-delete; all other writes use standard HTML form POST.
"""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_login
from app.models.cliente import Cliente
from app.models.enums import Ricorrenza, StatoServizio, TipoServizio
from app.models.servizio import Servizio
from app.models.utente import Utente
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


def _scadenza_class(s: Servizio, oggi: date) -> str:
    """Return a CSS class name based on the service's expiry status."""
    if s.data_scadenza < oggi:
        return "row-scaduta"
    if s.data_scadenza <= oggi + timedelta(days=s.preavviso_giorni):
        return "row-in-scadenza"
    return ""


def _clienti_attivi(db: Session) -> list[Cliente]:
    return db.scalars(select(Cliente).where(Cliente.attivo.is_(True)).order_by(Cliente.nome)).all()


def _form_choices(db: Session) -> dict:
    """Returns the dropdown data needed to render the service form."""
    return {
        "clienti_attivi": _clienti_attivi(db),
        "tipi_servizio": list(TipoServizio),
        "ricorrenze": list(Ricorrenza),
        "stati": list(StatoServizio),
    }


def _valori_da_servizio(s: Servizio) -> dict:
    return {
        "cliente_id": str(s.cliente_id),
        "descrizione": s.descrizione,
        "tipo": s.tipo.value,
        "data_scadenza": s.data_scadenza.isoformat(),
        "importo": str(s.importo),
        "quantita": str(s.quantita),
        "valuta": s.valuta,
        "ricorrenza": s.ricorrenza.value,
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
    data_scadenza_raw: str,
    importo_raw: str,
    quantita_raw: str,
    valuta: str,
    ricorrenza_raw: str,
    preavviso_giorni_raw: str,
    stato_raw: str,
    db: Session,
) -> tuple[list[str], dict]:
    """
    Validate form fields. Returns (errori, parsed_values).
    parsed_values contains typed objects only for fields that passed validation,
    None for those that failed. errori is empty when all fields are valid.
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

    # data_scadenza
    try:
        parsed["data_scadenza"] = date.fromisoformat(data_scadenza_raw)
    except (ValueError, TypeError):
        errori.append("Inserisci una data di scadenza valida.")

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

    # ricorrenza
    try:
        parsed["ricorrenza"] = Ricorrenza(ricorrenza_raw)
    except ValueError:
        errori.append("Seleziona una ricorrenza valida.")

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
    servizi = db.scalars(select(Servizio).order_by(Servizio.data_scadenza)).all()
    oggi = date.today()
    righe = [(s, _scadenza_class(s, oggi)) for s in servizi]
    return templates.TemplateResponse(
        request, "servizi/lista.html", {"user": user, "righe": righe, "oggi": oggi}
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
                "data_scadenza": "",
                "importo": "",
                "quantita": "1",
                "valuta": "EUR",
                "ricorrenza": Ricorrenza.annuale.value,
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
    data_scadenza: str = Form(""),
    importo: str = Form(""),
    quantita: str = Form("1"),
    valuta: str = Form("EUR"),
    ricorrenza: str = Form(""),
    preavviso_giorni: str = Form("30"),
    stato: str = Form(""),
    referente: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    valori = _valori_da_form(
        cliente_id=cliente_id, descrizione=descrizione, tipo=tipo,
        data_scadenza=data_scadenza, importo=importo, quantita=quantita,
        valuta=valuta, ricorrenza=ricorrenza, preavviso_giorni=preavviso_giorni,
        stato=stato, referente=referente, note=note,
    )
    errori, parsed = _valida(
        cliente_id_raw=cliente_id, descrizione=descrizione, tipo_raw=tipo,
        data_scadenza_raw=data_scadenza, importo_raw=importo, quantita_raw=quantita,
        valuta=valuta, ricorrenza_raw=ricorrenza, preavviso_giorni_raw=preavviso_giorni,
        stato_raw=stato, db=db,
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
    data_scadenza: str = Form(""),
    importo: str = Form(""),
    quantita: str = Form("1"),
    valuta: str = Form("EUR"),
    ricorrenza: str = Form(""),
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
        data_scadenza=data_scadenza, importo=importo, quantita=quantita,
        valuta=valuta, ricorrenza=ricorrenza, preavviso_giorni=preavviso_giorni,
        stato=stato, referente=referente, note=note,
    )
    errori, parsed = _valida(
        cliente_id_raw=cliente_id, descrizione=descrizione, tipo_raw=tipo,
        data_scadenza_raw=data_scadenza, importo_raw=importo, quantita_raw=quantita,
        valuta=valuta, ricorrenza_raw=ricorrenza, preavviso_giorni_raw=preavviso_giorni,
        stato_raw=stato, db=db,
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
