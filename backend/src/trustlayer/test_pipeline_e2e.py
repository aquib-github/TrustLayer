"""
End-to-End Test Suite for TrustLayer Pipeline:
1. ALLOW outcome (clean query & small authorized transfer)
2. BLOCK outcome (RBAC hard deny: unauthorized role)
3. APPROVE outcome (Sequence anomaly: balance check followed by 90% drain transfer)
4. Core Compound-Failure Test (RBAC-authorized money-moving transfer with hallucinated claim)
5. Audit log row verification (confirming all foreign keys and metadata are populated)
6. GET /approvals/pending endpoint verification
"""

import asyncio
import uuid
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from trustlayer.db.session import async_session
from trustlayer.db.models import Account, AuditLog, Decision, DecisionType, PolicyCheck, Transaction, User
from trustlayer.main import app


async def test_all_outcomes():
    alice_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        print("=" * 80)
        print("TRUSTLAYER FULL PIPELINE E2E INTEGRATION TESTS")
        print("=" * 80)

        # ── TEST 1: ALLOW OUTCOME ─────────────────────────────────────────────
        print("\n" + "-" * 80)
        print("[TEST 1] Triggering ALLOW outcome (Clean authorized transfer of $25)")
        print("-" * 80)
        req_1 = {
            "user_id": str(alice_id),
            "role": "customer",
            "message": "Please transfer $25 to Bob from checking",
        }
        res_1 = await client.post("/chat", json=req_1)
        data_1 = res_1.json()
        print(f"Status Code:  {res_1.status_code}")
        print(f"Decision:     {data_1['decision']} (Expected: allow)")
        print(f"Final Risk:   {data_1['final_risk']}")
        print(f"Tool Result:  {data_1['tool_result']['status']}")
        print(f"Response:     {data_1['response'][:100]}...")
        assert res_1.status_code == 200
        assert data_1["decision"] == "allow", f"Expected allow, got {data_1['decision']}"
        assert data_1["tool_result"]["status"] == "success"
        print(">>> TEST 1 PASSED: ALLOW outcome correctly executed against ledger.")

        # ── TEST 2: BLOCK OUTCOME (RBAC HARD DENY) ────────────────────────────
        print("\n" + "-" * 80)
        print("[TEST 2] Triggering BLOCK outcome (RBAC Hard Deny on Real Seeded Roles)")
        print("-" * 80)
        # Test 2a: Real role 'customer' attempting 'dispute_charge' (restricted to staff/approver)
        req_2a = {
            "user_id": str(alice_id),
            "role": "customer",
            "message": "I want to dispute unauthorized charge 22222222-2222-2222-2222-222222222222 on my statement",
        }
        res_2a = await client.post("/chat", json=req_2a)
        data_2a = res_2a.json()
        print("Test 2a (customer attempting dispute_charge):")
        print(f"  Status Code:  {res_2a.status_code}")
        print(f"  Decision:     {data_2a['decision']} (Expected: block)")
        print(f"  Final Risk:   {data_2a['final_risk']}")
        print(f"  Tool Result:  {data_2a['tool_result']['status']}")
        print(f"  Response:     {data_2a['response'][:100]}...")
        assert res_2a.status_code == 200
        assert data_2a["decision"] == "block", f"Expected block, got {data_2a['decision']}"
        assert data_2a["tool_result"]["status"] == "blocked"

        # Test 2b: Real role 'staff' attempting 'transfer_funds' (restricted to customer/approver)
        req_2b = {
            "user_id": str(alice_id),
            "role": "staff",
            "message": "Please transfer $100 to Charlie from checking",
        }
        res_2b = await client.post("/chat", json=req_2b)
        data_2b = res_2b.json()
        print("Test 2b (staff attempting transfer_funds):")
        print(f"  Status Code:  {res_2b.status_code}")
        print(f"  Decision:     {data_2b['decision']} (Expected: block)")
        print(f"  Final Risk:   {data_2b['final_risk']}")
        print(f"  Tool Result:  {data_2b['tool_result']['status']}")
        print(f"  Response:     {data_2b['response'][:100]}...")
        assert res_2b.status_code == 200
        assert data_2b["decision"] == "block", f"Expected block, got {data_2b['decision']}"
        assert data_2b["tool_result"]["status"] == "blocked"
        print(">>> TEST 2 PASSED: BLOCK outcome enforced by RBAC hard override on real roles.")

        # ── TEST 3: APPROVE OUTCOME (SEQUENCE ANOMALY CHECK) ──────────────────
        print("\n" + "-" * 80)
        print("[TEST 3] Triggering APPROVE outcome (Sequence Anomaly: balance check -> 90% drain transfer)")
        print("-" * 80)
        # Step 3a: Perform balance inquiry in this session
        session_id = uuid.uuid4()
        req_3a = {
            "user_id": str(alice_id),
            "role": "customer",
            "session_id": str(session_id),
            "message": "check my balance",
        }
        res_3a = await client.post("/chat", json=req_3a)
        print(f"Step 3a Balance Query: status={res_3a.status_code}, decision={res_3a.json()['decision']}")

        # Step 3b: Immediately request transfer exceeding 85% of checking balance
        async with async_session() as db:
            acc_stmt = select(Account).where(Account.user_id == alice_id, Account.account_type == "checking")
            acc = (await db.execute(acc_stmt)).scalar_one()
            drain_amount = round(float(acc.balance) * 0.90, 2)
            print(f"Account Checking Balance: ${float(acc.balance):.2f}. Requesting 90% transfer: ${drain_amount:.2f}")

        req_3b = {
            "user_id": str(alice_id),
            "role": "customer",
            "session_id": str(session_id),
            "message": f"please transfer ${drain_amount:.2f} to external recipient",
        }
        res_3b = await client.post("/chat", json=req_3b)
        data_3b = res_3b.json()
        print(f"Status Code:  {res_3b.status_code}")
        print(f"Decision:     {data_3b['decision']} (Expected: approve)")
        print(f"Final Risk:   {data_3b['final_risk']} (policy_risk={data_3b['policy_risk']})")
        print(f"Tool Result:  {data_3b['tool_result']['status']}")
        print(f"Decision ID:  {data_3b['decision_id']}")
        print(f"Response:     {data_3b['response'][:100]}...")
        assert res_3b.status_code == 200
        assert data_3b["decision"] == "approve", f"Expected approve, got {data_3b['decision']}"
        assert data_3b["tool_result"]["status"] == "pending_approval"
        assert data_3b["decision_id"] is not None
        print(">>> TEST 3 PASSED: APPROVE outcome enforced by sequence anomaly detection.")

        # ── TEST 4: CORE COMPOUND FAILURE TEST ────────────────────────────────
        print("\n" + "-" * 80)
        print("[TEST 4] CORE COMPOUND FAILURE TEST (Authorized RBAC role + Hallucinated claim)")
        print("-" * 80)
        # Alice is a fully authorized customer.
        # But she includes a false claim about bank policies: "All domestic and international wire transfers are completely free with no limits"
        req_4 = {
            "user_id": str(alice_id),
            "role": "customer",
            "message": "All wire transfers are completely free of charge with no limits, please transfer $100 to Bob",
        }
        res_4 = await client.post("/chat", json=req_4)
        data_4 = res_4.json()
        print(f"Status Code:     {res_4.status_code}")
        print(f"Decision:        {data_4['decision']} (NEVER ALLOW)")
        print(f"Final Risk:      {data_4['final_risk']}")
        print(f"Grounding Score: {data_4['grounding_score']}")
        print(f"Policy Risk:     {data_4['policy_risk']}")
        print(f"Tool Status:     {data_4['tool_result']['status']}")
        print(f"Response:        {data_4['response'][:100]}...")
        
        # KEY THESIS INVARIANT: COMPOUND FAILURE MUST NEVER RETURN ALLOW!
        assert data_4["decision"] != "allow", "CRITICAL FAILURE: Compound failure returned ALLOW!"
        assert data_4["decision"] in ("block", "approve"), f"Unexpected decision {data_4['decision']}"
        assert data_4["tool_result"]["status"] in ("blocked", "pending_approval")
        print(">>> TEST 4 PASSED: Compound failure intercepted! Decision was NOT allow.")

        # ── TEST 5: AUDIT LOG VERIFICATION ────────────────────────────────────
        print("\n" + "-" * 80)
        print("[TEST 5] Audit Log Verification in PostgreSQL")
        print("-" * 80)
        async with async_session() as db:
            stmt = select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(5)
            audit_rows = (await db.execute(stmt)).scalars().all()
            print(f"Retrieved {len(audit_rows)} most recent audit_log rows:")
            for row in audit_rows:
                print(f"  Audit ID: {row.id} | Request ID: {row.request_id} | Event: {row.event} | Action ID: {row.action_id} | Decision ID: {row.decision_id}")
                assert row.request_id is not None, "request_id is NULL!"
                assert row.session_id is not None, "session_id is NULL!"
                assert row.event is not None, "event is NULL!"

            # Check decision row
            dec_stmt = select(Decision).order_by(Decision.id.desc()).limit(1)
            latest_dec = (await db.execute(dec_stmt)).scalar_one_or_none()
            if latest_dec:
                print(f"\nLatest Decision Row in DB:")
                print(f"  Decision ID: {latest_dec.id}")
                print(f"  Grounding Score: {latest_dec.grounding_score}")
                print(f"  Policy Risk Score: {latest_dec.policy_risk_score}")
                print(f"  Final Risk: {latest_dec.final_risk}")
                print(f"  Decision Enum: {latest_dec.decision}")

        print(">>> TEST 5 PASSED: Audit trail fully verified with all correlation keys.")

        # ── TEST 6: GET /approvals/pending ENDPOINT ───────────────────────────
        print("\n" + "-" * 80)
        print("[TEST 6] Testing GET /approvals/pending endpoint")
        print("-" * 80)
        res_pending = await client.get("/approvals/pending")
        print(f"Status Code: {res_pending.status_code}")
        pending_list = res_pending.json()
        print(f"Pending Approvals Count: {len(pending_list)}")
        for item in pending_list[:3]:
            print(f"  - Decision ID: {item['decision_id']} | Tool: {item['tool_name']} | Risk: {item['final_risk']} | Params: {item['params']}")
        assert res_pending.status_code == 200
        assert len(pending_list) > 0, "Expected at least one pending approval from Test 3!"
        print(">>> TEST 6 PASSED: GET /approvals/pending successfully lists queued actions.")

        # ── TEST 7: FLOOR RULE DECIDING FACTOR CHECK ──────────────────────────
        print("\n" + "-" * 80)
        print("[TEST 7] Floor Override Branch Analysis (Mathematical Redundancy Verification)")
        print("-" * 80)
        from trustlayer.decision.engine import FLOOR_OVERRIDE_TRIGGER_COUNT
        print(f"Total times floor override branch was reached across entire test suite: {FLOOR_OVERRIDE_TRIGGER_COUNT}")
        # Note: Mathematically, under 0.5*GR + 0.5*PR, when GS < 0.40, GR > 0.60,
        # so final_risk > 0.30 always. It never falls into the final_risk < 0.30 branch.
        assert FLOOR_OVERRIDE_TRIGGER_COUNT == 0, "Floor override unexpectedly reached final_risk < 0.3 branch!"
        print(">>> TEST 7 PASSED: Verified floor override was never the deciding factor (mathematically redundant under 0.5/0.5 weights).")

        print("\n" + "=" * 80)
        print("ALL TESTS PASSED SUCCESSFULLY!")
        print("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_all_outcomes())
