"""
Chat API and Approval Queue endpoints for TrustLayer.

Provides:
- POST /chat: receives user messages, executes intercepted agent loop, returns decision and response.
- GET /approvals/pending: lists unresolved actions routed to Senior Staff / Approver.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trustlayer.agent.loop import run_agent_loop
from trustlayer.db.models import AgentAction, Decision, DecisionType
from trustlayer.db.session import get_db_session

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    user_id: uuid.UUID = Field(..., description="UUID of the user sending the message")
    message: str = Field(..., description="Message text sent by the user", min_length=1)
    role: str | None = Field(default="customer", description="Optional user role override ('customer', 'staff', 'approver')")
    session_id: uuid.UUID | None = Field(default=None, description="Optional chat session ID for multi-turn context")
    request_id: uuid.UUID | None = Field(default=None, description="Optional request correlation ID")


class ChatResponse(BaseModel):
    request_id: str
    session_id: str
    user_id: str
    role: str
    decision: str
    final_risk: float
    grounding_score: float | None = None
    policy_risk: float | None = None
    tool_called: str | None = None
    tool_params: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None
    decision_id: str | None = None
    audit_log_id: str | None = None
    response: str


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


@router.post("/chat", response_model=ChatResponse, status_code=status.HTTP_200_OK)
async def chat(
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db_session),
) -> ChatResponse:
    """
    POST /chat endpoint.
    Executes the agent loop with TrustLayer Interception Firewall.
    """
    try:
        result = await run_agent_loop(
            session=db,
            user_id=payload.user_id,
            message=payload.message,
            role=payload.role or "customer",
            session_id=payload.session_id,
            request_id=payload.request_id,
        )
        return ChatResponse(
            request_id=result["request_id"],
            session_id=result["session_id"],
            user_id=result["user_id"],
            role=result["role"],
            decision=result["decision"],
            final_risk=result["final_risk"],
            grounding_score=result.get("grounding_score"),
            policy_risk=result.get("policy_risk"),
            tool_called=result.get("tool_called"),
            tool_params=result.get("tool_params"),
            tool_result=result.get("tool_result"),
            decision_id=result.get("decision_id"),
            audit_log_id=result.get("audit_log_id"),
            response=result["response"],
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent loop error: {str(exc)}",
        ) from exc


@router.get("/approvals/pending", response_model=list[PendingApprovalItem], status_code=status.HTTP_200_OK)
async def get_pending_approvals(
    db: AsyncSession = Depends(get_db_session),
) -> list[PendingApprovalItem]:
    """
    GET /approvals/pending endpoint.
    Returns all unresolved actions queued for Senior Staff / Approver review.
    """
    try:
        stmt = (
            select(Decision, AgentAction)
            .outerjoin(AgentAction, Decision.action_id == AgentAction.id)
            .where(
                Decision.decision == DecisionType.APPROVE,
                Decision.resolved_at.is_(None),
            )
            .order_by(Decision.id.desc())
        )
        result = await db.execute(stmt)
        rows = result.all()

        pending_items = []
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
                )
            )
        return pending_items
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch pending approvals: {str(exc)}",
        ) from exc
