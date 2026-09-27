"""
Agent reasoning loop for TrustLayer.

In this phase (Agent Stub — no interception / firewall layer yet):
1. Analyzes the incoming user message (rule-based intent match + parameter extraction).
2. Directly executes the corresponding banking tool from `agent.tools`.
3. Synthesizes tool results into a clear natural-language response.
"""

import re
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trustlayer.agent import tools
from trustlayer.db.models import Role, User


def detect_intent(message: str) -> tuple[str | None, dict[str, Any]]:
    """
    Rule-based intent classifier and argument extractor.
    Returns (intent_name, extracted_arguments).
    """
    msg = message.lower().strip()

    # 1. Check Balance
    if any(kw in msg for kw in ["balance", "how much money", "account balance", "check balance", "what do i have"]):
        acc_type = None
        if "saving" in msg:
            acc_type = "savings"
        elif "checking" in msg:
            acc_type = "checking"
        return "check_balance", {"account_type": acc_type}

    # 2. Get Transactions
    if any(kw in msg for kw in ["transaction", "statement", "history", "recent activity", "charges", "recent payments"]):
        acc_type = None
        if "saving" in msg:
            acc_type = "savings"
        elif "checking" in msg:
            acc_type = "checking"
        # Extract limit if mentioned (e.g., "last 3 transactions")
        limit_match = re.search(r"last\s+(\d+)", msg)
        limit = int(limit_match.group(1)) if limit_match else 5
        return "get_transactions", {"limit": limit, "account_type": acc_type}

    # 3. Transfer Funds
    if any(kw in msg for kw in ["transfer", "send money", "send $", "pay "]):
        # Extract amount (e.g., "$100", "100.50", "50 dollars")
        amount = 50.0  # default test amount if unspecified
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

    # 4. Block Card
    if any(kw in msg for kw in ["block card", "lost card", "freeze card", "stolen card", "lock card"]):
        return "block_card", {"reason": "customer_reported_lost_or_stolen"}

    # 5. Dispute Charge
    if any(kw in msg for kw in ["dispute", "fraud", "unauthorized charge", "chargeback"]):
        # Extract transaction ID if UUID format present
        uuid_match = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", msg)
        tx_id = uuid_match.group(1) if uuid_match else None
        return "dispute_charge", {
            "transaction_id": tx_id,
            "reason": "unauthorized_transaction_reported_by_customer",
        }

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
            f"No further charges will be authorized on this card. If you require a replacement card, please let me know."
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


async def run_agent_loop(
    session: AsyncSession,
    user_id: uuid.UUID,
    message: str,
    role: str = "customer",
) -> dict[str, Any]:
    """
    Primary agent execution loop for Phase 1.
    Directly connects message intent to tool execution against the database.
    """
    # 1. Resolve role if needed from DB
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

    # 2. Detect intent & extract parameters
    intent, params = detect_intent(message)

    # 3. Call tool directly (no interception firewall in this phase)
    tool_result: dict[str, Any] | None = None

    if intent == "check_balance":
        tool_result = await tools.check_balance(session, user_id, **params)
    elif intent == "get_transactions":
        tool_result = await tools.get_transactions(session, user_id, **params)
    elif intent == "transfer_funds":
        tool_result = await tools.transfer_funds(session, user_id, **params)
    elif intent == "block_card":
        tool_result = await tools.block_card(session, user_id, **params)
    elif intent == "dispute_charge":
        tool_result = await tools.dispute_charge(session, user_id, **params)

    # 4. Synthesize natural language response
    natural_language_response = format_response(intent, tool_result, message)

    return {
        "user_id": str(user_id),
        "role": role,
        "intent": intent,
        "tool_called": intent,
        "tool_params": params,
        "tool_result": tool_result,
        "response": natural_language_response,
    }
