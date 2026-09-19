"""add rejected tool call status

Revision ID: a3017d29dc4f
Revises: 994d7e49f196
Create Date: 2026-09-15 02:32:12.954848

Autogenerate produced an empty migration for this — a known, documented
limitation: Alembic's autogenerate does not detect additions to a Postgres
native ENUM type's value set (it compares column/table structure, not enum
value lists). Hand-written below.

`ALTER TYPE ... ADD VALUE` cannot run inside a transaction block that also
*uses* the new value (Postgres restriction), but CAN run inside a
transaction that only adds it — which is exactly this migration, so no
`autocommit_block()` workaround is needed here. Verified by actually
running this against real Postgres, not assumed from the Postgres docs.

Downgrade is NOT reversible: Postgres has no `ALTER TYPE ... DROP VALUE`.
Reverting would require creating a new type without 'rejected', rewriting
every column that uses it, and dropping the old type — a real, disruptive
operation that shouldn't happen silently inside what looks like a routine
`alembic downgrade`. `downgrade()` raises rather than pretending to revert
something it can't.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3017d29dc4f'
down_revision: Union[str, Sequence[str], None] = '994d7e49f196'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE tool_call_status ADD VALUE IF NOT EXISTS 'rejected'")


def downgrade() -> None:
    raise NotImplementedError(
        "Cannot downgrade: Postgres has no ALTER TYPE ... DROP VALUE. "
        "Removing 'rejected' from tool_call_status requires manually "
        "creating a replacement enum type, migrating every column that "
        "uses it, and dropping the old type — not something this "
        "migration will do silently. If you actually need to revert this, "
        "do it by hand and update alembic_version accordingly."
    )
