"""
Agent reasoning loop with TrustLayer Interception Firewall.

Implements the end-to-end flow from docs/03 App Request Flow §1:
1. Receives message + session context.
2. Detects intent and proposes tool call + parameters.
3. TrustLayer Interception Layer (with request_id):
   - Parallel Branch A (Grounding): claims extracted -> retrieved evidence -> NLI verification -> grounding_score
   - Parallel Branch B (Policy): proposed tool checked against RBAC + sequence-anomaly check -> policy_risk
   - Decision Engine: applies hard overrides, then weighted risk formula -> ALLOW / BLOCK / APPROVE
4. Enforcement:
   - If ALLOW: execute tool against mock bank ledger DB -> format result
   - If BLOCK: action not executed -> safe fallback response
   - If APPROVE: action not executed -> queued in decisions table for Senior Staff / Approver
5. Always: decision + full context persisted to audit_log, claims, policy_checks, decisions.
"""

import logging
import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trustlayer.agent import tools
from trustlayer.audit.logger import log_structured_event, record_audit_pipeline
from trustlayer.db.models import ChatMessage, ChatSession, Role, User
from trustlayer.decision.engine import evaluate_decision
from trustlayer.grounding.extract import extract_claims
from trustlayer.grounding.retrieve import retrieve_evidence
from trustlayer.grounding.verify import (
    calculate_grounding_score,
    verify_claim_against_chunks,
)
from trustlayer.policy.rbac import check_rbac
from trustlayer.policy.sequence_check import check_sequence_anomaly

logger = logging.getLogger(__name__)


def detect_intent(message: str) -> tuple[str | None, dict[str, Any]]:
    """
    Rule-based intent classifier and argument extractor.
    Returns (intent_name, extracted_arguments).
    """
    msg = message.lower().strip()

    # 1. Dispute Charge (highest specificity)
    if any(kw in msg for kw in ["dispute", "fraud", "unauthorized charge", "chargeback"]):
        uuid_match = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", msg)
        tx_id = uuid_match.group(1) if uuid_match else None
        return "dispute_charge", {
            "transaction_id": tx_id,
            "reason": "unauthorized_transaction_reported_by_customer",
        }

    # 2. Block Card
    if any(kw in msg for kw in ["block card", "lost card", "freeze card", "stolen card", "lock card"]):
        return "block_card", {"reason": "customer_reported_lost_or_stolen"}

    # 3. Transfer Funds
    if any(kw in msg for kw in ["transfer", "send money", "send $", "pay "]):
        amount = 50.0  # default amount if unspecified
        amount_match = re.search(r"\$?\s*([0-9]+(?:\.[0-9]{1,2})?)\s*(?:dollars|\$)?", msg)
        if amount_match:
            try:
                parsed_amount = float(amount_match.group(1))
                if parsed_amount > 0:
                    amount = parsed_amount
            except ValueError:
                pass

        recipient = None
        to_match = re.search(r"(?:to|for)\s+([a-zA-Z0-9_\-\s]+?)(?:\s+from|\s+for|\.|$)", msg)
        if to_match:
            candidate = to_match.group(1).strip()
            if candidate not in ["savings", "checking", "account"]:
                recipient = candidate

        return "transfer_funds", {
            "amount": amount,
            "recipient": recipient or "external recipient",
            "from_account_type": "checking",
        }

    # 4. Check Balance
    if any(kw in msg for kw in ["balance", "how much money", "account balance", "check balance", "what do i have"]):
        acc_type = None
        if "saving" in msg:
            acc_type = "savings"
        elif "checking" in msg:
            acc_type = "checking"
        return "check_balance", {"account_type": acc_type}

    # 5. Get Transactions
    if any(kw in msg for kw in ["transaction", "statement", "history", "recent activity", "recent payments"]):
        acc_type = None
        if "saving" in msg:
            acc_type = "savings"
        elif "checking" in msg:
            acc_type = "checking"
        limit_match = re.search(r"last\s+(\d+)", msg)
        limit = int(limit_match.group(1)) if limit_match else 5
        return "get_transactions", {"limit": limit, "account_type": acc_type}

    return None, {}


