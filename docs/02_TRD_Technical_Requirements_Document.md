# Doc 02 — Technical Requirements Document (TRD)

**Project:** TrustLayer — Banking Support Assistant Case Study

---

## 1. Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Backend framework | FastAPI | Also hosts the tool-call interception point directly (no MCP layer) |
| Database | PostgreSQL + pgvector | Single local instance; replaces Qdrant for zero-budget/offline constraint |
| Embeddings | BGE-M3 | Runs locally |
| Claim extraction | Gemini API (`gemini-1.5-flash` or current equivalent) | Chosen over local Ollama for reliability, structured/JSON output, and easier implementation; introduces an internet + API-key runtime dependency for this one step (see Doc 01 NFR2) |
| NLI verification | DeBERTa-v3 | Entailment/contradiction/neutral classification per claim vs. evidence |
| Agent orchestration | LangGraph (or equivalent agent loop) | Reasoning + proposed tool call step |
| Policy engine | Custom RBAC + rule-based sequence check | Replaces OPA + Neo4j/NetworkX from original design |
| Frontend | React + Vite (structural reference only from an unrelated prior project) | Not yet started |
| Coding tool | Google Antigravity | Chosen over OpenAI Codex — free, multi-agent, browser-based verification |
| Fine-tuning compute | Google Colab (free tier) | One-time only, not part of runtime |

## 2. System Architecture

```
User (Customer/Staff/Approver)
        |
        v
   FastAPI Gateway (auth, role resolution, session mgmt)
        |
        v
   Agent (reasoning) --------------------> proposes tool call + generates response
        |
        v
   TrustLayer Interception Layer
        |
        +---------------------+----------------------+
        |                                             |
        v                                             v
   Module 1: Claim Grounding                  Module 2: Policy/RBAC
   - Extract claims (Gemini API)                  - Check role permissions
   - Access-aware retrieval                   - Sequence anomaly check
     (BGE-M3 -> pgvector, role-filtered)       - Compute policy_risk
   - NLI verify (DeBERTa-v3)
   - Compute grounding_risk
        |                                             |
        +---------------------+----------------------+
                              |
                              v
                     Decision Engine
              (hard overrides + weighted score)
                              |
              +---------------+----------------+
              |               |                |
              v               v                v
           ALLOW           BLOCK           APPROVE
              |                                |
              v                                v
     Execute against mock          Route to Senior Staff/
     bank ledger (DB)              Approver role
              |
              v
      Audit Log (Postgres) + Structured Logs (shared request_id)
              |
              v
      Admin Analytics Dashboard
```

## 3. Module 1 — Claim Grounding, Detailed

1. Agent produces a response referencing a claim (e.g. "your account is eligible for a fee waiver").
2. A Gemini API call extracts atomic claims from the response (structured/JSON output mode, so downstream parsing is reliable).
3. Each claim is embedded (BGE-M3) and used to retrieve top-k evidence chunks from `kb_embeddings`, filtered by the requesting user's `role_scope` (access-aware retrieval).
4. DeBERTa-v3 NLI scores each (claim, evidence) pair as entailment / neutral / contradiction.
5. `grounding_score` = aggregated entailment confidence across retrieved evidence (highest-support chunk wins, or a weighted aggregate — decide empirically during Week 2 build).
6. `grounding_risk = 1 - grounding_score`.

## 4. Module 2 — Policy/RBAC, Detailed

1. Look up the proposed tool call + parameters against the role's permitted action set.
2. **Hard deny** if the role is not permitted to perform this action at all (e.g. Customer calling an admin-only tool).
3. Sequence-anomaly rule check: flag patterns such as `check_balance` immediately followed by `transfer_funds` for (near-)full balance without intermediate re-authentication.
4. `policy_risk` = 0 (clean) to 1 (hard violation), with intermediate values for sequence-anomaly flags.

## 5. Decision Logic

**Step 1 — Hard overrides (checked before the weighted score):**
- RBAC hard deny → **BLOCK**, unconditionally.
- `grounding_score < 0.4` on any money-moving action (transfer, block-card, dispute-charge) → minimum **APPROVE**, never ALLOW outright.

**Step 2 — Weighted score (if no hard override triggered):**
```
final_risk = 0.5 * grounding_risk + 0.5 * policy_risk

final_risk < 0.3        -> ALLOW
0.3 <= final_risk <= 0.6 -> APPROVE  (routes to Senior Staff/Approver)
final_risk > 0.6         -> BLOCK
```
The 0.3 / 0.6 cutlines are initial values — tune against real score distributions once the Week 4 evaluation set has been run; document the final chosen values and rationale in the paper.

## 6. Failure / Fallback Behavior

- **Gemini API failure (timeout, rate limit, network/API-key error) or NLI failure:** ~3s timeout, one retry. On repeated failure, treat as `grounding_score = 0` → forces the hard-override floor → minimum APPROVE for money-moving actions. Never fail-open for financial actions. Rate limiting is a real risk on the free tier under repeated demo/testing calls — add basic response caching per claim during development to avoid burning quota, and keep a local backup key or a cached "known good" demo run ready in case live API calls fail during the viva.
- **Read-only actions** (check balance, get transactions): may fail-open with a logged warning if grounding infra errors, since no financial harm is possible.
- Every fallback trigger is logged distinctly in `audit_log` with `event = 'grounding_unavailable'` — this is documented as a robustness property in the paper/defense.

## 7. Evaluation Plan

### 7.1 Test Buckets (hand-crafted / lightly synthetic, ~15–25 cases each)
1. **Clean** — grounded claim, authorized action → expect ALLOW.
2. **Ungrounded-only** — false/hallucinated claim, action authorized → expect BLOCK/APPROVE, never ALLOW.
3. **Unauthorized-only** — true claim, action outside role/policy → expect BLOCK.
4. **Compound failure (core thesis case)** — false/ungrounded claim AND fully authorized action → expect BLOCK/APPROVE. This is the case no paper in the literature survey catches; system performance here is the headline result.

### 7.2 Metrics
- Grounding: precision / recall / F1 vs. NLI ground-truth labels.
- Decision gate accuracy vs. ground-truth allow/block/approve labels, reported **per bucket** (bucket 4 is the primary metric).
- Latency per decision (relevant given local-model/limited-hardware constraints).

### 7.3 Ablation Study (core evidence for the paper)
Run the same test set through three configurations:
- (a) RBAC-only (no grounding)
- (b) Grounding-only (no RBAC)
- (c) Full TrustLayer (both, jointly)

Expected result: (a) and (b) each fail bucket 4 independently; (c) catches it. This single comparison table is the paper's central empirical contribution.

## 8. Logging & Audit

- Every request gets a `request_id` generated at the FastAPI gateway, propagated through agent reasoning, Module 1, Module 2, and the decision engine.
- Structured logs (JSON) at each stage, correlated by `request_id` / `session_id`.
- `audit_log` table persists every decision with full inputs (grounding_score, policy_risk, final_risk, decision, approver if applicable) for compliance-style traceability.

## 9. Constraints Recap

- Zero budget (Gemini API free tier), embeddings + NLI fully offline, claim extraction requires internet + API key (Gemini API), i5/8GB/no-GPU hardware, single-instance (no scaling infra), 5-week solo build window ending 30 Oct 2026.
