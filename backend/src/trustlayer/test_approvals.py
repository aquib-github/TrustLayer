"""Integration test suite for the TrustLayer held action approval workflow."""
import asyncio
import uuid
from decimal import Decimal
from datetime import datetime
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from trustlayer.db.models import (
    Account,
    AgentAction,
    AuditLog,
    ChatSession,
    Decision,
    DecisionType,
    Transaction,
    User,
)
from trustlayer.db.session import async_session
from trustlayer.main import app


async def create_held_approval_in_db(
    user_id: uuid.UUID,
    tool_name: str,
    params: dict,
) -> tuple[uuid.UUID, uuid.UUID]:
    # Helper to insert a fresh held action and pending decision directly into PostgreSQL
    async with async_session() as db:
        session_obj = ChatSession(
            id=uuid.uuid4(),
            user_id=user_id,
            started_at=datetime.utcnow(),
        )
        db.add(session_obj)

        action_obj = AgentAction(
            id=uuid.uuid4(),
            session_id=session_obj.id,
            tool_name=tool_name,
            params_json=params,
            proposed_at=datetime.utcnow(),
        )
        db.add(action_obj)

        decision_obj = Decision(
            id=uuid.uuid4(),
            action_id=action_obj.id,
            grounding_score=Decimal("0.2000"),
            policy_risk_score=Decimal("0.8000"),
            final_risk=Decimal("0.5000"),
            decision=DecisionType.APPROVE,
            approver_id=None,
            resolved_at=None,
        )
        db.add(decision_obj)
        await db.commit()
        return decision_obj.id, action_obj.id


