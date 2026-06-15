"""
CRUD routes for customers (clienti).

All routes require an authenticated user (require_login).
Delete uses HTMX hx-delete; all other writes use standard HTML form POST.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_login
from app.models.cliente import Cliente
from app.models.servizio import Servizio
from app.models.utente import Utente
from app.templating import templates

router = APIRouter(prefix="/clienti")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_or_404(db: Session, cliente_id: int) -> Cliente:
    cliente = db.get(Cliente, cliente_id)
    if cliente is None:
        raise HTTPException(status_code=404, detail="Cliente non trovato")
    return cliente


def _valida_nome(nome: str) -> list[str]:
    if not nome.strip():
        return ["Il nome è obbligatorio."]
    if len(nome.strip()) > 200:
        return ["Il nome non può superare i 200 caratteri."]
    return []


def _valori_da_cliente(c: Cliente) -> dict:
    return {"nome": c.nome, "note": c.note or "", "attivo": c.attivo}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("")
def lista_clienti(
    request: Request,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    clienti = db.scalars(select(Cliente).order_by(Cliente.nome)).all()
    return templates.TemplateResponse(
        request, "clienti/lista.html", {"user": user, "clienti": clienti}
    )


@router.get("/nuovo")
def nuovo_form(request: Request, user: Utente = Depends(require_login)):
    return templates.TemplateResponse(
        request,
        "clienti/form.html",
        {
            "user": user,
            "titolo": "Nuovo cliente",
            "action": "/clienti",
            "valori": {"nome": "", "note": "", "attivo": True},
            "errori": [],
        },
    )


@router.post("")
def crea_cliente(
    request: Request,
    nome: str = Form(...),
    note: str = Form(""),
    attivo: Optional[str] = Form(None),  # checkbox: present when checked, absent when not
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    is_attivo = attivo is not None
    errori = _valida_nome(nome)
    if errori:
        return templates.TemplateResponse(
            request,
            "clienti/form.html",
            {
                "user": user,
                "titolo": "Nuovo cliente",
                "action": "/clienti",
                "valori": {"nome": nome, "note": note, "attivo": is_attivo},
                "errori": errori,
            },
            status_code=422,
        )
    db.add(Cliente(nome=nome.strip(), note=note.strip() or None, attivo=is_attivo))
    db.commit()
    return RedirectResponse(url="/clienti", status_code=303)


@router.get("/{cliente_id}/modifica")
def modifica_form(
    request: Request,
    cliente_id: int,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    cliente = _get_or_404(db, cliente_id)
    return templates.TemplateResponse(
        request,
        "clienti/form.html",
        {
            "user": user,
            "titolo": f"Modifica — {cliente.nome}",
            "action": f"/clienti/{cliente_id}/modifica",
            "valori": _valori_da_cliente(cliente),
            "errori": [],
        },
    )


@router.post("/{cliente_id}/modifica")
def aggiorna_cliente(
    request: Request,
    cliente_id: int,
    nome: str = Form(...),
    note: str = Form(""),
    attivo: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    cliente = _get_or_404(db, cliente_id)
    is_attivo = attivo is not None
    errori = _valida_nome(nome)
    if errori:
        return templates.TemplateResponse(
            request,
            "clienti/form.html",
            {
                "user": user,
                "titolo": f"Modifica — {cliente.nome}",
                "action": f"/clienti/{cliente_id}/modifica",
                "valori": {"nome": nome, "note": note, "attivo": is_attivo},
                "errori": errori,
            },
            status_code=422,
        )
    cliente.nome = nome.strip()
    cliente.note = note.strip() or None
    cliente.attivo = is_attivo
    db.commit()
    return RedirectResponse(url="/clienti", status_code=303)


@router.delete("/{cliente_id}")
def elimina_cliente(
    request: Request,
    cliente_id: int,
    db: Session = Depends(get_db),
    user: Utente = Depends(require_login),
):
    cliente = _get_or_404(db, cliente_id)

    n_servizi = db.scalar(
        select(func.count()).select_from(Servizio).where(Servizio.cliente_id == cliente_id)
    )
    if n_servizi:
        # Return the row with an inline error; HTMX swaps it in place (outerHTML).
        errore = (
            f"Impossibile eliminare: il cliente ha {n_servizi} "
            f"{'servizio collegato' if n_servizi == 1 else 'servizi collegati'}."
        )
        return templates.TemplateResponse(
            request, "clienti/_riga.html", {"c": cliente, "errore": errore}
        )

    db.delete(cliente)
    db.commit()
    # Empty 200: HTMX with hx-swap="outerHTML" on the target <tr> removes the row.
    return Response(status_code=200)
