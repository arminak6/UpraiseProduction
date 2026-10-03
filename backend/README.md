# UPRAISE backend

FastAPI preserves A/B/C grouping. Implemented routes are GET /health,
POST /v1/documents (A1), GET /v1/documents and DELETE /v1/documents/{document_id}
(A2), GET /v1/jobs/{job_id} (A4), and DELETE /v1/jobs/{job_id} (A6).
Run through root Compose; see ../README.md and docs/{a1,a2,a6}.md.
The user explicitly removed the A1 Ingestion Guard and job_id from the A1 body
on 2026-10-02. A1 returns 202 with document_id only; Location supplies the
unchanged A4 polling URL. Internal PostgreSQL jobs remain.

services/pdf contains the reviewed Docling/Nova PDF extraction components.
Its input.py checks readability, unsupported encryption and size/page limits.
Instruction/active-content, expected-hash, magic/declared MIME/extension and
source-host provenance checks are removed. OAuth/mTLS INGRESS and HTTPS/TLS/DNS
network handling remain. content_hash is required unverified source metadata;
source_hash is computed SHA-256. Current pipeline: pdf-ingestion-v1.3.
All available math and figure crops are enriched without per-document count
caps or a minimum figure-size cutoff.
Shared services handle chunking, Titan embeddings and format dispatch.
workflows/submission coordinates durable acceptance; workers/ingestion handles
leases/retries and publication. storage separates jobs, audit, files and vectors.
workflows/documents coordinates scoped A2 lists and recoverable vector deletion.
Deletion fences workers, removes all attempts and audits before returning 204.
workflows/jobs coordinates A6's CANCEL/RECEIVED reasons, durable worker fencing,
artifact cleanup and audit. Source PDFs and acknowledged active vectors remain.
Busy workers return recoverable 503 until output cleanup is complete; see docs/a6.md.
No lesson storage or learner-session persistence is added.
The separate Streamlit Testbench has A1, A2, A4 and A6 forms plus health/API explorer.
The user-approved local mode is enabled through ignored root .env on this
workstation. Production authentication is the default; see docs/local-testing.md.

Dependency authority is pyproject.toml plus generated uv.lock. Python 3.12,
CPU PyTorch and Docling 2.115.0 are installed with uv sync --locked --no-dev.
Tests run with the dev group using the documented Compose command. Docker
contexts allow source/manifests only and exclude local credentials and test data.