async def run_approvals_test_suite():
    # Test identifiers for seeded users
    alice_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    sarah_id = uuid.UUID("44444444-4444-4444-4444-444444444444")
    eleanor_id = uuid.UUID("66666666-6666-6666-6666-666666666666")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        print("Starting TrustLayer Approval Flow Tests...")

        # 1. Test Role Gating on GET /approvals/pending
        res_no_role = await client.get("/approvals/pending")
        assert res_no_role.status_code == 403, f"Expected 403 for unauthenticated GET, got {res_no_role.status_code}"

        res_cust = await client.get("/approvals/pending", headers={"X-User-Role": "customer"})
        assert res_cust.status_code == 403, f"Expected 403 for customer GET, got {res_cust.status_code}"

        res_staff = await client.get("/approvals/pending", headers={"X-User-Role": "staff"})
        assert res_staff.status_code == 403, f"Expected 403 for staff GET, got {res_staff.status_code}"

        res_approver = await client.get("/approvals/pending", headers={"X-User-Role": "approver"})
        assert res_approver.status_code == 200, f"Expected 200 for approver GET, got {res_approver.status_code}"
        print("Role gating verified for GET /approvals/pending.")

        # 2. Test Pagination and Newest-First Ordering
        dec_id_old, _ = await create_held_approval_in_db(alice_id, "transfer_funds", {"amount": 10.0, "recipient": "OldItem"})
        await asyncio.sleep(0.01)
        dec_id_new, _ = await create_held_approval_in_db(alice_id, "transfer_funds", {"amount": 20.0, "recipient": "NewItem"})

        res_page = await client.get("/approvals/pending?limit=2&offset=0", headers={"X-User-Role": "approver"})
        assert res_page.status_code == 200
        page_items = res_page.json()
        assert len(page_items) >= 2, "Expected at least 2 items in page"
        # Verify newest item appears before older item
        returned_ids = [item["decision_id"] for item in page_items]
        assert str(dec_id_new) in returned_ids
        assert returned_ids.index(str(dec_id_new)) < returned_ids.index(str(dec_id_old))
        print("Pagination and newest-first ordering verified.")

        # 3. Test Role Gating on POST /approvals/{id}/approve and POST /approvals/{id}/reject
        dec_gate_id, _ = await create_held_approval_in_db(alice_id, "transfer_funds", {"amount": 5.0, "recipient": "Bob"})

        res_app_cust = await client.post(f"/approvals/{dec_gate_id}/approve", headers={"X-User-Role": "customer"})
        assert res_app_cust.status_code == 403, "Customer should be rejected with 403"

        res_app_staff = await client.post(f"/approvals/{dec_gate_id}/approve", headers={"X-User-Role": "staff"})
        assert res_app_staff.status_code == 403, "Staff should be rejected with 403"

        res_app_body_cust = await client.post(
            f"/approvals/{dec_gate_id}/approve",
            json={"role": "customer", "user_id": str(alice_id)},
        )
        assert res_app_body_cust.status_code == 403, "Customer in body should be rejected with 403"

        res_rej_cust = await client.post(f"/approvals/{dec_gate_id}/reject", headers={"X-User-Role": "customer"})
        assert res_rej_cust.status_code == 403, "Customer reject should be rejected with 403"

        res_rej_staff = await client.post(f"/approvals/{dec_gate_id}/reject", headers={"X-User-Role": "staff"})
        assert res_rej_staff.status_code == 403, "Staff reject should be rejected with 403"
        print("Role gating verified for POST approve and reject endpoints.")

        # 4. Test Approve Executes Held Action and Marks it Executed
        dec_approve_id, _ = await create_held_approval_in_db(
            alice_id,
            "transfer_funds",
            {"amount": 15.0, "recipient": "ApprovedRecipient", "from_account_type": "checking"},
        )

        async with async_session() as db:
            tx_count_before = await db.scalar(select(func.count()).select_from(Transaction))

        # Perform approval as approver
        res_approve = await client.post(
            f"/approvals/{dec_approve_id}/approve",
            headers={"X-User-Role": "approver", "X-User-Id": str(eleanor_id)},
            json={"reason": "Manual risk review passed"},
        )
        assert res_approve.status_code == 200, f"Approve failed with status {res_approve.status_code}"
        approve_data = res_approve.json()
        assert approve_data["decision"] == "approve"
        assert approve_data["status"] == "executed"
        assert approve_data["tool_result"]["status"] == "success"
        assert approve_data["approver_id"] == str(eleanor_id)

        # Verify tool action actually executed once against the mock ledger
        async with async_session() as db:
            tx_count_after = await db.scalar(select(func.count()).select_from(Transaction))
            assert tx_count_after == tx_count_before + 1, "Transaction was not executed in ledger!"

            # Verify decision record was marked resolved
            dec_row = (await db.execute(select(Decision).where(Decision.id == dec_approve_id))).scalar_one()
            assert dec_row.resolved_at is not None, "resolved_at must be populated"
            assert dec_row.approver_id == eleanor_id, "approver_id must be populated"

        print("Approve flow execution and database state update verified.")

        # 5. Test Double-Resolve Returns 409 Conflict (No Double Execution)
        res_double_approve = await client.post(
            f"/approvals/{dec_approve_id}/approve",
            headers={"X-User-Role": "approver"},
        )
        assert res_double_approve.status_code == 409, f"Expected 409 on second approve, got {res_double_approve.status_code}"

        res_double_reject = await client.post(
            f"/approvals/{dec_approve_id}/reject",
            headers={"X-User-Role": "approver"},
        )
        assert res_double_reject.status_code == 409, f"Expected 409 on reject after approve, got {res_double_reject.status_code}"

        # Verify no double execution took place
        async with async_session() as db:
            tx_count_final = await db.scalar(select(func.count()).select_from(Transaction))
            assert tx_count_final == tx_count_after, "Ledger transaction count changed on 409 call!"
        print("Double-resolve 409 conflict and prevention of double execution verified.")

        # 6. Test Reject Marks Action Rejected and Never Executes Tool
        dec_reject_id, _ = await create_held_approval_in_db(
            alice_id,
            "transfer_funds",
            {"amount": 500.0, "recipient": "NeverExecutedRecipient", "from_account_type": "checking"},
        )

        async with async_session() as db:
            tx_count_pre_reject = await db.scalar(select(func.count()).select_from(Transaction))

        # Perform rejection as approver
        res_reject = await client.post(
            f"/approvals/{dec_reject_id}/reject",
            headers={"X-User-Role": "approver", "X-User-Id": str(eleanor_id)},
            json={"reason": "Fraud suspected on recipient"},
        )
        assert res_reject.status_code == 200, f"Reject failed with status {res_reject.status_code}"
        reject_data = res_reject.json()
        assert reject_data["decision"] == "reject"
        assert reject_data["status"] == "rejected"
        assert reject_data["tool_result"] is None
        assert reject_data["approver_id"] == str(eleanor_id)

        # Verify tool action was never executed
        async with async_session() as db:
            tx_count_post_reject = await db.scalar(select(func.count()).select_from(Transaction))
            assert tx_count_post_reject == tx_count_pre_reject, "Transaction was unexpectedly executed upon rejection!"

            dec_rej_row = (await db.execute(select(Decision).where(Decision.id == dec_reject_id))).scalar_one()
            assert dec_rej_row.resolved_at is not None, "resolved_at must be populated on rejection"
            assert dec_rej_row.approver_id == eleanor_id, "approver_id must be populated on rejection"

        # Verify double-resolve on rejected action also returns 409
        res_rej_again = await client.post(f"/approvals/{dec_reject_id}/reject", headers={"X-User-Role": "approver"})
        assert res_rej_again.status_code == 409
        res_app_on_rej = await client.post(f"/approvals/{dec_reject_id}/approve", headers={"X-User-Role": "approver"})
        assert res_app_on_rej.status_code == 409
        print("Reject flow and non-execution verified.")

        # 7. Test Audit Entries Written in PostgreSQL
        async with async_session() as db:
            # Query audit row for the approved decision
            approve_audit_stmt = select(AuditLog).where(AuditLog.decision_id == dec_approve_id)
            approve_audit = (await db.execute(approve_audit_stmt)).scalar_one_or_none()
            assert approve_audit is not None, "AuditLog entry for approval was not written!"
            assert "action_approved" in approve_audit.event
            assert str(eleanor_id) in approve_audit.event
            assert "Manual risk review passed" in approve_audit.event
            assert approve_audit.timestamp is not None

            # Query audit row for the rejected decision
            reject_audit_stmt = select(AuditLog).where(AuditLog.decision_id == dec_reject_id)
            reject_audit = (await db.execute(reject_audit_stmt)).scalar_one_or_none()
            assert reject_audit is not None, "AuditLog entry for rejection was not written!"
            assert "action_rejected" in reject_audit.event
            assert str(eleanor_id) in reject_audit.event
            assert "Fraud suspected on recipient" in reject_audit.event
            assert reject_audit.timestamp is not None

        print("Audit entries verification passed.")
        print("All approval flow tests passed successfully!")


if __name__ == "__main__":
    asyncio.run(run_approvals_test_suite())
