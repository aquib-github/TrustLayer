"""
Audit and structured logging module for TrustLayer (Step 4).

Implements:
1. Structured JSON logger emitting correlation-ready logs with request_id.
2. Persistence function to write end-to-end decision records into:
   - agent_actions
   - claims
   - grounding_results
   - policy_checks
   - decisions
   - audit_log
All correlated by request_id and session_id per docs/05 §1 and TRD §8.
"""

import json
import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from trustlayer.db.models import (
    AgentAction,
    AuditLog,
    Claim,
    Decision,
    DecisionType,
    GroundingResult,
    PolicyCheck,
)

logger = logging.getLogger("trustlayer.audit")


def log_structured_event(
    request_id: uuid.UUID | str,
    stage: str,
    event: str,
    data: dict[str, Any] | None = None,
) -> None:
    """
    Emits a structured JSON log entry correlated by request_id.
    """
    payload = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "request_id": str(request_id),
        "stage": stage,
        "event": event,
        "details": data or {},
    }
    logger.info("AUDIT_LOG_JSON: %s", json.dumps(payload, default=str))


async def record_audit_pipeline(
    session: AsyncSession,
    request_id: uuid.UUID,
    session_id: uuid.UUID,
    tool_name: str | None,
    tool_params: dict[str, Any] | None,
    claims_data: list[dict[str, Any]] | None,
    grounding_data: dict[str, Any] | None,
    policy_data: dict[str, Any] | None,
    decision_data: dict[str, Any],
    event: str = "decision_made",
) -> tuple[uuid.UUID | None, uuid.UUID]:
    """
    Persists the end-to-end TrustLayer decision record across all audit tables.

    Returns:
        (decision_id, audit_log_id)
    """
    now = datetime.utcnow()
    action_id: uuid.UUID | None = None
    decision_id: uuid.UUID | None = None

    # 1. Record AgentAction if a tool was proposed
    if tool_name:
        action_obj = AgentAction(
            id=uuid.uuid4(),
            session_id=session_id,
            tool_name=tool_name,
            params_json=tool_params or {},
            proposed_at=now,
        )
        session.add(action_obj)
        action_id = action_obj.id

        # 2. Record Claims and Grounding Results
        if claims_data:
            for c_info in claims_data:
                claim_text = c_info.get("claim", "")
                if not claim_text:
                    continue

                claim_obj = Claim(
                    id=uuid.uuid4(),
                    action_id=action_id,
                    claim_text=claim_text,
                    extracted_at=now,
                )
                session.add(claim_obj)

                # Grounding verification for this specific claim
                retrieved_ids = c_info.get("retrieved_doc_ids") or []
                # Convert string doc_ids to UUIDs if needed
                uuid_doc_ids = []
                for d_id in retrieved_ids:
                    try:
                        uuid_doc_ids.append(uuid.UUID(str(d_id)))
                    except (ValueError, TypeError):
                        pass

                grounding_res_obj = GroundingResult(
                    id=uuid.uuid4(),
                    claim_id=claim_obj.id,
                    retrieved_doc_ids=uuid_doc_ids,
                    nli_label=c_info.get("best_nli_label") or c_info.get("status", "neutral").lower(),
                    grounding_score=Decimal(f"{float(c_info.get('entailment_prob', 0.0)):.4f}"),
                )
                session.add(grounding_res_obj)

        # 3. Record PolicyCheck
        policy_data = policy_data or {}
        policy_check_obj = PolicyCheck(
            id=uuid.uuid4(),
            action_id=action_id,
            rbac_result="allowed" if policy_data.get("rbac_allowed", True) else "denied",
            sequence_anomaly_flag=bool(policy_data.get("sequence_anomaly_flag", False)),
            policy_risk_score=Decimal(f"{float(policy_data.get('policy_risk', 0.0)):.4f}"),
        )
        session.add(policy_check_obj)

        # 4. Record Decision
        raw_decision_str = str(decision_data.get("decision", "block")).lower()
        if raw_decision_str == "allow":
            decision_enum = DecisionType.ALLOW
        elif raw_decision_str == "approve":
            decision_enum = DecisionType.APPROVE
        else:
            decision_enum = DecisionType.BLOCK

        decision_obj = Decision(
            id=uuid.uuid4(),
            action_id=action_id,
            grounding_score=Decimal(f"{float(decision_data.get('grounding_score', 0.0)):.4f}"),
            policy_risk_score=Decimal(f"{float(decision_data.get('policy_risk', 0.0)):.4f}"),
            final_risk=Decimal(f"{float(decision_data.get('final_risk', 1.0)):.4f}"),
            decision=decision_enum,
            approver_id=None,
            resolved_at=None,
        )
        session.add(decision_obj)
        decision_id = decision_obj.id

    # 5. Record primary AuditLog row
    audit_log_obj = AuditLog(
        id=uuid.uuid4(),
        request_id=request_id,
        session_id=session_id,
        action_id=action_id,
        decision_id=decision_id,
        event=event or decision_data.get("event", "decision_made"),
        timestamp=now,
    )
    session.add(audit_log_obj)

    # Flush/commit records to DB
    await session.commit()
    await session.refresh(audit_log_obj)

    log_structured_event(
        request_id=request_id,
        stage="audit_persistence",
        event="audit_entry_created",
        data={
            "audit_log_id": str(audit_log_obj.id),
            "session_id": str(session_id),
            "action_id": str(action_id) if action_id else None,
            "decision_id": str(decision_id) if decision_id else None,
            "decision": decision_data.get("decision"),
            "final_risk": decision_data.get("final_risk"),
        },
    )

    return decision_id, audit_log_obj.id
