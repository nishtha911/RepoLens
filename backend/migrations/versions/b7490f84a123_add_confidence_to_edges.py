"""Add confidence to edges

Revision ID: b7490f84a123
Revises: aed062422173
Create Date: 2026-10-13 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7490f84a123'
down_revision: Union[str, Sequence[str], None] = 'aed062422173'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('edges', sa.Column('confidence', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('edges', 'confidence')
