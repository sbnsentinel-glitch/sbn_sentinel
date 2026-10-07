"""f24_multi_configuration

Revision ID: f24_multi_config
Revises: 
Create Date: 2026-10-07 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'f24_multi_config'
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    # F-24 schema change for multi-configuration support
    op.add_column('rules', sa.Column('multi_config', sa.JSON(), nullable=True))
    op.add_column('rule_versions', sa.Column('multi_config_overrides', sa.JSON(), nullable=True))

def downgrade() -> None:
    op.drop_column('rules', 'multi_config')
    op.drop_column('rule_versions', 'multi_config_overrides')
