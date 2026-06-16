"""override row: override_manuale flag and note

Revision ID: 6baa2f0065a0
Revises: 42cc85c934f4
Create Date: 2026-06-16 08:15:04.009144+02:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6baa2f0065a0'
down_revision: Union[str, None] = '42cc85c934f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Batch mode for SQLite, consistent with the previous migration.
    with op.batch_alter_table('override_importo') as batch_op:
        # server_default backfills existing rows as "not a manual override" (0)
        # so the new NOT NULL column can be added without breaking them. The
        # model sets the value in Python, so the server_default is only the
        # backfill.
        batch_op.add_column(
            sa.Column('override_manuale', sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column('note', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('override_importo') as batch_op:
        batch_op.drop_column('note')
        batch_op.drop_column('override_manuale')
