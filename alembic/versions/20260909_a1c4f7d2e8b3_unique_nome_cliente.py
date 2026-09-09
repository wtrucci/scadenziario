"""unique customer name (case-insensitive)

Adds a unique index on lower(nome) so the same customer cannot be entered
twice with different casing. The application already refuses duplicates with a
friendly message (routes/clienti.py); this index is the safety net for
imports, scripts and concurrent saves.

If the database already contains such duplicates the migration stops and lists
them: merging customers means moving their services, and that is a decision
for the operator, not for a migration.

Revision ID: a1c4f7d2e8b3
Revises: 9b495b63f9b6
Create Date: 2026-09-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c4f7d2e8b3"
down_revision: Union[str, None] = "9b495b63f9b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    duplicati = conn.execute(
        sa.text(
            "SELECT lower(nome) AS chiave, count(*) AS n "
            "FROM clienti GROUP BY lower(nome) HAVING count(*) > 1 "
            "ORDER BY chiave"
        )
    ).all()
    if duplicati:
        elenco = ", ".join(f"{r.chiave!r} ({r.n})" for r in duplicati)
        raise RuntimeError(
            "Impossibile applicare il vincolo di unicità sul nome cliente: "
            f"nel database esistono già nomi duplicati: {elenco}. "
            "Unisci o rinomina questi clienti (spostando i servizi sul cliente "
            "da tenere), poi ripeti la migrazione."
        )

    op.create_index(
        "ux_clienti_nome_lower", "clienti", [sa.text("lower(nome)")], unique=True
    )


def downgrade() -> None:
    op.drop_index("ux_clienti_nome_lower", table_name="clienti")
