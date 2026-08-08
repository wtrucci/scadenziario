"""servizio: durata_mesi and rinnovo_automatico

Revision ID: a1b2c3d4e5f6
Revises: 6baa2f0065a0
Create Date: 2026-08-08 17:00:00.000000+02:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '6baa2f0065a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('servizi') as batch_op:
        # Nullable: existing rows get a best-effort backfill below rather than
        # a hard requirement. New/edited services always set it (see
        # app/routes/servizi.py), which is what actually drives data_fine.
        batch_op.add_column(sa.Column('durata_mesi', sa.Integer(), nullable=True))
        # server_default backfills existing rows as "no auto-renewal" (0); the
        # model sets the value in Python afterwards.
        batch_op.add_column(
            sa.Column('rinnovo_automatico', sa.Boolean(), nullable=False, server_default=sa.false())
        )

    # Best-effort backfill of durata_mesi from the existing data_inizio/data_fine
    # pair, so the servizi list has something sensible to show right away. This
    # is purely informational until a service opts into rinnovo_automatico (all
    # existing rows default to False), so an approximate month count is fine —
    # the user can correct it from the edit form if it looks off.
    connection = op.get_bind()
    servizi = connection.execute(
        sa.text('SELECT id, data_inizio, data_fine FROM servizi')
    ).fetchall()
    for servizio_id, data_inizio, data_fine in servizi:
        anno_i, mese_i, giorno_i = (int(p) for p in data_inizio.split('-'))
        anno_f, mese_f, giorno_f = (int(p) for p in data_fine.split('-'))
        # Whole months between the two dates (like an age calculation): if
        # data_fine's day hasn't reached data_inizio's day yet, the last month
        # isn't complete. Handles both an old "exact anniversary" data_fine
        # (day matches -> no adjustment) and one already shaped like
        # calcola_data_fine's inclusive end (day is inizio.day - 1 -> -1 month).
        mesi = (anno_f - anno_i) * 12 + (mese_f - mese_i)
        if giorno_f < giorno_i:
            mesi -= 1
        mesi = max(mesi, 1)
        connection.execute(
            sa.text('UPDATE servizi SET durata_mesi = :mesi WHERE id = :id'),
            {'mesi': mesi, 'id': servizio_id},
        )


def downgrade() -> None:
    with op.batch_alter_table('servizi') as batch_op:
        batch_op.drop_column('rinnovo_automatico')
        batch_op.drop_column('durata_mesi')
