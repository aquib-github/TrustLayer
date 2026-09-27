"""
Seed script for synthetic banking knowledge base documents.

NOTE: All policies contained herein are completely SYNTHETIC and designed solely
for research and demonstration of TrustLayer's access-aware RAG and claim-grounding pipeline.
They do NOT represent any actual bank's real policies or private information.
"""

import asyncio
import uuid
from typing import Any

from sqlalchemy import select
from trustlayer.db.models import KBDocument
from trustlayer.db.session import async_session

SYNTHETIC_DOCUMENTS: list[dict[str, Any]] = [
    {
        "id": uuid.UUID("f0000001-0000-0000-0000-000000000001"),
        "title": "Overdraft Protection and Fee Schedule",
        "content": (
            "Standard checking accounts include an optional $100 overdraft buffer for qualifying accounts in good standing. "
            "Transactions that exceed the account balance within the buffer incur no penalty fee if repaid within 48 hours. "
            "Overdraft transactions exceeding the buffer incur a standard $35 overdraft fee per transaction, capped at a maximum "
            "of three fees ($105 total) in a single business day. Accounts open fewer than 90 days are not eligible for the buffer."
        ),
        "source": "synthetic",
        "role_scope": ["customer", "staff", "approver"],
    },
    {
        "id": uuid.UUID("f0000001-0000-0000-0000-000000000002"),
        "title": "Courtesy Fee Waiver Policy",
        "content": (
            "Customers may receive up to two courtesy fee waivers per calendar year for late payment or overdraft fees, provided the "
            "account has been maintained with no chargeback disputes for at least six consecutive months. Standard front-line staff "
            "can process waivers up to $50 per occurrence. Any waiver request exceeding $50 or any request for a third waiver in a year "
            "requires authorization from a Senior Staff / Approver."
        ),
        "source": "synthetic",
        "role_scope": ["customer", "staff", "approver"],
    },
    {
        "id": uuid.UUID("f0000001-0000-0000-0000-000000000003"),
        "title": "Daily Electronic Transfer and Withdrawal Limits",
        "content": (
            "Customers using digital online banking are subject to a daily electronic transfer limit of $2,500 for external ACH and P2P transfers. "
            "ATM cash withdrawals are limited to $1,000 per card per 24-hour cycle. Bank staff may authorize temporary limit increases up to "
            "$10,000 upon positive identity verification. Permanent limit increases or single transfers exceeding $10,000 strictly require "
            "manager/approver review."
        ),
        "source": "synthetic",
        "role_scope": ["customer", "staff", "approver"],
    },
    {
        "id": uuid.UUID("f0000001-0000-0000-0000-000000000004"),
        "title": "Debit Card Emergency Blocking and Fraud Prevention",
        "content": (
            "Customers can request an immediate card freeze or emergency block at any time via the automated assistant, mobile app, or phone. "
            "A temporary freeze can be reversed by the customer within 30 days. However, cards reported lost or stolen are permanently canceled "
            "and cannot be unblocked under any circumstances. A replacement debit card is issued automatically and arrives within 3 to 5 business days."
        ),
        "source": "synthetic",
        "role_scope": ["customer", "staff", "approver"],
    },
    {
        "id": uuid.UUID("f0000001-0000-0000-0000-000000000005"),
        "title": "Dispute Filing Deadlines and Provisional Credit Rules",
        "content": (
            "Disputes for unauthorized debit card charges or billing errors must be filed within 60 days of the date on the account statement "
            "showing the transaction. For eligible unauthorized claims exceeding $25, provisional credit is credited to the customer account "
            "within 10 business days while the merchant investigation is conducted. The investigation window concludes within 45 calendar days."
        ),
        "source": "synthetic",
        "role_scope": ["customer", "staff", "approver"],
    },
    {
        "id": uuid.UUID("f0000001-0000-0000-0000-000000000006"),
        "title": "Domestic and International Wire Transfer Guidelines",
        "content": (
            "Domestic wire requests submitted before 4:00 PM EST on business days are processed same-day with a fixed fee of $25. "
            "Incoming domestic wires are free of charge. International wire transfers require 1 to 3 business days for completion, incur a $45 "
            "outgoing transfer fee, and require full recipient SWIFT/BIC and IBAN details. Wire cancellation is only possible before release to the network."
        ),
        "source": "synthetic",
        "role_scope": ["customer", "staff", "approver"],
    },
    {
        "id": uuid.UUID("f0000001-0000-0000-0000-000000000007"),
        "title": "Checking Account Monthly Maintenance Fee Exemption",
        "content": (
            "The standard checking account carries a monthly maintenance fee of $12. This fee is automatically waived if the customer maintains "
            "a minimum daily ledger balance of at least $500 throughout the statement cycle, or receives qualifying direct deposits totaling at least "
            "$1,000 per month. Student accounts under age 24 are permanently exempt from monthly maintenance fees."
        ),
        "source": "synthetic",
        "role_scope": ["customer", "staff", "approver"],
    },
    {
        "id": uuid.UUID("f0000001-0000-0000-0000-000000000008"),
        "title": "Internal Sequence Anomaly and Anti-Draining Rules",
        "content": (
            "INTERNAL BANK COMPLIANCE ONLY: Automated monitoring flags account activity as a sequence anomaly when an account balance inquiry is "
            "followed within five minutes by a transfer or withdrawal request exceeding 85% of total account funds. Such transactions must not execute "
            "automatically; the session must be routed for step-up multi-factor re-authentication or human approver sign-off."
        ),
        "source": "synthetic",
        "role_scope": ["staff", "approver"],  # Staff and Approver only - not customer accessible
    },
    {
        "id": uuid.UUID("f0000001-0000-0000-0000-000000000009"),
        "title": "Personal and Auto Loan Grace Period and Late Charges",
        "content": (
            "Monthly installment payments for consumer auto and personal loans have a strict 10-calendar-day grace period following the scheduled "
            "due date. Payments received on or before the 10th day incur no late fee. Payments received on day 11 or later incur a late charge of "
            "5% of the unpaid monthly installment or $25, whichever is greater. Interest continues to accrue during the grace period."
        ),
        "source": "synthetic",
        "role_scope": ["customer", "staff", "approver"],
    },
    {
        "id": uuid.UUID("f0000001-0000-0000-0000-000000000010"),
        "title": "Manager Escalation and Human-in-the-Loop Thresholds",
        "content": (
            "INTERNAL POLICY FOR APPROVAL WORKFLOW: Customer requests that involve financial restitution over $100, wire limit increases above $25,000, "
            "or actions where automated grounding verification scores fall below 0.40 on money-moving tools cannot be resolved by front-line staff. "
            "These actions must be queued in the Approver Portal for Senior Staff review and signed off prior to ledger execution."
        ),
        "source": "synthetic",
        "role_scope": ["staff", "approver"],  # Staff and Approver only
    },
    {
        "id": uuid.UUID("f0000001-0000-0000-0000-000000000011"),
        "title": "Check Deposit Availability and Extended Hold Terms",
        "content": (
            "Under Regulation CC standards, the first $500 of deposited checks (mobile or ATM) is made available on the next business day. "
            "The remaining balance is available on the second business day. Deposits over $5,525, deposits into accounts open less than 30 days, "
            "or checks with unverified issuer funds are subject to exception holds extending up to 7 business days."
        ),
        "source": "synthetic",
        "role_scope": ["customer", "staff", "approver"],
    },
    {
        "id": uuid.UUID("f0000001-0000-0000-0000-000000000012"),
        "title": "International Travel and Foreign Transaction Assessments",
        "content": (
            "Debit card purchases made in foreign currencies or processed outside the United States are subject to an international transaction fee "
            "of 3% of the total purchase amount. Customers traveling internationally should place a travel notice on their debit card via mobile "
            "banking at least 24 hours prior to travel to prevent card activity from being blocked by automated fraud filters."
        ),
        "source": "synthetic",
        "role_scope": ["customer", "staff", "approver"],
    },
]


async def seed_kb_documents() -> None:
    """Populates kb_documents with synthetic policies."""
    async with async_session() as session:
        result = await session.execute(select(KBDocument.id))
        existing_ids = set(result.scalars().all())

        added_count = 0
        for doc_data in SYNTHETIC_DOCUMENTS:
            if doc_data["id"] in existing_ids:
                continue
            doc = KBDocument(
                id=doc_data["id"],
                title=doc_data["title"],
                content=doc_data["content"],
                source=doc_data["source"],
                role_scope=doc_data["role_scope"],
            )
            session.add(doc)
            added_count += 1

        await session.commit()
        print(f"Seeded {added_count} synthetic KB documents (total documents: {len(SYNTHETIC_DOCUMENTS)}).")


if __name__ == "__main__":
    asyncio.run(seed_kb_documents())
