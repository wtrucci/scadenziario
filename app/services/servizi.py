"""
Duplicate detection for services.

Unlike customers, two identical-looking services are often perfectly
legitimate: one customer here has fifteen "Sentinel One" contracts, one per
end customer, told apart only by the referente. So this is never a block —
only a warning the user confirms, to catch the case where the same contract
was entered twice by mistake.

A service counts as a look-alike when it belongs to the same customer and has
the same description AND the same referente (both compared up to casing and
spacing). The referente is part of the key precisely because it is what
distinguishes those fifteen contracts: leaving it out would warn on every
single one of them and train the user to click through the warning.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.servizio import Servizio
from app.services.testo import chiave_identita


def _chiave(descrizione: str, referente: str | None) -> tuple[str, str]:
    return chiave_identita(descrizione), chiave_identita(referente or "")


def trova_servizi_simili(
    db: Session,
    cliente_id: int,
    descrizione: str,
    referente: str | None,
    escludi_id: int | None = None,
) -> list[Servizio]:
    """Services of the same customer that look like the one being saved."""
    if not descrizione.strip():
        return []
    chiave = _chiave(descrizione, referente)
    query = select(Servizio).where(Servizio.cliente_id == cliente_id)
    if escludi_id is not None:
        query = query.where(Servizio.id != escludi_id)
    return [
        s for s in db.scalars(query.order_by(Servizio.data_scadenza))
        if _chiave(s.descrizione, s.referente) == chiave
    ]
