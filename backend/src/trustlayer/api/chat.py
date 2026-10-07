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


from trustlayer.api.approvals import PendingApprovalItem


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

