# Doc 05 — Backend / Data Schema

**Project:** TrustLayer — Banking Support Assistant Case Study
**Database:** PostgreSQL + pgvector extension

---

## 1. Schema (DDL-style overview)

```sql
-- Identity & roles
users(
  id UUID PRIMARY KEY,
  name TEXT,
  email TEXT UNIQUE,
  role_id INT REFERENCES roles(id),
  created_at TIMESTAMP
)

roles(
  id SERIAL PRIMARY KEY,
  name TEXT  -- 'customer' | 'staff' | 'approver'
)

-- Banking domain (mock ledger)
accounts(
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users(id),
  balance NUMERIC,
  account_type TEXT
)

transactions(
  id UUID PRIMARY KEY,
  account_id UUID REFERENCES accounts(id),
  amount NUMERIC,
  type TEXT,        -- 'debit' | 'credit' | 'transfer' | 'dispute'
  status TEXT,       -- 'pending' | 'completed' | 'blocked'
  created_at TIMESTAMP
)

-- Knowledge base (access-aware RAG)
kb_documents(
  id UUID PRIMARY KEY,
  title TEXT,
  content TEXT,
  source TEXT,         -- 'real' | 'synthetic'
  role_scope TEXT[]     -- which roles can retrieve this doc
)

kb_embeddings(
  id UUID PRIMARY KEY,
  doc_id UUID REFERENCES kb_documents(id),
  chunk_text TEXT,
  embedding VECTOR(1024),  -- BGE-M3 dimension — confirmed 1024 at implementation time
  chunk_index INT
)

-- Conversation
chat_sessions(
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users(id),
  started_at TIMESTAMP
)

chat_messages(
  id UUID PRIMARY KEY,
  session_id UUID REFERENCES chat_sessions(id),
  role TEXT,     -- 'user' | 'agent'
  content TEXT,
  created_at TIMESTAMP
)

-- Agent actions & TrustLayer pipeline
agent_actions(
  id UUID PRIMARY KEY,
  session_id UUID REFERENCES chat_sessions(id),
  tool_name TEXT,
  params_json JSONB,
  proposed_at TIMESTAMP
)

claims(
  id UUID PRIMARY KEY,
  action_id UUID REFERENCES agent_actions(id),
  claim_text TEXT,
  extracted_at TIMESTAMP
)

grounding_results(
  id UUID PRIMARY KEY,
  claim_id UUID REFERENCES claims(id),
  retrieved_doc_ids UUID[],
  nli_label TEXT,          -- 'entailment' | 'neutral' | 'contradiction'
  grounding_score NUMERIC(5,4)
)

policy_checks(
  id UUID PRIMARY KEY,
  action_id UUID REFERENCES agent_actions(id),
  rbac_result TEXT,          -- 'allowed' | 'denied'
  sequence_anomaly_flag BOOLEAN,
  policy_risk_score NUMERIC(5,4)
)

decisions(
  id UUID PRIMARY KEY,
  action_id UUID REFERENCES agent_actions(id),
  grounding_score NUMERIC(5,4),
  policy_risk_score NUMERIC(5,4),
  final_risk NUMERIC(5,4),      -- fixed-precision, migrated from float in 0002_numeric_precision — see note below
  decision decision_type,       -- Postgres ENUM: 'allow' | 'block' | 'approve' (lowercase, strictly enforced at the DB level)
  approver_id UUID REFERENCES users(id) NULL,
  resolved_at TIMESTAMP NULL
)

audit_log(
  id UUID PRIMARY KEY,
  request_id UUID,
  session_id UUID REFERENCES chat_sessions(id),
  action_id UUID REFERENCES agent_actions(id) NULL,
  decision_id UUID REFERENCES decisions(id) NULL,
  event TEXT,                  -- e.g. 'decision_made', 'grounding_unavailable', 'policy_engine_error'
  timestamp TIMESTAMP
)
```

## 2. Key Design Notes

