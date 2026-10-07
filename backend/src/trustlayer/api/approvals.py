"""Approval Queue and Resolution API endpoints for TrustLayer."""
import uuid
from datetime import datetime
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from trustlayer.agent.loop import execute_tool
from trustlayer.audit.logger import log_structured_event
from trustlayer.auth.dependencies import (
    DEFAULT_APPROVER_ID,
    get_current_user_and_role,
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
    proposed_at: str | None = None


class ApprovalActionRequest(BaseModel):
    reason: str | None = Field(default=None, description="Reason for the approval or rejection decision")
    role: str | None = Field(default=None, description="Optional caller role override")
    user_id: uuid.UUID | None = Field(default=None, description="Optional caller user ID")


class ApprovalActionResponse(BaseModel):
    approval_id: str
    decision: str
    status: str
    tool_name: str | None = None
    tool_result: dict[str, Any] | None = None
    approver_id: str | None = None
    resolved_at: str
    reason: str | None = None
    message: str


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
    # Build list of pending approval items for response payload
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
                proposed_at=action.proposed_at.isoformat() if action and action.proposed_at else None,
            )
        )
    return pending_items


async def _resolve_approver_identity(
    auth_header: dict[str, Any],
    payload: ApprovalActionRequest | None,
    db: AsyncSession,
) -> tuple[str, uuid.UUID]:
    # Determine effective role and user ID across headers and request body
    effective_role = auth_header.get("role")
    effective_user_id = auth_header.get("user_id")

    # Check request body fields if header credentials are not present
    if payload:
        if payload.user_id and not effective_user_id:
            effective_user_id = payload.user_id
            db_role = await resolve_role_for_user(payload.user_id, db)
            if db_role:
                effective_role = db_role
        if payload.role and not effective_role:
            effective_role = payload.role.strip().lower()

    # Enforce approver role restriction
    if not effective_role or effective_role.lower() != "approver":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Approver role required.",
        )

    approver_uuid = effective_user_id or DEFAULT_APPROVER_ID
    return effective_role.lower(), approver_uuid


@router.post("/{id}/approve", response_model=ApprovalActionResponse, status_code=status.HTTP_200_OK)
async def approve_held_action(
    id: uuid.UUID,
    payload: ApprovalActionRequest | None = None,
    auth_header: dict[str, Any] = Depends(get_current_user_and_role),
    db: AsyncSession = Depends(get_db_session),
) -> ApprovalActionResponse:
    # Validate approver authorization
    _, approver_uuid = await _resolve_approver_identity(auth_header, payload, db)

    # Fetch decision record by ID
    stmt = select(Decision).where(Decision.id == id)
    decision = (await db.execute(stmt)).scalar_one_or_none()
    if not decision:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval with ID {id} not found.",
        )

    # Reject resolution if already resolved to avoid double execution
    if decision.resolved_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Approval {id} has already been resolved at {decision.resolved_at.isoformat()}.",
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

    # Execute held tool action against mock ledger
    tool_result = await execute_tool(
        intent=action.tool_name or "",
        params=action.params_json or {},
        session=db,
        user_id=customer_user_id or approver_uuid,
    )

    # Update decision timestamp and approver identity
    now = datetime.utcnow()
    decision.resolved_at = now
    decision.approver_id = approver_uuid

    # Formulate resolution reason string
    reason = payload.reason if payload and payload.reason else "Approved by Senior Staff / Approver"

    # Write audit log row into database
    audit_entry = AuditLog(
        id=uuid.uuid4(),
        request_id=action.session_id,
        session_id=action.session_id,
        action_id=action.id,
        decision_id=decision.id,
        event=f"action_approved: actor={approver_uuid}, reason={reason}",
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
            "timestamp": now.isoformat() + "Z",
            "reason": reason,
            "decision_id": str(decision.id),
            "action_id": str(action.id),
            "tool_name": action.tool_name,
            "tool_result": tool_result,
        },
    )

    # Commit state changes and audit records to database
    await db.commit()

    return ApprovalActionResponse(
        approval_id=str(decision.id),
        decision="approve",
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
    auth_header: dict[str, Any] = Depends(get_current_user_and_role),
    db: AsyncSession = Depends(get_db_session),
) -> ApprovalActionResponse:
    # Validate approver authorization
    _, approver_uuid = await _resolve_approver_identity(auth_header, payload, db)

    # Fetch decision record by ID
    stmt = select(Decision).where(Decision.id == id)
    decision = (await db.execute(stmt)).scalar_one_or_none()
    if not decision:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval with ID {id} not found.",
        )

    # Reject resolution if already resolved to avoid double execution
    if decision.resolved_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Approval {id} has already been resolved at {decision.resolved_at.isoformat()}.",
        )

    # Fetch held agent action associated with this decision
    action_stmt = select(AgentAction).where(AgentAction.id == decision.action_id)
    action = (await db.execute(action_stmt)).scalar_one_or_none()

    # Tool action is explicitly never executed on rejection

    # Update decision timestamp and approver identity
    now = datetime.utcnow()
    decision.resolved_at = now
    decision.approver_id = approver_uuid

    # Formulate resolution reason string
    reason = payload.reason if payload and payload.reason else "Rejected by Senior Staff / Approver"

    # Write audit log row into database
    audit_entry = AuditLog(
        id=uuid.uuid4(),
        request_id=action.session_id if action else None,
        session_id=action.session_id if action else None,
        action_id=action.id if action else None,
        decision_id=decision.id,
        event=f"action_rejected: actor={approver_uuid}, reason={reason}",
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
            "timestamp": now.isoformat() + "Z",
            "reason": reason,
            "decision_id": str(decision.id),
            "action_id": str(action.id) if action else None,
            "tool_name": action.tool_name if action else None,
        },
    )

    # Commit state changes and audit records to database
    await db.commit()

    return ApprovalActionResponse(
        approval_id=str(decision.id),
        decision="reject",
        status="rejected",
        tool_name=action.tool_name if action else None,
        tool_result=None,
        approver_id=str(approver_uuid),
        resolved_at=now.isoformat() + "Z",
        reason=reason,
        message="Held action rejected. Tool action was not executed.",
    )
