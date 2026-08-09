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

    1. ``disdetto`` (manual flag)                    -> "disdetto" (always wins)
    2. effective end date already passed             -> "scaduto"
       (an auto-renewing contract's effective end date never falls behind
       ``oggi`` — see data_fine_effettiva — so it can never be "scaduto")
    3. effective end date within preavviso_giorni     -> "in_scadenza"
    4. otherwise                                      -> "attivo"

    What matters is ALWAYS the contract's end date (per explicit product
    decision): "scaduto" simply means data_fine passed without the renewal
    being confirmed. The pending renewal itself is surfaced as an occurrence
    (the "renewal proposal", see occorrenze_nel_periodo), not through this
    state. Billing that proposal extends the contract (see routes/servizi.py),
    which brings the state back to attivo — so an expired-and-then-confirmed
    contract heals on its own.
    """
    if servizio.disdetto:
        return "disdetto"
    if oggi is None:
        oggi = date.today()
    fine = data_fine_effettiva(servizio, riferimento=oggi)
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


def _passo_rinnovo(servizio: Servizio) -> int:
    """Length in months of each renewal block: ``durata_rinnovo_mesi`` if set
    (an initial term that differs from its renewals, e.g. 36 months up front
    then 12-month yearly renewals), otherwise the same as the initial
    ``durata_mesi``."""
    return servizio.durata_rinnovo_mesi or servizio.durata_mesi


def data_fine_effettiva(servizio: Servizio, riferimento: date) -> date:
    """The contract's effective end date, accounting for automatic renewal.

    Without auto-renewal (``rinnovo_automatico=False``, the default), this is
    simply ``servizio.data_fine``. With auto-renewal, the contract rolls
    forward one renewal block at a time (see ``_passo_rinnovo``) whenever it
    would otherwise already have expired, so it always covers at least up to
    ``riferimento``. Nothing is persisted: like occurrences themselves, the
    renewed end date is computed on the fly from data_fine + the renewal
    step, so no background job is needed to "advance" it.
    """
    fine = servizio.data_fine
    if not servizio.rinnovo_automatico or not servizio.durata_mesi:
        return fine
    passo = _passo_rinnovo(servizio)
    while fine < riferimento:
        fine = calcola_data_fine(fine + timedelta(days=1), passo)
    return fine


def durata_mesi_congelata(servizio: Servizio, riferimento: date) -> int:
    """The single ``durata_mesi`` value that reproduces the contract's
    CURRENT effective end date (as of ``riferimento``) with auto-renewal
    turned off.

    Used when the user disables ``rinnovo_automatico`` on an auto-renewing
    contract: without this, saving the form would recompute data_fine from
    the ORIGINAL durata_mesi alone, snapping the contract back to its first
    term and discarding every renewal already elapsed. Month-arithmetic
    composes additively (aggiungi_mesi never drifts — see the module
    docstring), so accumulating the same steps data_fine_effettiva would
    take, instead of it, gives back an equivalent single duration: replaying
    calcola_data_fine(data_inizio, durata_mesi_congelata(...)) reproduces the
    exact effective end date, whether or not renewal blocks were a different
    length than the initial term (see _passo_rinnovo).
    """
    durata_totale = servizio.durata_mesi
    fine = servizio.data_fine
    if not servizio.rinnovo_automatico or not fine:
        return durata_totale
    passo = _passo_rinnovo(servizio)
    while fine < riferimento:
        fine = calcola_data_fine(fine + timedelta(days=1), passo)
        durata_totale += passo
    return durata_totale


def occorrenze_nel_periodo(
    servizio: Servizio, data_da: date, data_a: date, oggi: date | None = None
) -> list[Occorrenza]:
    """Return the service's occurrences falling within ``[data_da, data_a]``.

    Occurrences start at ``servizio.data_inizio`` and repeat every
    ``cadenza_mesi`` months while the computed date is ``<= data_fine``. Each
    date falls on the day-of-month of ``data_inizio``, clamped to the last valid
    day of the target month (see module docstring). Dates outside the requested
    interval are filtered out.

    ``data_fine`` here means the EFFECTIVE end date (see ``data_fine_effettiva``):
    for an auto-renewing contract this rolls forward as needed to cover
    ``data_a``, so occurrences keep being generated past the originally stored
    end date without any stored value ever changing.

    A contract WITHOUT auto-renewal additionally yields ONE occurrence past
    its end date — the "renewal proposal" at the next anniversary (which is
    always data_fine + 1 day, since data_fine is the day before an
    anniversary): the renewal to be confirmed by the client. See the inline
    comment in the loop below. Cancelled contracts (disdetto) don't propose.

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
    fine_effettiva = data_fine_effettiva(servizio, riferimento=data_a)

    # Degenerate contract (end before start): no occurrences at all — without
    # this guard the renewal-proposal rule below would still emit one.
    if fine_effettiva < inizio:
        return occorrenze

    passo = 0
    while True:
        anno, mese = _avanza_mesi(inizio.year, inizio.month, passo * servizio.cadenza_mesi)
        # Clamp the target day to this month's last valid day (handles 31 -> 30,
        # and Feb 28/29). The reference day stays giorno_target every step, so it
        # is recovered whenever the month is long enough again.
        ultimo_giorno = calendar.monthrange(anno, mese)[1]
        data_occ = date(anno, mese, min(giorno_target, ultimo_giorno))

        # Occurrences are strictly increasing, so once we pass the effective
        # end date we stop — EXCEPT that a contract WITHOUT auto-renewal also
        # generates the first anniversary PAST its end date, as a "renewal
        # proposal": renewing there needs the client's go-ahead (that is what
        # not ticking rinnovo_automatico means), so the system must surface
        # that date in the dashboard/riepilogo and notify as it approaches,
        # instead of silently ending the contract. Billing it = the client
        # confirmed, and extends the contract by one renewal block (see
        # routes/servizi.py). Cancelled contracts (disdetto) propose nothing.
        # For auto-renewing contracts fine_effettiva already covers data_a,
        # so anything past it is out of the requested window anyway.
        oltre_fine = data_occ > fine_effettiva
        if oltre_fine and (servizio.rinnovo_automatico or servizio.disdetto):
            break

        if data_da <= data_occ <= data_a:
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

        if oltre_fine:
            # The renewal proposal is a single extra occurrence, never a
            # projection further into the future: past it, nothing is known
            # until the client confirms.
            break
        passo += 1

    return occorrenze
