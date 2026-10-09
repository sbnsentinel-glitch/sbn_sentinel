"""f21_f25_constraints

Revision ID: f21_f25_constraints
Revises: f08_concurrency
Create Date: 2026-10-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f21_f25_constraints'
down_revision = 'f08_concurrency'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # F-21: Idempotency uniqueness constraint on canonical ingress evidence
    try:
        with op.batch_alter_table("evidence_repository") as batch_op:
            batch_op.create_unique_constraint(
                "uq_evidence_source_fact",
                ["source_connector", "fact_key"]
            )
    except Exception:
        pass

    # F-25: Concurrency uniqueness constraint on connector cursors
    try:
        with op.batch_alter_table("connector_cursors") as batch_op:
            batch_op.create_unique_constraint(
                "uq_cursor_connector_resource",
                ["connector_id", "resource_type"]
            )
    except Exception:
        pass


def downgrade() -> None:
    try:
        with op.batch_alter_table("connector_cursors") as batch_op:
            batch_op.drop_constraint("uq_cursor_connector_resource", type_="unique")
    except Exception:
        pass

    try:
        with op.batch_alter_table("evidence_repository") as batch_op:
            batch_op.drop_constraint("uq_evidence_source_fact", type_="unique")
    except Exception:
        pass
