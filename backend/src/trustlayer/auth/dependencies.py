"""Authentication and role-gating dependencies for TrustLayer."""
import uuid
from typing import Any
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from trustlayer.db.models import Role, User
from trustlayer.db.session import get_db_session

# Seeded senior approver UUID for Eleanor Vance
DEFAULT_APPROVER_ID = uuid.UUID("66666666-6666-6666-6666-666666666666")


async def resolve_role_for_user(user_id: uuid.UUID, db: AsyncSession) -> str | None:
    # Query role name associated with the given user ID directly from DB
    stmt = (
        select(Role.name)
        .join(User, User.role_id == Role.id)
        .where(User.id == user_id)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def require_approver(
    x_user_id: str | None = Header(None, alias="X-User-Id"),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    # Require X-User-Id header as user identity
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: X-User-Id header required.",
        )

    try:
        user_uuid = uuid.UUID(str(x_user_id))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Invalid user ID format.",
        )

    # Derive role exclusively from the user's database record
    db_role = await resolve_role_for_user(user_uuid, db)
    if not db_role or db_role.lower() != "approver":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Approver role required.",
        )

    return {
        "user_id": user_uuid,
        "role": db_role.lower(),
    }