def format_response(intent: str | None, tool_result: dict[str, Any] | None, user_message: str) -> str:
    """
    Format tool execution output into a natural-language assistant response.
    """
    if not intent or not tool_result:
        return (
            "Hello! I am your TrustLayer banking assistant. I can help you with:\n"
            "• Checking your account balances\n"
            "• Viewing recent transactions\n"
            "• Transferring funds\n"
            "• Blocking lost or compromised cards\n"
            "• Disputing unauthorized charges\n\n"
            "How can I assist you today?"
        )

    if tool_result.get("status") == "error":
        return f"I encountered an issue processing your request: {tool_result.get('message', 'Unknown error')}."

    if intent == "check_balance":
        accounts = tool_result.get("accounts", [])
        if not accounts:
            return "You currently do not have any active bank accounts on file."
        acc_summaries = [f"{acc['account_type'].capitalize()}: ${acc['balance']:,.2f}" for acc in accounts]
        total = tool_result.get("total_balance", 0.0)
        return (
            f"Here is your current balance breakdown:\n"
            f"• " + "\n• ".join(acc_summaries) + "\n\n"
            f"Total available balance: ${total:,.2f}."
        )

    if intent == "get_transactions":
        txs = tool_result.get("transactions", [])
        if not txs:
            return "No recent transactions found for your accounts."
        lines = []
        for t in txs:
            date_str = t["created_at"][:10] if t.get("created_at") else "Recent"
            lines.append(f"• [{date_str}] {t['type'].upper()}: ${t['amount']:,.2f} ({t['status']}) - ID: {t['id'][:8]}...")
        return "Here are your recent transactions:\n" + "\n".join(lines)

    if intent == "transfer_funds":
        if tool_result.get("status") == "failed":
            return f"Unable to complete transfer: {tool_result.get('message', 'Insufficient funds')}."
        amount = tool_result.get("amount", 0.0)
        new_bal = tool_result.get("new_balance", 0.0)
        recipient = tool_result.get("recipient", "recipient")
        tx_id = tool_result.get("transaction_id", "")[:8]
        return (
            f"Successfully transferred ${amount:,.2f} to {recipient}.\n"
            f"Your remaining checking balance is ${new_bal:,.2f}. (Reference ID: {tx_id}...)"
        )

    if intent == "block_card":
        return (
            f"{tool_result.get('card_identifier', 'Your debit card')} has been successfully blocked for security. "
            f"No further charges will be authorized on this card."
        )

    if intent == "dispute_charge":
        amount = tool_result.get("amount", 0.0)
        dispute_id = tool_result.get("dispute_id", "")[:8]
        orig_id = tool_result.get("original_transaction_id", "")[:8]
        return (
            f"A dispute for ${amount:,.2f} on transaction {orig_id}... has been logged under Dispute #{dispute_id}... "
            f"Our fraud investigation team will review this claim within 1-2 business days."
        )

    return f"Request processed: {tool_result}"


