"""
Chat API endpoint for TrustLayer.

Provides POST /chat which receives user messages, runs the agent reasoning
loop with database-backed tool execution, and returns the response.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from trustlayer.agent.loop import run_agent_loop
from trustlayer.db.session import get_db_session

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    user_id: uuid.UUID = Field(..., description="UUID of the user sending the message")
    message: str = Field(..., description="Message text sent by the user", min_length=1)
    role: str | None = Field(default="customer", description="Optional user role override")


class ChatResponse(BaseModel):
    user_id: str
    role: str
    tool_called: str | None = None
    tool_params: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None
    response: str


@router.post("/chat", response_model=ChatResponse, status_code=status.HTTP_200_OK)
async def chat(
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db_session),
) -> ChatResponse:
    """
    POST /chat endpoint.
    Executes the agent loop with database session injection.
    """
    try:
        result = await run_agent_loop(
            session=db,
            user_id=payload.user_id,
            message=payload.message,
            role=payload.role or "customer",
        )
        return ChatResponse(
            user_id=result["user_id"],
            role=result["role"],
            tool_called=result["tool_called"],
            tool_params=result.get("tool_params"),
            tool_result=result.get("tool_result"),
            response=result["response"],
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent loop error: {str(exc)}",
        ) from exc
