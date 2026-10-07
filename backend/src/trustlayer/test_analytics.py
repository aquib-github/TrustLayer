"""Integration test suite for TrustLayer dashboard summary and audit log endpoints."""
import asyncio
import uuid
from decimal import Decimal
from datetime import datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from trustlayer.db.models import (
    AgentAction,
    AuditLog,
    ChatSession,
    Decision,
    DecisionType,
    User,
)
from trustlayer.db.session import async_session
from trustlayer.main import app


# Seeded user UUIDs matching db/seed.py
ALICE_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")      # customer
BOB_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")        # customer
SARAH_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")      # staff
ELEANOR_ID = uuid.UUID("66666666-6666-6666-6666-666666666666")    # approver


async def seed_known_decisions():
    # Insert deterministic decisions for count verification across roles and tools
    async with async_session() as db:
        now = datetime.utcnow()
        created_ids = []

        test_cases = [
            (ALICE_ID, "check_balance", DecisionType.ALLOW, "0.9500", "0.0000", "0.0000"),
            (ALICE_ID, "transfer_funds", DecisionType.APPROVE, "0.2000", "0.8000", "0.5000"),
            (ALICE_ID, "transfer_funds", DecisionType.BLOCK, "0.1000", "1.0000", "1.0000"),
            (SARAH_ID, "dispute_charge", DecisionType.BLOCK, "0.5000", "1.0000", "1.0000"),
            (ELEANOR_ID, "check_balance", DecisionType.ALLOW, "0.9000", "0.0000", "0.0000"),
        ]

        for user_id, tool, decision_type, gs, prs, fr in test_cases:
            session_obj = ChatSession(id=uuid.uuid4(), user_id=user_id, started_at=now)
            db.add(session_obj)

            action_obj = AgentAction(
                id=uuid.uuid4(),
                session_id=session_obj.id,
                tool_name=tool,
                params_json={"test": True},
                proposed_at=now,
            )
            db.add(action_obj)

            decision_obj = Decision(
                id=uuid.uuid4(),
                action_id=action_obj.id,
                grounding_score=Decimal(gs),
                policy_risk_score=Decimal(prs),
                final_risk=Decimal(fr),
                decision=decision_type,
            )
            db.add(decision_obj)

            audit_obj = AuditLog(
                id=uuid.uuid4(),
                request_id=uuid.uuid4(),
                session_id=session_obj.id,
                action_id=action_obj.id,
                decision_id=decision_obj.id,
                event="decision_made",
                event_type="decision_made",
                actor_id=None,
                reason=None,
                timestamp=now,
            )
            db.add(audit_obj)
            created_ids.append((decision_obj.id, audit_obj.id, tool, decision_type.value))

        await db.commit()
        return created_ids


