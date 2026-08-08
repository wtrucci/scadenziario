"""notifica_log: add tipo (preavviso / promemoria_7_giorni / contratto_scaduto)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-08 20:00:00.000000+02:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Distinguishes which of the three fixed triggers produced a log row, so
    # each is deduplicated independently (see app/services/notifiche.py). The
    # placeholder default only backfills pre-existing rows and is dropped
    # right after.
    with op.batch_alter_table('notifica_log') as batch_op:
        batch_op.add_column(
            sa.Column(
                'tipo',
                sa.Enum('preavviso', 'promemoria_7_giorni', 'contratto_scaduto',
                        name='tiponotifica', native_enum=False, length=30),
                nullable=False, server_default='preavviso',
            )
        )

    with op.batch_alter_table('notifica_log') as batch_op:
        batch_op.alter_column('tipo', server_default=None)


def downgrade() -> None:
    with op.batch_alter_table('notifica_log') as batch_op:
        batch_op.drop_column('tipo')
