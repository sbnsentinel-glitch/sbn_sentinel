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
    # F-28 schema change for severity_score (idempotent for existing DBs)
    bind = op.get_bind()
    insp = sa.inspect(bind)
    rules_cols = {c['name'] for c in insp.get_columns('rules')} if insp.has_table('rules') else set()

    with op.batch_alter_table("rules") as batch_op:
        if 'severity_score' not in rules_cols:
            batch_op.add_column(sa.Column('severity_score', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("rules") as batch_op:
        batch_op.drop_column('severity_score')