async def execute_tool(
    intent: str,
    params: dict[str, Any],
    session: AsyncSession,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    """Execute authorized tool against the mock banking ledger."""
    if intent == "check_balance":
        return await tools.check_balance(session, user_id, **params)
    elif intent == "get_transactions":
        return await tools.get_transactions(session, user_id, **params)
    elif intent == "transfer_funds":
        return await tools.transfer_funds(session, user_id, **params)
    elif intent == "block_card":
        return await tools.block_card(session, user_id, **params)
    elif intent == "dispute_charge":
        return await tools.dispute_charge(session, user_id, **params)
    return {"status": "error", "message": f"Unknown tool: {intent}"}


async def run_agent_loop(
    session: AsyncSession,
    user_id: uuid.UUID,
    message: str,
    role: str = "customer",
    session_id: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """
    Primary agent execution loop intercepted by TrustLayer.
    """
    req_id = request_id or uuid.uuid4()

    # 1. Resolve / Create ChatSession
    if not session_id:
        sess_stmt = (
            select(ChatSession)
            .where(ChatSession.user_id == user_id)
            .order_by(ChatSession.started_at.desc())
            .limit(1)
        )
        sess_res = await session.execute(sess_stmt)
        chat_sess = sess_res.scalar_one_or_none()
        if not chat_sess:
            chat_sess = ChatSession(
                id=uuid.uuid4(),
                user_id=user_id,
                started_at=datetime.utcnow(),
            )
            session.add(chat_sess)
            await session.commit()
            await session.refresh(chat_sess)
        session_id = chat_sess.id
    else:
        sess_stmt = select(ChatSession).where(ChatSession.id == session_id)
        sess_res = await session.execute(sess_stmt)
        chat_sess = sess_res.scalar_one_or_none()
        if not chat_sess:
            chat_sess = ChatSession(
                id=session_id,
                user_id=user_id,
                started_at=datetime.utcnow(),
            )
            session.add(chat_sess)
            await session.commit()
            await session.refresh(chat_sess)

    # 2. Record incoming message in DB
    user_msg = ChatMessage(
        id=uuid.uuid4(),
        session_id=session_id,
        role="user",
        content=message,
        created_at=datetime.utcnow(),
    )
    session.add(user_msg)
    await session.commit()

    # 3. Resolve user role from DB if default
    if not role or role == "customer":
        user_stmt = select(User).where(User.id == user_id)
        user_res = await session.execute(user_stmt)
        user = user_res.scalar_one_or_none()
        if user and user.role_id:
            role_stmt = select(Role).where(Role.id == user.role_id)
            role_res = await session.execute(role_stmt)
            role_obj = role_res.scalar_one_or_none()
            if role_obj:
                role = role_obj.name

    # 4. Reasoning: Detect intent & proposed tool call
    intent, params = detect_intent(message)

    log_structured_event(
        request_id=req_id,
        stage="agent_reasoning",
        event="intent_detected",
        data={"user_id": str(user_id), "role": role, "intent": intent, "params": params},
    )

    # ─────────────────────────────────────────────────────────────────────────
    # If no tool call proposed -> plain conversational query
    # ─────────────────────────────────────────────────────────────────────────
    if not intent:
        natural_language_response = format_response(None, None, message)
        agent_msg = ChatMessage(
            id=uuid.uuid4(),
            session_id=session_id,
            role="agent",
            content=natural_language_response,
            created_at=datetime.utcnow(),
        )
        session.add(agent_msg)
        await session.commit()

        # Log audit entry for chat interaction
        await record_audit_pipeline(
            session=session,
            request_id=req_id,
            session_id=session_id,
            tool_name=None,
            tool_params=None,
            claims_data=None,
            grounding_data={"grounding_score": 1.0, "grounding_risk": 0.0},
            policy_data={"rbac_allowed": True, "policy_risk": 0.0},
            decision_data={"decision": "allow", "final_risk": 0.0, "grounding_score": 1.0, "policy_risk": 0.0},
            event="chat_interaction",
        )

        return {
            "request_id": str(req_id),
            "session_id": str(session_id),
            "user_id": str(user_id),
            "role": role,
            "intent": None,
            "tool_called": None,
            "tool_params": None,
            "decision": "allow",
            "final_risk": 0.0,
            "grounding_score": 1.0,
            "policy_risk": 0.0,
            "tool_result": None,
            "response": natural_language_response,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # TrustLayer Interception Layer: Proposed Tool Call
    # ─────────────────────────────────────────────────────────────────────────

    # ── Parallel Branch A: Grounding Module (Module 1) ────────────────────────
    log_structured_event(req_id, stage="grounding", event="claim_extraction_started", data={"text": message})
    claims_res = extract_claims(message)
    extracted_claims = claims_res.get("claims", [])
    grounding_error = claims_res.get("error")

    claim_verifications = []
    for clm in extracted_claims:
        try:
            evidence = await retrieve_evidence(clm, role=role, top_k=3)
            verification = verify_claim_against_chunks(clm, evidence, role=role)
            doc_ids = [e.get("doc_id") for e in evidence if e.get("doc_id")]
            verification["retrieved_doc_ids"] = doc_ids
            claim_verifications.append(verification)
        except Exception as exc:
            logger.error("Grounding retrieval/NLI failed on claim '%s': %s", clm, exc)
            grounding_error = str(exc)

    if extracted_claims and claim_verifications:
        aggregate_grounding = calculate_grounding_score(claim_verifications)
        grounding_score = aggregate_grounding["grounding_score"]
        grounding_risk = aggregate_grounding["grounding_risk"]
    else:
        # No policy claims made in the prompt (e.g. straightforward "transfer $150 to Bob")
        aggregate_grounding = calculate_grounding_score([])
        grounding_score = 1.0
        grounding_risk = 0.0
        grounding_error = None

    log_structured_event(
        request_id=req_id,
        stage="grounding",
        event="grounding_completed",
        data={
            "claims_count": len(extracted_claims),
            "grounding_score": grounding_score,
            "grounding_risk": grounding_risk,
            "grounding_error": grounding_error,
        },
    )

    # ── Parallel Branch B: Policy Engine (Module 2) ───────────────────────────
    log_structured_event(req_id, stage="policy", event="policy_checks_started", data={"role": role, "tool": intent})

    rbac_res = check_rbac(role=role, tool_name=intent, params=params)
    seq_res = await check_sequence_anomaly(
        session=session,
        user_id=user_id,
        session_id=session_id,
        tool_name=intent,
        params=params,
    )

    # Combined policy risk: elevated if sequence anomaly or RBAC deny
    if rbac_res["hard_deny"]:
        policy_risk = 1.0
    else:
        policy_risk = float(seq_res.get("policy_risk", 0.0))

    policy_data = {
        "rbac_allowed": rbac_res["allowed"],
        "rbac_hard_deny": rbac_res["hard_deny"],
        "rbac_reason": rbac_res["reason"],
        "sequence_anomaly_flag": seq_res["flagged"],
        "sequence_reason": seq_res.get("reason"),
        "policy_risk": policy_risk,
    }

    log_structured_event(
        request_id=req_id,
        stage="policy",
        event="policy_checks_completed",
        data=policy_data,
    )

    # ── Decision Engine: Fusion and Overrides ─────────────────────────────────
    decision_res = evaluate_decision(
        grounding_score=grounding_score,
        policy_risk=policy_risk,
        rbac_allowed=rbac_res["allowed"],
        rbac_hard_deny=rbac_res["hard_deny"],
        tool_name=intent,
        grounding_error=grounding_error,
    )
    decision = decision_res["decision"]

    log_structured_event(
        request_id=req_id,
        stage="decision",
        event="decision_evaluated",
        data=decision_res,
    )

    # ── Audit Persistence ─────────────────────────────────────────────────────
    decision_id, audit_log_id = await record_audit_pipeline(
        session=session,
        request_id=req_id,
        session_id=session_id,
        tool_name=intent,
        tool_params=params,
        claims_data=claim_verifications,
        grounding_data=aggregate_grounding,
        policy_data=policy_data,
        decision_data=decision_res,
        event="decision_made",
    )

    # ── Action Enforcement ────────────────────────────────────────────────────
    tool_result: dict[str, Any] | None = None
    natural_language_response: str

    if decision == "allow":
        log_structured_event(req_id, stage="enforcement", event="action_allowed", data={"tool": intent})
        tool_result = await execute_tool(intent, params, session, user_id)
        natural_language_response = format_response(intent, tool_result, message)

    elif decision == "block":
        log_structured_event(req_id, stage="enforcement", event="action_blocked", data={"tool": intent, "reason": decision_res["reason"]})
        tool_result = {
            "status": "blocked",
            "reason": decision_res["reason"],
            "override_reason": decision_res["override_reason"],
        }
        natural_language_response = (
            f"I cannot complete your request to {intent.replace('_', ' ')} due to security and policy restrictions: "
            f"{decision_res['reason']} Please contact customer support if you believe this is in error."
        )

    else:  # decision == "approve"
        log_structured_event(req_id, stage="enforcement", event="action_queued_for_approval", data={"tool": intent, "decision_id": str(decision_id)})
        tool_result = {
            "status": "pending_approval",
            "decision_id": str(decision_id) if decision_id else None,
            "reason": decision_res["reason"],
            "override_reason": decision_res["override_reason"],
        }
        natural_language_response = (
            f"Your request to {intent.replace('_', ' ')} requires authorization from a Senior Staff / Approver before execution. "
            f"It has been routed to the approval queue for review (Reference: {str(decision_id)[:8] if decision_id else 'pending'}). "
            f"You will be notified once reviewed."
        )

    # Record agent response in session history
    agent_msg = ChatMessage(
        id=uuid.uuid4(),
        session_id=session_id,
        role="agent",
        content=natural_language_response,
        created_at=datetime.utcnow(),
    )
    session.add(agent_msg)
    await session.commit()

    return {
        "request_id": str(req_id),
        "session_id": str(session_id),
        "user_id": str(user_id),
        "role": role,
        "intent": intent,
        "tool_called": intent,
        "tool_params": params,
        "decision": decision,
        "final_risk": decision_res["final_risk"],
        "grounding_score": decision_res["grounding_score"],
        "policy_risk": decision_res["policy_risk"],
        "override_applied": decision_res["override_applied"],
        "decision_id": str(decision_id) if decision_id else None,
        "audit_log_id": str(audit_log_id),
        "tool_result": tool_result,
        "response": natural_language_response,
    }
