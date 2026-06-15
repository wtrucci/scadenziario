"""
Expiry-status presentation helper, shared by the services list, the dashboard
and the billing summary so the highlighting rule lives in a single place.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.models.servizio import Servizio


def classe_scadenza(servizio: Servizio, oggi: date) -> str:
    """Return the CSS row class for a service based on its expiry:

    - ``"row-scaduta"``     if it has already expired,
    - ``"row-in-scadenza"`` if it expires within its ``preavviso_giorni`` window,
    - ``""``                otherwise.
    """
    if servizio.data_scadenza < oggi:
        return "row-scaduta"
    if servizio.data_scadenza <= oggi + timedelta(days=servizio.preavviso_giorni):
        return "row-in-scadenza"
    return ""
