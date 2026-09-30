# Doc 02 — Technical Requirements Document (TRD)

**Project:** TrustLayer — Banking Support Assistant Case Study

---

## 1. Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Backend framework | FastAPI | Also hosts the tool-call interception point directly (no MCP layer) |
| Database | PostgreSQL + pgvector | Single local instance; replaces Qdrant for zero-budget/offline constraint |
| Embeddings | BGE-M3 (`BAAI/bge-m3`) | Runs locally, cached under `backend/models_cache` via `HF_HOME`/`HF_HUB_CACHE`; embedding dimension confirmed 1024 |
| Claim extraction | Gemini API (`gemini-flash-latest`, fallback `gemini-flash-lite-latest`) | Cloud call; introduces an internet + API-key runtime dependency (see Doc 01 NFR2). Auth uses Google's newer `AQ.`-prefix key format (Google is phasing out legacy `AIzaSy...` keys). Falls back to a deterministic heuristic sentence-splitter extractor if both models fail after retry |
| NLI verification | `cross-encoder/nli-deberta-v3-base` | Entailment/contradiction/neutral classification, run sentence-level (not full-chunk) to avoid cross-attention dilution on multi-sentence evidence — see §3 below |
| Agent orchestration | Custom rule-based intent-detection loop (`agent/loop.py`) | Reasoning + proposed tool call step; not LangGraph — kept simpler for the build timeline |
| Policy engine | Custom RBAC + rule-based sequence check | Replaces OPA + Neo4j/NetworkX from original design |
| Frontend | React + Vite (structural reference only from an unrelated prior project) | Not yet started |
| Coding tool | Google Antigravity | Chosen over OpenAI Codex — free, multi-agent, browser-based verification |
| Fine-tuning compute | Google Colab (free tier) | One-time only, not part of runtime |
| Local dev dependency | Docker Desktop, running `pgvector/pgvector:pg16` | Used only to host local Postgres+pgvector for development — unrelated to the "no Docker sandbox for tool execution" decision in Doc 01 §6, which is about runtime tool-call isolation, not dev infra |

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
   - NLI verify (sentence-level DeBERTa-v3)
   - Compute grounding_risk (graduated)
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
2. A Gemini API call extracts atomic claims from the response (structured/JSON output mode). If the API is unavailable after retry, a deterministic heuristic sentence-splitter is used as a fallback extractor (see §6).
3. Each claim is embedded (BGE-M3) and used to retrieve top-k evidence chunks from `kb_embeddings`, filtered by the requesting user's `role_scope` (access-aware retrieval).
4. **Sentence-level NLI matching:** rather than scoring a claim against a full multi-sentence evidence chunk directly, each retrieved chunk is split into candidate sentences and the claim is scored against each sentence individually. This avoids a measured failure mode where DeBERTa-v3's cross-attention gets diluted on long paragraphs, masking a word-for-word entailing sentence as NEUTRAL. If any sentence yields entailment probability > 0.5, the chunk is classified ENTAILED using that sentence.
5. **Relevance-gated contradiction:** a retrieved chunk with low similarity to the claim (`sim < 0.65`) is excluded from contributing a CONTRADICTED label — this prevents topically unrelated "distractor" chunks (e.g. a travel-fee policy retrieved alongside an overdraft-fee claim) from falsely registering as contradicting evidence.
6. **Grounding score aggregation (graduated, not binary veto):**
   - Base score: `GS_base = mean(best_entailment_probability_i)` across all extracted sub-claims `i` of the original claim.
   - Contradiction penalty: `penalty = mean(P(contradiction) for contradicted sub-claims) * (num_contradicted / num_total)`.
   - `grounding_score = max(0.0, GS_base * (1.0 - penalty))`.
   - `grounding_risk = 1.0 - grounding_score`.
   - This replaces an earlier binary "any contradiction zeroes the score" rule, which was found (via the Week 2 test suite) to incorrectly collapse multi-clause true claims to 0.0 whenever any one sub-clause was mis-scored — the graduated formula preserves partial-credit signal from correctly-entailed sub-claims.

