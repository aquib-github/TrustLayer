"""Add resolution_status to decisions and structured columns to audit_log

Revision ID: 0003_approval_hardening
Revises: 0002_numeric_precision
Create Date: 2026-10-07 17:55:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0003_approval_hardening'
down_revision: Union[str, None] = '0002_numeric_precision'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add explicit resolution_status column to decisions
    op.add_column('decisions', sa.Column('resolution_status', sa.Text(), nullable=True))

    # Add structured columns to audit_log table
    op.add_column('audit_log', sa.Column('event_type', sa.Text(), nullable=True))
    op.add_column('audit_log', sa.Column('actor_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=True))
    op.add_column('audit_log', sa.Column('reason', sa.Text(), nullable=True))

    # Populate event_type for existing audit_log records
    op.execute("UPDATE audit_log SET event_type = event WHERE event_type IS NULL AND event IS NOT NULL")

    # Add indexes for efficient audit filtering
    op.create_index('ix_audit_log_event_type', 'audit_log', ['event_type'])
    op.create_index('ix_audit_log_actor_id', 'audit_log', ['actor_id'])


def downgrade() -> None:
    # Drop audit_log indexes and added columns
    op.drop_index('ix_audit_log_actor_id', table_name='audit_log')
    op.drop_index('ix_audit_log_event_type', table_name='audit_log')
    op.drop_column('audit_log', 'reason')
    op.drop_column('audit_log', 'actor_id')
    op.drop_column('audit_log', 'event_type')

    # Drop resolution_status column from decisions
    op.drop_column('decisions', 'resolution_status')
