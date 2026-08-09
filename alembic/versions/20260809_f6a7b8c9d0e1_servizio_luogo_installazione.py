"""servizio: add luogo_installazione

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-09 08:00:00.000000+02:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('servizi') as batch_op:
        batch_op.add_column(sa.Column('luogo_installazione', sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('servizi') as batch_op:
        batch_op.drop_column('luogo_installazione')
