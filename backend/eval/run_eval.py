import asyncio
import csv
import json
import os
import pathlib
import time
import uuid
from typing import Any

from trustlayer.db.session import async_session
from trustlayer.decision.engine import evaluate_decision
from trustlayer.grounding.extract import extract_claims
from trustlayer.grounding.retrieve import retrieve_evidence
from trustlayer.grounding.verify import calculate_grounding_score, verify_claim_against_chunks
from trustlayer.policy.rbac import check_rbac
from trustlayer.policy.sequence_check import check_sequence_anomaly

# Maps test roles to known seeded database user identifiers
ROLE_DEFAULT_USERS: dict[str, uuid.UUID] = {
    "customer": uuid.UUID("11111111-1111-1111-1111-111111111111"),
    "staff": uuid.UUID("44444444-4444-4444-4444-444444444444"),
    "approver": uuid.UUID("66666666-6666-6666-6666-666666666666"),
}


async def evaluate_single_case(case: dict[str, Any]) -> dict[str, Any]:
    # Record start timestamp to measure end-to-end execution latency
    start_time = time.perf_counter()

    # Extract required case attributes from the evaluation payload
    case_id = case["id"]
    bucket = case["bucket"]
    user_role = case["user_role"]
    user_message = case.get("user_message", "")
    claims = case.get("agent_claims", [])
    proposed_tool_call = case.get("proposed_tool_call", {})
    expected = case["expected_decision"]

    # Extract proposed tool name and parameter dictionary
    tool_name = proposed_tool_call.get("tool") or proposed_tool_call.get("name") or proposed_tool_call.get("tool_name")
    params = proposed_tool_call.get("params", {})

    # Resolve target user UUID based on role or case override
    user_id = ROLE_DEFAULT_USERS.get(user_role, ROLE_DEFAULT_USERS["customer"])

    # Fall back to extracting claims from user message if agent claims are omitted
    if not claims and user_message and bucket == "user_injected_false_claim":
        extracted = extract_claims(user_message).get("claims", [])
        claims = extracted

    # Grounding stage: retrieve evidence chunks and evaluate NLI against role scope
    claim_verifications = []
    grounding_error = None
    for claim in claims:
        try:
            evidence = await retrieve_evidence(claim, role=user_role, top_k=3)
            verification = verify_claim_against_chunks(claim, evidence, role=user_role)
            claim_verifications.append(verification)
        except Exception as exc:
            grounding_error = str(exc)

    # Compute aggregate grounding score and risk from verification results
    if claims and claim_verifications:
        aggregate_grounding = calculate_grounding_score(claim_verifications)
        grounding_score = aggregate_grounding["grounding_score"]
    else:
        aggregate_grounding = calculate_grounding_score([])
        grounding_score = 1.0

    # Policy stage: evaluate role-based access control and session sequence anomalies
    rbac_res = check_rbac(role=user_role, tool_name=tool_name, params=params)
    async with async_session() as db:
        seq_res = await check_sequence_anomaly(
            session=db,
            user_id=user_id,
            session_id=None,
            tool_name=tool_name,
            params=params,
        )

    # Calculate policy risk score based on RBAC authorization status
    if rbac_res["hard_deny"]:
        policy_risk = 1.0
    else:
        policy_risk = float(seq_res.get("policy_risk", 0.0))

    # Decision stage: combine multi-source risk and apply policy overrides
    decision_res = evaluate_decision(
        grounding_score=grounding_score,
        policy_risk=policy_risk,
        rbac_allowed=rbac_res["allowed"],
        rbac_hard_deny=rbac_res["hard_deny"],
        tool_name=tool_name,
        grounding_error=grounding_error,
    )

    # Calculate total decision processing latency in seconds
    latency = round(time.perf_counter() - start_time, 4)
    actual = decision_res["decision"]
    risk_score = decision_res["final_risk"]

    return {
        "case id": case_id,
        "bucket": bucket,
        "expected": expected,
        "actual": actual,
        "risk score": risk_score,
        "grounding score": grounding_score,
        "latency": latency,
    }


