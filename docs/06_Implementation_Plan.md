# Doc 06 — Implementation Plan

**Project:** TrustLayer — Banking Support Assistant Case Study
**Window:** 29 Sep 2026 → 30 Oct 2026 (5 weeks)
**Build tool:** Google Antigravity
**Owner:** Aquib (sole implementer)

---

## Week 1 (Sep 29 – Oct 5) — Foundations

- Finalize DB schema (Doc 05) and stand up PostgreSQL + pgvector locally.
- FastAPI skeleton: auth, role resolution, basic CRUD against the mock bank ledger (`accounts`, `transactions`).
- Seed synthetic users/accounts/transactions across the three roles.
- Agent stub: a working reasoning loop that can call the 5 banking tools against the mock DB — **no grounding or firewall yet**, just get the happy path (Doc 03 §1, steps 1–4 and 9) working end to end.
- **Exit criteria:** a customer message can trigger a real tool call that reads/writes the mock ledger, with a plain response returned — no TrustLayer interception yet.

## Week 2 (Oct 6 – Oct 12) — Grounding Module (Module 1)

- Source banking knowledge base content — real dataset if found by ~Oct 8, else pivot to synthetic generation (with realistic noise) so this doesn't block the schedule.
- Populate `kb_documents` / `kb_embeddings` via BGE-M3.
- Set up Gemini API access (API key, SDK) for claim extraction; add basic per-claim response caching during development to conserve free-tier quota.
- Integrate DeBERTa-v3 for NLI verification; compute `grounding_score` per claim.
- Confirm actual BGE-M3 embedding dimension and finalize `kb_embeddings.embedding` column type accordingly.
- Test grounding in isolation (feed hand-crafted claims + evidence, check scores make sense) before wiring into the agent.
- **Exit criteria:** given a claim and a role, the system returns a grounding score backed by retrieved evidence, independent of the agent/tool-call path.

## Week 3 (Oct 13 – Oct 19) — Firewall + Decision Gate (Module 2 + Fusion)

- Build RBAC policy engine (role → permitted actions).
- Build rule-based sequence-anomaly check (Doc 02 §4).
- Implement the decision engine: hard overrides + weighted `final_risk` formula (Doc 02 §5).
- Wire Module 1 + Module 2 + Decision Engine into the actual interception point in the agent's tool-call step (Doc 03 §1, steps 5–8).
- Implement audit logging (`audit_log`) with `request_id` propagation through the whole pipeline.
- Implement fallback/fail-safe behavior (Doc 02 §6) for grounding/policy engine failures.
- **Exit criteria:** a full request flows through claim extraction → grounding → policy check → decision → (allow: execute / block: safe response) with everything logged.

## Week 4 (Oct 20 – Oct 26) — Approval Flow, Dashboard, Evaluation

- Build Senior Staff/Approver queue + resolution flow (Doc 03 §2).
- Build Staff/Admin analytics dashboard (Doc 03 §3, Doc 04 §2.3).
- Add customer-facing multi-turn chat session memory if not already solid from Week 1.
- **Build the evaluation test set** (Doc 02 §7.1 — 4 buckets, ~15–25 cases each) and run it against the full system.
- Run the ablation study (RBAC-only vs. grounding-only vs. full TrustLayer) — this produces the paper's central results table.
- Use real score distributions from this run to finalize the 0.3/0.6 decision thresholds (Doc 02 §5) — don't leave these as untested guesses.
- **Exit criteria:** evaluation results + ablation table exist and are saved; approval flow and dashboard are functional end to end.

## Week 5 (Oct 27 – Oct 30) — Polish + Buffer

- Fix whatever the Week 4 evaluation exposed (grounding tuning, threshold adjustment, UI gaps).
- Finalize write-ups: rewrite this document set's PRD (Doc 01) framing where needed, ensure Docs 02–06 match what was actually built (not just what was planned).
- Full demo run-through, rehearse explaining the compound-failure result for the viva/defense.
- This week is explicit slack — if ahead of schedule, spend it on polish (UI, docs, paper writing); if behind, it absorbs the overflow from Weeks 1–4.
- **Exit criteria:** system demo-ready, all 6 docs finalized, evaluation results ready to present, by Oct 30.

---

## Risk Watch List

| Risk | Mitigation |
|---|---|
| No real banking dataset found in time | Hard deadline of Oct 8 to decide; synthetic fallback is already planned, not an emergency pivot |
| Gemini API rate limits or unavailability during dev/testing/demo | Cache claim-extraction responses during development; keep a backup API key; have a pre-recorded/cached demo run ready as a fallback for the live viva |
| NLI/embedding latency too high on target hardware | Measure early in Week 2; these still run locally, so this remains a real risk independent of the API change |
| Decision thresholds feel arbitrary without real data | Explicitly deferred tuning to Week 4 using actual eval score distributions |
| Approval/dashboard UI takes longer than a week | These are lower-research-value than Modules 1/2 — acceptable to simplify UI polish and push into Week 5 buffer if needed |
| Running behind by Week 4 | Week 5 is reserved buffer specifically for this |
