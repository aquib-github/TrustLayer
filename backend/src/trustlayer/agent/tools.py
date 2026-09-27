"""
Banking domain tools for TrustLayer agent.

Implements the 5 core agentic tools against the mock bank ledger:
1. check_balance
2. get_transactions
3. transfer_funds
4. block_card
5. dispute_charge
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trustlayer.db.models import Account, Transaction, User


async def check_balance(
    session: AsyncSession,
    user_id: uuid.UUID,
    account_type: str | None = None,
) -> dict[str, Any]:
    """
    Check account balances for a user.

    Returns balances for all user accounts or a specific account type (e.g. checking/savings).
    """
    stmt = select(Account).where(Account.user_id == user_id)
    if account_type:
        stmt = stmt.where(Account.account_type == account_type.lower())

    result = await session.execute(stmt)
    accounts = result.scalars().all()

    if not accounts:
        return {
            "status": "error",
            "message": f"No accounts found for user {user_id}",
            "accounts": [],
            "total_balance": 0.0,
        }

    account_data = [
        {
            "account_id": str(acc.id),
            "account_type": acc.account_type,
            "balance": float(acc.balance),
        }
        for acc in accounts
    ]
    total_balance = sum(acc["balance"] for acc in account_data)

    return {
        "status": "success",
        "accounts": account_data,
        "total_balance": total_balance,
    }


async def get_transactions(
    session: AsyncSession,
    user_id: uuid.UUID,
    limit: int = 5,
    account_type: str | None = None,
) -> dict[str, Any]:
    """
    Retrieve recent transactions for a user's accounts.
    """
    # 1. Fetch user's account IDs
    acc_stmt = select(Account).where(Account.user_id == user_id)
    if account_type:
        acc_stmt = acc_stmt.where(Account.account_type == account_type.lower())

    acc_result = await session.execute(acc_stmt)
    accounts = acc_result.scalars().all()
    if not accounts:
        return {
            "status": "error",
            "message": f"No accounts found for user {user_id}",
            "transactions": [],
        }

    account_ids = [acc.id for acc in accounts]

    # 2. Fetch recent transactions
    tx_stmt = (
        select(Transaction)
        .where(Transaction.account_id.in_(account_ids))
        .order_by(Transaction.created_at.desc())
        .limit(limit)
    )
    tx_result = await session.execute(tx_stmt)
    tx_list = tx_result.scalars().all()

    transactions = [
        {
            "id": str(tx.id),
            "account_id": str(tx.account_id),
            "amount": float(tx.amount),
            "type": tx.type,
            "status": tx.status,
            "created_at": tx.created_at.isoformat() if tx.created_at else None,
        }
        for tx in tx_list
    ]

    return {
        "status": "success",
        "count": len(transactions),
        "transactions": transactions,
    }


async def transfer_funds(
    session: AsyncSession,
    user_id: uuid.UUID,
    amount: float | Decimal,
    from_account_type: str = "checking",
    to_account_id: uuid.UUID | str | None = None,
    recipient: str | None = None,
) -> dict[str, Any]:
    """
    Transfer funds from a user's account to another account or recipient.
    Deducts balance from source account and records a new transaction in the DB.
    """
    transfer_amount = Decimal(str(amount))
    if transfer_amount <= 0:
        return {
            "status": "error",
            "reason": "invalid_amount",
            "message": "Transfer amount must be greater than zero.",
        }

    # 1. Find source account
    acc_stmt = select(Account).where(
        Account.user_id == user_id,
        Account.account_type == from_account_type.lower(),
    )
    acc_result = await session.execute(acc_stmt)
    source_acc = acc_result.scalar_one_or_none()

    if not source_acc:
        # Fallback to any account owned by the user
        fallback_stmt = select(Account).where(Account.user_id == user_id).limit(1)
        fallback_result = await session.execute(fallback_stmt)
        source_acc = fallback_result.scalar_one_or_none()

    if not source_acc:
        return {
            "status": "error",
            "reason": "account_not_found",
            "message": f"No account found for user {user_id}.",
        }

    # 2. Check sufficient funds
    if source_acc.balance < transfer_amount:
        return {
            "status": "failed",
            "reason": "insufficient_funds",
            "message": f"Insufficient funds. Current balance: ${float(source_acc.balance):.2f}, requested: ${float(transfer_amount):.2f}.",
            "current_balance": float(source_acc.balance),
            "requested_amount": float(transfer_amount),
        }

    # 3. Deduct balance
    source_acc.balance -= transfer_amount

    # 4. Insert new transaction record into the database
    new_tx = Transaction(
        id=uuid.uuid4(),
        account_id=source_acc.id,
        amount=transfer_amount,
        type="transfer",
        status="completed",
        created_at=datetime.utcnow(),
    )
    session.add(new_tx)
    await session.commit()
    await session.refresh(source_acc)

    return {
        "status": "success",
        "action": "transfer_funds",
        "transaction_id": str(new_tx.id),
        "from_account_id": str(source_acc.id),
        "from_account_type": source_acc.account_type,
        "amount": float(transfer_amount),
        "recipient": recipient or str(to_account_id or "external"),
        "new_balance": float(source_acc.balance),
    }


async def block_card(
    session: AsyncSession,
    user_id: uuid.UUID,
    card_id: str | None = None,
    reason: str = "customer_request",
) -> dict[str, Any]:
    """
    Block a debit card associated with the user's account for security.
    """
    # Verify user exists and has an account
    acc_stmt = select(Account).where(Account.user_id == user_id).limit(1)
    acc_result = await session.execute(acc_stmt)
    account = acc_result.scalar_one_or_none()

    if not account:
        return {
            "status": "error",
            "reason": "account_not_found",
            "message": f"No account or card found for user {user_id}.",
        }

    card_label = card_id if card_id else f"Debit card ending in *{str(account.id)[-4:]}"

    return {
        "status": "success",
        "action": "block_card",
        "user_id": str(user_id),
        "card_identifier": card_label,
        "reason": reason,
        "card_status": "blocked",
        "timestamp": datetime.utcnow().isoformat(),
        "message": f"{card_label} has been immediately blocked.",
    }


async def dispute_charge(
    session: AsyncSession,
    user_id: uuid.UUID,
    transaction_id: uuid.UUID | str | None = None,
    reason: str = "unauthorized_charge",
) -> dict[str, Any]:
    """
    Dispute a transaction on the user's account.
    Creates a new dispute transaction or updates existing transaction status to pending dispute.
    """
    # 1. Fetch user's accounts
    acc_stmt = select(Account).where(Account.user_id == user_id)
    acc_result = await session.execute(acc_stmt)
    accounts = acc_result.scalars().all()
    if not accounts:
        return {
            "status": "error",
            "reason": "account_not_found",
            "message": f"No accounts found for user {user_id}.",
        }

    account_ids = [acc.id for acc in accounts]

    # 2. Identify the target transaction
    target_tx: Transaction | None = None
    if transaction_id:
        try:
            tx_uuid = uuid.UUID(str(transaction_id))
            target_stmt = select(Transaction).where(
                Transaction.id == tx_uuid,
                Transaction.account_id.in_(account_ids),
            )
            target_result = await session.execute(target_stmt)
            target_tx = target_result.scalar_one_or_none()
        except ValueError:
            pass

    if not target_tx:
        # Fallback to the latest completed debit transaction for the user
        latest_stmt = (
            select(Transaction)
            .where(
                Transaction.account_id.in_(account_ids),
                Transaction.type == "debit",
            )
            .order_by(Transaction.created_at.desc())
            .limit(1)
        )
        latest_result = await session.execute(latest_stmt)
        target_tx = latest_result.scalar_one_or_none()

    if not target_tx:
        # Fallback to any recent transaction
        any_tx_stmt = (
            select(Transaction)
            .where(Transaction.account_id.in_(account_ids))
            .order_by(Transaction.created_at.desc())
            .limit(1)
        )
        any_tx_result = await session.execute(any_tx_stmt)
        target_tx = any_tx_result.scalar_one_or_none()

    if not target_tx:
        return {
            "status": "error",
            "reason": "transaction_not_found",
            "message": "No eligible transactions found to dispute.",
        }

    # 3. Create a dispute transaction entry
    dispute_tx = Transaction(
        id=uuid.uuid4(),
        account_id=target_tx.account_id,
        amount=target_tx.amount,
        type="dispute",
        status="pending",
        created_at=datetime.utcnow(),
    )
    session.add(dispute_tx)
    await session.commit()

    return {
        "status": "success",
        "action": "dispute_charge",
        "dispute_id": str(dispute_tx.id),
        "original_transaction_id": str(target_tx.id),
        "amount": float(target_tx.amount),
        "reason": reason,
        "dispute_status": "pending",
        "message": f"Dispute submitted for transaction {str(target_tx.id)[:8]}... in the amount of ${float(target_tx.amount):.2f}.",
    }
