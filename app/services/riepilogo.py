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
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.cliente import Cliente
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
    db: Session,
    primo: date,
    *,
    escludi_disdetti: bool = False,
    cliente_id: int | None = None,
    referente: str | None = None,
) -> list[RigaOccorrenza]:
    """All occurrences falling within the month starting at ``primo``.

    Services are pre-filtered to those whose contract ``[data_inizio, data_fine]``
    overlaps the month; the occurrence engine then produces the exact dates.
    When ``escludi_disdetti`` is True, cancelled contracts (Servizio.disdetto)
    are left out entirely (used by the billing summary — a cancelled contract
    is never to be invoiced, however far its date range still runs). Note this
    is a different concern from the per-occurrence ``stato_visivo``: even
    without this flag, disdetto contracts never show "da_fatturare"/
    "in_scadenza" (see ``_stato_visivo`` in occorrenze.py) — this flag instead
    hides them from the summary entirely, not just from the urgent-alert states.

    ``cliente_id`` and ``referente`` are optional SQL filters on service columns.
    The per-occurrence visual state is NOT filtered here (it is computed, not a
    column): use ``filtra_per_stato_visivo`` on the result for that.
    """
    inizio = primo
    fine = ultimo_giorno_mese(primo)

    # Contract overlaps the month if it starts on/before the month end AND ends
    # on/after the month start. An auto-renewing contract has no fixed end (its
    # EFFECTIVE end rolls forward — see data_fine_effettiva), so the stored
    # data_fine alone would wrongly drop it from later months: match it on
    # rinnovo_automatico regardless of the stored data_fine instead.
    # The one-day margin on data_fine keeps the "renewal proposal" occurrence
    # visible (see occorrenze_nel_periodo): it falls on data_fine + 1 day,
    # which can land in the month AFTER the stored end date (e.g. a contract
    # ending on the last day of a month proposes its renewal on the 1st of
    # the next one).
    query = (
        select(Servizio)
        .where(Servizio.data_inizio <= fine)
        .where(or_(
            Servizio.data_fine >= inizio - timedelta(days=1),
            Servizio.rinnovo_automatico.is_(True),
        ))
        # Eager-load to avoid N+1 queries when expanding occurrences/grouping.
        .options(
            joinedload(Servizio.cliente),
            selectinload(Servizio.override_importi),
        )
    )
    if escludi_disdetti:
        query = query.where(Servizio.disdetto.is_(False))
    if cliente_id is not None:
        query = query.where(Servizio.cliente_id == cliente_id)
    if referente:
        query = query.where(Servizio.referente == referente)

    righe: list[RigaOccorrenza] = []
    for servizio in db.scalars(query):
        for occ in occorrenze_nel_periodo(servizio, inizio, fine):
            righe.append(RigaOccorrenza(servizio=servizio, occorrenza=occ))

    # Chronological within the month, then by customer name for a stable order.
    righe.sort(key=lambda r: (r.occorrenza.data_occorrenza, r.servizio.cliente.nome.lower()))
    return righe


def filtra_per_stato_visivo(
    righe: list[RigaOccorrenza], stato_visivo: str | None
) -> list[RigaOccorrenza]:
    """Keep only the rows whose occurrence has the given visual state.

    The visual state ("fatturato"/"da_fatturare"/"in_scadenza"/"normale") is
    computed by the occurrence engine, so it cannot be filtered in SQL. When
    ``stato_visivo`` is falsy the rows are returned unchanged.

    "da_fatturare" is broader than the row badge of the same name (which
    only marks the OVERDUE ones): it selects every not-yet-billed occurrence
    of a non-cancelled contract — the same population the dashboard's
    "Da fatturare" summary card counts and the riepilogo page lists — so the
    filter always returns exactly the rows the card announces.
    """
    if not stato_visivo:
        return righe
    if stato_visivo == "da_fatturare":
        return [
            r for r in righe
            if not r.occorrenza.fatturato and not r.servizio.disdetto
        ]
    return [r for r in righe if r.occorrenza.stato_visivo == stato_visivo]


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
