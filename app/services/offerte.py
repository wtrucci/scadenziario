"""
Renewal offers: which contracts need an offer sent to the customer, and when.

A contract WITHOUT automatic renewal stops at a renewal proposal: the next
cycle only happens if the customer says yes (see app/services/occorrenze.py).
Getting that yes starts with an offer, which has to go out well before the
date. This module lists those proposals and tells, for each, whether the offer
has been sent (OffertaRinnovo) and how urgent it is.

Auto-renewing contracts are left out on purpose: they renew without asking,
so there is nothing to offer. Cancelled ones (disdetto) propose nothing.

The thresholds are fixed rather than per-service: "offers go out at least a
month before" is how the office works, not a property of a contract, unlike
the notification warning (preavviso_giorni).
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.models.servizio import Servizio
from app.services.occorrenze import Occorrenza, occorrenze_nel_periodo

# How far ahead the page looks: renewals within the next two months.
GIORNI_ORIZZONTE = 60

# The offer should be out at least this many days before the renewal: closer
# than this, an unsent offer is flagged (yellow) and counted in the sidebar.
GIORNI_URGENZA = 30

# Display state of a row, in the order the page sorts urgency by.
STATI_OFFERTA = ("scaduta", "urgente", "da_inviare", "inviata")


@dataclass(frozen=True)
class RigaOfferta:
    """One renewal on the offers page."""

    servizio: Servizio
    rinnovo: Occorrenza          # the proposal: date, price, quantity
    inviata_il: datetime | None  # when the offer was marked as sent, if it was
    giorni: int                  # days from today to the renewal (negative = past)
    stato: str                   # one of STATI_OFFERTA

    @property
    def inviata(self) -> bool:
        return self.inviata_il is not None

    @property
    def da_sollecitare(self) -> bool:
        """Not sent and already late or close to it: what the sidebar counts."""
        return self.stato in ("scaduta", "urgente")


def rinnovo_da_proporre(servizio: Servizio, fino_a: date, oggi: date) -> Occorrenza | None:
    """The renewal still waiting for the customer's answer, if it falls by ``fino_a``.

    For a contract without automatic renewal the occurrence engine stops at
    the first cycle opening that is not billed: that is the proposal, and the
    only unbilled renewal it can ever generate. Billing it is the customer's
    yes, after which the proposal moves on to the next cycle by itself.

    A proposal can lie in the PAST (the date went by and nobody confirmed it):
    it is returned anyway, because an offer that was never sent is exactly
    what must not slip through.
    """
    if servizio.rinnovo_automatico or servizio.disdetto:
        return None
    for occ in occorrenze_nel_periodo(servizio, servizio.data_scadenza, fino_a, oggi=oggi):
        if occ.apre_ciclo and not occ.fatturato:
            return occ
    return None


def _stato(inviata: bool, giorni: int) -> str:
    if inviata:
        return "inviata"
    if giorni < 0:
        return "scaduta"
    if giorni <= GIORNI_URGENZA:
        return "urgente"
    return "da_inviare"


def righe_offerte(servizi: Iterable[Servizio], oggi: date | None = None) -> list[RigaOfferta]:
    """Every renewal that needs (or needed) an offer, most urgent first.

    Shown: every pending proposal up to GIORNI_ORIZZONTE days ahead, past ones
    included. A row leaves the page only when the renewal stops pending —
    billed (the customer said yes) or the contract cancelled (they said no) —
    never because an offer was marked: a past renewal whose offer went out is
    still waiting for an answer, and making it vanish on a click would also
    make that click impossible to undo.

    ``servizi`` should come with ``offerte_rinnovo`` and ``override_importi``
    loaded, or each service costs two extra queries.
    """
    if oggi is None:
        oggi = date.today()
    fino_a = oggi + timedelta(days=GIORNI_ORIZZONTE)

    righe = []
    for servizio in servizi:
        rinnovo = rinnovo_da_proporre(servizio, fino_a, oggi)
        if rinnovo is None:
            continue
        inviata_il = next(
            (o.inviata_il for o in servizio.offerte_rinnovo
             if o.data_rinnovo == rinnovo.data_occorrenza),
            None,
        )
        giorni = (rinnovo.data_occorrenza - oggi).days
        righe.append(RigaOfferta(
            servizio=servizio,
            rinnovo=rinnovo,
            inviata_il=inviata_il,
            giorni=giorni,
            stato=_stato(inviata_il is not None, giorni),
        ))

    righe.sort(key=lambda r: (r.rinnovo.data_occorrenza, r.servizio.cliente.nome.lower()))
    return righe
