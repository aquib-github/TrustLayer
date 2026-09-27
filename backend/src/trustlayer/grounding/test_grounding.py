"""
Standalone Grounding Test Script for TrustLayer (Module 1).

Runs hand-crafted test claims (true/grounded + false/hallucinated) through the
full extract -> retrieve -> verify pipeline and prints grounding_scores.

Usage:
    uv run python -m trustlayer.grounding.test_grounding

Prerequisites:
    1. kb_documents seeded:   uv run python -m trustlayer.db.seed_kb
    2. kb_embeddings populated: uv run python -m trustlayer.grounding.populate_embeddings

NOTE: This script uses SYNTHETIC knowledge base content. All claims below are
hand-crafted test data referencing the synthetic policies in db/seed_kb.py.
"""

import asyncio
import json
import logging
import sys
import time

from trustlayer.grounding.extract import extract_claims
from trustlayer.grounding.retrieve import retrieve_evidence
from trustlayer.grounding.verify import calculate_grounding_score, verify_claim_against_chunks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── Test cases ────────────────────────────────────────────────────────────
# Each case is a dict with:
#   - "label": descriptive label for the test
#   - "text": agent response text containing claims to extract and verify
#   - "role": the role scope for retrieval
#   - "expected": whether the claim(s) should be grounded or not
#                 ("grounded" = high score, "hallucinated" = low score)

TEST_CASES = [
    # ── TRUE / GROUNDED claims (should produce HIGH grounding_scores) ───
    {
        "label": "TRUE: Overdraft fee amount",
        "text": "Your account has been charged the standard $35 overdraft fee. Overdraft fees are capped at a maximum of three fees totaling $105 in a single business day.",
        "role": "customer",
        "expected": "grounded",
    },
    {
        "label": "TRUE: Dispute filing deadline",
        "text": "You must file a dispute for unauthorized charges within 60 days of the statement date. For eligible claims over $25, provisional credit is applied within 10 business days.",
        "role": "customer",
        "expected": "grounded",
    },
    {
        "label": "TRUE: Daily transfer limit",
        "text": "Your daily electronic transfer limit for external ACH and P2P transfers is $2,500.",
        "role": "customer",
        "expected": "grounded",
    },
    {
        "label": "TRUE: Card freeze reversal window",
        "text": "A temporary card freeze can be reversed by you within 30 days. Cards reported lost or stolen are permanently canceled and cannot be unblocked.",
        "role": "customer",
        "expected": "grounded",
    },
    # ── FALSE / HALLUCINATED claims (should produce LOW grounding_scores) ───
    {
        "label": "HALLUCINATED: Wrong overdraft fee",
        "text": "The overdraft fee for your account is only $10 per transaction and there is no daily limit on how many fees can be charged.",
        "role": "customer",
        "expected": "hallucinated",
    },
    {
        "label": "HALLUCINATED: Wrong dispute deadline",
        "text": "You have up to 180 days to file a dispute for unauthorized charges, and provisional credit is issued within 24 hours automatically.",
        "role": "customer",
        "expected": "hallucinated",
    },
    {
        "label": "HALLUCINATED: Made-up free wire policy",
        "text": "All domestic and international wire transfers are completely free of charge for all customers with no limits.",
        "role": "customer",
        "expected": "hallucinated",
    },
    {
        "label": "HALLUCINATED: Non-existent crypto policy",
        "text": "The bank offers cryptocurrency trading directly through your checking account with zero fees and instant settlement.",
        "role": "customer",
        "expected": "hallucinated",
    },
    # ── ROLE-SCOPE test (staff-only doc should NOT be found for customer) ─
    {
        "label": "ROLE-SCOPE: Internal anti-draining rule (customer role — should have low grounding)",
        "text": "The bank's internal policy flags balance checks followed by large transfers as sequence anomalies requiring re-authentication.",
        "role": "customer",
        "expected": "hallucinated",  # customer can't see staff-only docs
    },
    {
        "label": "ROLE-SCOPE: Internal anti-draining rule (staff role — should be grounded)",
        "text": "The bank's internal policy flags balance checks followed by large transfers exceeding 85% of account funds as sequence anomalies.",
        "role": "staff",
        "expected": "grounded",
    },
]


