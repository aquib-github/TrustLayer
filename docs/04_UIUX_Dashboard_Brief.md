# Doc 04 — UI/UX & Dashboard Brief

**Project:** TrustLayer — Banking Support Assistant Case Study

---

## 1. Visual Identity (carried over from the original Doc 01 draft)

- **Palette:**
  - Navy `#1E3A8A` — primary/structural
  - Blue `#2563EB` — interactive/primary actions
  - Green `#059669` — grounding-related indicators
  - Red `#DC2626` — firewall/block indicators
  - Purple `#7C3AED` — fusion/decision-engine indicators
  - Amber `#D97706` — approval-pending indicators
- **Typography:** Lora (serif) — carried over from Doc 01's original styling

Decision-state colors (green/red/purple/amber) should map consistently across every screen — e.g. an ALLOW badge is always green, BLOCK always red, APPROVE always amber — so the visual language reinforces the system's actual decision logic.

## 2. Screens by Role

### 2.1 Customer — Chat Interface
- Standard chat UI: message thread, input box, session persists across the conversation (multi-turn memory).
- When an action is taken on the customer's behalf, show a small inline status chip (Allowed / Pending Approval) — do not expose grounding scores or policy internals to the customer (that's staff-only detail).
- Pending-approval actions show a lightweight status ("Your request is under review") without technical detail.

### 2.2 Senior Staff / Approver — Approval Queue
- List view of pending actions, newest first, with: customer, requested action, flag reason (grounding/policy/both), timestamp.
- Detail view per item: original request, extracted claim(s), grounding score + the evidence chunk(s) it was checked against, policy_risk + which rule triggered, proposed action parameters.
- Two clear actions: **Approve** / **Deny**, with an optional note field.
- Resolved items move to a separate "Resolved" tab for audit reference.

### 2.3 Staff / Admin — Analytics Dashboard
- Top-level KPI row: total requests, % allowed / blocked / approved (color-coded per the palette above), average grounding score, average latency.
- Time-series chart: decision volume over time, stacked by decision type.
- Table: most-retrieved KB documents (surfaces gaps in the knowledge base).
- Table: sequence-anomaly and hard-deny trigger log — a compact security-relevant view.
- Fallback/degraded-mode indicator: surfaces `grounding_unavailable` events prominently, since these represent moments the system had to fall back to conservative defaults.
- Dashboard is read-only in v1 — no write actions from this screen.

## 3. Design Principles

- **Transparency for staff, simplicity for customers.** Staff/Approver views expose the "why" (scores, evidence, rule triggers); customer views never do — this mirrors the access-aware philosophy of the system itself.
- **Decision state is always visually explicit.** Every action anywhere in the UI that has passed through TrustLayer shows its decision state via the fixed color mapping — never just as plain text.
- **No dead ends.** A blocked or pending action always tells the customer what happens next (contact support / wait for review), never a silent failure.

## 4. Out of Scope for v1

- No customer-facing self-service dispute of a block/approval outcome.
- No mobile-specific layout — desktop-first, responsive is a stretch goal only if time in Week 5 buffer allows.
- No theming/customization beyond the fixed palette above.
