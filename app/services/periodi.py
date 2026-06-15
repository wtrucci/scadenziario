"""
Month-based navigation helpers for the dashboard and the billing summary.

A "period" here is a single calendar month, identified externally by the query
string ``?mese=YYYY-MM`` (sortable and unambiguous) and internally by a ``date``
set to the first day of that month.
"""
from __future__ import annotations

from datetime import date, timedelta

# Italian month names, indexed 1..12 (index 0 is unused).
MESI_IT = [
    "",
    "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
    "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre",
]


def mese_corrente() -> date:
    """First day of the current month."""
    oggi = date.today()
    return date(oggi.year, oggi.month, 1)


def parse_mese(mese_raw: str | None) -> date:
    """Parse a ``YYYY-MM`` string into the first day of that month.

    Falls back to the current month when the value is missing or malformed, so a
    bad query string can never break the page.
    """
    if not mese_raw:
        return mese_corrente()
    try:
        anno, mese = mese_raw.split("-")
        return date(int(anno), int(mese), 1)
    except (ValueError, TypeError):
        return mese_corrente()


def mese_successivo(primo: date) -> date:
    """First day of the month after ``primo``."""
    if primo.month == 12:
        return date(primo.year + 1, 1, 1)
    return date(primo.year, primo.month + 1, 1)


def mese_precedente(primo: date) -> date:
    """First day of the month before ``primo``."""
    if primo.month == 1:
        return date(primo.year - 1, 12, 1)
    return date(primo.year, primo.month - 1, 1)


def ultimo_giorno_mese(primo: date) -> date:
    """Last day of the month starting at ``primo`` (inclusive).

    Used to build the closed interval ``[primo, ultimo_giorno_mese(primo)]`` that
    the occurrence engine expects for a single month.
    """
    return mese_successivo(primo) - timedelta(days=1)


def etichetta_cadenza(mesi: int) -> str:
    """Human-readable billing cadence, e.g. 1 -> 'mensile', 3 -> 'trimestrale'.

    Falls back to 'ogni N mesi' for uncommon values.
    """
    comuni = {1: "mensile", 3: "trimestrale", 6: "semestrale", 12: "annuale"}
    return comuni.get(mesi, f"ogni {mesi} mesi")


def chiave_mese(primo: date) -> str:
    """The ``YYYY-MM`` query-string key for a month."""
    return f"{primo.year:04d}-{primo.month:02d}"


def etichetta_mese(primo: date) -> str:
    """Human-readable label, e.g. ``'Dicembre 2026'``."""
    return f"{MESI_IT[primo.month]} {primo.year}"