async def run_test_case(case: dict, case_num: int) -> dict:
    """Run a single test case through extract -> retrieve -> verify -> aggregate."""
    print(f"\n{'='*80}")
    print(f"  TEST {case_num}: {case['label']}")
    print(f"  Expected: {case['expected'].upper()}")
    print(f"  Role: {case['role']}")
    print(f"  Text: \"{case['text'][:100]}{'...' if len(case['text']) > 100 else ''}\"")
    print(f"{'='*80}")

    t0 = time.time()

    # Step 1: Extract claims (using Gemini API)
    print("\n  [1/3] Extracting claims via Gemini...")
    extraction = extract_claims(case["text"])
    claims = extraction["claims"]
    source = extraction["source"]
    print(f"        Source: {source}")
    print(f"        Claims extracted: {len(claims)}")
    for i, c in enumerate(claims):
        print(f"          {i+1}. \"{c}\"")

    # Step 2 & 3: For each claim, retrieve evidence + verify via NLI
    # Pipeline per TRD S3: retrieve (step 3) -> NLI verify (step 4) -> aggregate (step 5)
    claim_verifications = []
    for claim in claims:
        print(f"\n  [2/3] Retrieving evidence for: \"{claim[:80]}...\"")
        evidence = await retrieve_evidence(claim, role=case["role"], top_k=3)
        print(f"        Retrieved {len(evidence)} evidence chunks:")
        for j, ev in enumerate(evidence):
            print(f"          {j+1}. [{ev['doc_title']}] sim={ev['similarity']:.4f}")

        print(f"  [3/3] NLI verification...")
        # verify_claim_against_chunks runs DeBERTa NLI on each (claim, chunk) pair
        # and returns per-claim status: ENTAILED / CONTRADICTED / NEUTRAL
        verification = verify_claim_against_chunks(claim, evidence, role=case["role"])
        claim_verifications.append(verification)
        print(f"        status          = {verification['status']}")
        print(f"        probs: E={verification['entailment_prob']:.4f}  "
              f"N={verification['neutral_prob']:.4f}  "
              f"C={verification['contradiction_prob']:.4f}")
        if verification['role_restricted_chunks_filtered'] > 0:
            print(f"        role-filtered   = {verification['role_restricted_chunks_filtered']} chunks excluded")
        for k, pcr in enumerate(verification.get('per_chunk_results', [])):
            print(f"          chunk {k+1}: [{pcr.get('doc_title', '?')}]")
            print(f"            text: \"{pcr.get('chunk_text', '')}\"")
            print(f"            nli:  E={pcr['entailment_prob']:.4f}  "
                  f"N={pcr['neutral_prob']:.4f}  "
                  f"C={pcr['contradiction_prob']:.4f}  "
                  f"-> {pcr['nli_label']}")

    # Step 5 (TRD S3): Aggregate all claim verifications into a single grounding score
    # Probability-weighted: GS = mean(ent_probs), soft contradiction penalty
    aggregate = calculate_grounding_score(claim_verifications)
    overall_score = aggregate["grounding_score"]

    elapsed = time.time() - t0
    print(f"\n  -- RESULT --")
    print(f"  Overall grounding_score: {overall_score:.4f}")
    print(f"  Overall grounding_risk:  {aggregate['grounding_risk']:.4f}")
    print(f"  Claim counts:            E={aggregate['counts']['E']}  C={aggregate['counts']['C']}  N={aggregate['counts']['N']}")
    print(f"  Per-claim ent. probs:    {aggregate.get('per_claim_scores', [])}")
    if aggregate["contradiction_penalty_applied"]:
        print(f"  Contradiction penalty:   soft (max_C_prob={aggregate.get('max_contradiction_prob', 0):.4f})")
    print(f"  Expected:                {case['expected'].upper()}")
    match = (
        (case["expected"] == "grounded" and overall_score >= 0.5)
        or (case["expected"] == "hallucinated" and overall_score < 0.5)
    )
    status = "PASS" if match else "FAIL"
    print(f"  Verdict:                 {status}")
    print(f"  Time:                    {elapsed:.1f}s")

    return {
        "label": case["label"],
        "expected": case["expected"],
        "overall_grounding_score": round(overall_score, 4),
        "overall_grounding_risk": round(aggregate["grounding_risk"], 4),
        "counts": aggregate["counts"],
        "contradiction_penalty": aggregate["contradiction_penalty_applied"],
        "pass": match,
        "num_claims": len(claims),
        "elapsed_sec": round(elapsed, 1),
    }


async def main():
    print("=" * 80)
    print("  TrustLayer Module 1 -- Standalone Grounding Test")
    print("  Running extract -> retrieve -> verify pipeline on hand-crafted test cases")
    print("=" * 80)

    results = []
    for i, case in enumerate(TEST_CASES, 1):
        result = await run_test_case(case, i)
        results.append(result)

    # ── Summary table ─────────────────────────────────────────────────────
    print("\n\n" + "=" * 80)
    print("  SUMMARY")
    print("=" * 80)
    print(f"  {'#':<3} {'Label':<55} {'Expected':<13} {'Score':<8} {'Result'}")
    print(f"  {'-'*3} {'-'*55} {'-'*13} {'-'*8} {'-'*8}")
    pass_count = 0
    for i, r in enumerate(results, 1):
        status = "PASS" if r["pass"] else "FAIL"
        if r["pass"]:
            pass_count += 1
        print(
            f"  {i:<3} {r['label'][:55]:<55} {r['expected']:<13} "
            f"{r['overall_grounding_score']:.4f}  {status}"
        )
    print(f"\n  Passed: {pass_count}/{len(results)}")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
