"""
Business logic for the dashboard and the billing summary, built on top of the
occurrence engine (``app/services/occorrenze.py``), which is the single source
of truth for billable dates.

For a given month we:
1. select the services whose contract period overlaps the month (a portable,
   index-friendly SQL filter — no strftime/EXTRACT),
2. expand each service into its occurrences that fall inside the month, via
   ``occorrenze_nel_periodo`` (this is where overrides and the day-clamping
   rule are applied),
3. group the resulting rows by customer for the summary.

Summing happens in Python over exact ``Decimal`` values; the per-occurrence
total comes from ``Occorrenza.totale`` so the "total = qty x unit price" rule
lives in one place.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.cliente import Cliente
from app.models.enums import StatoServizio
from app.models.servizio import Servizio
from app.services.occorrenze import Occorrenza, occorrenze_nel_periodo
from app.services.periodi import ultimo_giorno_mese


@dataclass
class RigaOccorrenza:
    """One occurrence together with the service it belongs to.

    Pairing them lets the views show customer/description/referente (from the
    service) next to the occurrence's effective amount and date."""

    servizio: Servizio
    occorrenza: Occorrenza


def occorrenze_del_mese(
    db: Session, primo: date, *, solo_attivi: bool = False
) -> list[RigaOccorrenza]:
    """All occurrences falling within the month starting at ``primo``.

    Services are pre-filtered to those whose contract ``[data_inizio, data_fine]``
    overlaps the month; the occurrence engine then produces the exact dates.
    When ``solo_attivi`` is True only services in state ``attivo`` are considered
    (used by the billing summary, which excludes disdetti and rinnovati).
    """
    inizio = primo
    fine = ultimo_giorno_mese(primo)

    # Contract overlaps the month if it starts on/before the month end AND ends
    # on/after the month start.
    query = (
        select(Servizio)
        .where(Servizio.data_inizio <= fine)
        .where(Servizio.data_fine >= inizio)
        # Eager-load to avoid N+1 queries when expanding occurrences/grouping.
        .options(
            joinedload(Servizio.cliente),
            selectinload(Servizio.override_importi),
        )
    )
    if solo_attivi:
        query = query.where(Servizio.stato == StatoServizio.attivo)

    righe: list[RigaOccorrenza] = []
    for servizio in db.scalars(query):
        for occ in occorrenze_nel_periodo(servizio, inizio, fine):
            righe.append(RigaOccorrenza(servizio=servizio, occorrenza=occ))

    # Chronological within the month, then by customer name for a stable order.
    righe.sort(key=lambda r: (r.occorrenza.data_occorrenza, r.servizio.cliente.nome.lower()))
    return righe


@dataclass
class GruppoCliente:
    """A customer's occurrences for the selected month, with their subtotal."""

    cliente: Cliente
    righe: list[RigaOccorrenza] = field(default_factory=list)

    @property
    def subtotale(self) -> Decimal:
        """Sum of the occurrence totals for this customer (override-aware)."""
        return sum((r.occorrenza.totale for r in self.righe), Decimal("0"))


def raggruppa_per_cliente(righe: list[RigaOccorrenza]) -> list[GruppoCliente]:
    """Group occurrence rows by customer, returned sorted by customer name."""
    gruppi: dict[int, GruppoCliente] = {}
    for riga in righe:
        cliente_id = riga.servizio.cliente_id
        gruppo = gruppi.get(cliente_id)
        if gruppo is None:
            gruppo = GruppoCliente(cliente=riga.servizio.cliente)
            gruppi[cliente_id] = gruppo
        gruppo.righe.append(riga)
    return sorted(gruppi.values(), key=lambda g: g.cliente.nome.lower())


def totale_complessivo(gruppi: list[GruppoCliente]) -> Decimal:
    """Grand total for the month, across all customer groups."""
    return sum((g.subtotale for g in gruppi), Decimal("0"))
