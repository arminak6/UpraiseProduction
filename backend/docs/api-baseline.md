# UPRAISE draft API baseline

Authority: `UPRAISE_WP7_AI_Module_API_Specification_SIN_2026-09-25.docx`,
revision 0.1, SIN, 25 September 2026. The architecture diagram supplied in the
conversation is context. References remain drafts; changes require an explicit
project decision. The inventory below preserves the original Word baseline;
the differences table records explicit implementation decisions separately.

C1 `GET /health`, A1-A6, A8 ready-lesson listing and B1 lesson start are implemented.
Other endpoints remain reserved for their original responsibility. A3/A5's approved
contract is in `a3.md`; B1's integration details are in `b1.md`.
A/B/C grouping is preserved. The original Word was supplied again and checked
directly on 2026-10-06; this inventory preserves its draft contract. Current
A1 wire details and approved extensions are in `a1.md` and `a1-design.md`;
the user-requested A2 contract and operational interpretation are in `a2.md`.

## A authoring APIs

| ID | Original method and path in the overview | Responsibility and intended behavior |
| --- | --- | --- |
| A1 | `POST /v1/documents` | Async ingestion job; accept uploaded file or signed Moodle URL, provenance checks, extraction/OCR, chunking and embeddings into Qdrant; 202 with document/job IDs |
| A2 | `GET /v1/documents`; `DELETE /v1/documents/{id}` | List material by course or remove document vectors; list with status/version, 204 on delete |
| A3 | `POST /v1/lesson-jobs` | Async lesson generation through draft/reviewer/finalizer and speech; 202 job ID; deliver ordered DRAFT chunks with reviewer reports to Middleware by callback or A5 |
| A4 | `GET /v1/jobs/{job_id}` | Job status, current step, progress and errors; no LLM |
| A5 | `GET /v1/jobs/{job_id}/result`; `GET /v1/jobs/{job_id}/audio/{chunk_id}` | Pull temporary A3 JSON/audio within a short TTL; optional when callback delivery is used |
| A6 | `DELETE /v1/jobs/{job_id}` | Cancel running job or acknowledge receipt using CANCEL/RECEIVED; purge temporary output; 204 |
| A7 | `POST /v1/audio:regenerate` | Regenerate SSML, audio and lip-sync for author-edited text, optional review; sync for short text, otherwise job; author's text bypasses learner Input Guard |

## Approved authoring extension

