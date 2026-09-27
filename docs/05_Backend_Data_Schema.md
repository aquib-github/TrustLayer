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
  embedding VECTOR(1024),  -- BGE-M3 dimension
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
  grounding_score NUMERIC
)

policy_checks(
  id UUID PRIMARY KEY,
  action_id UUID REFERENCES agent_actions(id),
  rbac_result TEXT,          -- 'allowed' | 'denied'
  sequence_anomaly_flag BOOLEAN,
  policy_risk_score NUMERIC
)

decisions(
  id UUID PRIMARY KEY,
  action_id UUID REFERENCES agent_actions(id),
  grounding_score NUMERIC,
  policy_risk_score NUMERIC,
  final_risk NUMERIC,
  decision TEXT,               -- 'allow' | 'block' | 'approve'
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

- **`request_id` on `audit_log`** is the correlation key shared with structured application logs — every log line for a single request carries the same ID, satisfying the end-to-end traceability requirement.
- **`role_scope` on `kb_documents`** is what makes retrieval access-aware — the retrieval query always filters by the requesting user's role before the vector search runs, not after.
- **`kb_embeddings.embedding VECTOR(1024)`** — dimension must match BGE-M3's actual output size; confirm exact value when the embedding model is first loaded (adjust DDL if it differs).
- **`decisions.approver_id` is nullable** — only populated when `decision = 'approve'` and a Senior Staff/Approver has resolved it.
- **Mock ledger, not real banking integration** — `accounts`/`transactions` are entirely local/simulated; this is why no Docker sandbox is required for tool execution.

## 3. Indexing Notes

- `kb_embeddings.embedding` — pgvector index (IVFFlat or HNSW, whichever pgvector version supports) for similarity search performance on limited hardware.
- `audit_log.request_id` and `audit_log.session_id` — indexed for fast correlation lookups on the analytics dashboard.
- `chat_messages.session_id` — indexed for fast conversation history loading.

## 4. Data Population Plan

- **`kb_documents`/`kb_embeddings`:** populated from either a found real banking FAQ/policy dataset, or a synthetically generated one with realistic noise (typos, ambiguous phrasing) if no suitable real dataset is found by early Week 2.
- **`users`/`accounts`/`transactions`:** seeded with a small synthetic mock-bank dataset for the demo (a handful of customers across the three roles, with realistic-looking balances/transaction histories).
