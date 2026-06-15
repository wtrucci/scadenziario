"""
Presentation helper for an occurrence's temporal state, used by the dashboard so
the highlighting rule lives in a single place.

With the occurrence model every billable date is concrete, so relative to today
an occurrence is in one of three states:

- ``"row-passata"``      its date has already elapsed (shown but muted),
- ``"row-in-scadenza"``  its date falls within ``preavviso_giorni`` from today,
- ``""``                 further in the future (no highlight).
"""
from __future__ import annotations

from datetime import date, timedelta


def classe_occorrenza(data_occorrenza: date, preavviso_giorni: int, oggi: date) -> str:
    """Return the CSS row class for an occurrence given today's date."""
    if data_occorrenza < oggi:
        return "row-passata"
    if data_occorrenza <= oggi + timedelta(days=preavviso_giorni):
        return "row-in-scadenza"
    return ""