**Validated behavior (Week 2 test suite, `test_grounding.py`, 10 hand-crafted cases):** 8/10 passing with the two remaining gaps root-caused to DeBERTa-v3 sensitivity to pronoun framing ("by you" vs. "by the customer") and assertion-vs-policy tense framing ("has been charged" vs. conditional policy language) — noted as a documented NLI limitation, not a pipeline bug, and a candidate topic for the paper's limitations section.

## 4. Module 2 — Policy/RBAC, Detailed

1. Look up the proposed tool call + parameters against the role's permitted action set (concrete matrix defined in `policy/rbac.py` and reproduced below).
2. **Hard deny** if the role is not permitted to perform this action at all. Hard deny sets `policy_risk = 1.0` and forces BLOCK regardless of grounding.
3. **Sequence-anomaly rule check (concrete parameters, as implemented):** flags a pattern where `check_balance` is followed, within **5 minutes**, by `transfer_funds` draining **≥85% of the account balance**, without intermediate re-authentication. This sets `policy_risk = 0.8` when triggered.
4. `policy_risk` ranges 0 (clean) to 1 (hard violation), with the 0.8 intermediate value for sequence-anomaly flags as implemented above.

**RBAC permission matrix (as implemented in `policy/rbac.py`):**

| Role | check_balance | get_transactions | transfer_funds | block_card | dispute_charge |
|---|---|---|---|---|---|
| customer | allow | allow | allow | allow | deny |
| staff | allow | allow | deny | allow | allow |
| approver | allow | allow | allow | allow | allow |

Administrative tools (`waive_fee`, `increase_limit`, `override_hold`) also exist in the matrix, restricted to staff/approver. Both denial paths were verified end to end on real seeded roles (customer calling `dispute_charge`, staff calling `transfer_funds`): decision `block`, `final_risk = 1.0`, tool never executed.

## 5. Decision Logic

**Step 1 — Hard overrides (checked before the weighted score):**
- RBAC hard deny → **BLOCK**, unconditionally (`policy_risk = 1.0`).
- `grounding_score < 0.4` on any money-moving action → minimum **APPROVE**, never ALLOW outright. **Money-moving actions** are defined by the `MONEY_MOVING_TOOLS` set in `policy/rbac.py`: `transfer_funds`, `block_card`, `dispute_charge`, plus the administrative `waive_fee` and `override_hold`. Confirmed in code, and confirmed by live tests: a customer calling `block_card` and a staff member calling `dispute_charge`, each with a deliberately hallucinated claim and `policy_risk = 0.0`, both returned APPROVE (grounding_score ≈ 0, final_risk = 0.5) and were never ALLOWED. Note that under the current 0.5/0.5 weighting these outcomes are produced by the weighted formula itself, not by the floor rule (see the redundancy finding below), so tool-set membership only becomes load-bearing if the weights are ever changed.

**Step 2 — Weighted score:**
```
final_risk = 0.5 * grounding_risk + 0.5 * policy_risk

final_risk < 0.3        -> ALLOW
0.3 <= final_risk <= 0.6 -> APPROVE  (routes to Senior Staff/Approver)
final_risk > 0.6         -> BLOCK
```

