"""Initial schema creation for TrustLayer

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-09-27 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import pgvector
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = '0001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 0. Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 1. roles
    op.create_table(
        'roles',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    # 2. users
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.Text(), nullable=True),
        sa.Column('email', sa.Text(), nullable=True),
        sa.Column('role_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )

    # 3. accounts
    op.create_table(
        'accounts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('balance', sa.Numeric(), nullable=True),
        sa.Column('account_type', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # 4. transactions
    op.create_table(
        'transactions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('account_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('amount', sa.Numeric(), nullable=True),
        sa.Column('type', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # 5. kb_documents
    op.create_table(
        'kb_documents',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('source', sa.Text(), nullable=True),
        sa.Column('role_scope', postgresql.ARRAY(sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # 6. kb_embeddings
    op.create_table(
        'kb_embeddings',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('doc_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('chunk_text', sa.Text(), nullable=True),
        sa.Column('embedding', Vector(1024), nullable=True),
        sa.Column('chunk_index', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['doc_id'], ['kb_documents.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # 7. chat_sessions
    op.create_table(
        'chat_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('started_at', sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # 8. chat_messages
    op.create_table(
        'chat_messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('session_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('role', sa.Text(), nullable=True),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_chat_messages_session_id', 'chat_messages', ['session_id'])

    # 9. agent_actions
    op.create_table(
        'agent_actions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('session_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('tool_name', sa.Text(), nullable=True),
        sa.Column('params_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('proposed_at', sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # 10. claims
    op.create_table(
        'claims',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('action_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('claim_text', sa.Text(), nullable=True),
        sa.Column('extracted_at', sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(['action_id'], ['agent_actions.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # 11. grounding_results
    op.create_table(
        'grounding_results',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('claim_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('retrieved_doc_ids', postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=True),
        sa.Column('nli_label', sa.Text(), nullable=True),
        sa.Column('grounding_score', sa.Numeric(), nullable=True),
        sa.ForeignKeyConstraint(['claim_id'], ['claims.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # 12. policy_checks
    op.create_table(
        'policy_checks',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('action_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('rbac_result', sa.Text(), nullable=True),
        sa.Column('sequence_anomaly_flag', sa.Boolean(), nullable=True),
        sa.Column('policy_risk_score', sa.Numeric(), nullable=True),
        sa.ForeignKeyConstraint(['action_id'], ['agent_actions.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # 13. decisions
    op.create_table(
        'decisions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('action_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('grounding_score', sa.Numeric(), nullable=True),
        sa.Column('policy_risk_score', sa.Numeric(), nullable=True),
        sa.Column('final_risk', sa.Numeric(), nullable=True),
        sa.Column(
            'decision',
            sa.Enum('allow', 'block', 'approve', name='decision_type'),
            nullable=False,
        ),
        sa.Column('approver_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('resolved_at', sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(['action_id'], ['agent_actions.id'], ),
        sa.ForeignKeyConstraint(['approver_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # 14. audit_log
    op.create_table(
        'audit_log',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('request_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('session_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('action_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('decision_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('event', sa.Text(), nullable=True),
        sa.Column('timestamp', sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(['action_id'], ['agent_actions.id'], ),
        sa.ForeignKeyConstraint(['decision_id'], ['decisions.id'], ),
        sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_log_request_id', 'audit_log', ['request_id'])
    op.create_index('ix_audit_log_session_id', 'audit_log', ['session_id'])


def downgrade() -> None:
    op.drop_index('ix_audit_log_session_id', table_name='audit_log')
    op.drop_index('ix_audit_log_request_id', table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_table('decisions')
    sa.Enum(name='decision_type').drop(op.get_bind(), checkfirst=True)
    op.drop_table('policy_checks')
    op.drop_table('grounding_results')
    op.drop_table('claims')
    op.drop_table('agent_actions')
    op.drop_index('ix_chat_messages_session_id', table_name='chat_messages')
    op.drop_table('chat_messages')
    op.drop_table('chat_sessions')
    op.drop_table('kb_embeddings')
    op.drop_table('kb_documents')
    op.drop_table('transactions')
    op.drop_table('accounts')
    op.drop_table('users')
    op.drop_table('roles')
