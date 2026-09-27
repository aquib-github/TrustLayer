"""End-to-end verification script for POST /chat and the agent tools."""
import asyncio
import uuid
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, func
from trustlayer.db.session import async_session
from trustlayer.db.models import Transaction, Account, User
from trustlayer.main import app


async def main():
    alice_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    
    # Use ASGI transport to test the FastAPI app directly
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        print("=== 1. Testing GET /health ===")
        res = await client.get("/health")
        print("Health status:", res.status_code, res.json())

        print("\n=== 2. Testing POST /chat: 'check my balance' ===")
        payload = {
            "user_id": str(alice_id),
            "message": "check my balance"
        }
        res = await client.post("/chat", json=payload)
        print("Status code:", res.status_code)
        balance_res = res.json()
        print("Response payload:")
        print(balance_res)

        print("\n=== 3. Testing POST /chat: 'transfer $150 to Bob' ===")
        async with async_session() as session:
            count_before = await session.scalar(select(func.count()).select_from(Transaction))
            print(f"Transactions count BEFORE transfer: {count_before}")

        transfer_payload = {
            "user_id": str(alice_id),
            "message": "please transfer $150 to Bob from checking"
        }
        res = await client.post("/chat", json=transfer_payload)
        print("Status code:", res.status_code)
        transfer_res = res.json()
        print("Response payload:")
        print(transfer_res)

        async with async_session() as session:
            count_after = await session.scalar(select(func.count()).select_from(Transaction))
            print(f"Transactions count AFTER transfer: {count_after}")
            assert count_after == count_before + 1, "New row was not added to transactions!"

            # Query the newly inserted transaction
            stmt = select(Transaction).order_by(Transaction.created_at.desc()).limit(1)
            new_tx = (await session.execute(stmt)).scalar_one()
            print(f"Verified new transaction in DB: ID={new_tx.id}, Amount={new_tx.amount}, Type={new_tx.type}, Status={new_tx.status}, CreatedAt={new_tx.created_at}")

        print("\n=== 4. Testing POST /chat: 'show my recent transactions' ===")
        tx_payload = {
            "user_id": str(alice_id),
            "message": "show my recent transactions"
        }
        res = await client.post("/chat", json=tx_payload)
        print("Recent transactions response:")
        print(res.json()["response"])


if __name__ == "__main__":
    asyncio.run(main())
