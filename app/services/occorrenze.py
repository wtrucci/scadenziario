"""
Occurrence-calculation engine — the core of the application.

A Servizio is a recurring contract valid from ``data_inizio`` to ``data_fine``
(inclusive), billed every ``cadenza_mesi`` months. The individual billable
dates ("occorrenze") are NOT stored: they are computed on the fly here.

Key rule — the "target day" must not drift. Each occurrence is computed
independently from the month/year of ``data_inizio`` (never by stepping from the
previous occurrence), then the day is clamped to the last valid day of the
target month. So a contract starting on the 31st gives Jan 31 -> Feb 28/29 ->
Mar 31 (back to 31, no drift) -> Apr 30 -> ...

All monetary values stay Decimal, never float.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.servizio import Servizio


@dataclass(frozen=True)
class Occorrenza:
    """A single computed billable date for a service."""

    data_occorrenza: date
    importo: Decimal       # effective unit price (override or service default)
    quantita: int          # effective quantity (override or service default)
    da_override: bool      # True if importo/quantita come from an OverrideImporto

    @property
    def totale(self) -> Decimal:
        """Occurrence total: effective unit price × effective quantity."""
        return self.importo * self.quantita


def _avanza_mesi(anno: int, mese: int, mesi: int) -> tuple[int, int]:
    """Add ``mesi`` months to (anno, mese), returning the new (anno, mese).

    Works in absolute month arithmetic so it never depends on a day-of-month.
    """
    totale = anno * 12 + (mese - 1) + mesi
    return totale // 12, totale % 12 + 1


def occorrenze_nel_periodo(
    servizio: Servizio, data_da: date, data_a: date
) -> list[Occorrenza]:
    """Return the service's occurrences falling within ``[data_da, data_a]``.

    Occurrences start at ``servizio.data_inizio`` and repeat every
    ``cadenza_mesi`` months while the computed date is ``<= data_fine``. Each
    date falls on the day-of-month of ``data_inizio``, clamped to the last valid
    day of the target month (see module docstring). Dates outside the requested
    interval are filtered out.
    """
    if servizio.cadenza_mesi < 1:
        # A cadence of 0 (or negative) would never advance the date: guard
        # against it explicitly instead of looping forever.
        raise ValueError("cadenza_mesi must be at least 1")

    # Map occurrence date -> override, so we can apply per-date corrections.
    override_per_data = {
        ov.data_occorrenza: ov for ov in servizio.override_importi
    }

    inizio = servizio.data_inizio
    giorno_target = inizio.day
    occorrenze: list[Occorrenza] = []

    passo = 0
    while True:
        anno, mese = _avanza_mesi(inizio.year, inizio.month, passo * servizio.cadenza_mesi)
        # Clamp the target day to this month's last valid day (handles 31 -> 30,
        # and Feb 28/29). The reference day stays giorno_target every step, so it
        # is recovered whenever the month is long enough again.
        ultimo_giorno = calendar.monthrange(anno, mese)[1]
        data_occ = date(anno, mese, min(giorno_target, ultimo_giorno))

        # Occurrences are strictly increasing, so once we pass data_fine we stop.
        if data_occ > servizio.data_fine:
            break

        if data_da <= data_occ <= data_a:
            override = override_per_data.get(data_occ)
            if override is not None:
                occorrenze.append(Occorrenza(
                    data_occorrenza=data_occ,
                    importo=override.importo,
                    quantita=override.quantita,
                    da_override=True,
                ))
            else:
                occorrenze.append(Occorrenza(
                    data_occorrenza=data_occ,
                    importo=servizio.importo,
                    quantita=servizio.quantita,
                    da_override=False,
                ))

        passo += 1

    return occorrenze