- **`request_id` on `audit_log`** is the correlation key shared with structured application logs — every log line for a single request carries the same ID, satisfying the end-to-end traceability requirement. Confirmed working via direct joins across `audit_log` → `decisions` → `agent_actions`.
- **`role_scope` on `kb_documents`** is what makes retrieval access-aware — t he retrieval query always filters by the requesting user's role before the vector search runs, not after. Confirmed working: a staff-only document is correctly invisible to customer-role retrieval (grounding_score 0.0 for a customer-role claim about a staff-only sequence-anomaly policy vs. 0.99+ for the same claim under staff role).
- **`kb_embeddings.embedding VECTOR(1024)`** — confirmed matches BGE-M3's actual output dimension (1024) at implementation time.
- **`decisions.decision`** is implemented as a strict Postgres ENUM type (`decision_type`), not a free-text column — verified to reject any value other than exactly `allow`, `block`, `approve` (lowercase) at the database level, including rejecting uppercase variants and other candidate values tested during implementation.
- **`decisions.final_risk` must be `NUMERIC(5,4)`, not `float8`/`double precision`.** A float type was found in practice to return binary floating-point precision artifacts when queried directly (e.g. `0.40000000000000002220446049250313080847263336181640625` instead of `0.4`) — unacceptable for an auditable financial risk score. This is now fixed: Alembic migration `0002_numeric_precision` converted `final_risk`, `policy_risk_score` and `grounding_score` (in `decisions`, `policy_checks` and `grounding_results`) to `NUMERIC(5,4)`, verified via `\d decisions` and direct queries returning clean values such as `0.4000`.
- **`decisions.approver_id` is nullable** — only populated when `decision = 'approve'` and a Senior Staff/Approver has resolved it.
- **Mock ledger, not real banking integration** — `accounts`/`transactions` are entirely local/simulated; this is why no Docker sandbox is required for tool execution. (Docker is separately used as a local dev convenience to host Postgres+pgvector — see Doc 02 §1 — which is unrelated to this design decision.)

## 3. Indexing Notes

- `kb_embeddings.embedding` — pgvector index (IVFFlat or HNSW, whichever pgvector version supports) for similarity search performance on limited hardware.
- `audit_log.request_id` and `audit_log.session_id` — indexed for fast correlation lookups on the analytics dashboard.
- `chat_messages.session_id` — indexed for fast conversation history loading.

## 4. Data Population Plan

- **`kb_documents`/`kb_embeddings`:** no suitable real banking dataset was found by the Week 2 checkpoint, so the fallback plan was exercised — populated with 12 synthetic policy documents (overdraft fees, dispute deadlines, transfer limits, card blocking, international fees, an internal staff-only sequence-anomaly policy, etc.), clearly marked `source = 'synthetic'`, embedded via BGE-M3 into `kb_embeddings`.
- **`users`/`accounts`/`transactions`:** seeded with synthetic users across the three roles (3 customers, 2 staff, 1 approver at last count), with accounts and a handful of transactions per account — confirmed populated and queryable directly via Postgres.

## 5. API Endpoints — Approvals Flow

### 5.1 POST `/approvals/{id}/approve`
Approves a pending held action, executes the underlying banking tool call against the mock ledger, sets resolution timestamps and approver ID, and writes an audit log entry.

- **Access:** Approver role only (`X-User-Role: approver`, approver `user_id`, or `role: "approver"`). Other roles (customer, staff) receive `403 Forbidden`.
- **Precondition:** Approval `{id}` must exist (`404 Not Found` if missing) and must not have been previously resolved (`409 Conflict` if already resolved).

**Request Headers & Parameters:**
```http
POST /approvals/5f420ccd-3e6c-42ac-b041-67073d5ef404/approve HTTP/1.1
Host: localhost:8000
Content-Type: application/json
X-User-Role: approver
X-User-Id: 66666666-6666-6666-6666-666666666666
```

**Request Body (JSON, all fields optional):**
```json
{
  "reason": "Manual review passed: customer confirmed transfer intention over phone",
  "role": "approver",
  "user_id": "66666666-6666-6666-6666-666666666666"
}
```

