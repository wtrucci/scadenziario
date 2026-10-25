"""notifications about supplier contracts

notifica_log rows were always about a customer service. They can now be about
a supplier contract instead: servizio_id becomes nullable, a nullable
contratto_fornitore_id is added, and a check constraint keeps exactly one of
the two set.

SQLite cannot alter a column in place, so this runs in batch mode: Alembic
builds the new table, copies every existing row across and swaps it in. All
existing rows have servizio_id set, so they satisfy the new constraint as
they are — nothing in the log is changed.

Revision ID: f2a7c3e9b5d1
Revises: e4b8d2f1a6c9
Create Date: 2026-10-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2a7c3e9b5d1"
down_revision: Union[str, None] = "e4b8d2f1a6c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("notifica_log") as batch:
        batch.alter_column("servizio_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("contratto_fornitore_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_notifica_log_contratto_fornitore", "contratti_fornitore",
            ["contratto_fornitore_id"], ["id"],
        )
        batch.create_check_constraint(
            "ck_notifica_log_un_solo_oggetto",
            "(servizio_id IS NULL) <> (contratto_fornitore_id IS NULL)",
        )


def downgrade() -> None:
    # Supplier rows cannot exist without the new column: drop them first, or
    # making servizio_id NOT NULL again would fail.
    op.execute("DELETE FROM notifica_log WHERE servizio_id IS NULL")
    with op.batch_alter_table("notifica_log") as batch:
        batch.drop_constraint("ck_notifica_log_un_solo_oggetto", type_="check")
        batch.drop_constraint("fk_notifica_log_contratto_fornitore", type_="foreignkey")
        batch.drop_column("contratto_fornitore_id")
        batch.alter_column("servizio_id", existing_type=sa.Integer(), nullable=False)
