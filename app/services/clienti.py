"""
Customer name rules: normalisation and duplicate detection.

Two different questions are answered here, and they must not be confused:

* "Is this the SAME customer, written differently?" — only casing and spacing
  differ ("ACME  srl" vs "Acme Srl"). This is a hard duplicate: it is blocked,
  and a unique index on ``lower(nome)`` backs the check at the database level.
* "Is this SUSPICIOUSLY similar to an existing customer?" — same name once
  punctuation and the legal form are ignored ("Acme S.r.l." vs "Acme Srl").
  These may legitimately be two distinct customers, so the answer is a warning
  the user can override, never a block.

Both comparisons are done in Python over the customer list, which is small
(hundreds at most) and read once per save.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.cliente import Cliente

# Legal forms carry no identity: "Acme Srl" and "Acme S.p.A." differ as
# companies, but as typed names they are the same "Acme" plus a suffix the user
# may have written in any of a dozen ways. They are dropped only for the
# similarity warning, never for the hard duplicate check.
FORME_GIURIDICHE = {
    "srl", "srls", "spa", "sas", "snc", "sapa", "scarl", "sc", "ss",
    "coop", "soc", "societa", "ltd", "llc", "inc", "gmbh", "sa", "bv", "plc",
    "di", "e", "c", "cooperativa",
}


def normalizza_nome(nome: str) -> str:
    """Return the name as it should be STORED: trimmed, inner runs of
    whitespace collapsed to a single space. Casing is left as the user typed
    it — "IBM" must not become "Ibm"."""
    return re.sub(r"\s+", " ", nome).strip()


def chiave_identita(nome: str) -> str:
    """Key for the hard duplicate check: same name up to casing and spacing.

    Must stay consistent with the ``lower(nome)`` unique index in the database:
    since names are stored already normalised, lowercasing the normalised name
    is exactly what the index sees.
    """
    return normalizza_nome(nome).casefold()


def chiave_similarita(nome: str) -> str:
    """Key for the soft "looks like an existing customer" warning: accents,
    punctuation and legal forms removed, remaining words sorted.

    Word order is dropped on purpose — "Rossi Mario" and "Mario Rossi" are the
    same person written by two different operators.
    """
    # NFKD + ASCII fold, so "Società" and "Societa" collapse together.
    senza_accenti = unicodedata.normalize("NFKD", nome.casefold())
    senza_accenti = senza_accenti.encode("ascii", "ignore").decode("ascii")
    # Dots and apostrophes are DELETED rather than turned into separators, so
    # "S.r.l." becomes the single word "srl" and matches "Srl"; every other
    # punctuation mark separates words.
    senza_punti = re.sub(r"[.'\u2019]", "", senza_accenti)
    parole = re.findall(r"[a-z0-9]+", senza_punti)
    significative = [p for p in parole if p not in FORME_GIURIDICHE]
    # If the name is nothing BUT legal forms, keep the words: an empty key
    # would match every other such name.
    return " ".join(sorted(significative or parole))


def _altri_clienti(db: Session, escludi_id: int | None) -> list[Cliente]:
    query = select(Cliente).order_by(Cliente.nome)
    if escludi_id is not None:
        query = query.where(Cliente.id != escludi_id)
    return list(db.scalars(query))


def trova_duplicato(db: Session, nome: str, escludi_id: int | None = None) -> Cliente | None:
    """The existing customer that is the same name as ``nome``, if any."""
    chiave = chiave_identita(nome)
    for c in _altri_clienti(db, escludi_id):
        if chiave_identita(c.nome) == chiave:
            return c
    return None


def trova_simili(db: Session, nome: str, escludi_id: int | None = None) -> list[Cliente]:
    """Existing customers whose name looks like ``nome`` without being equal.

    The exact duplicate, if present, is not repeated here: it is reported by
    :func:`trova_duplicato` as a blocking error.
    """
    chiave_id = chiave_identita(nome)
    chiave_sim = chiave_similarita(nome)
    if not chiave_sim:
        return []
    return [
        c
        for c in _altri_clienti(db, escludi_id)
        if chiave_identita(c.nome) != chiave_id and chiave_similarita(c.nome) == chiave_sim
    ]
