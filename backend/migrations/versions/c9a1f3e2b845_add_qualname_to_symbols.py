"""Add qualname to symbols

Revision ID: c9a1f3e2b845
Revises: b7490f84a123
Create Date: 2026-10-23 23:58:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9a1f3e2b845'
down_revision: Union[str, Sequence[str], None] = 'b7490f84a123'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('symbols', sa.Column('qualname', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('symbols', 'qualname')
