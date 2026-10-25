"""supplier contracts

Adds contratti_fornitore: what we buy from suppliers and resell, with the
supplier's own renewal dates (see app/models/contratto_fornitore.py). A new
table only, nothing existing is touched.

Revision ID: e4b8d2f1a6c9
Revises: c7e2a9b4d1f6
Create Date: 2026-10-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4b8d2f1a6c9"
down_revision: Union[str, None] = "c7e2a9b4d1f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "contratti_fornitore",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fornitore", sa.String(length=200), nullable=False),
        sa.Column("descrizione", sa.String(length=300), nullable=False),
        sa.Column("riferimento", sa.String(length=200), nullable=True),
        sa.Column("data_scadenza", sa.Date(), nullable=False),
        sa.Column("cadenza_mesi", sa.Integer(), nullable=False),
        sa.Column("rinnovo_automatico", sa.Boolean(), nullable=False),
        sa.Column("importo", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("quantita", sa.Integer(), nullable=False),
        sa.Column("valuta", sa.String(length=3), nullable=False),
        sa.Column("preavviso_giorni", sa.Integer(), nullable=False),
        sa.Column("disdetto", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("creato_il", sa.DateTime(timezone=True), nullable=False),
        sa.Column("aggiornato_il", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_contratti_fornitore_data_scadenza"), "contratti_fornitore",
        ["data_scadenza"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_contratti_fornitore_data_scadenza"), table_name="contratti_fornitore")
    op.drop_table("contratti_fornitore")
