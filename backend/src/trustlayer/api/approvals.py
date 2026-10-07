"""Approval Queue and Resolution API endpoints for TrustLayer."""
import uuid
from datetime import datetime
from typing import Any
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from trustlayer.agent.loop import execute_tool
from trustlayer.audit.logger import log_structured_event
from trustlayer.auth.dependencies import (
    DEFAULT_APPROVER_ID,
    require_approver,
    resolve_role_for_user,
)
from trustlayer.db.models import (
    AgentAction,
    AuditLog,
    ChatSession,
    Decision,
    DecisionType,
)
from trustlayer.db.session import get_db_session

# Router for all approval queue operations
router = APIRouter(prefix="/approvals", tags=["approvals"])


class PendingApprovalItem(BaseModel):
    decision_id: str
    action_id: str | None = None
    session_id: str | None = None
    tool_name: str | None = None
    params: dict[str, Any] | None = None
    grounding_score: float | None = None
    policy_risk_score: float | None = None
    final_risk: float | None = None
    decision: str
    resolution_status: str | None = "pending"
    proposed_at: str | None = None


class ApprovalActionRequest(BaseModel):
    reason: str | None = Field(default=None, description="Reason for the approval or rejection decision")
    user_id: uuid.UUID | None = Field(default=None, description="Optional user ID fallback if not supplied via X-User-Id header")


class ApprovalActionResponse(BaseModel):
    approval_id: str
    decision: str
    resolution_status: str
    status: str
    tool_name: str | None = None
    tool_result: dict[str, Any] | None = None
    approver_id: str
    resolved_at: str
    reason: str | None = None
    message: str


async def _resolve_approver(
    x_user_id: str | None,
    payload: ApprovalActionRequest | None,
    db: AsyncSession,
) -> uuid.UUID:
    # Resolve user ID exclusively from X-User-Id header or payload user_id
    raw_id = x_user_id or (str(payload.user_id) if payload and payload.user_id else None)
    if not raw_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: X-User-Id header required.",
        )

    try:
        uid = uuid.UUID(str(raw_id))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Invalid user ID format.",
        )

    # Derive role exclusively from the database record for this user
    role = await resolve_role_for_user(uid, db)
    if not role or role.lower() != "approver":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Approver role required.",
        )

    return uid


@router.get("/pending", response_model=list[PendingApprovalItem], status_code=status.HTTP_200_OK)
async def get_pending_approvals(
    limit: int = Query(default=50, ge=1, le=100, description="Maximum items to return"),
    offset: int = Query(default=0, ge=0, description="Offset for pagination"),
    auth: dict[str, Any] = Depends(require_approver),
    db: AsyncSession = Depends(get_db_session),
) -> list[PendingApprovalItem]:
    # Query pending unresolved approvals ordered newest first with pagination
    stmt = (
        select(Decision, AgentAction)
        .outerjoin(AgentAction, Decision.action_id == AgentAction.id)
        .where(
            Decision.decision == DecisionType.APPROVE,
            Decision.resolved_at.is_(None),
        )
        .order_by(AgentAction.proposed_at.desc().nullslast(), Decision.id.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.all()

    pending_items = []
    # Build list of pending approval items including resolution_status
    for dec, action in rows:
        pending_items.append(
            PendingApprovalItem(
                decision_id=str(dec.id),
                action_id=str(dec.action_id) if dec.action_id else None,
                session_id=str(action.session_id) if action else None,
                tool_name=action.tool_name if action else None,
                params=action.params_json if action else None,
                grounding_score=float(dec.grounding_score) if dec.grounding_score is not None else None,
                policy_risk_score=float(dec.policy_risk_score) if dec.policy_risk_score is not None else None,
                final_risk=float(dec.final_risk) if dec.final_risk is not None else None,
                decision=dec.decision.value if hasattr(dec.decision, "value") else str(dec.decision),
                resolution_status=dec.resolution_status or "pending",
                proposed_at=action.proposed_at.isoformat() if action and action.proposed_at else None,
            )
        )
    return pending_items


@router.post("/{id}/approve", response_model=ApprovalActionResponse, status_code=status.HTTP_200_OK)
async def approve_held_action(
    id: uuid.UUID,
    payload: ApprovalActionRequest | None = None,
    x_user_id: str | None = Header(None, alias="X-User-Id"),
    db: AsyncSession = Depends(get_db_session),
) -> ApprovalActionResponse:
    # Authenticate caller and derive approver role exclusively from database
    approver_uuid = await _resolve_approver(x_user_id, payload, db)

    # Verify that the decision record exists in the database
    check_stmt = select(Decision).where(Decision.id == id)
    decision = (await db.execute(check_stmt)).scalar_one_or_none()
    if not decision:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval with ID {id} not found.",
        )

    now = datetime.utcnow()
    reason = payload.reason if payload and payload.reason else "Approved by Senior Staff / Approver"

    # Claim the approval atomically using a conditional UPDATE
    claim_stmt = (
        update(Decision)
        .where(
            Decision.id == id,
            Decision.resolved_at.is_(None),
        )
        .values(
            resolved_at=now,
            approver_id=approver_uuid,
            resolution_status="approved",
        )
    )
    claim_result = await db.execute(claim_stmt)

    # If row was not updated, another concurrent request claimed it or it is already resolved
    if claim_result.rowcount != 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Approval {id} has already been resolved at {decision.resolved_at.isoformat() if decision.resolved_at else 'earlier'}.",
        )

    # Fetch held agent action associated with this decision
    action_stmt = select(AgentAction).where(AgentAction.id == decision.action_id)
    action = (await db.execute(action_stmt)).scalar_one_or_none()
    if not action:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Associated agent action for approval {id} not found.",
        )

    # Retrieve target customer user ID from chat session
    sess_stmt = select(ChatSession).where(ChatSession.id == action.session_id)
    chat_sess = (await db.execute(sess_stmt)).scalar_one_or_none()
    customer_user_id = chat_sess.user_id if chat_sess else None

    # Execute held tool action against mock ledger strictly after winning atomic claim
    tool_result = await execute_tool(
        intent=action.tool_name or "",
        params=action.params_json or {},
        session=db,
        user_id=customer_user_id or approver_uuid,
    )

    # Write structured audit log entry into database
    audit_entry = AuditLog(
        id=uuid.uuid4(),
        request_id=action.session_id,
        session_id=action.session_id,
        action_id=action.id,
        decision_id=decision.id,
        event=f"action_approved: actor={approver_uuid}, reason={reason}",
        event_type="action_approved",
        actor_id=approver_uuid,
        reason=reason,
        timestamp=now,
    )
    db.add(audit_entry)

    # Emit structured JSON audit log entry
    log_structured_event(
        request_id=str(action.session_id),
        stage="approval_resolution",
        event="action_approved",
        data={
            "actor": str(approver_uuid),
            "decision": "approve",
            "resolution_status": "approved",
            "timestamp": now.isoformat() + "Z",
            "reason": reason,
            "decision_id": str(decision.id),
            "action_id": str(action.id),
            "tool_name": action.tool_name,
            "tool_result": tool_result,
        },
    )

    # Commit all state changes and audit record to database
    await db.commit()

    return ApprovalActionResponse(
        approval_id=str(decision.id),
        decision="approve",
        resolution_status="approved",
        status="executed",
        tool_name=action.tool_name,
        tool_result=tool_result,
        approver_id=str(approver_uuid),
        resolved_at=now.isoformat() + "Z",
        reason=reason,
        message="Held action approved and executed successfully.",
    )