A8 is a user-approved extension dated 2026-10-07, separate from the original
Word inventory: `GET /v1/lesson-jobs` lists ready generated lessons for frontend
selection. See [A8's contract](a8.md). A7 remains reserved as above.

B1 was approved on 2026-10-07 with process-local temporary state and the existing
24-hour lesson-output retention. Its implemented contract adds an exact
`lesson_job_id`, verified Middleware approval/consent assertion and local draft
preview. The user approved removing `capabilities` from B1's response on
2026-10-07. See [B1's contract](b1.md). B2-B6 remain reserved.

## B runtime APIs

| ID | Original method and path in the overview | Responsibility and intended behavior |
| --- | --- | --- |
| B1 | `POST /v1/sessions` | Open in-memory session with consent, RAG scope and capabilities; return session ID, AI notice and expiry |
| B2 | `POST /v1/sessions/{id}/chunk-end` | Gate reply PROCEED/HIGH_COGNITIVE_LOAD in under 300 ms; no Input Guard/RAG/LLM; always PROCEED without telemetry consent |
| B3 | `POST /v1/sessions/{id}/high-load-choice` | Receive CONTINUE/ASK/STOP; update prompt counters/cooldown, continue ASK via B4 or STOP via B5; 204 |
| B4 | `POST /v1/sessions/{id}/interrupts` | Current message only, text/audio; classify question/repeat; return REPEAT control or grounded RAG answer with speech/sources; sync or streamed, approximately 2 s to first audio |
| B5 | `POST /v1/sessions/{id}/end` | Clear chat, cognitive-load state and counters; 204; also clean up on inactivity TTL |
| B6 | `POST /v1/sessions/{id}/telemetry` | Consent-gated derived telemetry; 202 with no values, 403 without consent; update cognitive-load and prompt-guard state in memory only |

## C operations and administration APIs

| ID | Original method and path | Responsibility and intended behavior |
| --- | --- | --- |
| C1 | `GET /health`; `GET /ready` | Application liveness and integration readiness; 200/503. `/ready` reports Qdrant, PostgreSQL and AWS status; `/health` has no INGRESS |
| C2 | `GET /v1/status` | Degraded-mode flags for answers, STT, TTS, generation and kill switch; Middleware uses these to disable questions |
| C3 | `PUT /v1/admin/kill-switch` | Operator-only feature disabling by global/course scope; return new state and audit |
| C4 | `GET /v1/admin/audit`; `DELETE /v1/admin/audit` | Operator-only audit JSON/CSV export or erasure by pseudonym; delete returns 204 |
| C5 | `GET /v1/version` | Model, prompt and pipeline versions for audit and AI-generated labelling; SIN operator use |

## Architecture constraints

- The Middleware owns lesson pace, lesson/chunk storage and human approval;
  lessons are delivered as DRAFT with reviewer reports. Edited text triggers A7.
- Qdrant stores ingested course material/embeddings only, inside the AI Module;
  the diagram excludes personal data from it.
- Original diagram: PostgreSQL is audit-only. On 2026-10-02 the user explicitly
  approved separate durable ingestion-job storage alongside audit. Implementation
  uses `upraise_jobs` and `upraise_audit` databases, plus persistent processing
  artifacts. On 2026-10-06 the user explicitly requested durable finalized
  lesson text as in Uprise/v2: `prepared_lesson_chunks` stores the text used for
  speech, SSML, provenance, review and model/prompt versions alongside the
  operational lesson jobs. Audio remains temporary. Session state remains
  in memory and never persisted; incoming B4 audio is discarded after STT.
- The internal cognitive-load flag never leaves the module. B2's decision
  value HIGH_COGNITIVE_LOAD is nevertheless an explicit external control reply
  in both references; B6 returns no flag or telemetry values.
- The draft specifies Middleware callers on a private network/VPN, OAuth2
  client credentials plus mTLS, and session JWT claims. Admin APIs are SIN-only.
  A1/A2/A4/A6 implement OAuth JWT validation and trusted mTLS-gateway enforcement,
  denying production access until integration settings are supplied. The explicit
  local development exception is recorded in the differences table. Deployment TLS and
  the actual Moodle identity provider still need integration configuration.

## Differences and unresolved decisions

These rows distinguish unresolved discussion points from explicitly approved
A1 decisions.

| Topic | Difference or unclear detail | Scaffold treatment |
| --- | --- | --- |
| Interrupt intents | Diagram's Agent 2 says ask/repeat/continue/pause/stop. Word B4 currently classifies question/repeat and returns REPEAT/ANSWER; open point 8 only proposes adding CONTINUE/STOP | Keep original B3/B4/B5 responsibilities. No new intent, pause API or reply type |
| Temporary lesson output | Diagram says no lesson storage inside the module. Word A3/A5/A6 allow a temporary local result/audio copy until acknowledgement/TTL for pull mode | On 2026-10-05 the user approved A3/A5, temporary output until explicit A6 receipt or configurable 24-hour TTL. On 2026-10-06 the user requested permanent retention of finalized lesson text as in Uprise/v2. Text/SSML and provenance/review/versions now survive cleanup in PostgreSQL; A5 JSON/audio remain temporary and audit stays content-free |
| Job/checkpoint state | Word open point 4 suggests Redis or a job table, to check against audit-only PostgreSQL; diagram shows no job store | User approved durable PostgreSQL ingestion jobs on 2026-10-02. Separate worker, database roles and processing artifacts implement recovery; no learner-session checkpoints |
| Placeholder names | Word overview uses `{id}` for A2 and B2-B6; detailed sections use `{document_id}` and `{session_id}` | User's 2026-10-02 A2 request explicitly uses document_id; implemented that detailed form. Runtime placeholders remain reserved |
| Telemetry path | Diagram draws Middleware telemetry. Word B6 leaves direct versus Middleware routing open and suggests a possible `/v1/sessions/{session_id}/telemetry/ws` | Preserve original REST B6. WebSocket is an open possibility, not an added contract |
| Spoken answer transport | Word gives SSE event order for B4 while common conventions/open points leave SSE versus WebSocket unresolved | No streaming transport selected |
| Runtime providers | Word A3/B4 refer to AWS EU-region speech/LLM. The scaffold prompt leaves providers undecided; diagram's Speech Services is generic | A3 now uses the user-approved original V3 GPT OSS/Qwen teacher workflow and EU-region Polly audio/visemes. B4 providers remain deferred |
| Audit scope | Common convention says every API ends in AUDIT, but A2 lists audit for delete only, A4 omits an audit step, and B6 says no data are persisted | A1 lifecycle and A2 deletion audit are implemented; A2 list has no audit per the explicit request. Broader API/read/rejection audit scope and retention need clarification |
| Liveness payload | Word defines C1 purpose/status but no `/health` JSON schema | Scaffold returns `{"status":"alive"}` with 200, without claiming integration or AI readiness |
| A1 enrichment | Earlier Word A1 overview says LLM=No; old extraction uses generative Nova math/figure enrichment | User explicitly approved the old pipeline including Nova on 2026-10-02; applies to A1 extraction only |
| A1 Ingestion Guard | Original A1 requires instruction scanning and origin/MIME/hash provenance checks | User explicitly removed this stage on 2026-10-02. No instruction/active-content, expected-hash, magic/declared MIME/extension or source-host allowlist checks remain. PDF readability/encryption/size/page support limits, OAuth/mTLS INGRESS and HTTPS/TLS/DNS network handling remain |
| A1 accepted response | Original Word A1 returns document_id and job_id | User requested job_id removal on 2026-10-02, then restored it on 2026-10-06 after checking the Word specification. A1 now returns both document_id and job_id. Location identifies the A4 route for the same ingestion job; use that job_id with A4/A6. A5 uses the lesson job_id returned by A3 |
| A1 input details | Draft describes content hash/version without a complete wire schema | After the explicit 2026-10-02 guard removal, content_hash is required opaque unverified source metadata, including Moodle SHA-1. Internal source_hash is actual SHA-256. Optional version and Idempotency-Key support byte-aware retries, including legacy accepted requests |
| A2 status/list source | Draft describes Qdrant reads but status/version for accepted documents also live in the approved PostgreSQL job registry; queued/failed documents can have no vectors | User requested A2 on 2026-10-02. Return all undeleted client/course submissions with registry status/version and active Qdrant chunk counts; preserve the Word's methods, grouping and delete-only audit. Durable deletion markers fence workers and recover interrupted deletion |
| A6 reason and cleanup | Draft gives CANCEL/RECEIVED and temporary-output purge without state or parameter placement details | User requested implementation on 2026-10-02. Required reason query parameter; CANCEL fences QUEUED/RUNNING and sets CANCELLED, RECEIVED acknowledges DONE/FAILED. Preserve originals/history/active vectors; durable cleanup and audit precede 204. Busy writers return retryable 503 until cleanup completes. See a6.md |
| Local testing authentication | Draft requires OAuth/mTLS INGRESS; those integration settings are not yet supplied for Streamlit testing | On 2026-10-02 the user explicitly approved local testing mode. It requires environment=development plus local_test_mode=true; production defaults remain authenticated. Fixed testing client, local/private network peers and current authoring scopes only; API methods/payloads/extraction remain unchanged. See local-testing.md |

Other Word open points remain open: headset/server STT, current-chunk context
for RAG, SUPSI feature format, B2 timeout and STOP notification, and full versus
sentence-level grounding before audio streaming. No scaffold code resolves them.

On 2026-10-05 the user approved mTLS plus short-lived OAuth2 tokens for AI → MW
callbacks as a design addition, configurable source windows (default five pages),
word-based lesson chunks and exactly one optional finalizer revision. The
native V3 implementation validates edited text without a second model review. Source
uncertainty remains explicit; all lessons are DRAFT. See `a3.md` for the full wire
contract, idempotency, scope, callback retry and expiry behavior.

On 2026-10-06 the user requested level-aware explanations. A3's existing
beginner/intermediate/advanced option now guides the generator, reviewer and
editor system prompts. The level changes teaching difficulty, with the same
source-grounding and coverage requirements. A5 records the applied level and
prompt version; prior jobs are not regenerated automatically.

## Infrastructure sources

The selected versions are explicit. Healthcheck commands are verified against
the running images during scaffold validation; source documentation alone is
not a substitute for that check.

- [uv Docker integration and locked dependency installation](https://docs.astral.sh/uv/guides/integration/docker/)
- [Qdrant health endpoints](https://qdrant.tech/documentation/ops-monitoring/monitoring/)
- [Qdrant v1.19.1 image source](https://github.com/qdrant/qdrant/blob/v1.19.1/Dockerfile)
- [Official Python image definitions](https://github.com/docker-library/official-images/blob/master/library/python)
- [Official PostgreSQL image definitions](https://github.com/docker-library/official-images/blob/master/library/postgres)
