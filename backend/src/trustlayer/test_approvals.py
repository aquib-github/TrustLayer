"""Hardened integration test suite for the TrustLayer held action approval workflow."""
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
            resolution_status=None,
            approver_id=None,
            resolved_at=None,
        )
        db.add(decision_obj)
        await db.commit()
        return decision_obj.id, action_obj.id


async def run_approvals_test_suite():
    # Identifiers for seeded users across different roles
    alice_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    sarah_id = uuid.UUID("44444444-4444-4444-4444-444444444444")
    eleanor_id = uuid.UUID("66666666-6666-6666-6666-666666666666")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        print("Starting Hardened TrustLayer Approval Flow Tests...")

        # 1. Test Role Gating strictly derived from database records
        res_no_id = await client.get("/approvals/pending")
        assert res_no_id.status_code == 403, "Missing X-User-Id header must return 403"

        res_invalid_id = await client.get("/approvals/pending", headers={"X-User-Id": "not-a-uuid"})
        assert res_invalid_id.status_code == 403, "Invalid UUID in X-User-Id must return 403"

        res_cust = await client.get("/approvals/pending", headers={"X-User-Id": str(alice_id)})
        assert res_cust.status_code == 403, "Customer user ID must return 403"

        res_staff = await client.get("/approvals/pending", headers={"X-User-Id": str(sarah_id)})
        assert res_staff.status_code == 403, "Staff user ID must return 403"

        # Verify client-supplied role strings without valid approver ID are strictly rejected
        res_role_bypass = await client.get("/approvals/pending?role=approver", headers={"X-User-Id": str(alice_id)})
        assert res_role_bypass.status_code == 403, "Role query parameter override attempt must be ignored and return 403"

        res_approver = await client.get("/approvals/pending", headers={"X-User-Id": str(eleanor_id)})
        assert res_approver.status_code == 200, "Approver user ID must return 200"
        print("Role gating verified: role is strictly derived from user DB record.")

        # 2. Test Pagination, Newest-First Ordering, and Pending Resolution Status
        dec_id_old, _ = await create_held_approval_in_db(alice_id, "transfer_funds", {"amount": 10.0, "recipient": "OldItem"})
        await asyncio.sleep(0.01)
        dec_id_new, _ = await create_held_approval_in_db(alice_id, "transfer_funds", {"amount": 20.0, "recipient": "NewItem"})

        res_page = await client.get("/approvals/pending?limit=2&offset=0", headers={"X-User-Id": str(eleanor_id)})
        assert res_page.status_code == 200
        page_items = res_page.json()
        assert len(page_items) >= 2, "Expected at least 2 items in page"
        # Verify resolution_status exposed as pending
        assert page_items[0]["resolution_status"] == "pending"
        # Verify newest item appears before older item
        returned_ids = [item["decision_id"] for item in page_items]
        assert str(dec_id_new) in returned_ids
        assert returned_ids.index(str(dec_id_new)) < returned_ids.index(str(dec_id_old))
        print("Pagination, newest-first ordering, and pending resolution status verified.")

        # 3. Test Role Gating on POST endpoints
        dec_gate_id, _ = await create_held_approval_in_db(alice_id, "transfer_funds", {"amount": 5.0, "recipient": "Bob"})

        res_app_no_auth = await client.post(f"/approvals/{dec_gate_id}/approve")
        assert res_app_no_auth.status_code == 403, "Unauthenticated approve must return 403"

        res_app_cust = await client.post(f"/approvals/{dec_gate_id}/approve", headers={"X-User-Id": str(alice_id)})
        assert res_app_cust.status_code == 403, "Customer approve must return 403"

        res_app_staff = await client.post(f"/approvals/{dec_gate_id}/approve", headers={"X-User-Id": str(sarah_id)})
        assert res_app_staff.status_code == 403, "Staff approve must return 403"

        res_rej_cust = await client.post(f"/approvals/{dec_gate_id}/reject", headers={"X-User-Id": str(alice_id)})
        assert res_rej_cust.status_code == 403, "Customer reject must return 403"

        res_rej_staff = await client.post(f"/approvals/{dec_gate_id}/reject", headers={"X-User-Id": str(sarah_id)})
        assert res_rej_staff.status_code == 403, "Staff reject must return 403"
        print("Role gating on POST approve and reject endpoints verified.")

        # 4. Test Approve Executes Held Action, Sets Resolution Status, and Updates Ledger
        dec_approve_id, _ = await create_held_approval_in_db(
            alice_id,
            "transfer_funds",
            {"amount": 15.0, "recipient": "ApprovedRecipient", "from_account_type": "checking"},
        )

        async with async_session() as db:
            tx_count_before = await db.scalar(select(func.count()).select_from(Transaction))

        res_approve = await client.post(
            f"/approvals/{dec_approve_id}/approve",
            headers={"X-User-Id": str(eleanor_id)},
            json={"reason": "Manual risk review passed"},
        )
        assert res_approve.status_code == 200, f"Approve failed with status {res_approve.status_code}"
        approve_data = res_approve.json()
        assert approve_data["decision"] == "approve"
        assert approve_data["resolution_status"] == "approved", "Expected resolution_status == 'approved' in API response"
        assert approve_data["status"] == "executed"
        assert approve_data["tool_result"]["status"] == "success"
        assert approve_data["approver_id"] == str(eleanor_id)

        # Verify tool action executed exactly once against mock ledger
        async with async_session() as db:
            tx_count_after = await db.scalar(select(func.count()).select_from(Transaction))
            assert tx_count_after == tx_count_before + 1, "Transaction was not executed in ledger!"

            # Verify decision record resolution_status and timestamps in database
            dec_row = (await db.execute(select(Decision).where(Decision.id == dec_approve_id))).scalar_one()
            assert dec_row.resolved_at is not None, "resolved_at must be populated"
            assert dec_row.resolution_status == "approved", "resolution_status on DB row must be 'approved'"
            assert dec_row.approver_id == eleanor_id, "approver_id must be populated"

        print("Approve execution and explicit resolution_status verified.")

        # 5. Test Double-Resolve Returns 409 Conflict
        res_double_approve = await client.post(
            f"/approvals/{dec_approve_id}/approve",
            headers={"X-User-Id": str(eleanor_id)},
        )
        assert res_double_approve.status_code == 409, f"Expected 409 on second approve, got {res_double_approve.status_code}"

        res_double_reject = await client.post(
            f"/approvals/{dec_approve_id}/reject",
            headers={"X-User-Id": str(eleanor_id)},
        )
        assert res_double_reject.status_code == 409, f"Expected 409 on reject after approve, got {res_double_reject.status_code}"

        async with async_session() as db:
            tx_count_final = await db.scalar(select(func.count()).select_from(Transaction))
            assert tx_count_final == tx_count_after, "Ledger transaction count changed on 409 call!"
        print("Double-resolve 409 conflict verified.")

        # 6. Test Reject Sets Resolution Status and Never Executes Tool
        dec_reject_id, _ = await create_held_approval_in_db(
            alice_id,
            "transfer_funds",
            {"amount": 500.0, "recipient": "NeverExecutedRecipient", "from_account_type": "checking"},
        )

        async with async_session() as db:
            tx_count_pre_reject = await db.scalar(select(func.count()).select_from(Transaction))

        res_reject = await client.post(
            f"/approvals/{dec_reject_id}/reject",
            headers={"X-User-Id": str(eleanor_id)},
            json={"reason": "Fraud suspected on recipient"},
        )
        assert res_reject.status_code == 200, f"Reject failed with status {res_reject.status_code}"
        reject_data = res_reject.json()
        assert reject_data["decision"] == "reject"
        assert reject_data["resolution_status"] == "rejected", "Expected resolution_status == 'rejected' in API response"
        assert reject_data["status"] == "rejected"
        assert reject_data["tool_result"] is None
        assert reject_data["approver_id"] == str(eleanor_id)

        # Verify tool action was never executed
        async with async_session() as db:
            tx_count_post_reject = await db.scalar(select(func.count()).select_from(Transaction))
            assert tx_count_post_reject == tx_count_pre_reject, "Transaction was unexpectedly executed upon rejection!"

            dec_rej_row = (await db.execute(select(Decision).where(Decision.id == dec_reject_id))).scalar_one()
            assert dec_rej_row.resolved_at is not None, "resolved_at must be populated on rejection"
            assert dec_rej_row.resolution_status == "rejected", "resolution_status on DB row must be 'rejected'"
            assert dec_rej_row.approver_id == eleanor_id, "approver_id must be populated on rejection"

        res_rej_again = await client.post(f"/approvals/{dec_reject_id}/reject", headers={"X-User-Id": str(eleanor_id)})
        assert res_rej_again.status_code == 409
        print("Reject flow and non-execution verified.")

        # 7. Test Concurrency: Simultaneous Approve Calls (Exactly One Executes, Other Gets 409)
        dec_concurrent_id, _ = await create_held_approval_in_db(
            alice_id,
            "transfer_funds",
            {"amount": 25.0, "recipient": "ConcurrentRecipient", "from_account_type": "checking"},
        )

        async with async_session() as db:
            tx_count_pre_concurrent = await db.scalar(select(func.count()).select_from(Transaction))

        # Launch two simultaneous approve requests concurrently
        results = await asyncio.gather(
            client.post(f"/approvals/{dec_concurrent_id}/approve", headers={"X-User-Id": str(eleanor_id)}, json={"reason": "Concurrent Attempt 1"}),
            client.post(f"/approvals/{dec_concurrent_id}/approve", headers={"X-User-Id": str(eleanor_id)}, json={"reason": "Concurrent Attempt 2"}),
        )
        status_codes = sorted([r.status_code for r in results])
        assert status_codes == [200, 409], f"Expected exactly one 200 and one 409, got {status_codes}"

        # Verify tool action was executed strictly once
        async with async_session() as db:
            tx_count_post_concurrent = await db.scalar(select(func.count()).select_from(Transaction))
            assert tx_count_post_concurrent == tx_count_pre_concurrent + 1, "Concurrent approve executed more or less than once!"
        print("Atomic resolve concurrency test verified: exactly one succeeded, other received 409.")

        # 8. Test Structured Audit Log in PostgreSQL
        async with async_session() as db:
            # Query structured audit row for approved decision
            approve_audit_stmt = select(AuditLog).where(AuditLog.decision_id == dec_approve_id)
            approve_audit = (await db.execute(approve_audit_stmt)).scalar_one_or_none()
            assert approve_audit is not None, "AuditLog entry for approval was not written!"
            assert approve_audit.event_type == "action_approved", f"Expected event_type 'action_approved', got {approve_audit.event_type}"
            assert approve_audit.actor_id == eleanor_id, f"Expected actor_id {eleanor_id}, got {approve_audit.actor_id}"
            assert approve_audit.decision_id == dec_approve_id, "Audit decision_id mismatch"
            assert approve_audit.reason == "Manual risk review passed", f"Expected structured reason, got {approve_audit.reason}"
            assert "action_approved" in (approve_audit.event or ""), "Readable event string must be retained"
            assert approve_audit.timestamp is not None

            # Query structured audit row for rejected decision
            reject_audit_stmt = select(AuditLog).where(AuditLog.decision_id == dec_reject_id)
            reject_audit = (await db.execute(reject_audit_stmt)).scalar_one_or_none()
            assert reject_audit is not None, "AuditLog entry for rejection was not written!"
            assert reject_audit.event_type == "action_rejected", f"Expected event_type 'action_rejected', got {reject_audit.event_type}"
            assert reject_audit.actor_id == eleanor_id, f"Expected actor_id {eleanor_id}, got {reject_audit.actor_id}"
            assert reject_audit.decision_id == dec_reject_id, "Audit decision_id mismatch"
            assert reject_audit.reason == "Fraud suspected on recipient", f"Expected structured reason, got {reject_audit.reason}"
            assert "action_rejected" in (reject_audit.event or ""), "Readable event string must be retained"
            assert reject_audit.timestamp is not None

        print("Structured audit log columns verified.")
        print("All hardened approval flow tests passed successfully!")


if __name__ == "__main__":
    asyncio.run(run_approvals_test_suite())
