"""
Nightly database backup.

Copies the SQLite database with sqlite3's own ``backup()`` API rather than
copying the file: the app may be writing while the job runs, and a plain file
copy can capture a torn, unusable database. ``backup()`` produces a consistent
snapshot under concurrent writes.

Every fresh copy is then re-opened and checked with ``PRAGMA integrity_check``,
because a backup nobody ever reads back is not a backup. A failure is raised to
the caller (the scheduler), which reports it on the notification channel.

Only local copies are made, into a directory inside the mounted data volume.
Getting them off-site belongs to the host (rsync, a cloud client), not here.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("scadenziario.backup")

# Prefix + timestamp, so the names sort chronologically as plain strings and
# rotation never has to parse a date.
_PREFISSO = "scadenziario-"
_ESTENSIONE = ".db"
_FORMATO_ORA = "%Y%m%d-%H%M%S"


class BackupFallito(RuntimeError):
    """The backup could not be produced, or came out corrupt."""


def percorso_db(database_url: str) -> Path:
    """Extract the file path from a SQLite ``DATABASE_URL``.

    Only SQLite is supported: on PostgreSQL this job has no business running
    (the server has its own tooling), so the caller is told plainly.
    """
    if not database_url.startswith("sqlite:"):
        raise BackupFallito(
            f"backup supportato solo su SQLite, DATABASE_URL={database_url!r}"
        )
    # sqlite:///relative/path  or  sqlite:////absolute/path
    resto = database_url.split("sqlite:", 1)[1].lstrip("/")
    if database_url.startswith("sqlite:////"):
        resto = "/" + resto
    return Path(resto)


def esegui_backup(database_url: str, cartella: str | Path, da_tenere: int) -> Path:
    """Write one consistent snapshot, verify it, rotate the old ones.

    Returns the path of the copy just written. Raises ``BackupFallito`` if the
    snapshot cannot be taken or does not pass its integrity check — in that
    case the bad file is removed, so a corrupt copy can never be mistaken for
    a good one, and older healthy copies are left untouched.
    """
    sorgente = percorso_db(database_url)
    if not sorgente.exists():
        raise BackupFallito(f"database non trovato: {sorgente}")

    cartella = Path(cartella)
    cartella.mkdir(parents=True, exist_ok=True)
    destinazione = _nome_libero(cartella)

    try:
        con_sorgente = sqlite3.connect(sorgente)
        try:
            con_destinazione = sqlite3.connect(destinazione)
            try:
                con_sorgente.backup(con_destinazione)
            finally:
                con_destinazione.close()
        finally:
            con_sorgente.close()
    except sqlite3.Error as exc:
        destinazione.unlink(missing_ok=True)
        raise BackupFallito(f"copia fallita: {exc}") from exc

    esito = _verifica(destinazione)
    if esito != "ok":
        destinazione.unlink(missing_ok=True)
        raise BackupFallito(f"copia corrotta ({esito}), scartata: {destinazione.name}")

    ruota(cartella, da_tenere)
    logger.info("Backup completato: %s", destinazione)
    return destinazione


def _nome_libero(cartella: Path) -> Path:
    """A destination name that is not in use yet.

    The timestamp has one-second resolution, so two runs in the same second
    (the nightly job plus a manual one, a retry) would land on the same path —
    and since a failed snapshot deletes its own file, the second run could wipe
    the good copy the first one had just made. Suffixing keeps every snapshot
    responsible for its own file only.
    """
    base = datetime.now().strftime(_FORMATO_ORA)
    candidato = cartella / f"{_PREFISSO}{base}{_ESTENSIONE}"
    contatore = 1
    while candidato.exists():
        candidato = cartella / f"{_PREFISSO}{base}-{contatore}{_ESTENSIONE}"
        contatore += 1
    return candidato


def _verifica(percorso: Path) -> str:
    """Re-open the copy and run integrity_check, returning its verdict."""
    try:
        con = sqlite3.connect(percorso)
        try:
            return con.execute("pragma integrity_check").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error as exc:
        return str(exc)


def elenco_backup(cartella: str | Path) -> list[Path]:
    """Existing backups, oldest first (the timestamped names sort by date)."""
    cartella = Path(cartella)
    if not cartella.is_dir():
        return []
    return sorted(cartella.glob(f"{_PREFISSO}*{_ESTENSIONE}"))


def ruota(cartella: str | Path, da_tenere: int) -> list[Path]:
    """Delete the oldest copies beyond ``da_tenere``. Returns what was removed.

    ``da_tenere`` below 1 is treated as "keep everything": a misconfigured
    value must never be read as an instruction to wipe the backups.
    """
    if da_tenere < 1:
        return []
    esistenti = elenco_backup(cartella)
    da_eliminare = esistenti[:-da_tenere] if len(esistenti) > da_tenere else []
    for vecchio in da_eliminare:
        vecchio.unlink(missing_ok=True)
        logger.info("Backup ruotato via: %s", vecchio.name)
    return da_eliminare
