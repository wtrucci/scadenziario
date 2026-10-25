"""
Supplier contracts: when they expire and what state they are in.

Much simpler than the customer side (app/services/occorrenze.py): nothing is
invoiced here, so there are no occurrences to generate and no billing state to
read. A supplier contract has one date — the next renewal — and the only
question is how close it is.

The states reuse the customer contract's names and labels (Attivo / In
scadenza / Scaduto / Disdetto), so the badges and colours mean the same thing
on both pages.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.contratto_fornitore import ContrattoFornitore
from app.services.occorrenze import aggiungi_mesi


def prossima_scadenza(contratto: ContrattoFornitore, oggi: date) -> date:
    """The next date the contract renews (or must be renewed) on.

    Auto-renewing: the supplier renews it by itself, so the stored date only
    anchors the cycle and the expiry is the first renewal from today on. Each
    step is computed from the anchor, never from the previous step, so a
    contract on the 31st does not drift to the 28th after February.

    Otherwise it is the stored date as is, even when past: an unrenewed
    contract that went by is exactly what should be on screen.
    """
    if not contratto.rinnovo_automatico or contratto.data_scadenza >= oggi:
        return contratto.data_scadenza
    passo = 1
    while (scadenza := aggiungi_mesi(contratto.data_scadenza, passo * contratto.cadenza_mesi)) < oggi:
        passo += 1
    return scadenza


def stato_contratto_fornitore(contratto: ContrattoFornitore, oggi: date) -> str:
    """Display state, by precedence: disdetto, scaduto, in_scadenza, attivo.

    An auto-renewing contract is never "scaduto": its expiry always rolls
    forward to today or later (see prossima_scadenza).
    """
    if contratto.disdetto:
        return "disdetto"
    scadenza = prossima_scadenza(contratto, oggi)
    if scadenza < oggi:
        return "scaduto"
    if scadenza <= oggi + timedelta(days=contratto.preavviso_giorni):
        return "in_scadenza"
    return "attivo"


def scadenza_dopo_rinnovo(contratto: ContrattoFornitore) -> date:
    """Where the "Rinnovato" button moves a manually-renewed contract to.

    One cadence after the current date. Unlike the auto-renewing case this
    steps from the stored date itself — there is no other anchor to keep — so
    on a monthly contract the 31st becomes the 28th after February and stays
    there. Rare enough (manual renewals are almost always yearly), and the
    date can always be corrected in the form.
    """
    return aggiungi_mesi(contratto.data_scadenza, contratto.cadenza_mesi)


def fornitori_esistenti(db: Session) -> list[str]:
    """Supplier names already used, for the form's suggestions."""
    return list(db.scalars(
        select(ContrattoFornitore.fornitore).distinct().order_by(ContrattoFornitore.fornitore)
    ))
