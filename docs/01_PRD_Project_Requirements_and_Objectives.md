# Doc 01 — Project Requirements & Objectives

**Project:** TrustLayer — Access-Aware RAG Gateway with Claim-Level Grounding Verification
**Case Study:** Role-Based Banking Support Assistant
**Paper Title:** *Design and Evaluation of an Access-Aware RAG Gateway with Claim-Level Grounding Verification: A Role-Based Banking Support Assistant Case Study*
**Owner:** Aquib — BE Computer Science / Data Science (AI & ML), VCET, Mumbai University
**Target Completion:** 30 October 2026

---

## 1. Problem Statement

Agentic AI systems are increasingly deployed in production to take real actions on a company's behalf — not just answer questions. Two failure modes exist today, and the research/industry landscape treats them as separate problems:

- **Type-A failure (hallucination):** the agent's underlying belief/claim is factually wrong.
- **Type-B failure (unauthorized action):** the agent takes an action it isn't permitted to take.

Existing research (see literature survey, 21 papers) splits cleanly into two disconnected groups: tool-call security/firewall systems that verify *what an agent is allowed to do*, and hallucination-detection systems that verify *what an agent believes is true*. **No system evaluates both together.**

The compound failure this leaves open: an agent can pass every authorization check (the action is fully permitted) while still acting on a false or ungrounded claim — e.g., an agent that is authorized to process a refund does so because it hallucinated that a return policy applies, when it doesn't. Companies deploying agentic AI cannot absorb the financial and trust cost of this failure mode, and no current tool catches it before execution.

## 2. Objective

Build a middleware — **TrustLayer** — that sits between an AI agent's reasoning step and tool execution, and jointly evaluates:
1. Whether the claim that justifies the proposed action is **factually grounded** in evidence (claim-level NLI verification against a knowledge base).
2. Whether the action itself is **authorized** under role-based policy.

...and gates execution (allow / block / require human approval) based on both, in real time, before the action executes.

## 3. Case Study Domain: Banking Support Assistant

Banking was chosen because it has natural role tiers, high-stakes financial actions, and a real regulatory/policy analogue (manager approval workflows) that maps cleanly onto the human-approval decision branch.

### 3.1 Core Agentic Capabilities (must-have)
The assistant must reason and take real actions via tool calls — this is non-negotiable; without it, the project is "just RAG." Required tools:
- Check balance
- Get transactions
- Transfer funds
- Block card
- Dispute charge

### 3.2 Roles (three-tier)
| Role | Description |
|---|---|
| Customer | End user; interacts via chat with multi-turn session memory |
| Bank Staff / Admin | Elevated access; can view usage analytics (query/action types, resource usage) |
| Senior Staff / Approver | Receives and resolves actions routed to human approval, per company-policy-style manager approval |

## 4. Functional Requirements

- **FR1:** System must retrieve knowledge base content filtered by the requesting user's role before generation (access-aware RAG).
- **FR2:** System must extract atomic claims from the agent's generated response/reasoning.
- **FR3:** System must verify each claim against retrieved evidence using NLI and compute a grounding score.
- **FR4:** System must intercept every proposed tool call before execution.
- **FR5:** System must check the proposed action against RBAC policy for the user's role.
- **FR6:** System must run a rule-based sequence-anomaly check (e.g., check-balance immediately followed by transfer-all-funds without re-auth).
- **FR7:** System must combine grounding risk + policy risk into a final decision: **allow / block / approve** (see Doc 02 for exact logic).
- **FR8:** Actions routed to "approve" must reach a Senior Staff/Approver role for resolution.
- **FR9:** System must log every decision with a shared request/session ID, correlated across structured logs and a persistent audit table.
- **FR10:** Admin/staff must have a dashboard showing usage analytics.
- **FR11:** Customer-facing chat must retain multi-turn session context.

## 5. Non-Functional Requirements

- **NFR1 — Zero budget:** every component must be free, open-source, or self-hosted. No paid cloud tiers (e.g. no Neo4j Aura, no Qdrant Cloud).
- **NFR2 — Runtime dependencies:** embeddings (BGE-M3) and NLI verification (DeBERTa-v3) run fully locally/offline. Claim extraction uses the **Gemini API** (cloud call) as a deliberate trade-off for reliability and ease of implementation — this means the running system requires internet connectivity and a valid API key at runtime for that one step. This is a conscious deviation from the original "fully offline" goal, documented here rather than left implicit; a fallback/offline-capable claim-extraction path (e.g. local Ollama model) is noted as a possible future extension if the API dependency proves risky for the live demo.
- **NFR3 — Hardware constraint:** must run on Intel i5-1035G1, 8GB RAM, integrated graphics — no local GPU inference or training.
- **NFR4 — Single-instance scope:** no load balancer or horizontal scaling; this is a local demo system, not a production service.
- **NFR5 — Logging:** comprehensive structured logging across the whole system (not just the audit decision log), correlated via a shared request/session ID.
- **NFR6 — Research quality:** design decisions must be documented with rationale suitable for a published paper and for technical interview discussion.

## 6. Out of Scope (explicit simplifications vs. original full design)

| Original idea | Simplified to | Reason |
|---|---|---|
| Qdrant vector DB | pgvector | Zero-budget, single local Postgres instance |
| Neo4j + NetworkX tool-dependency graph | Rule-based sequence check | 5-week solo build; graph DB adds infra overhead without proportional research value |
| Trained XGBoost risk fusion | Documented weighted rule (grounding + policy risk) | No labeled training data pipeline feasible in timeframe; noted as future extension |
| Docker sandbox for tool execution | Mocked bank ledger in local DB | Nothing genuinely risky to contain since there's no real bank integration |
| Standalone injection-detection module | Folded into RBAC/policy check | Reduces module count without losing the defense |
| MCP (Model Context Protocol) | Direct FastAPI interception | Industry guide flagged MCP as optional ("if required"); not required for this scope |

## 7. Success Criteria

- End-to-end demo: customer submits a request → agent reasons → TrustLayer intercepts → grounding + policy checks run → decision (allow/block/approve) is enforced → audited.
- Evaluation shows TrustLayer catches the **compound failure case** (grounded-false-claim + authorized-action) that RBAC-only and grounding-only baselines each miss (see Doc 02 §6 for evaluation design).
- All 6 planning documents complete and internally consistent with the final banking/agentic scope.
- System is demo-ready and defensible in a viva/interview by 30 October 2026.

## 8. Stakeholders

- **Aquib** — sole designer/implementer (avoid describing the project as "solo" in official documentation, though it practically is)
- **College project guide** — approved the project
- **Independent industry expert** — acting as an additional technical guide
