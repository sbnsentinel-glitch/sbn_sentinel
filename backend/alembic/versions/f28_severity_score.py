"""f28_severity_score

Revision ID: f28_severity_score
Revises: f24_multi_config
Create Date: 2026-10-07 12:05:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f28_severity_score'
down_revision = 'f24_multi_config'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # F-28 schema change for severity_score
    op.add_column('rules', sa.Column('severity_score', sa.Integer(), nullable=True))

def downgrade() -> None:
    op.drop_column('rules', 'severity_score')
