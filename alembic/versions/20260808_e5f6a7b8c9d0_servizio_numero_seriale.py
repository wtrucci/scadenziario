"""servizio: add numero_seriale

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-08 23:00:00.000000+02:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('servizi') as batch_op:
        batch_op.add_column(sa.Column('numero_seriale', sa.String(length=100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('servizi') as batch_op:
        batch_op.drop_column('numero_seriale')
