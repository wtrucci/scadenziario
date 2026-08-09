"""servizio: add durata_rinnovo_mesi

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-09 09:00:00.000000+02:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('servizi') as batch_op:
        batch_op.add_column(sa.Column('durata_rinnovo_mesi', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('servizi') as batch_op:
        batch_op.drop_column('durata_rinnovo_mesi')
