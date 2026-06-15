"""
Business logic for the dashboard (services expiring in a given month) and the
billing summary (same, but only *active* services, grouped by customer).

Design note — grouping and summing happen in Python, not in SQL:
the per-service total is ``quantita * importo``, already expressed by
``Servizio.totale``. Summing in Python (over exact ``Decimal`` values) keeps the
"total = quantity x unit price" rule in that single property instead of
duplicating it in a SQL expression. Month filtering, on the other hand, is done
at the query level with a portable half-open date range.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.cliente import Cliente
from app.models.enums import StatoServizio
from app.models.servizio import Servizio
from app.services.periodi import mese_successivo


def servizi_del_mese(
    db: Session, primo: date, *, solo_attivi: bool = False
) -> list[Servizio]:
    """Services whose expiry falls within the month starting at ``primo``.

    Uses a half-open range ``[primo, mese_successivo)`` so it stays portable
    between SQLite and PostgreSQL and can use the index on ``data_scadenza``.
    When ``solo_attivi`` is True, only services in state ``attivo`` are returned
    (used by the billing summary, which excludes disdetti and rinnovati).
    """
    inizio = primo
    fine = mese_successivo(primo)
    query = (
        select(Servizio)
        .where(Servizio.data_scadenza >= inizio)
        .where(Servizio.data_scadenza < fine)
    )
    if solo_attivi:
        query = query.where(Servizio.stato == StatoServizio.attivo)
    query = query.order_by(Servizio.data_scadenza, Servizio.id)
    return list(db.scalars(query).all())


@dataclass
class GruppoCliente:
    """A customer's services for the selected month, with their subtotal."""

    cliente: Cliente
    servizi: list[Servizio] = field(default_factory=list)

    @property
    def subtotale(self) -> Decimal:
        """Sum of the line totals for this customer."""
        return sum((s.totale for s in self.servizi), Decimal("0"))


def raggruppa_per_cliente(servizi: list[Servizio]) -> list[GruppoCliente]:
    """Group services by customer, returned sorted by customer name."""
    gruppi: dict[int, GruppoCliente] = {}
    for s in servizi:
        gruppo = gruppi.get(s.cliente_id)
        if gruppo is None:
            gruppo = GruppoCliente(cliente=s.cliente)
            gruppi[s.cliente_id] = gruppo
        gruppo.servizi.append(s)
    return sorted(gruppi.values(), key=lambda g: g.cliente.nome.lower())


def totale_complessivo(gruppi: list[GruppoCliente]) -> Decimal:
    """Grand total for the month, across all customer groups."""
    return sum((g.subtotale for g in gruppi), Decimal("0"))
