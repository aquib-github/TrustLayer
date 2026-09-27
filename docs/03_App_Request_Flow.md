# Doc 03 — App / Request Flow

**Project:** TrustLayer — Banking Support Assistant Case Study

---

## 1. Primary Flow — Customer Chat Request (Happy Path)

1. Customer logs in → session created (`chat_sessions`), role resolved as `customer`.
2. Customer sends a message (e.g. "Can you waive my late fee and show my balance?").
3. Message stored (`chat_messages`), multi-turn context loaded from prior turns in the same session.
4. Agent reasons over the message + context, retrieves role-scoped knowledge (access-aware RAG), and proposes:
   - a natural-language response, containing one or more **claims**, and
   - zero or more **tool calls** (e.g. `check_balance`).
5. Request enters **TrustLayer Interception Layer** with a fresh `request_id`.
6. **Parallel branch A (Grounding):** claims extracted → retrieved evidence (role-filtered) → NLI verification → `grounding_score`.
7. **Parallel branch B (Policy):** proposed tool call(s) checked against RBAC + sequence-anomaly rules → `policy_risk`.
8. **Decision Engine** applies hard overrides, then weighted score → **ALLOW / BLOCK / APPROVE**.
9. **If ALLOW:** tool call executes against the mock bank ledger DB → result returned to agent → final response sent to customer.
10. **If BLOCK:** action is not executed; agent returns a safe fallback response to the customer (e.g. "I can't complete that action — please contact support"), decision logged.
11. **If APPROVE:** action is queued and routed to a Senior Staff/Approver; customer is informed the request is pending approval.
12. **Always:** decision + full context logged to `audit_log`; structured logs emitted at each stage keyed by `request_id`.

## 2. Approval Sub-Flow — Senior Staff/Approver

1. Approver logs in, sees a queue of pending actions routed to them (`decisions` where `decision = 'approve'` and unresolved).
2. Approver opens an action → sees: original customer request, agent's claim(s), grounding_score + evidence used, policy_risk + reason for flag, proposed action + parameters.
3. Approver resolves as **Approve** or **Deny**, per real-world manager-approval policy.
4. Resolution recorded (`approver_id`, timestamp) in `decisions` / `audit_log`.
5. If approved → action executes against the mock ledger; customer is notified.
6. If denied → action does not execute; customer is notified with a generic decline message.

## 3. Staff/Admin Flow — Usage Analytics

1. Staff/Admin logs in, lands on the analytics dashboard.
2. Dashboard queries aggregate data from `audit_log`, `decisions`, `agent_actions`, `kb_documents` usage:
   - Query/action volume over time
   - Breakdown of decisions (allow/block/approve) over time
   - Most-retrieved knowledge base documents
   - Sequence-anomaly / hard-deny trigger frequency
   - Fallback/grounding-unavailable event frequency
3. No write actions from this view in v1 — read-only analytics.

## 4. Error / Fallback Flow

1. If Module 1 (grounding) fails or times out (Ollama/NLI unavailable):
   - Money-moving action → forced minimum APPROVE (never ALLOW), `event = 'grounding_unavailable'` logged.
   - Read-only action → fail-open with a logged warning.
2. If Module 2 (policy) fails unexpectedly → fail-closed (default BLOCK) for any action, since policy is the harder safety boundary. Logged as `event = 'policy_engine_error'`.

## 5. Sequence Diagram (textual)

```
Customer -> FastAPI Gateway: message
FastAPI Gateway -> Agent: message + session context
Agent -> RAG Retriever: role-scoped query
RAG Retriever -> Agent: evidence chunks
Agent -> TrustLayer: proposed response + tool call(s)
TrustLayer -> Module1(Grounding): claims + evidence
TrustLayer -> Module2(Policy): tool call + role
Module1 -> TrustLayer: grounding_score
Module2 -> TrustLayer: policy_risk
TrustLayer -> DecisionEngine: grounding_score, policy_risk
DecisionEngine -> TrustLayer: decision (allow/block/approve)
alt decision == allow
    TrustLayer -> MockLedgerDB: execute tool call
    MockLedgerDB -> TrustLayer: result
    TrustLayer -> Agent: result
    Agent -> Customer: final response
else decision == block
    TrustLayer -> Agent: blocked, no execution
    Agent -> Customer: safe fallback response
else decision == approve
    TrustLayer -> ApproverQueue: pending action
    TrustLayer -> Customer: "pending approval" notice
end
TrustLayer -> AuditLog: full decision record
TrustLayer -> StructuredLogs: request_id-correlated events
```
