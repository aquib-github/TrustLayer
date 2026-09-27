"""
NLI verification module for TrustLayer (Module 1, Step 4-5).

Uses DeBERTa-v3 (via HuggingFace transformers / cross-encoder, running locally) to classify
each (claim, evidence_chunk) pair as ENTAILED, CONTRADICTED, or NEUTRAL.

Implements grounding score calculation per TRD S3 step 5:
  Probability-weighted aggregation: GS = mean(per-claim entailment_prob)
with graduated contradiction penalty (replaces hard zero-veto):
  penalty = avg_contradiction_prob * (num_contradicted / total_claims)
  final_score = base_score * (1 - penalty)
with role-scope check:
  if evidence is retrieved from a document whose role_scope excludes the user's role,
  treat that evidence chunk as invalid / unsupported.
"""

import logging
import os
import re
from typing import Any

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from trustlayer.config import settings

logger = logging.getLogger(__name__)

# ── Singleton model loader ────────────────────────────────────────────────

_nli_tokenizer = None
_nli_model = None

# Default to deberta-v3 cross-encoder (local cache in backend/models_cache)
_MODEL_NAME = os.getenv("NLI_MODEL_NAME", "cross-encoder/nli-deberta-v3-base")


def _load_nli_model():
    """Lazy-load the NLI model and tokenizer once."""
    global _nli_tokenizer, _nli_model
    if _nli_tokenizer is None:
        logger.info("Loading NLI model: %s (local cache: %s)...", _MODEL_NAME, os.environ.get("HF_HOME"))
        _nli_tokenizer = AutoTokenizer.from_pretrained(
            _MODEL_NAME,
            token=settings.hf_token or None,
        )
        _nli_model = AutoModelForSequenceClassification.from_pretrained(
            _MODEL_NAME,
            token=settings.hf_token or None,
        )
        _nli_model.eval()
        logger.info("NLI model loaded. id2label: %s", _nli_model.config.id2label)
    return _nli_tokenizer, _nli_model


def verify_claim_against_evidence(
    claim: str,
    evidence: str,
) -> dict[str, Any]:
    """
    Classify a single (claim, evidence) pair via NLI.
    Convention for NLI cross-encoder: premise=evidence, hypothesis=claim.

    Returns:
        {
            "nli_label": "entailment" | "neutral" | "contradiction",
            "entailment_prob": float,
            "neutral_prob": float,
            "contradiction_prob": float,
        }
    """
    tokenizer, model = _load_nli_model()

    inputs = tokenizer(
        evidence,
        claim,
        return_tensors="pt",
        truncation=True,
        max_length=512,
        padding=True,
    )

    with torch.no_grad():
        outputs = model(**inputs)
        probs = torch.softmax(outputs.logits, dim=-1)[0]

    # Map output indices using model config
    id2label = model.config.id2label
    label_probs = {}
    for idx, label_name in id2label.items():
        label_probs[label_name.lower()] = float(probs[idx])

    predicted_idx = int(torch.argmax(probs))
    predicted_label = id2label[predicted_idx].lower()

    return {
        "nli_label": predicted_label,
        "entailment_prob": label_probs.get("entailment", 0.0),
        "neutral_prob": label_probs.get("neutral", 0.0),
        "contradiction_prob": label_probs.get("contradiction", 0.0),
    }


