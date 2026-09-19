"""add tool_calls idempotency_key

Revision ID: be1b4ec8805a
Revises: ee9499881f43
Create Date: 2026-09-17 18:02:31.539399

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'be1b4ec8805a'
down_revision: Union[str, Sequence[str], None] = 'ee9499881f43'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UQ_NAME = "uq_tool_calls_idempotency_key"


def upgrade() -> None:
    """Upgrade schema.

    The constraint is NAMED explicitly. Autogenerate emitted
    `create_unique_constraint(None, ...)` and a matching
    `drop_constraint(None, ...)` in downgrade, which Alembic itself
    warned "will fail as rendered" — and it did, with
    `CompileError: Can't emit DROP CONSTRAINT ...; it has no name`, when
    the downgrade was actually run. Naming it fixes both directions.
    """
    op.add_column('tool_calls', sa.Column('idempotency_key', sa.String(length=128), nullable=True))
    op.create_unique_constraint(UQ_NAME, 'tool_calls', ['idempotency_key'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(UQ_NAME, 'tool_calls', type_='unique')
    op.drop_column('tool_calls', 'idempotency_key')
