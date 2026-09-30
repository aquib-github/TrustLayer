"""
Role-Based Access Control (RBAC) policy module for TrustLayer (Module 2, Step 1).

Implements role-permission checks per docs/05 Backend Data Schema roles table:
  - customer: end user; can perform standard account operations on their own accounts.
  - staff: bank staff; has elevated access for servicing customer accounts.
  - approver: senior staff / manager; full authority including override / sign-off actions.

Returns a hard-deny result if a role attempts an action outside its permitted set per docs/02 §4.
"""

from typing import Any

# The 5 primary banking tools in TrustLayer
TOOL_CHECK_BALANCE = "check_balance"
TOOL_GET_TRANSACTIONS = "get_transactions"
TOOL_TRANSFER_FUNDS = "transfer_funds"
TOOL_BLOCK_CARD = "block_card"
TOOL_DISPUTE_CHARGE = "dispute_charge"

# Administrative / elevated tools (staff / approver only)
TOOL_WAIVE_FEE = "waive_fee"
TOOL_INCREASE_LIMIT = "increase_limit"
TOOL_OVERRIDE_HOLD = "override_hold"

# Money-moving / financial risk tools per TRD §5
MONEY_MOVING_TOOLS = {
    TOOL_TRANSFER_FUNDS,
    TOOL_BLOCK_CARD,
    TOOL_DISPUTE_CHARGE,
    TOOL_WAIVE_FEE,
    TOOL_OVERRIDE_HOLD,
}

# Read-only tools
READ_ONLY_TOOLS = {
    TOOL_CHECK_BALANCE,
    TOOL_GET_TRANSACTIONS,
}

# Role permissions matrix per docs/05 and PRD §3.2
# 5 Core Banking Tools:
# - customer: check_balance, get_transactions, transfer_funds, block_card (dispute_charge is staff/approver only)
# - staff: check_balance, get_transactions, block_card, dispute_charge, waive_fee, increase_limit (transfer_funds restricted)
# - approver: all 5 core tools + administrative override tools
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "customer": {
        TOOL_CHECK_BALANCE,
        TOOL_GET_TRANSACTIONS,
        TOOL_TRANSFER_FUNDS,
        TOOL_BLOCK_CARD,
    },
    "staff": {
        TOOL_CHECK_BALANCE,
        TOOL_GET_TRANSACTIONS,
        TOOL_BLOCK_CARD,
        TOOL_DISPUTE_CHARGE,
        TOOL_WAIVE_FEE,
        TOOL_INCREASE_LIMIT,
    },
    "approver": {
        TOOL_CHECK_BALANCE,
        TOOL_GET_TRANSACTIONS,
        TOOL_TRANSFER_FUNDS,
        TOOL_BLOCK_CARD,
        TOOL_DISPUTE_CHARGE,
        TOOL_WAIVE_FEE,
        TOOL_INCREASE_LIMIT,
        TOOL_OVERRIDE_HOLD,
    },
}


def is_money_moving_tool(tool_name: str | None) -> bool:
    """Returns True if the tool performs financial or security-critical state changes."""
    return tool_name in MONEY_MOVING_TOOLS


def check_rbac(
    role: str | None,
    tool_name: str | None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Checks whether the requested role has permission to execute the proposed tool.

    Returns:
        {
            "allowed": bool,
            "hard_deny": bool,
            "role": str,
            "tool_name": str | None,
            "is_money_moving": bool,
            "reason": str,
        }
    """
    normalized_role = (role or "customer").lower().strip()
    tool = (tool_name or "").lower().strip()

    if not tool:
        # No tool call proposed (plain conversation) -> allowed
        return {
            "allowed": True,
            "hard_deny": False,
            "role": normalized_role,
            "tool_name": None,
            "is_money_moving": False,
            "reason": "No tool execution requested.",
        }

    money_moving = is_money_moving_tool(tool)

    # Check if role exists in our defined matrix
    if normalized_role not in ROLE_PERMISSIONS:
        return {
            "allowed": False,
            "hard_deny": True,
            "role": normalized_role,
            "tool_name": tool,
            "is_money_moving": money_moving,
            "reason": f"Unknown or unauthorized role '{normalized_role}'. Hard deny enforced.",
        }

    permitted_tools = ROLE_PERMISSIONS[normalized_role]

    if tool not in permitted_tools:
        return {
            "allowed": False,
            "hard_deny": True,
            "role": normalized_role,
            "tool_name": tool,
            "is_money_moving": money_moving,
            "reason": (
                f"Role '{normalized_role}' is not permitted to execute tool '{tool}'. "
                f"Hard deny enforced."
            ),
        }

    return {
        "allowed": True,
        "hard_deny": False,
        "role": normalized_role,
        "tool_name": tool,
        "is_money_moving": money_moving,
        "reason": f"Tool '{tool}' is permitted for role '{normalized_role}'.",
    }