@router.post("/{id}/reject", response_model=ApprovalActionResponse, status_code=status.HTTP_200_OK)
async def reject_held_action(
    id: uuid.UUID,
    payload: ApprovalActionRequest | None = None,
    x_user_id: str | None = Header(None, alias="X-User-Id"),
    db: AsyncSession = Depends(get_db_session),
) -> ApprovalActionResponse:
    # Authenticate caller and derive approver role exclusively from database
    approver_uuid = await _resolve_approver(x_user_id, payload, db)

    # Verify that the decision record exists in the database
    check_stmt = select(Decision).where(Decision.id == id)
    decision = (await db.execute(check_stmt)).scalar_one_or_none()
    if not decision:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval with ID {id} not found.",
        )

    now = datetime.utcnow()
    reason = payload.reason if payload and payload.reason else "Rejected by Senior Staff / Approver"

    # Claim the rejection atomically using a conditional UPDATE
    claim_stmt = (
        update(Decision)
        .where(
            Decision.id == id,
            Decision.resolved_at.is_(None),
        )
        .values(
            resolved_at=now,
            approver_id=approver_uuid,
            resolution_status="rejected",
        )
    )
    claim_result = await db.execute(claim_stmt)

    # If row was not updated, another concurrent request claimed it or it is already resolved
    if claim_result.rowcount != 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Approval {id} has already been resolved at {decision.resolved_at.isoformat() if decision.resolved_at else 'earlier'}.",
        )

    # Fetch held agent action associated with this decision
    action_stmt = select(AgentAction).where(AgentAction.id == decision.action_id)
    action = (await db.execute(action_stmt)).scalar_one_or_none()

    # Tool action is explicitly never executed on rejection

    # Write structured audit log entry into database
    audit_entry = AuditLog(
        id=uuid.uuid4(),
        request_id=action.session_id if action else None,
        session_id=action.session_id if action else None,
        action_id=action.id if action else None,
        decision_id=decision.id,
        event=f"action_rejected: actor={approver_uuid}, reason={reason}",
        event_type="action_rejected",
        actor_id=approver_uuid,
        reason=reason,
        timestamp=now,
    )
    db.add(audit_entry)

    # Emit structured JSON audit log entry
    log_structured_event(
        request_id=str(action.session_id if action else decision.id),
        stage="approval_resolution",
        event="action_rejected",
        data={
            "actor": str(approver_uuid),
            "decision": "reject",
            "resolution_status": "rejected",
            "timestamp": now.isoformat() + "Z",
            "reason": reason,
            "decision_id": str(decision.id),
            "action_id": str(action.id) if action else None,
            "tool_name": action.tool_name if action else None,
        },
    )

    # Commit all state changes and audit record to database
    await db.commit()

    return ApprovalActionResponse(
        approval_id=str(decision.id),
        decision="reject",
        resolution_status="rejected",
        status="rejected",
        tool_name=action.tool_name if action else None,
        tool_result=None,
        approver_id=str(approver_uuid),
        resolved_at=now.isoformat() + "Z",
        reason=reason,
        message="Held action rejected. Tool action was not executed.",
    )
