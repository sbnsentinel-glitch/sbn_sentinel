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
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    # F-21: Idempotency uniqueness constraint on canonical ingress evidence
    if "evidence_repository" in tables:
        existing = {
            c["name"]
            for c in inspector.get_unique_constraints("evidence_repository")
        }
        if "uq_evidence_source_fact" not in existing:
            with op.batch_alter_table("evidence_repository") as batch_op:
                batch_op.create_unique_constraint(
                    "uq_evidence_source_fact",
                    ["source_connector", "fact_key"]
                )

    # F-25: Concurrency uniqueness constraint on connector cursors
    cursor_table = "sync_cursors" if "sync_cursors" in tables else ("connector_cursors" if "connector_cursors" in tables else None)
    if cursor_table:
        existing_cursor = {
            c["name"]
            for c in inspector.get_unique_constraints(cursor_table)
        }
        if "uq_cursor_connector_resource" not in existing_cursor:
            with op.batch_alter_table(cursor_table) as batch_op:
                batch_op.create_unique_constraint(
                    "uq_cursor_connector_resource",
                    ["connector_id", "resource_type"]
                )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    cursor_table = "sync_cursors" if "sync_cursors" in tables else ("connector_cursors" if "connector_cursors" in tables else None)
    if cursor_table:
        existing_cursor = {
            c["name"]
            for c in inspector.get_unique_constraints(cursor_table)
        }
        if "uq_cursor_connector_resource" in existing_cursor:
            with op.batch_alter_table(cursor_table) as batch_op:
                batch_op.drop_constraint("uq_cursor_connector_resource", type_="unique")

    if "evidence_repository" in tables:
        existing = {
            c["name"]
            for c in inspector.get_unique_constraints("evidence_repository")
        }
        if "uq_evidence_source_fact" in existing:
            with op.batch_alter_table("evidence_repository") as batch_op:
                batch_op.drop_constraint("uq_evidence_source_fact", type_="unique")
