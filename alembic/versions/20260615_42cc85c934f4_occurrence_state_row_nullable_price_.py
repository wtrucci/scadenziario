"""occurrence state row: nullable price, fatturato flag

Revision ID: 42cc85c934f4
Revises: f714e88a07ea
Create Date: 2026-06-15 22:42:48.993625+02:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '42cc85c934f4'
down_revision: Union[str, None] = 'f714e88a07ea'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite cannot ALTER a column's nullability in place, so we use batch mode
    # (Alembic recreates the table, preserving FK and the unique constraint).
    with op.batch_alter_table('override_importo') as batch_op:
        # server_default backfills existing rows as "not billed" (0) so the new
        # NOT NULL column can be added without breaking them. The model sets the
        # value in Python, so the server_default is only needed for this backfill.
        batch_op.add_column(
            sa.Column('fatturato', sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column('fatturato_il', sa.DateTime(timezone=True), nullable=True)
        )
        # Price/quantity become optional: NULL means "use the service default".
        batch_op.alter_column('importo',
                   existing_type=sa.NUMERIC(precision=10, scale=2),
                   nullable=True)
        batch_op.alter_column('quantita',
                   existing_type=sa.INTEGER(),
                   nullable=True)


def downgrade() -> None:
    # The old model used this table purely as a price/quantity override, so
    # importo/quantita were NOT NULL. Rows that exist only to carry the
    # fatturato flag (importo/quantita left NULL) have no meaning in the old
    # model and would violate the restored NOT NULL constraint, so we delete
    # them first. NOTE: this discards the per-occurrence "fatturato" state,
    # which is unavoidable since the columns holding it are being dropped.
    op.execute(
        "DELETE FROM override_importo WHERE importo IS NULL OR quantita IS NULL"
    )
    with op.batch_alter_table('override_importo') as batch_op:
        batch_op.alter_column('quantita',
                   existing_type=sa.INTEGER(),
                   nullable=False)
        batch_op.alter_column('importo',
                   existing_type=sa.NUMERIC(precision=10, scale=2),
                   nullable=False)
        batch_op.drop_column('fatturato_il')
        batch_op.drop_column('fatturato')
