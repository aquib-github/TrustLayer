"""
Sequence anomaly check module for TrustLayer (Module 2, Step 2).

Implements the rule-based sequence anomaly check described in docs/02 §4 and
knowledge base policy 8 (Internal Sequence Anomaly and Anti-Draining Rules):
  Automated monitoring flags account activity as a sequence anomaly when an
  account balance inquiry is followed within 5 minutes by a transfer or withdrawal
  request exceeding 85% of total account funds without intermediate re-authentication.

Returns:
  - sequence_anomaly_flag: bool
  - policy_risk: float (0.0 to 1.0)
  - reason: str
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trustlayer.db.models import Account, AgentAction, ChatMessage, ChatSession

logger = logging.getLogger(__name__)

# Compliance thresholds per docs/02 §4 and seed KB document 8
NEAR_FULL_BALANCE_THRESHOLD = 0.85  # Exceeding 85% of funds
INQUIRY_WINDOW_MINUTES = 5          # Within 5 minutes
SEQUENCE_ANOMALY_POLICY_RISK = 0.80 # Elevated policy risk for draining anomaly


async def check_sequence_anomaly(
    session: AsyncSession,
    user_id: uuid.UUID,
    session_id: uuid.UUID | None,
    tool_name: str | None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Evaluates proposed action for sequence anomalies against recent session history.

    Returns:
        {
            "flagged": bool,
            "anomaly_type": str | None,
            "policy_risk": float,
            "transfer_ratio": float | None,
            "current_balance": float | None,
            "reason": str,
        }
    """
    if tool_name != "transfer_funds":
        return {
            "flagged": False,
            "anomaly_type": None,
            "policy_risk": 0.0,
            "transfer_ratio": None,
            "current_balance": None,
            "reason": f"Tool '{tool_name}' is not subject to transfer sequence checks.",
        }

    params = params or {}
    try:
        transfer_amount = float(params.get("amount", 0.0))
    except (ValueError, TypeError):
        transfer_amount = 0.0

    if transfer_amount <= 0:
        return {
            "flagged": False,
            "anomaly_type": None,
            "policy_risk": 0.0,
            "transfer_ratio": 0.0,
            "current_balance": None,
            "reason": "Transfer amount is non-positive; handled by ledger validation.",
        }

    # 1. Fetch user's current account balance (checking or total)
    acc_stmt = select(Account).where(Account.user_id == user_id)
    acc_result = await session.execute(acc_stmt)
    accounts = acc_result.scalars().all()

    if not accounts:
        return {
            "flagged": False,
            "anomaly_type": None,
            "policy_risk": 0.0,
            "transfer_ratio": None,
            "current_balance": 0.0,
            "reason": "User has no accounts on file.",
        }

    # Look for checking account balance first, else total balance
    checking_acc = next((a for a in accounts if a.account_type == "checking"), None)
    total_balance = float(checking_acc.balance) if checking_acc else sum(float(a.balance) for a in accounts)

    if total_balance <= 0:
        transfer_ratio = 1.0
    else:
        transfer_ratio = round(transfer_amount / total_balance, 4)

    is_near_full_drain = transfer_ratio >= NEAR_FULL_BALANCE_THRESHOLD

    # 2. Check for recent balance inquiry within the last 5 minutes
    cutoff_time = datetime.utcnow() - timedelta(minutes=INQUIRY_WINDOW_MINUTES)
    has_recent_balance_check = False

    # Check via prior recorded agent_actions in DB
    action_stmt = (
        select(AgentAction)
        .join(ChatSession, AgentAction.session_id == ChatSession.id)
        .where(
            ChatSession.user_id == user_id,
            AgentAction.tool_name == "check_balance",
            AgentAction.proposed_at >= cutoff_time,
        )
        .order_by(AgentAction.proposed_at.desc())
        .limit(1)
    )
    action_res = await session.execute(action_stmt)
    if action_res.scalar_one_or_none():
        has_recent_balance_check = True

    # Also check recent chat_messages in session for balance query
    if not has_recent_balance_check and session_id:
        msg_stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.role == "user",
                ChatMessage.created_at >= cutoff_time,
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(5)
        )
        msg_res = await session.execute(msg_stmt)
        recent_messages = msg_res.scalars().all()
        for m in recent_messages:
            content_lower = (m.content or "").lower()
            if any(k in content_lower for k in ["balance", "how much", "what do i have", "account balance"]):
                has_recent_balance_check = True
                break

    # 3. Determine anomaly flag and policy risk
    if is_near_full_drain and has_recent_balance_check:
        logger.warning(
            "Sequence anomaly detected for user %s: balance check followed by %.1f%% transfer ($%.2f / $%.2f)",
            user_id, transfer_ratio * 100, transfer_amount, total_balance
        )
        return {
            "flagged": True,
            "anomaly_type": "balance_inquiry_followed_by_near_full_transfer",
            "policy_risk": SEQUENCE_ANOMALY_POLICY_RISK,
            "transfer_ratio": transfer_ratio,
            "current_balance": total_balance,
            "reason": (
                f"Sequence anomaly flagged: balance inquiry followed within {INQUIRY_WINDOW_MINUTES} min "
                f"by a transfer of ${transfer_amount:,.2f} ({transfer_ratio * 100:.1f}% of total balance) "
                f"without intermediate re-authentication."
            ),
        }

    # If it's a near full drain without explicit balance check, assign moderate risk
    if is_near_full_drain:
        return {
            "flagged": False,
            "anomaly_type": "high_ratio_transfer",
            "policy_risk": 0.35,
            "transfer_ratio": transfer_ratio,
            "current_balance": total_balance,
            "reason": (
                f"High-ratio transfer: ${transfer_amount:,.2f} is {transfer_ratio * 100:.1f}% "
                f"of account balance (${total_balance:,.2f})."
            ),
        }

    return {
        "flagged": False,
        "anomaly_type": None,
        "policy_risk": 0.0,
        "transfer_ratio": transfer_ratio,
        "current_balance": total_balance,
        "reason": f"Normal transfer volume (${transfer_amount:,.2f} / ${total_balance:,.2f}). No sequence anomaly.",
    }