**Response (200 OK):**
```json
{
  "approval_id": "5f420ccd-3e6c-42ac-b041-67073d5ef404",
  "decision": "approve",
  "status": "executed",
  "tool_name": "transfer_funds",
  "tool_result": {
    "status": "success",
    "action": "transfer_funds",
    "transaction_id": "9a38f38d-195f-45b6-96ec-bb571a07bb5c",
    "from_account_id": "a1111111-1111-1111-1111-111111111111",
    "from_account_type": "checking",
    "amount": 4585.95,
    "recipient": "external recipient",
    "new_balance": 509.55
  },
  "approver_id": "66666666-6666-6666-6666-666666666666",
  "resolved_at": "2026-10-07T12:05:00.123456Z",
  "reason": "Manual review passed: customer confirmed transfer intention over phone",
  "message": "Held action approved and executed successfully."
}
```

**Conflict Response (409 Conflict — Double-resolve prevention):**
```json
{
  "detail": "Approval 5f420ccd-3e6c-42ac-b041-67073d5ef404 has already been resolved at 2026-10-07T12:05:00.123456."
}
```

**Forbidden Response (403 Forbidden — Non-approver role):**
```json
{
  "detail": "Forbidden: Approver role required."
}
```

---

### 5.2 POST `/approvals/{id}/reject`
Rejects a pending held action. The tool action is **never executed** against the ledger. The decision is marked resolved, approver ID recorded, and an audit log entry written.

- **Access:** Approver role only. Other roles receive `403 Forbidden`.
- **Precondition:** Approval `{id}` must exist (`404 Not Found`) and must not have been previously resolved (`409 Conflict`).

**Request Headers & Parameters:**
```http
POST /approvals/5f420ccd-3e6c-42ac-b041-67073d5ef404/reject HTTP/1.1
Host: localhost:8000
Content-Type: application/json
X-User-Role: approver
X-User-Id: 66666666-6666-6666-6666-666666666666
```

**Request Body (JSON, all fields optional):**
```json
{
  "reason": "Suspected account takeover: customer denied authorizing 90% balance drain",
  "role": "approver",
  "user_id": "66666666-6666-6666-6666-666666666666"
}
```

**Response (200 OK):**
```json
{
  "approval_id": "5f420ccd-3e6c-42ac-b041-67073d5ef404",
  "decision": "reject",
  "status": "rejected",
  "tool_name": "transfer_funds",
  "tool_result": null,
  "approver_id": "66666666-6666-6666-6666-666666666666",
  "resolved_at": "2026-10-07T12:06:15.654321Z",
  "reason": "Suspected account takeover: customer denied authorizing 90% balance drain",
  "message": "Held action rejected. Tool action was not executed."
}
```

**Conflict Response (409 Conflict — Double-resolve prevention):**
```json
{
  "detail": "Approval 5f420ccd-3e6c-42ac-b041-67073d5ef404 has already been resolved at 2026-10-07T12:06:15.654321."
}
```

---

### 5.3 GET `/approvals/pending`
Lists all unresolved actions queued for Senior Staff / Approver review, paginated and sorted newest first.

- **Access:** Approver role only (`X-User-Role: approver`, `?role=approver`, or approver `user_id`). Other roles receive `403 Forbidden`.
- **Query Parameters:**
  - `limit` (integer, default: 50, min: 1, max: 100): Maximum records to return.
  - `offset` (integer, default: 0, min: 0): Records to skip for pagination.

**Request:**
```http
GET /approvals/pending?limit=10&offset=0 HTTP/1.1
Host: localhost:8000
X-User-Role: approver
```

**Response (200 OK):**
```json
[
  {
    "decision_id": "5f420ccd-3e6c-42ac-b041-67073d5ef404",
    "action_id": "5ae4145b-ff2f-4b79-98a1-22ac18be6a18",
    "session_id": "8b32d201-1402-4fc8-9f1e-f3b145ac7462",
    "tool_name": "transfer_funds",
    "params": {
      "amount": 4585.95,
      "recipient": "external recipient",
      "from_account_type": "checking"
    },
    "grounding_score": 1.0,
    "policy_risk_score": 0.8,
    "final_risk": 0.4,
    "decision": "approve",
    "proposed_at": "2026-10-07T12:04:30.987654Z"
  }
]
```

