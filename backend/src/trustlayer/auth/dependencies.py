"""Authentication and role-gating dependencies for TrustLayer."""
import uuid
from typing import Any
from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from trustlayer.db.models import Role, User
from trustlayer.db.session import get_db_session

# Seeded senior approver UUID for Eleanor Vance
DEFAULT_APPROVER_ID = uuid.UUID("66666666-6666-6666-6666-666666666666")


async def resolve_role_for_user(user_id: uuid.UUID, db: AsyncSession) -> str | None:
    # Query role name associated with the given user ID
    stmt = (
        select(Role.name)
        .join(User, User.role_id == Role.id)
        .where(User.id == user_id)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_current_user_and_role(
    x_user_role: str | None = Header(None, alias="X-User-Role"),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
    role: str | None = Query(None),
    user_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    # Extract candidate role and user ID from headers or query parameters
    raw_user_id = x_user_id or user_id
    raw_role = x_user_role or role

    resolved_role: str | None = None
    resolved_user_id: uuid.UUID | None = None

    # Resolve role from database if user ID is supplied
    if raw_user_id:
        try:
            resolved_user_id = uuid.UUID(str(raw_user_id))
            db_role = await resolve_role_for_user(resolved_user_id, db)
            if db_role:
                resolved_role = db_role
        except (ValueError, TypeError):
            pass

    # Fall back to raw role string if database lookup is not available
    if raw_role and not resolved_role:
        resolved_role = raw_role.strip().lower()

    return {
        "user_id": resolved_user_id,
        "role": resolved_role,
    }


async def require_approver(
    auth_info: dict[str, Any] = Depends(get_current_user_and_role),
) -> dict[str, Any]:
    # Restrict endpoint to users carrying the approver role
    role = auth_info.get("role")
    if not role or role.lower() != "approver":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Approver role required.",
        )
    return auth_info