def verify_claim_against_chunks(
    claim: str,
    evidence_chunks: list[dict[str, Any]],
    role: str | None = None,
) -> dict[str, Any]:
    """
    Verifies an atomic claim against retrieved evidence chunks.

    Applies role-scope check:
      If a chunk's document role_scope excludes the user's role, the chunk is
      treated as invalid / unauthorized, so the claim cannot be grounded on it.

    Classification for the claim:
      - CONTRADICTED: if any valid chunk is classified as contradiction.
      - ENTAILED: if not contradicted, and at least one valid chunk is entailed.
      - NEUTRAL: otherwise (unsupported by valid evidence).
    """
    if not evidence_chunks:
        return {
            "claim": claim,
            "status": "NEUTRAL",
            "best_nli_label": "neutral",
            "entailment_prob": 0.0,
            "contradiction_prob": 0.0,
            "neutral_prob": 1.0,
            "best_evidence": "",
            "best_doc_title": "",
            "role_restricted_chunks_filtered": 0,
            "per_chunk_results": [],
        }

    valid_chunks = []
    filtered_count = 0

    for chunk in evidence_chunks:
        # Check role_scope if provided
        chunk_role_scope = chunk.get("role_scope")
        if role and chunk_role_scope:
            if isinstance(chunk_role_scope, str):
                allowed_roles = [r.strip() for r in chunk_role_scope.split(",")]
            elif isinstance(chunk_role_scope, list):
                allowed_roles = chunk_role_scope
            else:
                allowed_roles = ["customer", "staff", "approver"]

            if role not in allowed_roles and "all" not in allowed_roles:
                filtered_count += 1
                continue

        valid_chunks.append(chunk)

    if not valid_chunks:
        return {
            "claim": claim,
            "status": "NEUTRAL",
            "best_nli_label": "neutral",
            "entailment_prob": 0.0,
            "contradiction_prob": 0.0,
            "neutral_prob": 1.0,
            "best_evidence": "",
            "best_doc_title": "",
            "role_restricted_chunks_filtered": filtered_count,
            "per_chunk_results": [],
        }

    per_chunk = []
    best_entailment = 0.0
    best_contradiction = 0.0
    best_chunk_text = ""
    best_chunk_title = ""

    def _split_into_sentences(text: str) -> list[str]:
        return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]

    for idx, chunk in enumerate(valid_chunks):
        chunk_text = chunk.get("chunk_text", "")
        if not chunk_text:
            continue

        sim = chunk.get("similarity", 0.0)
        is_relevant = (idx == 0 or sim >= 0.65)
        whole_nli = verify_claim_against_evidence(claim, chunk_text)
        sentences = _split_into_sentences(chunk_text)

        # Check if any individual sentence clearly entails the claim.
        # Paragraphs dilute cross-attention, so sentence-level matching finds the specific policy statement.
        entailing_sents = []
        for sent in sentences:
            s_nli = verify_claim_against_evidence(claim, sent)
            if s_nli["entailment_prob"] > 0.5:
                entailing_sents.append((s_nli["entailment_prob"], sent, s_nli))

        if entailing_sents:
            entailing_sents.sort(key=lambda x: x[0], reverse=True)
            best_c_ent = entailing_sents[0][0]
            best_c_cont = entailing_sents[0][2]["contradiction_prob"]
            best_matching_text = entailing_sents[0][1]
            chunk_label = "entailment"
        elif whole_nli["entailment_prob"] > 0.5:
            best_c_ent = whole_nli["entailment_prob"]
            best_c_cont = whole_nli["contradiction_prob"]
            best_matching_text = chunk_text
            chunk_label = "entailment"
        elif is_relevant:
            # No entailment found. Check if the relevant chunk/sentences directly contradict
            cont_sents = []
            for sent in sentences:
                s_nli = verify_claim_against_evidence(claim, sent)
                cont_sents.append((s_nli["contradiction_prob"], sent, s_nli))
            cont_sents.sort(key=lambda x: x[0], reverse=True)

            if cont_sents and cont_sents[0][0] > whole_nli["contradiction_prob"] and cont_sents[0][0] > 0.5:
                best_c_cont = cont_sents[0][0]
                best_c_ent = cont_sents[0][2]["entailment_prob"]
                best_matching_text = cont_sents[0][1]
                chunk_label = "contradiction"
            elif whole_nli["contradiction_prob"] > 0.5:
                best_c_cont = whole_nli["contradiction_prob"]
                best_c_ent = whole_nli["entailment_prob"]
                best_matching_text = chunk_text
                chunk_label = "contradiction"
            else:
                best_c_cont = whole_nli["contradiction_prob"]
                best_c_ent = whole_nli["entailment_prob"]
                best_matching_text = chunk_text
                chunk_label = "neutral"
        else:
            # Low-similarity distractor chunk from unrelated policy: cannot contradict
            best_c_ent = whole_nli["entailment_prob"]
            best_c_cont = 0.0
            best_matching_text = chunk_text
            chunk_label = "neutral"

        effective_cont_prob = best_c_cont if is_relevant else 0.0

        entry = {
            "chunk_text": best_matching_text[:120] + ("..." if len(best_matching_text) > 120 else ""),
            "doc_title": chunk.get("doc_title", ""),
            "similarity": sim,
            "nli_label": chunk_label,
            "entailment_prob": round(best_c_ent, 4),
            "neutral_prob": round(max(0.0, 1.0 - max(best_c_ent, effective_cont_prob)), 4),
            "contradiction_prob": round(effective_cont_prob, 4),
        }
        per_chunk.append(entry)

        if best_c_ent > best_entailment:
            best_entailment = best_c_ent
            if best_c_ent > best_contradiction:
                best_chunk_text = best_matching_text
                best_chunk_title = chunk.get("doc_title", "")

        if effective_cont_prob > best_contradiction:
            best_contradiction = effective_cont_prob
            if effective_cont_prob > best_entailment:
                best_chunk_text = best_matching_text
                best_chunk_title = chunk.get("doc_title", "")

    # Assign overall claim status
    if best_entailment > 0.5 and best_entailment > best_contradiction:
        status = "ENTAILED"
        best_label = "entailment"
    elif best_contradiction > 0.5 and best_contradiction > best_entailment:
        status = "CONTRADICTED"
        best_label = "contradiction"
    else:
        status = "NEUTRAL"
        best_label = "neutral"

    return {
        "claim": claim,
        "status": status,
        "best_nli_label": best_label,
        "entailment_prob": round(best_entailment, 4),
        "contradiction_prob": round(best_contradiction, 4),
        "neutral_prob": round(max(0.0, 1.0 - max(best_entailment, best_contradiction)), 4),
        "best_evidence": best_chunk_text[:200],
        "best_doc_title": best_chunk_title,
        "role_restricted_chunks_filtered": filtered_count,
        "per_chunk_results": per_chunk,
    }


