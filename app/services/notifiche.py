"""
Expiration notifications.

Once a day (see app/scheduler.py) every non-cancelled service is checked for
three independent triggers, each with its own dedup (see NotificaLog.tipo —
a failed attempt is not "already notified" and is retried on the next run):

1. ``preavviso``            — an occurrence enters its warning window, sized
   per service via ``preavviso_giorni`` (mirrors the dashboard's
   "in_scadenza" state).
2. ``promemoria_7_giorni``  — a fixed extra reminder 7 days before an
   occurrence, regardless of the service's own ``preavviso_giorni``.

Both occurrence triggers only ever fire on a RENEWAL (``Occorrenza.apre_ciclo``),
never on an instalment inside a commitment: a yearly subscription invoiced
monthly is one deadline to act on, not twelve, and notifying every instalment
would train the operator to ignore the channel. The instalments still show up
on the dashboard and in the monthly summary — they are invoices to issue, not
deadlines to chase. For the usual contract (empty ``durata_impegno_mesi``)
every occurrence is a renewal, so nothing changes.
3. ``contratto_scaduto``    — the CONTRACT itself (not a single occurrence)
   has reached ``stato_contratto == "scaduto"``. This never fires for a
   ``rinnovo_automatico`` contract: its effective end date is never in the
   past (see ``fine_copertura``), so it can never be "scaduto" — that
   invariant is enough to skip the notification, no extra check needed here.

Supplier contracts (ContrattoFornitore) get the same three triggers on their
single date — the next renewal, see app/services/fornitori.py — with messages
that say plainly it is something WE buy. They have no occurrences and no
instalments, so the ``apre_ciclo`` rule does not apply to them.

Adding a new channel later means: write a ``invia_<canale>`` function here,
add it to the dispatch in ``invia_notifiche_scadenza``, and add the enum
value in app/models/enums.py.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models.contratto_fornitore import ContrattoFornitore
from app.models.enums import CanalNotifica, TipoNotifica
from app.models.notifica_log import NotificaLog
from app.models.servizio import Servizio
from app.services import fornitori
from app.services.occorrenze import Occorrenza, fine_copertura, occorrenze_nel_periodo, stato_contratto

logger = logging.getLogger("scadenziario.notifiche")

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

# Fixed reminder window for TipoNotifica.promemoria_7_giorni, independent of
# each service's own preavviso_giorni.
GIORNI_PROMEMORIA_FISSO = 7


@dataclass
class CandidatoNotifica:
    """One notification due to be sent."""

    servizio: Servizio
    tipo: TipoNotifica
    # The date logged in NotificaLog.data_occorrenza for dedup purposes: an
    # occurrence date for preavviso/promemoria_7_giorni, the contract's
    # effective end date for contratto_scaduto.
    data_riferimento: date
    # None only for contratto_scaduto, which is not about one occurrence.
    occorrenza: Occorrenza | None = None


def occorrenze_da_notificare(db: Session, oggi: date | None = None) -> list[CandidatoNotifica]:
    """All (service, trigger) pairs due for a notification right now.

    Cancelled contracts (disdetto) are skipped entirely for all three
    triggers: a cancelled contract's occurrences never reach "in_scadenza"
    (see _stato_visivo), and there is no point warning about a cancelled
    contract "expiring".
    """
    if oggi is None:
        oggi = date.today()

    servizi = db.scalars(
        select(Servizio)
        .where(Servizio.disdetto.is_(False))
        .options(selectinload(Servizio.override_importi), selectinload(Servizio.notifiche))
    ).all()

    candidati: list[CandidatoNotifica] = []
    for servizio in servizi:
        gia_notificate = {
            (log.data_occorrenza, log.tipo)
            for log in servizio.notifiche
            if log.esito and log.canale == CanalNotifica.telegram
        }

        # 1) Personalised warning window.
        for occ in occorrenze_nel_periodo(
            servizio, oggi, oggi + timedelta(days=servizio.preavviso_giorni), oggi=oggi
        ):
            if not occ.apre_ciclo or occ.stato_visivo != "in_scadenza":
                continue
            chiave = (occ.data_occorrenza, TipoNotifica.preavviso)
            if chiave not in gia_notificate:
                candidati.append(CandidatoNotifica(
                    servizio, TipoNotifica.preavviso, occ.data_occorrenza, occ
                ))

        # 2) Fixed 7-day reminder — independent of preavviso_giorni, so it
        # also fires for services with a shorter or longer configured window.
        for occ in occorrenze_nel_periodo(
            servizio, oggi, oggi + timedelta(days=GIORNI_PROMEMORIA_FISSO), oggi=oggi
        ):
            if not occ.apre_ciclo or occ.fatturato:
                continue
            chiave = (occ.data_occorrenza, TipoNotifica.promemoria_7_giorni)
            if chiave not in gia_notificate:
                candidati.append(CandidatoNotifica(
                    servizio, TipoNotifica.promemoria_7_giorni, occ.data_occorrenza, occ
                ))

        # 3) Contract expired. Auto-renewing contracts are excluded for free:
        # stato_contratto never returns "scaduto" for them.
        if stato_contratto(servizio, oggi=oggi) == "scaduto":
            scadenza = fine_copertura(servizio, oggi)
            chiave = (scadenza, TipoNotifica.contratto_scaduto)
            if chiave not in gia_notificate:
                candidati.append(CandidatoNotifica(
                    servizio, TipoNotifica.contratto_scaduto, scadenza
                ))

    return candidati


@dataclass
class CandidatoFornitore:
    """One notification due about a supplier contract."""

    contratto: ContrattoFornitore
    tipo: TipoNotifica
    # The renewal date the warning is about (dedup key, with tipo).
    data_riferimento: date


def contratti_fornitore_da_notificare(
    db: Session, oggi: date | None = None
) -> list[CandidatoFornitore]:
    """Supplier contracts due for a notification right now.

    The same three triggers as the customer side, on the contract's next
    renewal date. Cancelled contracts are skipped; auto-renewing ones never
    reach "scaduto" (see fornitori.stato_contratto_fornitore), so they only
    ever get the two advance warnings — which is the point: an automatic
    renewal is the one you must catch BEFORE it charges you.

    Monthly auto-renewing contracts (S3 storage and the like) are skipped
    entirely: they renew every month, so with any usual warning window they
    would be "about to renew" forever and send two messages a month each —
    and a monthly subscription can be cancelled whenever you like, so there
    is no deadline to catch.
    """
    if oggi is None:
        oggi = date.today()

    contratti = db.scalars(
        select(ContrattoFornitore)
        .where(ContrattoFornitore.disdetto.is_(False))
        .options(selectinload(ContrattoFornitore.notifiche))
    ).all()

    candidati: list[CandidatoFornitore] = []
    for contratto in contratti:
        if contratto.rinnovo_automatico and contratto.cadenza_mesi == 1:
            continue
        gia_notificate = {
            (log.data_occorrenza, log.tipo)
            for log in contratto.notifiche
            if log.esito and log.canale == CanalNotifica.telegram
        }
        scadenza = fornitori.prossima_scadenza(contratto, oggi)
        giorni = (scadenza - oggi).days

        dovute = []
        if 0 <= giorni <= contratto.preavviso_giorni:
            dovute.append(TipoNotifica.preavviso)
        if 0 <= giorni <= GIORNI_PROMEMORIA_FISSO:
            dovute.append(TipoNotifica.promemoria_7_giorni)
        if fornitori.stato_contratto_fornitore(contratto, oggi) == "scaduto":
            dovute.append(TipoNotifica.contratto_scaduto)

        for tipo in dovute:
            if (scadenza, tipo) not in gia_notificate:
                candidati.append(CandidatoFornitore(contratto, tipo, scadenza))

    return candidati


_INTESTAZIONI = {
    TipoNotifica.preavviso: "⏰ Scadenza in avvicinamento",
    TipoNotifica.promemoria_7_giorni: "🔔 Promemoria: scadenza tra 7 giorni",
}


def messaggio_notifica(candidato: CandidatoNotifica) -> str:
    """Build the Telegram message text for one candidate.

    The amount is intentionally left out (these are reminders to act, not a
    billing figure); the referente is included only when the service has one.
    """
    s = candidato.servizio
    referente = f"Referente: {s.referente}\n" if s.referente else ""

    if candidato.tipo is TipoNotifica.contratto_scaduto:
        return (
            f"⚠️ Contratto scaduto\n"
            f"Cliente: {s.cliente.nome}\n"
            f"Servizio: {s.descrizione}\n"
            f"{referente}"
            f"Scaduto il: {candidato.data_riferimento.strftime('%d/%m/%Y')}\n"
            f"Non si rinnova automaticamente: valutare se contattare il cliente."
        )

    occ = candidato.occorrenza
    return (
        f"{_INTESTAZIONI[candidato.tipo]}\n"
        f"Cliente: {s.cliente.nome}\n"
        f"Servizio: {s.descrizione}\n"
        f"{referente}"
        f"Data: {occ.data_occorrenza.strftime('%d/%m/%Y')}"
    )


_INTESTAZIONI_FORNITORE = {
    TipoNotifica.preavviso: "🛒 Rinnovo fornitore in avvicinamento",
    TipoNotifica.promemoria_7_giorni: "🛒 Promemoria: rinnovo fornitore tra 7 giorni",
    TipoNotifica.contratto_scaduto: "🛒⚠️ Contratto fornitore scaduto",
}


def messaggio_fornitore(candidato: CandidatoFornitore) -> str:
    """Telegram text for a supplier contract — distinct from the customer
    ones at a glance, since the action is the opposite (renew or cancel what
    we buy, not invoice someone)."""
    c = candidato.contratto
    riferimento = f"Riferimento: {c.riferimento}\n" if c.riferimento else ""
    data = candidato.data_riferimento.strftime("%d/%m/%Y")
    if candidato.tipo is TipoNotifica.contratto_scaduto:
        coda = f"Scaduto il: {data}\nNon si rinnova da solo: rinnovarlo o segnarlo come disdetto."
    elif c.rinnovo_automatico:
        coda = f"Si rinnova automaticamente il: {data}\nSe non serve più, va disdetto prima."
    else:
        coda = f"Da rinnovare entro il: {data}"
    return (
        f"{_INTESTAZIONI_FORNITORE[candidato.tipo]}\n"
        f"Fornitore: {c.fornitore}\n"
        f"Contratto: {c.descrizione}\n"
        f"{riferimento}"
        f"{coda}"
    )


def _parse_chat_id(grezzo: str) -> tuple[str, int | None]:
    """Split TELEGRAM_CHAT_ID into (chat_id, message_thread_id).

    A supergroup topic is addressed as "CHAT_ID:TOPIC_ID" (e.g.
    "-1001234567890:42"); a plain chat/group/channel is just "CHAT_ID" and
    has no thread id. Only the last ':' is treated as the separator, since a
    chat id itself never contains one (numeric or "@username").
    """
    if ":" in grezzo:
        chat_id, topic_id = grezzo.rsplit(":", 1)
        if topic_id.strip().lstrip("-").isdigit():
            return chat_id.strip(), int(topic_id)
    return grezzo, None


def invia_telegram(testo: str) -> tuple[bool, str | None]:
    """Send ``testo`` via the Telegram Bot API. Returns (esito, dettaglio):
    esito is True on success, dettaglio carries the error on failure (or the
    reason it was skipped, e.g. missing configuration).

    TELEGRAM_CHAT_ID may be "CHAT_ID:TOPIC_ID" to target a specific topic in a
    supergroup with topics enabled; otherwise the message goes to the chat as
    a normal message (works for a private chat, a group, or a channel).
    """
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return False, "TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID non configurati"

    chat_id, message_thread_id = _parse_chat_id(settings.TELEGRAM_CHAT_ID)
    payload = {"chat_id": chat_id, "text": testo}
    if message_thread_id is not None:
        payload["message_thread_id"] = message_thread_id

    url = TELEGRAM_API_URL.format(token=settings.TELEGRAM_BOT_TOKEN)
    try:
        risposta = httpx.post(url, json=payload, timeout=10.0)
        if risposta.status_code == 200:
            return True, None
        return False, f"HTTP {risposta.status_code}: {risposta.text[:300]}"
    except httpx.HTTPError as exc:
        return False, str(exc)


def invia_notifiche_scadenza(db: Session) -> int:
    """Send one Telegram notification per candidate — customer services first,
    then supplier contracts — logging every attempt (success or failure) so a
    successful send is never repeated.

    Returns the number of candidates processed (sent or attempted).
    """
    clienti = occorrenze_da_notificare(db)
    for candidato in clienti:
        _invia_e_registra(
            db, messaggio_notifica(candidato), candidato.tipo, candidato.data_riferimento,
            servizio_id=candidato.servizio.id,
        )
    fornitori_dovuti = contratti_fornitore_da_notificare(db)
    for candidato in fornitori_dovuti:
        _invia_e_registra(
            db, messaggio_fornitore(candidato), candidato.tipo, candidato.data_riferimento,
            contratto_fornitore_id=candidato.contratto.id,
        )
    db.commit()
    return len(clienti) + len(fornitori_dovuti)


def _invia_e_registra(
    db: Session, testo: str, tipo: TipoNotifica, data_riferimento: date, *,
    servizio_id: int | None = None, contratto_fornitore_id: int | None = None,
) -> None:
    """Send one message and log the attempt against whichever it is about."""
    esito, dettaglio = invia_telegram(testo)
    db.add(
        NotificaLog(
            servizio_id=servizio_id,
            contratto_fornitore_id=contratto_fornitore_id,
            data_occorrenza=data_riferimento,
            tipo=tipo,
            canale=CanalNotifica.telegram,
            esito=esito,
            dettaglio=dettaglio,
        )
    )
    if not esito:
        logger.warning(
            "Notifica Telegram (%s) non inviata per servizio_id=%s contratto_fornitore_id=%s data=%s: %s",
            tipo.value, servizio_id, contratto_fornitore_id, data_riferimento, dettaglio,
        )
