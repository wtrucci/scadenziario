"""
Occurrence-calculation engine — the core of the application.

A Servizio hangs off ONE date, ``data_scadenza``: the day its current billing
cycle starts. Occurrences are NOT stored — they are computed here by stepping
``cadenza_mesi`` months from that date.

``durata_impegno_mesi`` says how long the customer is committed for. Left
empty, the commitment is a single billing, so every occurrence is itself a
renewal. Set, the commitment is billed in instalments and only what follows it
needs confirming.

With ``rinnovo_automatico`` the cycle repeats on its own and occurrences run
indefinitely. Without it, generation stops one occurrence past the commitment:
that occurrence is the renewal proposal, and past it nothing is known until the
customer confirms.

Key rule — the "target day" must not drift. Each occurrence is computed
independently from the month/year of ``data_scadenza`` (never by stepping from
the previous occurrence), then the day is clamped to the last valid day of the
target month. So a cycle starting on the 31st gives Jan 31 -> Feb 28/29 ->
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
    importo: Decimal       # effective unit price (override/snapshot or service default)
    quantita: int          # effective quantity (override/snapshot or service default)
    da_override: bool      # True only for a deliberate manual override (not a billing snapshot)
    fatturato: bool        # True if this occurrence has been marked as billed
    stato_visivo: str      # "fatturato" | "da_fatturare" | "in_scadenza" | "normale"

    @property
    def totale(self) -> Decimal:
        """Occurrence total: effective unit price × effective quantity."""
        return self.importo * self.quantita


def _stato_visivo(
    data_occorrenza: date, fatturato: bool, preavviso_giorni: int, oggi: date, *,
    disdetto: bool = False,
) -> str:
    """Classify an occurrence for display, by precedence.

    1. billed                          -> "fatturato"
    2. contract cancelled (disdetto), not billed -> "normale" (never nag to
       invoice something the customer has cancelled — see stato_contratto)
    3. not billed and already past     -> "da_fatturare" (the alert: don't forget it)
    4. not billed and due within the warning window -> "in_scadenza"
    5. otherwise (future)              -> "normale"
    """
    if fatturato:
        return "fatturato"
    if disdetto:
        return "normale"
    if data_occorrenza < oggi:
        return "da_fatturare"
    if data_occorrenza <= oggi + timedelta(days=preavviso_giorni):
        return "in_scadenza"
    return "normale"


# The four contract-level states shown as the "Stato" badge, in a stable order
# for filter dropdowns. Unlike the old stato column these are NOT stored:
# attivo/in_scadenza/scaduto are computed from dates (see stato_contratto);
# only "disdetto" is a manual decision (Servizio.disdetto), which always wins.
STATI_CONTRATTO = ("attivo", "in_scadenza", "scaduto", "disdetto")

ETICHETTE_STATO_CONTRATTO = {
    "attivo": "Attivo",
    "in_scadenza": "In scadenza",
    "scaduto": "Scaduto",
    "disdetto": "Disdetto",
}


def stato_contratto(servizio: Servizio, oggi: date | None = None) -> str:
    """The contract's own display state — distinct from a single occurrence's
    stato_visivo. By precedence:

    1. ``disdetto`` (manual flag)      -> "disdetto" (always wins)
    2. cover already ended             -> "scaduto"
    3. cover ends within preavviso_giorni -> "in_scadenza"
    4. otherwise                       -> "attivo"

    See ``fine_copertura`` for what "cover" means. An auto-renewing contract
    can never read "scaduto": it renews whether or not you got round to
    invoicing, so an unbilled occurrence of one shows up as "da fatturare",
    not as an expired contract.
    """
    if servizio.disdetto:
        return "disdetto"
    if oggi is None:
        oggi = date.today()
    fine = fine_copertura(servizio, oggi)
    if fine < oggi:
        return "scaduto"
    if fine <= oggi + timedelta(days=servizio.preavviso_giorni):
        return "in_scadenza"
    return "attivo"


def _avanza_mesi(anno: int, mese: int, mesi: int) -> tuple[int, int]:
    """Add ``mesi`` months to (anno, mese), returning the new (anno, mese).

    Works in absolute month arithmetic so it never depends on a day-of-month.
    """
    totale = anno * 12 + (mese - 1) + mesi
    return totale // 12, totale % 12 + 1


def aggiungi_mesi(d: date, mesi: int) -> date:
    """Add ``mesi`` months to ``d``, clamping the day to the target month's
    last valid day (e.g. Jan 31 + 1 month -> Feb 28/29, not Mar 3).

    Shared by occurrence-date generation and contract-duration math
    (``calcola_data_fine``), so both use the same day-clamping rule.
    """
    anno, mese = _avanza_mesi(d.year, d.month, mesi)
    ultimo_giorno = calendar.monthrange(anno, mese)[1]
    return date(anno, mese, min(d.day, ultimo_giorno))


def calcola_data_fine(data_inizio: date, durata_mesi: int) -> date:
    """The inclusive end date of a ``durata_mesi``-long contract starting at
    ``data_inizio`` — the day before the same date ``durata_mesi`` months later,
    so a 12-month contract starting Jan 1 covers Jan 1 through Dec 31.
    """
    return aggiungi_mesi(data_inizio, durata_mesi) - timedelta(days=1)


def mesi_impegno(servizio: Servizio) -> int:
    """How many months the customer is committed to per cycle.

    Empty ``durata_impegno_mesi`` means the commitment is a single billing, so
    it equals the cadence: every occurrence is itself a renewal. That is the
    common case — set the field only when a commitment is billed in
    instalments (a yearly subscription invoiced monthly: cadenza 1, impegno 12).
    """
    return servizio.durata_impegno_mesi or servizio.cadenza_mesi


def fine_impegno(servizio: Servizio) -> date:
    """Last day of the commitment that starts at ``data_scadenza``."""
    return calcola_data_fine(servizio.data_scadenza, mesi_impegno(servizio))


def scadenza_congelata(servizio: Servizio, oggi: date) -> date:
    """Where an auto-renewing contract's cycle has actually reached today.

    Auto-renewal rolls the cycle forward without writing anything down, so
    turning it off has to pin the cycle where it has got to; otherwise the
    contract would snap back to a cycle years in the past and immediately
    propose a renewal that is already history.
    """
    impegno = mesi_impegno(servizio)
    scadenza = servizio.data_scadenza
    while calcola_data_fine(scadenza, impegno) < oggi:
        scadenza = calcola_data_fine(scadenza, impegno) + timedelta(days=1)
    return scadenza


def fine_copertura(servizio: Servizio, oggi: date) -> date:
    """Last day the customer is actually covered — what the contract badge reads.

    An auto-renewing contract rolls forward on its own, so its cover always
    reaches at least ``oggi``: it can never show as expired just because an
    invoice is late.

    Otherwise the cycle starting at ``data_scadenza`` only covers the customer
    once it has been paid for. Until that first occurrence is billed the
    contract is still living on the previous cycle, which ended the day before
    — which is exactly what makes a pending renewal read "in scadenza" and
    then "scaduto" if it is never confirmed.
    """
    impegno = mesi_impegno(servizio)
    if servizio.rinnovo_automatico:
        fine = calcola_data_fine(servizio.data_scadenza, impegno)
        while fine < oggi:
            fine = calcola_data_fine(fine + timedelta(days=1), impegno)
        return fine
    ciclo_pagato = any(
        ov.data_occorrenza == servizio.data_scadenza and ov.fatturato
        for ov in servizio.override_importi
    )
    if not ciclo_pagato:
        return servizio.data_scadenza - timedelta(days=1)
    return calcola_data_fine(servizio.data_scadenza, impegno)


def occorrenze_nel_periodo(
    servizio: Servizio, data_da: date, data_a: date, oggi: date | None = None
) -> list[Occorrenza]:
    """Return the service's occurrences falling within ``[data_da, data_a]``.

    Occurrences step ``cadenza_mesi`` months from ``data_scadenza``, each date
    falling on that date's day-of-month clamped to the target month's last
    valid day (see module docstring). Dates outside the requested interval are
    filtered out.

    How far they run depends on the commitment. With ``rinnovo_automatico``
    the cycle repeats on its own, so they run to ``data_a``. Without it, they
    stop one occurrence past ``fine_impegno``: that last one is the renewal
    PROPOSAL — the renewal the customer still has to confirm. Billing it is
    that confirmation (see routes/servizi.py), which moves data_scadenza
    forward. Nothing beyond it is generated, because nothing beyond it is
    known. A cancelled contract (disdetto) proposes nothing.

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
    stato_per_data = {ov.data_occorrenza: ov for ov in servizio.override_importi}
    occorrenze: list[Occorrenza] = []

    def aggiungi(data_occ: date) -> None:
        """Materialise one occurrence, applying its state row if any."""
        if not (data_da <= data_occ <= data_a):
            return
        stato = stato_per_data.get(data_occ)

        # Price/quantity: use the state row only where it is actually set;
        # NULL means "fall back to the service default". An importo may be a
        # manual override OR an automatic billing snapshot, so it alone is
        # not an override: the badge follows override_manuale only.
        importo = stato.importo if (stato and stato.importo is not None) else servizio.importo
        quantita = stato.quantita if (stato and stato.quantita is not None) else servizio.quantita
        da_override = bool(stato and stato.override_manuale)
        fatturato = stato.fatturato if stato is not None else False

        occorrenze.append(Occorrenza(
            data_occorrenza=data_occ,
            importo=importo,
            quantita=quantita,
            da_override=da_override,
            fatturato=fatturato,
            stato_visivo=_stato_visivo(
                data_occ, fatturato, servizio.preavviso_giorni, oggi,
                disdetto=servizio.disdetto,
            ),
        ))

    # Walk forward from the cycle start. Each occurrence that lands on a cycle
    # boundary opens a new commitment; the ones in between are its instalments.
    impegno = mesi_impegno(servizio)
    passo = 0
    while True:
        scarto = passo * servizio.cadenza_mesi
        data_occ = aggiungi_mesi(servizio.data_scadenza, scarto)
        if data_occ > data_a:
            break
        apre_un_ciclo = scarto % impegno == 0
        if not servizio.rinnovo_automatico and apre_un_ciclo:
            stato = stato_per_data.get(data_occ)
            if stato is None or not stato.fatturato:
                # An unbilled cycle opening is the renewal still waiting on the
                # customer, and the last thing we know: what comes after depends
                # on an answer we do not have. Billing it IS the answer, and
                # generation resumes from the next cycle on its own. A cancelled
                # contract is not even asked.
                if not servizio.disdetto:
                    aggiungi(data_occ)
                break
        aggiungi(data_occ)
        passo += 1

    return occorrenze