async def run_analytics_test_suite():
    # Seed deterministic decisions to verify aggregation counts
    seeded = await seed_known_decisions()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        print("Starting TrustLayer Dashboard & Audit Log Tests...")

        # ── 1. Role Gating ────────────────────────────────────────────────
        # No header → 403
        res = await client.get("/dashboard/summary")
        assert res.status_code == 403, f"Expected 403 without header, got {res.status_code}"

        res = await client.get("/audit")
        assert res.status_code == 403, f"Expected 403 without header, got {res.status_code}"

        # Customer → 403
        res = await client.get("/dashboard/summary", headers={"X-User-Id": str(ALICE_ID)})
        assert res.status_code == 403, f"Customer must get 403 on /dashboard/summary, got {res.status_code}"

        res = await client.get("/audit", headers={"X-User-Id": str(ALICE_ID)})
        assert res.status_code == 403, f"Customer must get 403 on /audit, got {res.status_code}"

        # Staff → 200
        res = await client.get("/dashboard/summary", headers={"X-User-Id": str(SARAH_ID)})
        assert res.status_code == 200, f"Staff must get 200 on /dashboard/summary, got {res.status_code}"

        res = await client.get("/audit", headers={"X-User-Id": str(SARAH_ID)})
        assert res.status_code == 200, f"Staff must get 200 on /audit, got {res.status_code}"

        # Approver → 200
        res = await client.get("/dashboard/summary", headers={"X-User-Id": str(ELEANOR_ID)})
        assert res.status_code == 200, f"Approver must get 200 on /dashboard/summary, got {res.status_code}"

        res = await client.get("/audit", headers={"X-User-Id": str(ELEANOR_ID)})
        assert res.status_code == 200, f"Approver must get 200 on /audit, got {res.status_code}"

        # Invalid UUID → 403
        res = await client.get("/dashboard/summary", headers={"X-User-Id": "not-valid"})
        assert res.status_code == 403, f"Invalid UUID must get 403, got {res.status_code}"

        print("  [1] Role gating verified: customer=403, staff=200, approver=200.")

        # ── 2. Dashboard Summary Counts ───────────────────────────────────
        res = await client.get("/dashboard/summary", headers={"X-User-Id": str(ELEANOR_ID)})
        assert res.status_code == 200
        data = res.json()

        # Total must reflect at least the 5 seeded decisions
        assert data["total_decisions"] >= 5, f"Expected >= 5 total decisions, got {data['total_decisions']}"

        # by_decision must have at least 2 allows, 1 approve, 2 blocks from our seed
        assert data["by_decision"]["allow"] >= 2, f"Expected >= 2 allows, got {data['by_decision']['allow']}"
        assert data["by_decision"]["approve"] >= 1, f"Expected >= 1 approve, got {data['by_decision']['approve']}"
        assert data["by_decision"]["block"] >= 2, f"Expected >= 2 blocks, got {data['by_decision']['block']}"

        # by_role must have customer entries from our seed
        assert data["by_role"]["customer"] >= 3, f"Expected >= 3 customer decisions, got {data['by_role']['customer']}"

        # by_tool must contain check_balance and transfer_funds
        tool_names = [t["tool_name"] for t in data["by_tool"]]
        assert "check_balance" in tool_names, f"Expected check_balance in by_tool, got {tool_names}"
        assert "transfer_funds" in tool_names, f"Expected transfer_funds in by_tool, got {tool_names}"

        # avg_scores_per_decision must exist and have numeric values
        assert len(data["avg_scores_per_decision"]) >= 1, "Expected at least 1 avg_scores entry"
        for entry in data["avg_scores_per_decision"]:
            assert entry["decision"] in ("allow", "approve", "block"), f"Unexpected decision: {entry['decision']}"
            if entry["avg_risk"] is not None:
                assert 0.0 <= entry["avg_risk"] <= 1.0, f"avg_risk out of range: {entry['avg_risk']}"

        print("  [2] Dashboard summary counts and SQL aggregation verified.")

        # ── 3. Dashboard Date Range Filter ────────────────────────────────
        # Use tomorrow as from date to get zero results from our seed
        tomorrow = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
        res = await client.get(
            f"/dashboard/summary?from={tomorrow}",
            headers={"X-User-Id": str(ELEANOR_ID)},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["total_decisions"] == 0, f"Expected 0 decisions with future from filter, got {data['total_decisions']}"
        assert data["date_from"] == tomorrow

        # Use yesterday-to-tomorrow to capture all
        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        res = await client.get(
            f"/dashboard/summary?from={yesterday}&to={tomorrow}",
            headers={"X-User-Id": str(ELEANOR_ID)},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["total_decisions"] >= 5, f"Expected >= 5 with broad date range, got {data['total_decisions']}"

        print("  [3] Dashboard date range filter verified.")

        # ── 4. Audit Log Pagination ───────────────────────────────────────
        res = await client.get("/audit?limit=2&offset=0", headers={"X-User-Id": str(ELEANOR_ID)})
        assert res.status_code == 200
        data = res.json()
        assert data["limit"] == 2
        assert data["offset"] == 0
        assert len(data["items"]) <= 2, f"Expected at most 2 items, got {len(data['items'])}"
        assert data["total"] >= 5, f"Expected total >= 5, got {data['total']}"

        # Page 2 should have different items
        res2 = await client.get("/audit?limit=2&offset=2", headers={"X-User-Id": str(ELEANOR_ID)})
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["offset"] == 2
        if len(data["items"]) > 0 and len(data2["items"]) > 0:
            assert data["items"][0]["audit_id"] != data2["items"][0]["audit_id"], "Pagination must return different items"

        # Verify newest-first ordering
        res_order = await client.get("/audit?limit=50", headers={"X-User-Id": str(ELEANOR_ID)})
        order_data = res_order.json()
        if len(order_data["items"]) >= 2:
            ts0 = order_data["items"][0].get("timestamp", "")
            ts1 = order_data["items"][1].get("timestamp", "")
            if ts0 and ts1:
                assert ts0 >= ts1, f"Expected newest first: {ts0} >= {ts1}"

        print("  [4] Audit log pagination and newest-first ordering verified.")

        # ── 5. Audit Log Decision Filter ──────────────────────────────────
        res = await client.get("/audit?decision=allow", headers={"X-User-Id": str(ELEANOR_ID)})
        assert res.status_code == 200
        data = res.json()
        for item in data["items"]:
            if item["decision"]:
                assert item["decision"] == "allow", f"Decision filter returned: {item['decision']}"

        res = await client.get("/audit?decision=block", headers={"X-User-Id": str(ELEANOR_ID)})
        assert res.status_code == 200
        data = res.json()
        for item in data["items"]:
            if item["decision"]:
                assert item["decision"] == "block", f"Decision filter returned: {item['decision']}"

        print("  [5] Audit log decision filter verified.")

        # ── 6. Audit Log Tool Filter ──────────────────────────────────────
        res = await client.get("/audit?tool=check_balance", headers={"X-User-Id": str(ELEANOR_ID)})
        assert res.status_code == 200
        data = res.json()
        assert data["total"] >= 2, f"Expected >= 2 check_balance audits, got {data['total']}"
        for item in data["items"]:
            if item["tool_name"]:
                assert item["tool_name"] == "check_balance", f"Tool filter returned: {item['tool_name']}"

        print("  [6] Audit log tool filter verified.")

        # ── 7. Audit Log Role Filter ──────────────────────────────────────
        res = await client.get("/audit?role=customer", headers={"X-User-Id": str(ELEANOR_ID)})
        assert res.status_code == 200
        data = res.json()
        assert data["total"] >= 3, f"Expected >= 3 customer role audits, got {data['total']}"
        for item in data["items"]:
            if item["user_role"]:
                assert item["user_role"] == "customer", f"Role filter returned: {item['user_role']}"

        print("  [7] Audit log role filter verified.")

        # ── 8. Audit Log Actor ID Filter ──────────────────────────────────
        res = await client.get(
            f"/audit?actor_id={ELEANOR_ID}",
            headers={"X-User-Id": str(ELEANOR_ID)},
        )
        assert res.status_code == 200
        data = res.json()
        for item in data["items"]:
            assert item["actor_id"] == str(ELEANOR_ID), f"Actor filter returned: {item['actor_id']}"

        print("  [8] Audit log actor_id filter verified.")

        # ── 9. Audit Log Date Range Filter ────────────────────────────────
        res = await client.get(
            f"/audit?from={tomorrow}",
            headers={"X-User-Id": str(ELEANOR_ID)},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 0, f"Expected 0 audits with future from, got {data['total']}"

        print("  [9] Audit log date range filter verified.")

        # ── 10. Audit Item Contains Structured Columns ────────────────────
        res = await client.get("/audit?limit=10", headers={"X-User-Id": str(ELEANOR_ID)})
        assert res.status_code == 200
        data = res.json()
        assert len(data["items"]) > 0, "Expected at least 1 audit item"
        item = data["items"][0]
        # Verify all structured columns are present in the response
        expected_fields = [
            "audit_id", "request_id", "session_id", "event", "event_type",
            "timestamp", "decision", "tool_name",
        ]
        for field in expected_fields:
            assert field in item, f"Missing field '{field}' in audit response"

        print("  [10] Audit item structured columns verified.")

        print("All dashboard and audit log tests passed successfully!")


if __name__ == "__main__":
    asyncio.run(run_analytics_test_suite())
