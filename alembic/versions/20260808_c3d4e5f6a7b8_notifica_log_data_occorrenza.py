"""notifica_log: add data_occorrenza, unique per (servizio, data, canale)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-08 19:00:00.000000+02:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Without this column a recurring service could only ever be notified once
    # in its lifetime (there would be no way to tell "already notified for the
    # occurrence due in March" from "already notified for the one due in
    # April"). The placeholder default only matters for pre-existing rows (the
    # notification feature did not exist before this migration, so in practice
    # there are none); it is dropped right after the backfill. No unique
    # constraint on (servizio, data, canale): a failed attempt and a later
    # successful retry both need their own row (see app/services/notifiche.py
    # — dedup is "was there a *successful* send", decided in application code).
    with op.batch_alter_table('notifica_log') as batch_op:
        batch_op.add_column(
            sa.Column('data_occorrenza', sa.Date(), nullable=False, server_default='2026-01-01')
        )

    with op.batch_alter_table('notifica_log') as batch_op:
        batch_op.alter_column('data_occorrenza', server_default=None)


def downgrade() -> None:
    with op.batch_alter_table('notifica_log') as batch_op:
        batch_op.drop_column('data_occorrenza')
