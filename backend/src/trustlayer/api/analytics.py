"""Dashboard Summary and Audit Log API endpoints for TrustLayer."""
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from trustlayer.auth.dependencies import require_staff_or_approver
from trustlayer.db.models import (
    AgentAction,
    AuditLog,
    ChatSession,
    Decision,
    DecisionType,
    Role,
    User,
)
from trustlayer.db.session import get_db_session

# Router for dashboard and audit log operations
router = APIRouter(tags=["analytics"])


# ── Response models ──────────────────────────────────────────────────────

class DecisionBreakdown(BaseModel):
    allow: int = 0
    approve: int = 0
    block: int = 0


class RoleBreakdown(BaseModel):
    customer: int = 0
    staff: int = 0
    approver: int = 0


class ToolBreakdown(BaseModel):
    tool_name: str
    count: int


class DecisionAvgScores(BaseModel):
    decision: str
    avg_risk: float | None = None
    avg_grounding: float | None = None


class DashboardSummaryResponse(BaseModel):
    total_decisions: int
    by_decision: DecisionBreakdown
    by_role: RoleBreakdown
    by_tool: list[ToolBreakdown]
    avg_scores_per_decision: list[DecisionAvgScores]
    date_from: str | None = None
    date_to: str | None = None


class AuditLogItem(BaseModel):
    audit_id: str
    request_id: str | None = None
    session_id: str | None = None
    action_id: str | None = None
    decision_id: str | None = None
    event: str | None = None
    event_type: str | None = None
    actor_id: str | None = None
    reason: str | None = None
    timestamp: str | None = None
    decision: str | None = None
    tool_name: str | None = None
    grounding_score: float | None = None
    policy_risk_score: float | None = None
    final_risk: float | None = None
    user_role: str | None = None


class AuditLogResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AuditLogItem]


# ── GET /dashboard/summary ───────────────────────────────────────────────

@router.get(
    "/dashboard/summary",
    response_model=DashboardSummaryResponse,
    status_code=status.HTTP_200_OK,
)
async def get_dashboard_summary(
    date_from: str | None = Query(default=None, alias="from", description="ISO date filter start (inclusive)"),
    date_to: str | None = Query(default=None, alias="to", description="ISO date filter end (inclusive)"),
    auth: dict[str, Any] = Depends(require_staff_or_approver),
    db: AsyncSession = Depends(get_db_session),
) -> DashboardSummaryResponse:
    # Parse optional date filters into datetime bounds
    dt_from = _parse_date(date_from)
    dt_to = _parse_date(date_to, end_of_day=True)

    # Base condition joining decisions to agent_actions for timestamp filtering
    date_conditions = []
    if dt_from:
        date_conditions.append(AgentAction.proposed_at >= dt_from)
    if dt_to:
        date_conditions.append(AgentAction.proposed_at <= dt_to)

    # 1. Aggregate counts by decision type using SQL CASE expressions
    by_decision_stmt = (
        select(
            func.count().label("total"),
            func.sum(case((Decision.decision == DecisionType.ALLOW, 1), else_=0)).label("allow_count"),
            func.sum(case((Decision.decision == DecisionType.APPROVE, 1), else_=0)).label("approve_count"),
            func.sum(case((Decision.decision == DecisionType.BLOCK, 1), else_=0)).label("block_count"),
        )
        .select_from(Decision)
        .outerjoin(AgentAction, Decision.action_id == AgentAction.id)
    )
    for cond in date_conditions:
        by_decision_stmt = by_decision_stmt.where(cond)
    row = (await db.execute(by_decision_stmt)).one()
    total_decisions = row.total or 0
    by_decision = DecisionBreakdown(
        allow=row.allow_count or 0,
        approve=row.approve_count or 0,
        block=row.block_count or 0,
    )

    # 2. Aggregate counts by user role via session->user->role join
    by_role_stmt = (
        select(Role.name, func.count().label("cnt"))
        .select_from(Decision)
        .join(AgentAction, Decision.action_id == AgentAction.id)
        .join(ChatSession, AgentAction.session_id == ChatSession.id)
        .join(User, ChatSession.user_id == User.id)
        .join(Role, User.role_id == Role.id)
    )
    for cond in date_conditions:
        by_role_stmt = by_role_stmt.where(cond)
    by_role_stmt = by_role_stmt.group_by(Role.name)
    role_rows = (await db.execute(by_role_stmt)).all()
    by_role = RoleBreakdown()
    for rr in role_rows:
        if rr.name == "customer":
            by_role.customer = rr.cnt
        elif rr.name == "staff":
            by_role.staff = rr.cnt
        elif rr.name == "approver":
            by_role.approver = rr.cnt

    # 3. Aggregate counts by tool name
    by_tool_stmt = (
        select(AgentAction.tool_name, func.count().label("cnt"))
        .select_from(Decision)
        .join(AgentAction, Decision.action_id == AgentAction.id)
    )
    for cond in date_conditions:
        by_tool_stmt = by_tool_stmt.where(cond)
    by_tool_stmt = by_tool_stmt.group_by(AgentAction.tool_name).order_by(func.count().desc())
    tool_rows = (await db.execute(by_tool_stmt)).all()
    by_tool = [ToolBreakdown(tool_name=tr.tool_name or "unknown", count=tr.cnt) for tr in tool_rows]

    # 4. Average risk and grounding scores per decision type
    avg_stmt = (
        select(
            Decision.decision,
            func.avg(Decision.final_risk).label("avg_risk"),
            func.avg(Decision.grounding_score).label("avg_grounding"),
        )
        .select_from(Decision)
        .outerjoin(AgentAction, Decision.action_id == AgentAction.id)
    )
    for cond in date_conditions:
        avg_stmt = avg_stmt.where(cond)
    avg_stmt = avg_stmt.group_by(Decision.decision)
    avg_rows = (await db.execute(avg_stmt)).all()
    avg_scores = [
        DecisionAvgScores(
            decision=ar.decision.value if hasattr(ar.decision, "value") else str(ar.decision),
            avg_risk=round(float(ar.avg_risk), 4) if ar.avg_risk is not None else None,
            avg_grounding=round(float(ar.avg_grounding), 4) if ar.avg_grounding is not None else None,
        )
        for ar in avg_rows
    ]

    return DashboardSummaryResponse(
        total_decisions=total_decisions,
        by_decision=by_decision,
        by_role=by_role,
        by_tool=by_tool,
        avg_scores_per_decision=avg_scores,
        date_from=date_from,
        date_to=date_to,
    )


