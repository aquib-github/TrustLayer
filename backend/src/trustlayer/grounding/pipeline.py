"""
Verification pipeline for TrustLayer (Module 1, Step 5).

Assembles the full verification pipeline:
  extract_claims -> retrieve_evidence (access-aware) -> verify_claim (NLI) -> calculate_grounding_score

Provides the top-level `verify_response(response, role, context)` function per TRD §3 and Implementation Plan.
"""

import logging
import time
from typing import Any

from trustlayer.grounding.extract import extract_claims
from trustlayer.grounding.retrieve import retrieve_evidence
from trustlayer.grounding.verify import calculate_grounding_score, verify_claim_against_chunks

logger = logging.getLogger(__name__)


async def verify_response(
    response: str,
    role: str = "customer",
    context: str | None = None,
    top_k: int = 3,
) -> dict[str, Any]:
    """
    Verifies an agent's proposed response against the knowledge base.

    Workflow:
      1. Extract atomic factual claims using Gemini API (with deterministic fallback).
      2. For each claim, retrieve top_k evidence chunks access-filtered by the user's role.
      3. For each claim, verify against evidence using DeBERTa-v3 NLI (entailment/contradiction/neutral).
      4. Calculate overall grounding score:
           GS = |E| / (|E| + |C| + |N|)
         with contradiction penalty (if any claim is CONTRADICTED, GS = 0.0).
      5. Return structured result.

    Args:
        response: Agent response text to ground.
        role: Requesting user's role ("customer", "staff", "approver").
        context: Optional conversation context.
        top_k: Number of evidence chunks to retrieve per claim.

    Returns:
        {
            "response": str,
            "role": str,
            "grounding_score": float,         # 0.0 to 1.0
            "grounding_risk": float,          # 1.0 - grounding_score
            "decision_hint": "ALLOW" | "APPROVE" | "BLOCK",
            "claims": list[dict],             # Detailed per-claim verification results
            "counts": {"E": int, "C": int, "N": int},
            "contradiction_penalty_applied": bool,
            "latency_ms": float,
            "claim_extraction_source": str,
        }
    """
    t_start = time.perf_counter()

    # Step 1: Extract claims
    extraction = extract_claims(response)
    claims: list[str] = extraction.get("claims", [])
    extraction_source: str = extraction.get("source", "unknown")

    # If no factual claims found (e.g. greeting or empty response)
    if not claims:
        latency_ms = round((time.perf_counter() - t_start) * 1000, 2)
        return {
            "response": response,
            "role": role,
            "grounding_score": 1.0,
            "grounding_risk": 0.0,
            "decision_hint": "ALLOW",
            "claims": [],
            "counts": {"E": 0, "C": 0, "N": 0},
            "contradiction_penalty_applied": False,
            "latency_ms": latency_ms,
            "claim_extraction_source": extraction_source,
        }

    # Step 2 & 3: For each claim, retrieve and verify
    claim_verifications = []
    for claim in claims:
        evidence_chunks = await retrieve_evidence(query=claim, role=role, top_k=top_k)
        verification = verify_claim_against_chunks(
            claim=claim,
            evidence_chunks=evidence_chunks,
            role=role,
        )
        claim_verifications.append(verification)

    # Step 4: Calculate grounding score with contradiction penalty
    score_result = calculate_grounding_score(claim_verifications)
    gs = score_result["grounding_score"]
    risk = score_result["grounding_risk"]
    penalty = score_result["contradiction_penalty_applied"]
    counts = score_result["counts"]

    # Decision hint based on grounding score threshold per TRD §5:
    # grounding_score < 0.4 on money actions => minimum APPROVE / BLOCK
    if penalty or gs == 0.0:
        decision_hint = "BLOCK"
    elif gs < 0.5:
        decision_hint = "APPROVE"
    else:
        decision_hint = "ALLOW"

    latency_ms = round((time.perf_counter() - t_start) * 1000, 2)

    return {
        "response": response,
        "role": role,
        "grounding_score": gs,
        "grounding_risk": risk,
        "decision_hint": decision_hint,
        "claims": claim_verifications,
        "counts": counts,
        "contradiction_penalty_applied": penalty,
        "latency_ms": latency_ms,
        "claim_extraction_source": extraction_source,
    }
