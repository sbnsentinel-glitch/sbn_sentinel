"""production_baseline

Revision ID: 000_baseline
Revises: 
Create Date: 2026-10-01 00:00:00.000000

"""
import os
import sys

curr_dir = os.path.dirname(__file__)
runtime_path = os.path.abspath(os.path.join(curr_dir, "../../src/runtime"))
if runtime_path not in sys.path:
    sys.path.insert(0, runtime_path)

from alembic import op
import sqlalchemy as sa
from app.db.database import Base
from app.models import (  # noqa: F401
    event, signal, user, otp, governance_storage, intelligence,
    organization, encounter, clinic, billing, insurance, connector,
    audit, telemetry, settings, rule, evidence
)
from app.services.cursor_store import CursorModel  # noqa: F401

# revision identifiers, used by Alembic.
revision = '000_baseline'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ensure baseline tables exist idempotently against existing or fresh database
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    pass
