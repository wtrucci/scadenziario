"""
Helpers for the list filters shared by the services page and the dashboard.

These only provide the *options* shown in the filter dropdowns. The actual
filtering lives where each filter belongs: SQL filters on service columns are in
``occorrenze_del_mese`` / the services route, while the per-occurrence visual
state is filtered in ``riepilogo.filtra_per_stato_visivo``.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.cliente import Cliente
from app.models.servizio import Servizio


def clienti_disponibili(db: Session) -> list[Cliente]:
    """All customers, ordered by name — the options for the customer filter."""
    return db.scalars(select(Cliente).order_by(Cliente.nome)).all()


def referenti_disponibili(db: Session) -> list[str]:
    """Distinct non-empty referente values, ordered alphabetically.

    NULL/empty referenti are excluded so the dropdown only lists real choices.
    """
    righe = db.scalars(
        select(Servizio.referente)
        .where(Servizio.referente.is_not(None))
        .where(Servizio.referente != "")
        .distinct()
        .order_by(Servizio.referente)
    ).all()
    return list(righe)


def descrizioni_disponibili(db: Session) -> list[str]:
    """Distinct descrizione values already used, ordered alphabetically.

    Used to power the autocomplete suggestions on the service form.
    """
    righe = db.scalars(
        select(Servizio.descrizione)
        .distinct()
        .order_by(Servizio.descrizione)
    ).all()
    return list(righe)
