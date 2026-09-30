"""
Decision Engine module for TrustLayer (Step 3).

Implements the decision logic and risk fusion from docs/02 TRD §5 and §6:
1. Hard overrides:
   - RBAC hard deny -> BLOCK, unconditionally.
   - grounding_score < 0.4 on any money-moving action -> minimum APPROVE, never ALLOW outright.
2. Weighted risk score:
   final_risk = 0.5 * grounding_risk + 0.5 * policy_risk
   where grounding_risk = 1.0 - grounding_score.
3. Decision cutlines:
   - final_risk < 0.3          -> ALLOW
   - 0.3 <= final_risk <= 0.6  -> APPROVE (routes to Senior Staff/Approver)
   - final_risk > 0.6           -> BLOCK
4. Fallback behavior (docs/02 §6):
   - Policy engine failure -> fail-closed BLOCK ('policy_engine_error').
   - Grounding failure -> on money-moving: grounding_score = 0.0 -> minimum APPROVE.
     on read-only: fail-open with logged warning ('grounding_unavailable').
"""

import logging
from typing import Any

from trustlayer.policy.rbac import is_money_moving_tool

logger = logging.getLogger(__name__)

# Decision thresholds per docs/02 §5
ALLOW_THRESHOLD = 0.30
APPROVE_THRESHOLD = 0.60
MONEY_MOVING_GROUNDING_FLOOR = 0.40
FLOOR_OVERRIDE_TRIGGER_COUNT: int = 0


