"""
SQLAlchemy ORM models for every table defined in docs/05_Backend_Data_Schema.md.

Tables (14 total):
  roles, users, accounts, transactions,
  kb_documents, kb_embeddings,
  chat_sessions, chat_messages,
  agent_actions, claims, grounding_results, policy_checks, decisions,
  audit_log
"""

import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Numeric,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ── Enums ─────────────────────────────────────────────────────────────────

class DecisionType(str, enum.Enum):
    ALLOW = "allow"
    BLOCK = "block"
    APPROVE = "approve"


# ── Base ──────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """Shared declarative base for all models."""
    pass


# ── Identity & Roles ─────────────────────────────────────────────────────

class Role(Base):
    """
    roles(
      id   SERIAL PRIMARY KEY,
      name TEXT  -- 'customer' | 'staff' | 'approver'
    )
    """

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    # relationships
    users: Mapped[list["User"]] = relationship(back_populates="role")


class User(Base):
    """
    users(
      id         UUID PRIMARY KEY,
      name       TEXT,
      email      TEXT UNIQUE,
      role_id    INT REFERENCES roles(id),
      created_at TIMESTAMP
    )
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, nullable=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=True)
    role_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("roles.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )

    # relationships
    role: Mapped["Role"] = relationship(back_populates="users")
    accounts: Mapped[list["Account"]] = relationship(back_populates="user")
    chat_sessions: Mapped[list["ChatSession"]] = relationship(back_populates="user")


# ── Banking Domain (Mock Ledger) ─────────────────────────────────────────

class Account(Base):
    """
    accounts(
      id           UUID PRIMARY KEY,
      user_id      UUID REFERENCES users(id),
      balance      NUMERIC,
      account_type TEXT
    )
    """

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    balance: Mapped[float] = mapped_column(Numeric, nullable=True)
    account_type: Mapped[str] = mapped_column(Text, nullable=True)

    # relationships
    user: Mapped["User"] = relationship(back_populates="accounts")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="account")


class Transaction(Base):
    """
    transactions(
      id         UUID PRIMARY KEY,
      account_id UUID REFERENCES accounts(id),
      amount     NUMERIC,
      type       TEXT,        -- 'debit' | 'credit' | 'transfer' | 'dispute'
      status     TEXT,        -- 'pending' | 'completed' | 'blocked'
      created_at TIMESTAMP
    )
    """

    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True
    )
    amount: Mapped[float] = mapped_column(Numeric, nullable=True)
    type: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )

    # relationships
    account: Mapped["Account"] = relationship(back_populates="transactions")


# ── Knowledge Base (Access-Aware RAG) ────────────────────────────────────

class KBDocument(Base):
    """
    kb_documents(
      id         UUID PRIMARY KEY,
      title      TEXT,
      content    TEXT,
      source     TEXT,         -- 'real' | 'synthetic'
      role_scope TEXT[]        -- which roles can retrieve this doc
    )
    """

    __tablename__ = "kb_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=True)
    role_scope = mapped_column(ARRAY(Text), nullable=True)

    # relationships
    embeddings: Mapped[list["KBEmbedding"]] = relationship(back_populates="document")


class KBEmbedding(Base):
    """
    kb_embeddings(
      id          UUID PRIMARY KEY,
      doc_id      UUID REFERENCES kb_documents(id),
      chunk_text  TEXT,
      embedding   VECTOR(1024),  -- BGE-M3 dimension
      chunk_index INT
    )
    """

    __tablename__ = "kb_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("kb_documents.id"), nullable=True
    )
    chunk_text: Mapped[str] = mapped_column(Text, nullable=True)
    embedding = mapped_column(Vector(1024), nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=True)

    # relationships
    document: Mapped["KBDocument"] = relationship(back_populates="embeddings")


# ── Conversation ─────────────────────────────────────────────────────────

class ChatSession(Base):
    """
    chat_sessions(
      id         UUID PRIMARY KEY,
      user_id    UUID REFERENCES users(id),
      started_at TIMESTAMP
    )
    """

    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )

    # relationships
    user: Mapped["User"] = relationship(back_populates="chat_sessions")
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="session")
    agent_actions: Mapped[list["AgentAction"]] = relationship(back_populates="session")
    audit_entries: Mapped[list["AuditLog"]] = relationship(back_populates="session")


class ChatMessage(Base):
    """
    chat_messages(
      id         UUID PRIMARY KEY,
      session_id UUID REFERENCES chat_sessions(id),
      role       TEXT,     -- 'user' | 'agent'
      content    TEXT,
      created_at TIMESTAMP
    )
    """

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("chat_sessions.id"), nullable=True
    )
    role: Mapped[str] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )

    # relationships
    session: Mapped["ChatSession"] = relationship(back_populates="messages")


# ── Agent Actions & TrustLayer Pipeline ──────────────────────────────────

class AgentAction(Base):
    """
    agent_actions(
      id          UUID PRIMARY KEY,
      session_id  UUID REFERENCES chat_sessions(id),
      tool_name   TEXT,
      params_json JSONB,
      proposed_at TIMESTAMP
    )
    """

    __tablename__ = "agent_actions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("chat_sessions.id"), nullable=True
    )
    tool_name: Mapped[str] = mapped_column(Text, nullable=True)
    params_json = mapped_column(JSONB, nullable=True)
    proposed_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )

    # relationships
    session: Mapped["ChatSession"] = relationship(back_populates="agent_actions")
    claims: Mapped[list["Claim"]] = relationship(back_populates="action")
    policy_checks: Mapped[list["PolicyCheck"]] = relationship(back_populates="action")
    decisions: Mapped[list["Decision"]] = relationship(back_populates="action")
    audit_entries: Mapped[list["AuditLog"]] = relationship(back_populates="action")


class Claim(Base):
    """
    claims(
      id           UUID PRIMARY KEY,
      action_id    UUID REFERENCES agent_actions(id),
      claim_text   TEXT,
      extracted_at TIMESTAMP
    )
    """

    __tablename__ = "claims"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    action_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agent_actions.id"), nullable=True
    )
    claim_text: Mapped[str] = mapped_column(Text, nullable=True)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )

    # relationships
    action: Mapped["AgentAction"] = relationship(back_populates="claims")
    grounding_results: Mapped[list["GroundingResult"]] = relationship(
        back_populates="claim"
    )


class GroundingResult(Base):
    """
    grounding_results(
      id                UUID PRIMARY KEY,
      claim_id          UUID REFERENCES claims(id),
      retrieved_doc_ids UUID[],
      nli_label         TEXT,          -- 'entailment' | 'neutral' | 'contradiction'
      grounding_score   NUMERIC
    )
    """

    __tablename__ = "grounding_results"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("claims.id"), nullable=True
    )
    retrieved_doc_ids = mapped_column(ARRAY(PG_UUID(as_uuid=True)), nullable=True)
    nli_label: Mapped[str] = mapped_column(Text, nullable=True)
    grounding_score: Mapped[float] = mapped_column(Numeric, nullable=True)

    # relationships
    claim: Mapped["Claim"] = relationship(back_populates="grounding_results")


class PolicyCheck(Base):
    """
    policy_checks(
      id                     UUID PRIMARY KEY,
      action_id              UUID REFERENCES agent_actions(id),
      rbac_result            TEXT,          -- 'allowed' | 'denied'
      sequence_anomaly_flag  BOOLEAN,
      policy_risk_score      NUMERIC
    )
    """

    __tablename__ = "policy_checks"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    action_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agent_actions.id"), nullable=True
    )
    rbac_result: Mapped[str] = mapped_column(Text, nullable=True)
    sequence_anomaly_flag: Mapped[bool] = mapped_column(Boolean, nullable=True)
    policy_risk_score: Mapped[float] = mapped_column(Numeric, nullable=True)

    # relationships
    action: Mapped["AgentAction"] = relationship(back_populates="policy_checks")


class Decision(Base):
    """
    decisions(
      id                UUID PRIMARY KEY,
      action_id         UUID REFERENCES agent_actions(id),
      grounding_score   NUMERIC,
      policy_risk_score NUMERIC,
      final_risk        NUMERIC,
      decision          TEXT,               -- 'allow' | 'block' | 'approve'
      approver_id       UUID REFERENCES users(id) NULL,
      resolved_at       TIMESTAMP NULL
    )
    """

    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    action_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agent_actions.id"), nullable=True
    )
    grounding_score: Mapped[float] = mapped_column(Numeric, nullable=True)
    policy_risk_score: Mapped[float] = mapped_column(Numeric, nullable=True)
    final_risk: Mapped[float] = mapped_column(Numeric, nullable=True)
    decision: Mapped[DecisionType] = mapped_column(
        SAEnum(
            DecisionType,
            name="decision_type",
            native_enum=True,
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    approver_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # relationships
    action: Mapped["AgentAction"] = relationship(back_populates="decisions")
    approver: Mapped["User"] = relationship(foreign_keys=[approver_id])
    audit_entries: Mapped[list["AuditLog"]] = relationship(back_populates="decision_rel")


class AuditLog(Base):
    """
    audit_log(
      id          UUID PRIMARY KEY,
      request_id  UUID,
      session_id  UUID REFERENCES chat_sessions(id),
      action_id   UUID REFERENCES agent_actions(id) NULL,
      decision_id UUID REFERENCES decisions(id) NULL,
      event       TEXT,   -- e.g. 'decision_made', 'grounding_unavailable', ...
      timestamp   TIMESTAMP
    )
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("chat_sessions.id"), nullable=True
    )
    action_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agent_actions.id"), nullable=True
    )
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("decisions.id"), nullable=True
    )
    event: Mapped[str] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )

    # relationships
    session: Mapped["ChatSession"] = relationship(back_populates="audit_entries")
    action: Mapped["AgentAction"] = relationship(back_populates="audit_entries")
    decision_rel: Mapped["Decision"] = relationship(back_populates="audit_entries")
