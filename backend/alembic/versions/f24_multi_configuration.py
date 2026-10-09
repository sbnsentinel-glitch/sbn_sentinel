"""f24_multi_configuration

Revision ID: f24_multi_config
Revises: 000_baseline
Create Date: 2026-10-07 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f24_multi_config'
down_revision = '000_baseline'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # F-24 schema change for multi-configuration support (idempotent for existing DBs)
    bind = op.get_bind()
    insp = sa.inspect(bind)
    rules_cols = {c['name'] for c in insp.get_columns('rules')} if insp.has_table('rules') else set()
    rule_ver_cols = {c['name'] for c in insp.get_columns('rule_versions')} if insp.has_table('rule_versions') else set()

    with op.batch_alter_table("rules") as batch_op:
        if 'multi_config' not in rules_cols:
            batch_op.add_column(sa.Column('multi_config', sa.JSON(), nullable=True))

    if insp.has_table('rule_versions') and 'multi_config_overrides' not in rule_ver_cols:
        with op.batch_alter_table("rule_versions") as batch_op:
            batch_op.add_column(sa.Column('multi_config_overrides', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("rule_versions") as batch_op:
        batch_op.drop_column('multi_config_overrides')
    with op.batch_alter_table("rules") as batch_op:
        batch_op.drop_column('multi_config')