def evaluate_decision(
    grounding_score: float | None,
    policy_risk: float,
    rbac_allowed: bool = True,
    rbac_hard_deny: bool = False,
    tool_name: str | None = None,
    grounding_error: str | None = None,
    policy_error: str | None = None,
) -> dict[str, Any]:
    """
    Evaluates TrustLayer multi-source risk and enforces policy boundaries.

    Returns:
        {
            "decision": "allow" | "block" | "approve",
            "final_risk": float,
            "grounding_score": float,
            "grounding_risk": float,
            "policy_risk": float,
            "override_applied": bool,
            "override_reason": str | None,
            "event": str,
            "reason": str,
        }
    """
    is_money_moving = is_money_moving_tool(tool_name)
    event = "decision_made"
    override_applied = False
    override_reason: str | None = None

    # ── Fallback 1: Policy Engine Failure (Fail-Closed) ───────────────────
    if policy_error is not None:
        logger.error("Policy engine failure reported: %s. Enforcing fail-closed BLOCK.", policy_error)
        return {
            "decision": "block",
            "final_risk": 1.0,
            "grounding_score": round(grounding_score if grounding_score is not None else 0.0, 4),
            "grounding_risk": 1.0,
            "policy_risk": 1.0,
            "override_applied": True,
            "override_reason": f"Fail-closed safety override: policy engine error ({policy_error})",
            "event": "policy_engine_error",
            "reason": "Policy verification infrastructure failed. Request blocked by fail-closed policy.",
        }

    # ── Fallback 2: Grounding Failure / Unavailable ───────────────────────
    if grounding_error is not None or grounding_score is None:
        event = "grounding_unavailable"
        if is_money_moving:
            logger.warning(
                "Grounding unavailable for money-moving tool '%s'. Setting grounding_score=0.0 (minimum APPROVE floor).",
                tool_name,
            )
            grounding_score = 0.0
        else:
            # Read-only actions (check_balance, get_transactions, or no tool) fail-open with logged warning
            logger.info("Grounding unavailable for read-only action '%s'. Failing-open with logged warning.", tool_name)
            grounding_score = 1.0

    # Ensure grounding_score and policy_risk are bounded in [0.0, 1.0]
    grounding_score = max(0.0, min(1.0, float(grounding_score)))
    policy_risk = max(0.0, min(1.0, float(policy_risk)))
    grounding_risk = round(1.0 - grounding_score, 4)

    # ── Step 1: Hard Overrides (checked before weighted score) ────────────

    # Override 1: RBAC Hard Deny -> BLOCK unconditionally
    if rbac_hard_deny or not rbac_allowed:
        logger.warning("Hard override triggered: RBAC violation for tool '%s'.", tool_name)
        return {
            "decision": "block",
            "final_risk": 1.0,
            "grounding_score": grounding_score,
            "grounding_risk": grounding_risk,
            "policy_risk": 1.0,
            "override_applied": True,
            "override_reason": f"RBAC hard deny: role unauthorized for tool '{tool_name}'",
            "event": event,
            "reason": f"Action blocked: role does not have authorization to execute tool '{tool_name}'.",
        }

    # Floor trigger check: grounding_score < 0.4 on money-moving action
    money_moving_floor_active = is_money_moving and (grounding_score < MONEY_MOVING_GROUNDING_FLOOR)

    # ── Step 2: Weighted Risk Fusion ──────────────────────────────────────
    # final_risk = 0.5 * grounding_risk + 0.5 * policy_risk
    raw_final_risk = 0.5 * grounding_risk + 0.5 * policy_risk
    final_risk = round(max(0.0, min(1.0, raw_final_risk)), 4)

    # ── Step 3: Threshold Evaluation ──────────────────────────────────────
    if final_risk > APPROVE_THRESHOLD:
        decision = "block"
        reason = f"Combined risk ({final_risk:.4f}) exceeds block threshold (> {APPROVE_THRESHOLD})."
    elif final_risk >= ALLOW_THRESHOLD:
        decision = "approve"
        reason = (
            f"Combined risk ({final_risk:.4f}) requires human sign-off "
            f"({ALLOW_THRESHOLD} <= risk <= {APPROVE_THRESHOLD})."
        )
    else:
        # final_risk < ALLOW_THRESHOLD (< 0.3)
        # NOTE ON MATHEMATICAL REDUNDANCY:
        # Under current weights (0.5 * grounding_risk + 0.5 * policy_risk) and ALLOW_THRESHOLD = 0.30:
        # If grounding_score < 0.40, then grounding_risk = (1.0 - grounding_score) > 0.60.
        # Since policy_risk >= 0.0, raw_final_risk = 0.5 * grounding_risk + 0.5 * policy_risk
        # strictly satisfies: raw_final_risk > 0.5 * 0.60 + 0 = 0.30.
        # Therefore, final_risk is ALWAYS >= 0.30 whenever grounding_score < 0.40,
        # which means the weighted formula alone guarantees an outcome of APPROVE (or BLOCK),
        # and final_risk can NEVER be < 0.30 to reach this 'else' branch.
        # This floor override block is currently mathematically redundant with the weighted formula,
        # but is explicitly retained for defense-in-depth and future unequal weighting scenarios
        # (e.g. if grounding weight were reduced below 0.5, or policy_risk had negative offsets).
        if money_moving_floor_active:
            global FLOOR_OVERRIDE_TRIGGER_COUNT
            FLOOR_OVERRIDE_TRIGGER_COUNT += 1
            decision = "approve"
            override_applied = True
            override_reason = (
                f"Floor override: grounding_score ({grounding_score:.4f}) < {MONEY_MOVING_GROUNDING_FLOOR} "
                f"on money-moving action '{tool_name}' forces minimum APPROVE."
            )
            reason = (
                f"Action requires review: grounding confidence ({grounding_score:.4f}) is below {MONEY_MOVING_GROUNDING_FLOOR} "
                f"for financial action '{tool_name}'."
            )
        else:
            decision = "allow"
            reason = f"Combined risk ({final_risk:.4f}) is clean (< {ALLOW_THRESHOLD}). Action permitted."

    return {
        "decision": decision,
        "final_risk": final_risk,
        "grounding_score": grounding_score,
        "grounding_risk": grounding_risk,
        "policy_risk": policy_risk,
        "override_applied": override_applied,
        "override_reason": override_reason,
        "event": event,
        "reason": reason,
    }