def is_decision_correct(expected: str, actual: str, bucket: str) -> bool:
    # Accept either approve or block for false claim buckets where either is valid
    if bucket in ("user_injected_false_claim", "agent_generated_false_claim"):
        return actual in ("approve", "block") and expected in ("approve", "block")
    # Require exact decision equality for benign, control, and RBAC buckets
    return expected == actual


async def run_evaluation() -> None:
    # Resolve file paths for evaluation dataset and output results CSV
    eval_dir = pathlib.Path(__file__).resolve().parent
    cases_file = eval_dir / "cases.json"
    results_file = eval_dir / "results.csv"

    # Read all test cases from the JSON dataset file
    with open(cases_file, "r", encoding="utf-8") as f:
        cases = json.load(f)

    # Process all cases sequentially and collect evaluation records
    results = []
    print(f"Executing TrustLayer evaluation across {len(cases)} test cases...")
    for idx, case in enumerate(cases, start=1):
        record = await evaluate_single_case(case)
        results.append(record)
        print(f"[{idx:02d}/{len(cases):02d}] {record['case id']} ({record['bucket']}): expected={record['expected']}, actual={record['actual']}, risk={record['risk score']:.4f}, gs={record['grounding score']:.4f}, latency={record['latency']:.4f}s")

    # Write evaluation metrics and decisions to results CSV file
    fieldnames = ["case id", "bucket", "expected", "actual", "risk score", "grounding score", "latency"]
    with open(results_file, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    # Aggregate accuracy metrics per evaluation bucket
    bucket_counts: dict[str, int] = {}
    bucket_correct: dict[str, int] = {}
    for r in results:
        b = r["bucket"]
        bucket_counts[b] = bucket_counts.get(b, 0) + 1
        if is_decision_correct(r["expected"], r["actual"], b):
            bucket_correct[b] = bucket_correct.get(b, 0) + 1

    # Calculate false positive counts for benign and matched control buckets
    benign_total = bucket_counts.get("benign", 0)
    benign_fp = sum(1 for r in results if r["bucket"] == "benign" and r["actual"] != "allow")
    benign_fpr = (benign_fp / benign_total) if benign_total > 0 else 0.0

    control_total = bucket_counts.get("agent_true_claim_control", 0)
    control_fp = sum(1 for r in results if r["bucket"] == "agent_true_claim_control" and r["actual"] != "allow")
    control_fpr = (control_fp / control_total) if control_total > 0 else 0.0

    combined_clean_total = benign_total + control_total
    combined_fp = benign_fp + control_fp
    combined_fpr = (combined_fp / combined_clean_total) if combined_clean_total > 0 else 0.0

    # Print summary performance table to stdout
    print("\n" + "=" * 70)
    print("TRUSTLAYER EVALUATION SUMMARY REPORT")
    print("=" * 70)
    print(f"{'Bucket':<32} | {'Correct':<8} | {'Total':<6} | {'Accuracy':<8}")
    print("-" * 70)
    for b, count in bucket_counts.items():
        correct = bucket_correct.get(b, 0)
        acc = (correct / count) * 100.0 if count > 0 else 0.0
        print(f"{b:<32} | {correct:<8} | {count:<6} | {acc:>6.2f}%")
    print("-" * 70)
    overall_correct = sum(bucket_correct.values())
    overall_total = len(results)
    overall_acc = (overall_correct / overall_total) * 100.0 if overall_total > 0 else 0.0
    print(f"{'OVERALL':<32} | {overall_correct:<8} | {overall_total:<6} | {overall_acc:>6.2f}%")
    print("=" * 70)

    # Print false positive rate analysis for clean operational buckets
    print("\nFALSE POSITIVE RATE (FPR) REPORT:")
    print(f"  Benign Bucket FPR:         {benign_fp}/{benign_total} ({benign_fpr * 100.0:.2f}%)")
    print(f"  True-Claim Control FPR:    {control_fp}/{control_total} ({control_fpr * 100.0:.2f}%)")
    print(f"  Combined Clean FPR:        {combined_fp}/{combined_clean_total} ({combined_fpr * 100.0:.2f}%)")
    print("=" * 70)
    print(f"Results successfully saved to: {results_file}")


if __name__ == "__main__":
    # Execute the asynchronous evaluation pipeline runner
    asyncio.run(run_evaluation())
