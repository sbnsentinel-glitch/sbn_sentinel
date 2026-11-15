"""cp08_recommendation_fields

Revision ID: cp08_recommendation_fields
Revises: f28_severity_score
Create Date: 2026-10-24 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'cp08_recommendation_fields'
down_revision = 'f21_f25_constraints'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CP-08: Additive nullable columns for problem, business_impact, and expected_outcome
    bind = op.get_bind()
    insp = sa.inspect(bind)
    table_name = "governed_recommendations"
    existing_cols = {c['name'] for c in insp.get_columns(table_name)} if insp.has_table(table_name) else set()

    with op.batch_alter_table(table_name) as batch_op:
        if 'problem' not in existing_cols:
            batch_op.add_column(sa.Column('problem', sa.Text(), nullable=True))
        if 'business_impact' not in existing_cols:
            batch_op.add_column(sa.Column('business_impact', sa.Text(), nullable=True))
        if 'expected_outcome' not in existing_cols:
            batch_op.add_column(sa.Column('expected_outcome', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("governed_recommendations") as batch_op:
        batch_op.drop_column('expected_outcome')
        batch_op.drop_column('business_impact')
        batch_op.drop_column('problem')