def calculate_grounding_score(
    claim_verifications: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Computes grounding score per TRD S3 step 5 -- probability-weighted aggregation.

    Instead of binary label counts with a hard contradiction veto, uses per-claim
    entailment probabilities for a graduated score reflecting actual NLI confidence.

    Aggregation:
      base_score = mean(per-claim entailment_prob)

    Graduated contradiction penalty (replaces hard zero-veto):
      If any claim is CONTRADICTED, scale down proportionally:
        penalty = avg_contradiction_prob * (num_contradicted / total_claims)
        final_score = base_score * (1 - penalty)

    This preserves signal from well-supported claims even when one sub-claim
    is contradicted, while still penalizing contradictions proportionally.

    grounding_risk = 1.0 - grounding_score.
    """
    if not claim_verifications:
        return {
            "grounding_score": 1.0,
            "grounding_risk": 0.0,
            "counts": {"E": 0, "C": 0, "N": 0},
            "contradiction_penalty_applied": False,
            "max_contradiction_prob": 0.0,
            "per_claim_scores": [],
        }

    # Per-claim entailment probability (best supporting chunk)
    claim_scores = [cv.get("entailment_prob", 0.0) for cv in claim_verifications]
    base_score = sum(claim_scores) / len(claim_scores)

    # Count statuses for reporting
    entailed_count = sum(1 for c in claim_verifications if c.get("status") == "ENTAILED")
    contradicted_count = sum(1 for c in claim_verifications if c.get("status") == "CONTRADICTED")
    neutral_count = sum(1 for c in claim_verifications if c.get("status") == "NEUTRAL")

    # Graduated contradiction penalty
    contradicted_claims = [c for c in claim_verifications if c.get("status") == "CONTRADICTED"]
    if contradicted_claims:
        avg_cont_prob = sum(
            c.get("contradiction_prob", 0.0) for c in contradicted_claims
        ) / len(contradicted_claims)
        max_cont_prob = max(
            c.get("contradiction_prob", 0.0) for c in contradicted_claims
        )
        # Weight penalty by fraction of claims contradicted
        contradiction_weight = len(contradicted_claims) / len(claim_verifications)
        penalty = avg_cont_prob * contradiction_weight
        score = base_score * (1.0 - penalty)
        penalty_applied = True
    else:
        max_cont_prob = 0.0
        score = base_score
        penalty_applied = False

    score = max(score, 0.0)

    return {
        "grounding_score": round(score, 4),
        "grounding_risk": round(1.0 - score, 4),
        "counts": {
            "E": entailed_count,
            "C": contradicted_count,
            "N": neutral_count,
        },
        "contradiction_penalty_applied": penalty_applied,
        "max_contradiction_prob": round(max_cont_prob, 4),
        "per_claim_scores": [round(s, 4) for s in claim_scores],
    }
