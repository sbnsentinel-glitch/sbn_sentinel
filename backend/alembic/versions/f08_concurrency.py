"""f08_concurrency

Revision ID: f08_concurrency
Revises: f28_severity_score
Create Date: 2026-10-07 13:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'f08_concurrency'
down_revision = 'f28_severity_score'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # F-05: Human-decision persistence fidelity
    op.add_column('governed_decisions', sa.Column('reason', sa.String(), nullable=True))
    op.add_column('governed_decisions', sa.Column('authority_basis', sa.String(), nullable=True))
    op.add_column('governed_decisions', sa.Column('override_indicator', sa.Boolean(), nullable=True))
    
    # F-06: Recommendation authority persistence
    op.add_column('governed_recommendations', sa.Column('authority_requirement', sa.String(), nullable=True))

    # F-07: Action timing persistence and continuity
    op.add_column('governed_actions', sa.Column('execute_by', sa.String(), nullable=True))

    # F-08: Atomic decision/action/execution concurrency
    op.create_index(
        "uq_recorded_decision_per_rec", "governed_decisions", ["recommendation_id"],
        unique=True, postgresql_where=sa.text("status = 'RECORDED'")
    )
    op.create_unique_constraint("uq_attempt_no", "governed_execution_attempts", ["action_id", "attempt_number"])

def downgrade() -> None:
    op.drop_constraint("uq_attempt_no", "governed_execution_attempts", type_="unique")
    op.drop_index("uq_recorded_decision_per_rec", table_name="governed_decisions")
    
    op.drop_column('governed_actions', 'execute_by')
    op.drop_column('governed_recommendations', 'authority_requirement')
    op.drop_column('governed_decisions', 'override_indicator')
    op.drop_column('governed_decisions', 'authority_basis')
    op.drop_column('governed_decisions', 'reason')
