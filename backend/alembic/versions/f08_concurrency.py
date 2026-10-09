"""f08_concurrency

Revision ID: f08_concurrency
Revises: f28_severity_score
Create Date: 2026-10-07 13:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f08_concurrency'
down_revision = 'f28_severity_score'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    dec_cols = {c['name'] for c in insp.get_columns('governed_decisions')} if insp.has_table('governed_decisions') else set()
    rec_cols = {c['name'] for c in insp.get_columns('governed_recommendations')} if insp.has_table('governed_recommendations') else set()
    act_cols = {c['name'] for c in insp.get_columns('governed_actions')} if insp.has_table('governed_actions') else set()

    # F-05: Human-decision persistence fidelity
    with op.batch_alter_table("governed_decisions") as batch_op:
        if 'reason' not in dec_cols:
            batch_op.add_column(sa.Column('reason', sa.String(), nullable=True))
        if 'authority_basis' not in dec_cols:
            batch_op.add_column(sa.Column('authority_basis', sa.String(), nullable=True))
        if 'override_indicator' not in dec_cols:
            batch_op.add_column(sa.Column('override_indicator', sa.Boolean(), nullable=True))

    # F-06: Recommendation authority persistence
    with op.batch_alter_table("governed_recommendations") as batch_op:
        if 'authority_requirement' not in rec_cols:
            batch_op.add_column(sa.Column('authority_requirement', sa.String(), nullable=True))

    # F-07: Action timing persistence and continuity
    with op.batch_alter_table("governed_actions") as batch_op:
        if 'execute_by' not in act_cols:
            batch_op.add_column(sa.Column('execute_by', sa.String(), nullable=True))

    # F-08: Atomic decision/action/execution concurrency
    if insp.has_table('governed_decisions'):
        existing_indices = {idx['name'] for idx in insp.get_indexes('governed_decisions')}
        if "uq_recorded_decision_per_rec" not in existing_indices:
            bind_dialect = bind.dialect.name
            if bind_dialect == "postgresql":
                op.create_index(
                    "uq_recorded_decision_per_rec", "governed_decisions", ["recommendation_id"],
                    unique=True, postgresql_where=sa.text("status = 'RECORDED'")
                )
            else:
                op.create_index(
                    "uq_recorded_decision_per_rec", "governed_decisions", ["recommendation_id"],
                    unique=False
                )

    if insp.has_table('governed_execution_attempts'):
        existing_ucs = {c['name'] for c in insp.get_unique_constraints('governed_execution_attempts')}
        if "uq_attempt_no" not in existing_ucs:
            with op.batch_alter_table("governed_execution_attempts") as batch_op:
                batch_op.create_unique_constraint("uq_attempt_no", ["action_id", "attempt_number"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table('governed_execution_attempts'):
        existing_ucs = {c['name'] for c in insp.get_unique_constraints('governed_execution_attempts')}
        if "uq_attempt_no" in existing_ucs:
            with op.batch_alter_table("governed_execution_attempts") as batch_op:
                batch_op.drop_constraint("uq_attempt_no", type_="unique")

    if insp.has_table('governed_decisions'):
        existing_indices = {idx['name'] for idx in insp.get_indexes('governed_decisions')}
        if "uq_recorded_decision_per_rec" in existing_indices:
            op.drop_index("uq_recorded_decision_per_rec", table_name="governed_decisions")

    with op.batch_alter_table("governed_actions") as batch_op:
        batch_op.drop_column('execute_by')
    with op.batch_alter_table("governed_recommendations") as batch_op:
        batch_op.drop_column('authority_requirement')
    with op.batch_alter_table("governed_decisions") as batch_op:
        batch_op.drop_column('override_indicator')
        batch_op.drop_column('authority_basis')
        batch_op.drop_column('reason')
