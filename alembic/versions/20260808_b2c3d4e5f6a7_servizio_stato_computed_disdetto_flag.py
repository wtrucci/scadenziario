"""servizio: computed stato, disdetto flag replaces the stato column

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-08 18:00:00.000000+02:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('servizi') as batch_op:
        batch_op.add_column(
            sa.Column('disdetto', sa.Boolean(), nullable=False, server_default=sa.false())
        )

    # Backfill disdetto from the old stato column: both 'disdetto' and
    # 'rinnovato' meant "no longer to be billed off this row" (a manual
    # renewal used to close the old row and open a new one — durata_mesi /
    # rinnovo_automatico now handle that without needing a second state).
    # 'attivo' and 'scaduto' become disdetto=False: whether the contract is
    # currently active or expired is now computed from dates (stato_contratto).
    connection = op.get_bind()
    connection.execute(
        sa.text("UPDATE servizi SET disdetto = 1 WHERE stato IN ('disdetto', 'rinnovato')")
    )

    with op.batch_alter_table('servizi') as batch_op:
        batch_op.drop_column('stato')


def downgrade() -> None:
    with op.batch_alter_table('servizi') as batch_op:
        batch_op.add_column(
            sa.Column('stato', sa.String(length=20), nullable=False, server_default='attivo')
        )

    connection = op.get_bind()
    connection.execute(
        sa.text("UPDATE servizi SET stato = 'disdetto' WHERE disdetto = 1")
    )

    with op.batch_alter_table('servizi') as batch_op:
        batch_op.drop_column('disdetto')
