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
from datetime import date, timedelta
from decimal import Decimal

from app.models.servizio import Servizio


@dataclass(frozen=True)
class Occorrenza:
    """A single computed billable date for a service."""

    data_occorrenza: date
    importo: Decimal       # effective unit price (override or service default)
    quantita: int          # effective quantity (override or service default)
    da_override: bool      # True only if importo/quantita are actually corrected
    fatturato: bool        # True if this occurrence has been marked as billed
    stato_visivo: str      # "fatturato" | "da_fatturare" | "in_scadenza" | "normale"

    @property
    def totale(self) -> Decimal:
        """Occurrence total: effective unit price × effective quantity."""
        return self.importo * self.quantita


def _stato_visivo(
    data_occorrenza: date, fatturato: bool, preavviso_giorni: int, oggi: date
) -> str:
    """Classify an occurrence for display, by precedence.

    1. billed                       -> "fatturato"
    2. not billed and already past  -> "da_fatturare" (the alert: don't forget it)
    3. not billed and due within the warning window -> "in_scadenza"
    4. otherwise (future)           -> "normale"
    """
    if fatturato:
        return "fatturato"
    if data_occorrenza < oggi:
        return "da_fatturare"
    if data_occorrenza <= oggi + timedelta(days=preavviso_giorni):
        return "in_scadenza"
    return "normale"


def _avanza_mesi(anno: int, mese: int, mesi: int) -> tuple[int, int]:
    """Add ``mesi`` months to (anno, mese), returning the new (anno, mese).

    Works in absolute month arithmetic so it never depends on a day-of-month.
    """
    totale = anno * 12 + (mese - 1) + mesi
    return totale // 12, totale % 12 + 1


def occorrenze_nel_periodo(
    servizio: Servizio, data_da: date, data_a: date, oggi: date | None = None
) -> list[Occorrenza]:
    """Return the service's occurrences falling within ``[data_da, data_a]``.

    Occurrences start at ``servizio.data_inizio`` and repeat every
    ``cadenza_mesi`` months while the computed date is ``<= data_fine``. Each
    date falls on the day-of-month of ``data_inizio``, clamped to the last valid
    day of the target month (see module docstring). Dates outside the requested
    interval are filtered out.

    For each date a per-occurrence STATE row (OverrideImporto) may exist:
    ``importo``/``quantita`` are used only when not NULL (otherwise the service
    defaults apply), and ``fatturato`` is read from it. ``oggi`` (defaulting to
    today) drives ``stato_visivo``.
    """
    if servizio.cadenza_mesi < 1:
        # A cadence of 0 (or negative) would never advance the date: guard
        # against it explicitly instead of looping forever.
        raise ValueError("cadenza_mesi must be at least 1")

    if oggi is None:
        oggi = date.today()

    # Map occurrence date -> state row, so we can apply per-date corrections.
    stato_per_data = {
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
            stato = stato_per_data.get(data_occ)

            # Price/quantity: use the state row only where it is actually set;
            # NULL means "fall back to the service default". So a row that
            # exists purely to carry the fatturato flag is NOT an override.
            importo = stato.importo if (stato and stato.importo is not None) else servizio.importo
            quantita = stato.quantita if (stato and stato.quantita is not None) else servizio.quantita
            da_override = stato is not None and stato.ha_override_importo
            fatturato = stato.fatturato if stato is not None else False

            occorrenze.append(Occorrenza(
                data_occorrenza=data_occ,
                importo=importo,
                quantita=quantita,
                da_override=da_override,
                fatturato=fatturato,
                stato_visivo=_stato_visivo(
                    data_occ, fatturato, servizio.preavviso_giorni, oggi
                ),
            ))

        passo += 1

    return occorrenze
