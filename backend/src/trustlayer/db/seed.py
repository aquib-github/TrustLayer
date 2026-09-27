"""
Seed script to populate initial mock banking data for TrustLayer.

Populates:
1. Roles: customer, staff, approver
2. Synthetic users across all roles
3. Accounts with balances for customers
4. Sample transactions across accounts
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from trustlayer.db.models import Account, Role, Transaction, User
from trustlayer.db.session import async_session, engine


async def seed_data() -> None:
    async with async_session() as session:
        # Check if already seeded
        result = await session.execute(select(Role))
        existing_roles = {r.name: r for r in result.scalars().all()}

        role_names = ["customer", "staff", "approver"]
        roles: dict[str, Role] = {}

        for name in role_names:
            if name in existing_roles:
                roles[name] = existing_roles[name]
            else:
                new_role = Role(name=name)
                session.add(new_role)
                roles[name] = new_role

        await session.flush()

        # Check existing users
        res_users = await session.execute(select(User))
        existing_users = {u.email: u for u in res_users.scalars().all()}

        users_to_create = [
            # Customers
            {
                "id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
                "name": "Alice Johnson",
                "email": "alice.johnson@example.com",
                "role": roles["customer"],
                "created_at": datetime.utcnow() - timedelta(days=60),
            },
            {
                "id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
                "name": "Bob Smith",
                "email": "bob.smith@example.com",
                "role": roles["customer"],
                "created_at": datetime.utcnow() - timedelta(days=45),
            },
            {
                "id": uuid.UUID("33333333-3333-3333-3333-333333333333"),
                "name": "Charlie Brown",
                "email": "charlie.brown@example.com",
                "role": roles["customer"],
                "created_at": datetime.utcnow() - timedelta(days=30),
            },
            # Staff
            {
                "id": uuid.UUID("44444444-4444-4444-4444-444444444444"),
                "name": "Sarah Davis",
                "email": "sarah.davis@banktrust.internal",
                "role": roles["staff"],
                "created_at": datetime.utcnow() - timedelta(days=90),
            },
            {
                "id": uuid.UUID("55555555-5555-5555-5555-555555555555"),
                "name": "David Miller",
                "email": "david.miller@banktrust.internal",
                "role": roles["staff"],
                "created_at": datetime.utcnow() - timedelta(days=75),
            },
            # Approver
            {
                "id": uuid.UUID("66666666-6666-6666-6666-666666666666"),
                "name": "Eleanor Vance",
                "email": "eleanor.vance@banktrust.internal",
                "role": roles["approver"],
                "created_at": datetime.utcnow() - timedelta(days=120),
            },
        ]

        created_users: dict[str, User] = {}
        for u_data in users_to_create:
            if u_data["email"] in existing_users:
                created_users[u_data["email"]] = existing_users[u_data["email"]]
            else:
                user = User(
                    id=u_data["id"],
                    name=u_data["name"],
                    email=u_data["email"],
                    role_id=u_data["role"].id,
                    created_at=u_data["created_at"],
                )
                session.add(user)
                created_users[u_data["email"]] = user

        await session.flush()

        # Accounts for customers
        res_accounts = await session.execute(select(Account))
        existing_account_ids = {a.id for a in res_accounts.scalars().all()}

        alice = created_users["alice.johnson@example.com"]
        bob = created_users["bob.smith@example.com"]
        charlie = created_users["charlie.brown@example.com"]

        accounts_to_create = [
            {
                "id": uuid.UUID("a1111111-1111-1111-1111-111111111111"),
                "user_id": alice.id,
                "balance": Decimal("5420.50"),
                "account_type": "checking",
            },
            {
                "id": uuid.UUID("a1111111-1111-1111-1111-222222222222"),
                "user_id": alice.id,
                "balance": Decimal("12850.00"),
                "account_type": "savings",
            },
            {
                "id": uuid.UUID("b2222222-2222-2222-2222-111111111111"),
                "user_id": bob.id,
                "balance": Decimal("1250.75"),
                "account_type": "checking",
            },
            {
                "id": uuid.UUID("c3333333-3333-3333-3333-111111111111"),
                "user_id": charlie.id,
                "balance": Decimal("340.00"),
                "account_type": "checking",
            },
        ]

        created_accounts: dict[uuid.UUID, Account] = {}
        for acc_data in accounts_to_create:
            if acc_data["id"] in existing_account_ids:
                continue
            account = Account(
                id=acc_data["id"],
                user_id=acc_data["user_id"],
                balance=acc_data["balance"],
                account_type=acc_data["account_type"],
            )
            session.add(account)
            created_accounts[acc_data["id"]] = account

        await session.flush()

        # Sample transactions
        res_transactions = await session.execute(select(Transaction))
        existing_tx_ids = {t.id for t in res_transactions.scalars().all()}

        now = datetime.utcnow()
        transactions_to_create = [
            {
                "id": uuid.UUID("d1111111-1111-1111-1111-111111111111"),
                "account_id": uuid.UUID("a1111111-1111-1111-1111-111111111111"),
                "amount": Decimal("45.20"),
                "type": "debit",
                "status": "completed",
                "created_at": now - timedelta(days=2),
            },
            {
                "id": uuid.UUID("d1111111-1111-1111-1111-222222222222"),
                "account_id": uuid.UUID("a1111111-1111-1111-1111-111111111111"),
                "amount": Decimal("2500.00"),
                "type": "credit",
                "status": "completed",
                "created_at": now - timedelta(days=5),
            },
            {
                "id": uuid.UUID("d1111111-1111-1111-1111-333333333333"),
                "account_id": uuid.UUID("a1111111-1111-1111-1111-111111111111"),
                "amount": Decimal("300.00"),
                "type": "transfer",
                "status": "completed",
                "created_at": now - timedelta(hours=12),
            },
            {
                "id": uuid.UUID("d2222222-2222-2222-2222-111111111111"),
                "account_id": uuid.UUID("b2222222-2222-2222-2222-111111111111"),
                "amount": Decimal("120.00"),
                "type": "debit",
                "status": "completed",
                "created_at": now - timedelta(days=1),
            },
            {
                "id": uuid.UUID("d2222222-2222-2222-2222-222222222222"),
                "account_id": uuid.UUID("b2222222-2222-2222-2222-111111111111"),
                "amount": Decimal("75.00"),
                "type": "dispute",
                "status": "pending",
                "created_at": now - timedelta(hours=3),
            },
            {
                "id": uuid.UUID("d3333333-3333-3333-3333-111111111111"),
                "account_id": uuid.UUID("c3333333-3333-3333-3333-111111111111"),
                "amount": Decimal("500.00"),
                "type": "transfer",
                "status": "blocked",
                "created_at": now - timedelta(hours=1),
            },
        ]

        for tx_data in transactions_to_create:
            if tx_data["id"] in existing_tx_ids:
                continue
            tx = Transaction(
                id=tx_data["id"],
                account_id=tx_data["account_id"],
                amount=tx_data["amount"],
                type=tx_data["type"],
                status=tx_data["status"],
                created_at=tx_data["created_at"],
            )
            session.add(tx)

        await session.commit()
        print("Database seeded successfully with roles, users, accounts, and transactions!")


if __name__ == "__main__":
    asyncio.run(seed_data())
