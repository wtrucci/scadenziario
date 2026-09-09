"""
Text normalisation shared by the duplicate checks.

Customer names and service descriptions are typed by hand, so the same thing
gets written in slightly different ways. These two helpers say what "the same
text" means, and both the customer check (services/clienti.py) and the service
check (services/servizi.py) are built on them.
"""
from __future__ import annotations

import re


def normalizza_nome(nome: str) -> str:
    """Return the text as it should be STORED: trimmed, inner runs of
    whitespace collapsed to a single space. Casing is left as the user typed
    it — "IBM" must not become "Ibm"."""
    return re.sub(r"\s+", " ", nome).strip()


def chiave_identita(nome: str) -> str:
    """Key for the hard duplicate check: the same text up to casing and
    spacing.

    For customers this must stay consistent with the ``lower(nome)`` unique
    index in the database: since names are stored already normalised,
    lowercasing the normalised name is exactly what the index sees.
    """
    return normalizza_nome(nome).casefold()