# ── GET /audit ───────────────────────────────────────────────────────────

@router.get(
    "/audit",
    response_model=AuditLogResponse,
    status_code=status.HTTP_200_OK,
)
async def get_audit_log(
    limit: int = Query(default=50, ge=1, le=100, description="Maximum items to return"),
    offset: int = Query(default=0, ge=0, description="Offset for pagination"),
    decision: str | None = Query(default=None, description="Filter by decision type: allow, approve, block"),
    role: str | None = Query(default=None, description="Filter by originating user role"),
    tool: str | None = Query(default=None, description="Filter by tool name"),
    actor_id: str | None = Query(default=None, description="Filter by actor UUID"),
    date_from: str | None = Query(default=None, alias="from", description="ISO date filter start"),
    date_to: str | None = Query(default=None, alias="to", description="ISO date filter end"),
    auth: dict[str, Any] = Depends(require_staff_or_approver),
    db: AsyncSession = Depends(get_db_session),
) -> AuditLogResponse:
    # Build base query joining audit_log to decision, action, session, user, role
    base = (
        select(
            AuditLog,
            Decision.decision.label("dec_decision"),
            Decision.grounding_score.label("dec_grounding"),
            Decision.policy_risk_score.label("dec_policy_risk"),
            Decision.final_risk.label("dec_final_risk"),
            AgentAction.tool_name.label("act_tool"),
            Role.name.label("user_role"),
        )
        .select_from(AuditLog)
        .outerjoin(Decision, AuditLog.decision_id == Decision.id)
        .outerjoin(AgentAction, AuditLog.action_id == AgentAction.id)
        .outerjoin(ChatSession, AuditLog.session_id == ChatSession.id)
        .outerjoin(User, ChatSession.user_id == User.id)
        .outerjoin(Role, User.role_id == Role.id)
    )

    # Apply optional filters
    dt_from = _parse_date(date_from)
    dt_to = _parse_date(date_to, end_of_day=True)
    if dt_from:
        base = base.where(AuditLog.timestamp >= dt_from)
    if dt_to:
        base = base.where(AuditLog.timestamp <= dt_to)
    if decision:
        base = base.where(Decision.decision == _to_decision_enum(decision))
    if role:
        base = base.where(Role.name == role.lower())
    if tool:
        base = base.where(AgentAction.tool_name == tool)
    if actor_id:
        try:
            actor_uuid = uuid.UUID(str(actor_id))
            base = base.where(AuditLog.actor_id == actor_uuid)
        except (ValueError, TypeError):
            pass

    # Count total matching rows before pagination
    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # Fetch paginated rows ordered newest first
    data_stmt = base.order_by(AuditLog.timestamp.desc().nullslast(), AuditLog.id.desc()).offset(offset).limit(limit)
    rows = (await db.execute(data_stmt)).all()

    items = []
    for row in rows:
        audit: AuditLog = row[0]
        items.append(
            AuditLogItem(
                audit_id=str(audit.id),
                request_id=str(audit.request_id) if audit.request_id else None,
                session_id=str(audit.session_id) if audit.session_id else None,
                action_id=str(audit.action_id) if audit.action_id else None,
                decision_id=str(audit.decision_id) if audit.decision_id else None,
                event=audit.event,
                event_type=audit.event_type,
                actor_id=str(audit.actor_id) if audit.actor_id else None,
                reason=audit.reason,
                timestamp=audit.timestamp.isoformat() + "Z" if audit.timestamp else None,
                decision=row.dec_decision.value if row.dec_decision and hasattr(row.dec_decision, "value") else (str(row.dec_decision) if row.dec_decision else None),
                tool_name=row.act_tool,
                grounding_score=round(float(row.dec_grounding), 4) if row.dec_grounding is not None else None,
                policy_risk_score=round(float(row.dec_policy_risk), 4) if row.dec_policy_risk is not None else None,
                final_risk=round(float(row.dec_final_risk), 4) if row.dec_final_risk is not None else None,
                user_role=row.user_role,
            )
        )

    return AuditLogResponse(total=total, limit=limit, offset=offset, items=items)


# ── Helpers ──────────────────────────────────────────────────────────────

def _parse_date(raw: str | None, end_of_day: bool = False) -> datetime | None:
    # Parse an ISO date string into a datetime, optionally set to end of day
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00").replace("z", "+00:00"))
        if end_of_day and dt.hour == 0 and dt.minute == 0 and dt.second == 0:
            dt = dt.replace(hour=23, minute=59, second=59)
        return dt
    except (ValueError, TypeError):
        return None


def _to_decision_enum(raw: str) -> DecisionType:
    # Convert a raw string to the DecisionType enum value
    mapping = {"allow": DecisionType.ALLOW, "approve": DecisionType.APPROVE, "block": DecisionType.BLOCK}
    return mapping.get(raw.lower(), DecisionType.BLOCK)
