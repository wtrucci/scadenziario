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
3. ``contratto_scaduto``    — the CONTRACT itself (not a single occurrence)
   has reached ``stato_contratto == "scaduto"``. This never fires for a
   ``rinnovo_automatico`` contract: its effective end date is never in the
   past (see ``data_fine_effettiva``), so it can never be "scaduto" — that
   invariant is enough to skip the notification, no extra check needed here.

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
from app.models.enums import CanalNotifica, TipoNotifica
from app.models.notifica_log import NotificaLog
from app.models.servizio import Servizio
from app.services.occorrenze import Occorrenza, data_fine_effettiva, occorrenze_nel_periodo, stato_contratto

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
            if occ.stato_visivo != "in_scadenza":
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
            if occ.fatturato:
                continue
            chiave = (occ.data_occorrenza, TipoNotifica.promemoria_7_giorni)
            if chiave not in gia_notificate:
                candidati.append(CandidatoNotifica(
                    servizio, TipoNotifica.promemoria_7_giorni, occ.data_occorrenza, occ
                ))

        # 3) Contract expired. Auto-renewing contracts are excluded for free:
        # stato_contratto never returns "scaduto" for them.
        if stato_contratto(servizio, oggi=oggi) == "scaduto":
            scadenza = data_fine_effettiva(servizio, riferimento=oggi)
            chiave = (scadenza, TipoNotifica.contratto_scaduto)
            if chiave not in gia_notificate:
                candidati.append(CandidatoNotifica(
                    servizio, TipoNotifica.contratto_scaduto, scadenza
                ))

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
    """Send one Telegram notification per candidate, logging every attempt
    (success or failure) so a successful send is never repeated.

    Returns the number of candidates processed (sent or attempted).
    """
    candidati = occorrenze_da_notificare(db)
    for candidato in candidati:
        esito, dettaglio = invia_telegram(messaggio_notifica(candidato))
        db.add(
            NotificaLog(
                servizio_id=candidato.servizio.id,
                data_occorrenza=candidato.data_riferimento,
                tipo=candidato.tipo,
                canale=CanalNotifica.telegram,
                esito=esito,
                dettaglio=dettaglio,
            )
        )
        if not esito:
            logger.warning(
                "Notifica Telegram (%s) non inviata per servizio_id=%s data=%s: %s",
                candidato.tipo.value, candidato.servizio.id, candidato.data_riferimento, dettaglio,
            )
    db.commit()
    return len(candidati)