**Documented finding — floor-rule redundancy under equal weighting:** given the 0.5/0.5 weighting above, whenever `grounding_score < 0.4` (i.e. `grounding_risk > 0.6`), `final_risk = 0.5*grounding_risk + 0.5*policy_risk ≥ 0.5*0.6 = 0.3` regardless of `policy_risk`'s value — meaning `final_risk` can never fall below the 0.3 ALLOW threshold in that case, and the weighted formula alone already guarantees APPROVE-or-worse. The explicit hard-override floor rule in Step 1 is therefore currently **mathematically redundant** with the weighted formula under equal weighting — it never actively changes an outcome the weighted formula wouldn't already produce. It is kept in the implementation as explicit defense-in-depth and to remain correct if the 0.5/0.5 weights are ever tuned unequally (e.g. if `policy_risk`'s weight is later reduced, the floor rule would become load-bearing). This is worth stating explicitly in the paper's methodology section rather than presenting the floor rule as an independently-verified second safeguard in the current configuration.

The 0.3 / 0.6 cutlines are initial values — tune against real score distributions once the Week 4 evaluation set has been run; document the final chosen values and rationale in the paper.

## 6. Failure / Fallback Behavior

- **Gemini API failure (timeout, rate limit, network/API-key error):** retried once (~6-8s timeout per attempt) across two model tiers (`gemini-flash-latest` → `gemini-flash-lite-latest`). On exhausted retries, falls back to a **deterministic heuristic sentence-splitter extractor** (splits on sentence boundaries, filters greetings/pleasantries) rather than immediately treating the claim as ungrounded — this keeps the pipeline functional in a demo even if Gemini is fully unavailable, at the cost of less precise claim boundaries.
- **NLI failure (model/inference error) on a money-moving action:** treated as `grounding_score = 0` → forces minimum APPROVE via the floor logic. Never fail-open for financial actions.
- **Policy engine failure (unexpected exception):** fail-closed — default BLOCK, `policy_risk = 1.0`.
- **Read-only actions** (check balance, get transactions): may fail-open with a logged warning if grounding infra errors, since no financial harm is possible.
- Every fallback trigger is logged distinctly in `audit_log` with `event = 'grounding_unavailable'` or `event = 'policy_engine_error'` — documented as a robustness property in the paper/defense.
- Rate limiting is a real risk on the Gemini free tier under repeated demo/testing calls — add basic response caching per claim during development to avoid burning quota, and keep a local backup key or a cached "known good" demo run ready in case live API calls fail during the viva.

## 7. Evaluation Plan

### 7.1 Test Buckets (hand-crafted / lightly synthetic, ~15–25 cases each)
1. **Clean** — grounded claim, authorized action → expect ALLOW.
2. **Ungrounded-only** — false/hallucinated claim, action authorized → expect BLOCK/APPROVE, never ALLOW.
3. **Unauthorized-only** — true claim, action outside role/policy → expect BLOCK.
4. **Compound failure (core thesis case)** — false/ungrounded claim AND fully authorized action → expect BLOCK/APPROVE. This is the case no paper in the literature survey catches; system performance here is the headline result. **A working example was manually validated in Week 3**: a customer, fully RBAC-authorized to call `transfer_funds`, includes a hallucinated claim about a fee waiver promotion; grounding correctly returns a low score (NLI contradiction against the real fee policy), and the decision engine correctly routes to APPROVE rather than ALLOW despite full RBAC authorization — the compound failure is caught end to end. Note that in the current test cases the false claim is injected via the user's message text (the agent is a rule-based intent matcher, not an LLM generating its own claims), so what is verified today is claim grounding on the request text. Decide before the Week 4 evaluation whether to also cover agent-generated claims, since the thesis is framed around the agent's own beliefs.

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

- Every request gets a `request_id` (UUIDv4) generated at the FastAPI gateway, propagated through `claims`, `grounding_results`, `policy_checks`, `decisions`, `agent_actions`, and `audit_log` — confirmed via direct Postgres queries joining these tables on `request_id`/`decision_id`/`action_id`.
- Structured logs at each stage, correlated by `request_id` / `session_id`.
- `audit_log` table persists every decision with full inputs (grounding_score, policy_risk, final_risk, decision, approver if applicable) for compliance-style traceability. **`decisions.final_risk` must be a fixed-precision type (`NUMERIC`), not `float8`/`double precision`** — a float type was found to produce precision artifacts (e.g. displaying `0.40000000000000002220446049250313080847263336181640625` instead of `0.4`) when queried directly, which is unacceptable for an auditable financial risk score; this is now migrated (Alembic migration `0002_numeric_precision`) for `final_risk`, `policy_risk_score` and `grounding_score`, and verified via direct psql queries returning clean values such as `0.4000`.

## 9. Constraints Recap

- Zero budget (Gemini API free tier, Docker Desktop free tier for local Postgres dev), embeddings + NLI fully offline (cached locally under `backend/models_cache`), claim extraction requires internet + API key (Gemini API), i5/8GB/no-GPU hardware, single-instance (no scaling infra), 5-week solo build window ending 30 Oct 2026.
