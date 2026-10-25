"""renewal offers

Adds offerte_rinnovo: one row per renewal whose offer has been sent to the
customer (see app/models/offerta_rinnovo.py). A new table only, nothing
existing is touched: every renewal starts as "offer to send".

Revision ID: c7e2a9b4d1f6
Revises: a1c4f7d2e8b3
Create Date: 2026-10-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7e2a9b4d1f6"
down_revision: Union[str, None] = "a1c4f7d2e8b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "offerte_rinnovo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("servizio_id", sa.Integer(), nullable=False),
        sa.Column("data_rinnovo", sa.Date(), nullable=False),
        sa.Column("inviata_il", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["servizio_id"], ["servizi.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("servizio_id", "data_rinnovo", name="uq_offerta_servizio_data"),
    )


def downgrade() -> None:
    op.drop_table("offerte_rinnovo")
