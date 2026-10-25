"""
Supplier contracts: what we buy and resell, with the supplier's own deadlines.

- ``GET    /fornitori``                    list (search + state cards)
- ``GET    /fornitori/nuovo``              empty form
- ``POST   /fornitori``                    create
- ``GET    /fornitori/{id}/modifica``      edit form
- ``POST   /fornitori/{id}/modifica``      update
- ``POST   /fornitori/{id}/rinnovato``     move a manual contract to its next period (HTMX)
- ``DELETE /fornitori/{id}``               delete (HTMX)

Kept entirely apart from customer services: these are costs, and nothing here
reaches the dashboard, the monthly summary or the offers page. Dates and
states are computed in app/services/fornitori.py.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_login
from app.models.contratto_fornitore import ContrattoFornitore
from app.models.utente import Utente
from app.services.fornitori import (
    fornitori_esistenti,
    prossima_scadenza,
    scadenza_dopo_rinnovo,
    stato_contratto_fornitore,
)
from app.services.occorrenze import ETICHETTE_STATO_CONTRATTO
from app.services.periodi import etichetta_cadenza
from app.templating import templates

router = APIRouter(prefix="/fornitori")

# Card filters: the two states that call for action.
FILTRI_STATO = ("in_scadenza", "scaduto")

CAMPI_RICERCA = (
    ContrattoFornitore.fornitore,
    ContrattoFornitore.descrizione,
    ContrattoFornitore.riferimento,
    ContrattoFornitore.note,
)


class ModuloContratto(BaseModel):
    """The form exactly as posted: every field a raw string, validated by
    _valida. Checkboxes are absent when unticked, hence None."""

    fornitore: str = ""
    descrizione: str = ""
    riferimento: str = ""
    data_scadenza: str = ""
    cadenza_mesi: str = ""
    rinnovo_automatico: str | None = None
    importo: str = ""
    quantita: str = "1"
    valuta: str = "EUR"
    preavviso_giorni: str = "30"
    disdetto: str | None = None
    note: str = ""

    def valori(self) -> dict:
        """What the template shows in the fields (checkboxes as booleans)."""
        dati = self.model_dump()
        dati["rinnovo_automatico"] = self.rinnovo_automatico is not None
        dati["disdetto"] = self.disdetto is not None
        return dati


VALORI_NUOVO = ModuloContratto(cadenza_mesi="12").valori()


def _valori_da_contratto(c: ContrattoFornitore) -> dict:
    return {
        "fornitore": c.fornitore,
        "descrizione": c.descrizione,
        "riferimento": c.riferimento or "",
        "data_scadenza": c.data_scadenza.isoformat(),
        "cadenza_mesi": str(c.cadenza_mesi),
        "rinnovo_automatico": c.rinnovo_automatico,
        "importo": str(c.importo),
        "quantita": str(c.quantita),
        "valuta": c.valuta,
        "preavviso_giorni": str(c.preavviso_giorni),
        "disdetto": c.disdetto,
        "note": c.note or "",
    }


def _intero(grezzo: str, minimo: int, errore: str, errori: list[str]) -> int | None:
    """Parse a whole number >= minimo, or record ``errore``."""
    try:
        valore = int(grezzo)
    except (TypeError, ValueError):
        valore = None
    if valore is None or valore < minimo:
        errori.append(errore)
        return None
    return valore


def _valida(modulo: ModuloContratto) -> tuple[list[str], dict]:
    """Check the form. Returns (errors, column values ready for the model)."""
    errori: list[str] = []
    dati: dict = {
        "fornitore": modulo.fornitore.strip(),
        "descrizione": modulo.descrizione.strip(),
        "riferimento": modulo.riferimento.strip() or None,
        "valuta": modulo.valuta.strip().upper(),
        "note": modulo.note.strip() or None,
        "rinnovo_automatico": modulo.rinnovo_automatico is not None,
        "disdetto": modulo.disdetto is not None,
    }
    if not dati["fornitore"]:
        errori.append("Il fornitore è obbligatorio.")
    if not dati["descrizione"]:
        errori.append("La descrizione è obbligatoria.")
    if not dati["valuta"]:
        errori.append("La valuta è obbligatoria.")

    try:
        dati["data_scadenza"] = date.fromisoformat(modulo.data_scadenza)
    except ValueError:
        errori.append("La data di scadenza è obbligatoria (gg/mm/aaaa).")

    dati["cadenza_mesi"] = _intero(
        modulo.cadenza_mesi, 1, "Cadenza non valida: un numero intero di mesi (es. 12).", errori)
    dati["quantita"] = _intero(
        modulo.quantita, 1, "La quantità deve essere un intero di almeno 1.", errori)
    dati["preavviso_giorni"] = _intero(
        modulo.preavviso_giorni, 0, "Preavviso non valido: un numero intero di giorni.", errori)

    try:
        dati["importo"] = Decimal(modulo.importo.replace(",", "."))
        if dati["importo"] < 0:
            errori.append("Il costo non può essere negativo.")
    except InvalidOperation:
        errori.append("Costo non valido: usa un numero decimale (es. 99.90).")

    return errori, dati


def _get_or_404(db: Session, contratto_id: int) -> ContrattoFornitore:
    c = db.get(ContrattoFornitore, contratto_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Contratto fornitore non trovato")
    return c


def _contesto_lista(db: Session, stato: str | None, q: str | None) -> dict:
    stato_val = stato if stato in FILTRI_STATO else None
    ricerca = (q or "").strip()

    query = select(ContrattoFornitore).order_by(ContrattoFornitore.fornitore)
    # Every word must match somewhere, as in the services search.
    for parola in ricerca.split():
        schema = f"%{parola}%"
        query = query.where(or_(*[campo.ilike(schema) for campo in CAMPI_RICERCA]))

    oggi = date.today()
    tutte = [
        (c, etichetta_cadenza(c.cadenza_mesi), prossima_scadenza(c, oggi),
         stato_contratto_fornitore(c, oggi))
        for c in db.scalars(query)
    ]
    conteggi = {st: sum(1 for r in tutte if r[3] == st) for st in FILTRI_STATO}

    def url(stato_link: str | None) -> str:
        parametri = {}
        if stato_link:
            parametri["stato"] = stato_link
        if ricerca:
            parametri["q"] = ricerca
        return "/fornitori" + ("?" + urlencode(parametri) if parametri else "")

    return {
        "righe": [r for r in tutte if stato_val is None or r[3] == stato_val],
        "conteggi": conteggi,
        # A card applies its filter, or clears it when already active.
        "link_stati": {st: url(None if st == stato_val else st) for st in FILTRI_STATO},
        "filtri": {"stato": stato_val or "", "q": ricerca},
        "etichette_stato": ETICHETTE_STATO_CONTRATTO,
        # For the "Rinnovato" confirmation, which names the new date.
        "dopo_rinnovo": scadenza_dopo_rinnovo,
    }


def _form(request: Request, db: Session, user: Utente, *, titolo: str, action: str,
          valori: dict, errori: list[str], contratto: ContrattoFornitore | None = None,
          status_code: int = 200):
    return templates.TemplateResponse(
        request,
        "fornitori/form.html",
        {
            "user": user,
            "titolo": titolo,
            "action": action,
            "valori": valori,
            "errori": errori,
            "contratto": contratto,
            # On an auto-renewing contract the stored date is only the anchor
            # and may be long past: opening it must still answer "when does
            # it expire?".
            "prossima": prossima_scadenza(contratto, date.today()) if contratto else None,
            "fornitori_esistenti": fornitori_esistenti(db),
        },
        status_code=status_code,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("")
def lista_fornitori(
    request: Request,
    stato: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    contesto = {"user": user, **_contesto_lista(db, stato, q)}
    template = (
        "fornitori/_risultati.html"
        if request.headers.get("HX-Request")
        else "fornitori/lista.html"
    )
    return templates.TemplateResponse(request, template, contesto)


@router.get("/nuovo")
def nuovo_form(
    request: Request,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    return _form(request, db, user, titolo="Nuovo contratto fornitore",
                 action="/fornitori", valori=VALORI_NUOVO, errori=[])


@router.post("")
def crea_contratto(
    request: Request,
    modulo: Annotated[ModuloContratto, Form()],
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    errori, dati = _valida(modulo)
    if errori:
        return _form(request, db, user, titolo="Nuovo contratto fornitore",
                     action="/fornitori", valori=modulo.valori(), errori=errori,
                     status_code=422)
    db.add(ContrattoFornitore(**dati))
    db.commit()
    return RedirectResponse(url="/fornitori", status_code=303)


@router.get("/{contratto_id}/modifica")
def modifica_form(
    request: Request,
    contratto_id: int,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    c = _get_or_404(db, contratto_id)
    return _form(request, db, user, titolo=f"Modifica — {c.descrizione}",
                 action=f"/fornitori/{c.id}/modifica",
                 valori=_valori_da_contratto(c), errori=[], contratto=c)


@router.post("/{contratto_id}/modifica")
def aggiorna_contratto(
    request: Request,
    contratto_id: int,
    modulo: Annotated[ModuloContratto, Form()],
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    c = _get_or_404(db, contratto_id)
    errori, dati = _valida(modulo)
    if errori:
        return _form(request, db, user, titolo=f"Modifica — {c.descrizione}",
                     action=f"/fornitori/{c.id}/modifica", valori=modulo.valori(),
                     errori=errori, contratto=c, status_code=422)
    for campo, valore in dati.items():
        setattr(c, campo, valore)
    db.commit()
    return RedirectResponse(url="/fornitori", status_code=303)


@router.post("/{contratto_id}/rinnovato")
def segna_rinnovato(
    request: Request,
    contratto_id: int,
    stato: str | None = Form(None),
    q: str | None = Form(None),
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    """The contract has been renewed with the supplier: move it one period on.

    Only for contracts WITHOUT automatic renewal — the others move by
    themselves, and the button is not shown for them. The whole results region
    is re-rendered, because the state cards change too.
    """
    c = _get_or_404(db, contratto_id)
    if c.rinnovo_automatico or c.disdetto:
        raise HTTPException(status_code=400, detail="Il contratto non si rinnova a mano")
    c.data_scadenza = scadenza_dopo_rinnovo(c)
    db.commit()
    return templates.TemplateResponse(
        request, "fornitori/_risultati.html", {"user": user, **_contesto_lista(db, stato, q)}
    )


@router.delete("/{contratto_id}")
def elimina_contratto(
    contratto_id: int,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    db.delete(_get_or_404(db, contratto_id))
    db.commit()
    return Response(status_code=200)
