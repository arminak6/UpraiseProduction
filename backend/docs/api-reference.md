# UPRAISE backend API reference

This reference covers all 25 application HTTP operations in the UPRAISE FastAPI backend. Each entry gives the HTTP method, path, permission, inputs, and success response. The [JSON schema tables](#json-schemas) list the fields and types for named request and response bodies.

A-series operations handle course material and lesson generation. B-series operations handle lesson delivery and its lifecycle. C-series operations report service health and manage operational controls.

- [Authentication and response conventions](#calling-the-api)
- [Source documents and courses](#source-documents-and-courses)
- [Lesson generation, jobs, and teacher review](#lesson-generation-jobs-and-teacher-review)
- [Learning sessions](#learning-sessions)
- [Health, status, and administration](#health-status-and-administration)
- [JSON schemas](#json-schemas)

## Calling the API

Use `http://127.0.0.1:8000` in the local JWT Compose setup. For production, use the deployment's HTTPS API gateway and the middleware's mTLS client certificate. The backend publishes its OpenAPI JSON at `GET /openapi.json`, Swagger UI at `GET /docs`, and ReDoc at `GET /redoc`.

### Service JWT

Protected routes take `Authorization: Bearer <access_token>`. The issuer signs the JWT with `RS256` or `ES256`. The backend checks the configured `iss` and `aud`, expiry (`exp` in Unix seconds), allowed caller identity (`client_id`, with `azp` or `sub` as fallbacks), route permission (`scope`, a space-separated string), and course access (`upraise_courses`, an array of strings). `iat` is optional. `sub` can be omitted if `client_id` or `azp` identifies the caller.

The **scope** is the permission named in each entry below. For instance, `documents:read jobs:read` permits both document listing and job status calls for authorized resources. Resource IDs are also checked against the calling client and course. [Production ingress settings](a1.md#ingress-configuration)

### Local token endpoint

In the JWT Compose setup, `POST http://127.0.0.1:8001/oauth/token` exchanges test client credentials for a JWT. Enable the token listener with the local test client secret configuration. This token is scoped to `demo-course`. The production identity provider supplies its own token URL. [Local setup and curl commands](local-testing.md#direct-curl-with-a-jwt)

| Input | Location | Required | Value |
| --- | --- | --- | --- |
| Client ID and secret | HTTP Basic auth | Yes | Local client ID `auth-demo-middleware` and its configured test secret |
| `grant_type` | URL-encoded form | Yes | `client_credentials` |
| `scope` | URL-encoded form | Yes | One or more assigned permissions, separated by spaces |

**Output** `200 OK` with `access_token` (the signed JWT), `token_type` (`Bearer`) and `expires_in` (`300` seconds locally). A wrong client ID or secret returns `401` with `error: invalid_client`. An unsupported grant or scope returns `400` with `error: unsupported_grant_type` or `invalid_scope`. Use the `access_token` value in the backend's `Authorization` header.

With the local JWT stack running, this PowerShell example gets a fresh token and lists documents for `demo-course`. Enter the value after `=` in `codex_helper/jwt-test/client.env` when prompted.

```powershell
$upraiseTestSecret = Read-Host "UPRAISE test client secret"
$jwt = (curl.exe -fsS -u "auth-demo-middleware:$upraiseTestSecret" -d "grant_type=client_credentials&scope=documents:read" http://127.0.0.1:8001/oauth/token | ConvertFrom-Json).access_token
curl.exe -i -H "Authorization: Bearer $jwt" "http://127.0.0.1:8000/v1/documents?course_id=demo-course"
```

### Learner-session JWT

B1, B2, B4, and B5 also take `X-UPRAISE-Session-JWT`, a signed assertion from the middleware. It binds the learner, exact generated lesson and result hash, and session approval and consent. Use the same session binding for follow-up calls. The standalone JWT Compose token endpoint issues service tokens. A separately configured signer supplies learner assertions. Explicit development draft previews opened in JWT-free local test mode can omit this header. [Learner assertion claims](b1.md#production-identity-consent-and-approval)

### Request and response conventions

- Path arguments appear in `{braces}` in the reference. Query arguments go after `?`. Send a JSON body with `Content-Type: application/json` unless an entry specifies multipart upload.
- `200` returns a result, `201` creates a session, `202` accepts an asynchronous job, and `204` returns an empty body. Poll the job ID from a `202` response to learn when processing finishes.
- `401` covers missing or invalid authentication, `403` denied permission, `404` a missing or inaccessible resource, `409` a conflicting resource state, `410` expired or disposed output, `413` a size limit, `415` an unsupported body format, `422` invalid inputs, and `503` unavailable dependencies, controls, or pending cleanup. Individual endpoints use the statuses relevant to their operation.
- Error bodies normally contain `detail`, either a short error code or an array of validation issues. Some validation responses also include `fields`. If present, honor `Retry-After` before retrying. A1 and A3 accept `Idempotency-Key` for safe retries of the same request.

## Source documents and courses

### A1 `POST /v1/documents`

**Scope** `documents:write`. Upload teaching material or submit an HTTPS Moodle PDF URL. The caller needs access to the given course.

| Input | Location | Required | Type and meaning |
| --- | --- | --- | --- |
| `course_id` | Body | Yes | String, 1–120 characters. Authorized course ID |
| `activity_id` | Body | Yes | String, 1–120 characters. Course activity ID |
| `language` | Body | Yes | Source language tag, 2–35 characters, such as `it` or `en-GB` |
| `source_ref` | Body | Yes | String, 1–200 characters. Upstream source or file ID |
| `content_hash` | Body | Yes | String, 1–200 characters. Declared upstream revision or hash |
| `version` | Body | No | String, 1–120 characters. Optional upstream version label |
| `file` | Multipart body | Upload only | One PDF, UTF-8 CSV, Word DOCX/DOC, or PowerPoint PPTX |
| `moodle_url` | JSON body | URL mode only | HTTPS Moodle PDF URL, up to 8,192 characters |
| `include_speaker_notes` | Multipart body | No | PPTX only. Form value `true` or `false`, default `false` |
| `include_hidden_slides` | Multipart body | No | PPTX only. Form value `true` or `false`, default `false` |
| `Idempotency-Key` | Header | No | Keep the same key for an identical submission retry |

**Output** `202 Accepted` with [AcceptedDocument](#accepteddocument), which contains `document_id` and `job_id`. `Location` points to `/v1/jobs/{job_id}`. Store the document ID for A2 and A3 and the job ID for A4. [Ingestion contract](a1.md)

### A2 `GET /v1/documents`

**Scope** `documents:read`. List the caller's material, including accepted jobs that are still processing.

**Input** Optional query `course_id` (string, 1–120 characters). Omitting it lists material across the caller's allowed courses. No body.

**Output** `200 OK` with an array of [DocumentSummary](#documentsummary) objects, newest submissions first. The array is empty when no visible document matches. [Document listing](a2.md#list)

### A2 `DELETE /v1/documents/{document_id}`

**Scope** `documents:write`. Remove one source and generated material that depends on it.

**Input** Required path `document_id` (UUID). No query or body.

**Output** `204 No Content` after cleanup. An authorized repeat can finish pending cleanup. [Document deletion](a2.md#delete)

### `GET /v1/documents/{document_id}/slides/{slide_number}`

**Scope** `documents:read`. Fetch the retained original rendering of a PPTX slide.

**Input** Required path `document_id` (UUID) and `slide_number` (one-based integer, 1–5,000). Required query `document_version_id` (UUID), which must match the retained source. No body.

**Output** `200 OK` with binary `image/png` and `Cache-Control: no-store`. An unavailable preview or mismatched version returns `404`. [PowerPoint ingestion](powerpoint-ingestion.md)

### A2 `DELETE /v1/courses/{course_id}`

**Scope** `documents:write`. Remove the caller's source material and generated lessons for a course.

**Input** Required path `course_id` (string, 1–120 characters). No query or body.

**Output** `204 No Content` after cleanup. Pending cleanup may return `503`. The same DELETE can resume it. [Course deletion](a2.md#delete-an-entire-course)

## Lesson generation, jobs, and teacher review

### A3 `POST /v1/lesson-jobs`

**Scope** `lessons:write`. Queue a draft at one difficulty level.

**Input** JSON [LessonInput](#lessoninput). Required fields are `lesson_id`, `course_id`, `activity_id`, `document_ids`, `language`, and `voice`. Use document IDs returned by A1 for the caller's sources in the requested course. Optional fields are `teaching_version`, `options`, `allow_incomplete_source`, and `callback_url`. Within [LessonOptions](#lessonoptions), `learner_level` defaults to `intermediate`. Optional `Idempotency-Key` header identifies an identical retry.

**Output** `202 Accepted` with [AcceptedLesson](#acceptedlesson), which contains `job_id`. `Location` points to `/v1/jobs/{job_id}`. [Single-level generation](a3.md)

### A3 v2 `POST /v2/lesson-jobs`

**Scope** `lessons:write`. Queue a draft at one or more difficulty levels using a shared plan.

**Input** JSON [AlignedLessonInput](#alignedlessoninput). The six required fields and the optional `Idempotency-Key` header are the same as A3 v1. Select levels with the top-level `levels` array of 1–8 unique configured names. Omit it or send `null` to generate all configured levels. [AlignedOptions](#alignedoptions) controls chunk sizes and source windows. `target_chunk_words` must be at most `max_chunk_words`.

**Output** `202 Accepted` with [AcceptedLesson](#acceptedlesson). `Location` points to `/v1/jobs/{job_id}`. [Aligned generation contract](a3-v2.md)

### A4 `GET /v1/jobs/{job_id}`

**Scope** `jobs:read`. Check an ingestion, generation, or revision job.

**Input** Required path `job_id` (UUID), returned by A1, A3, or A7. No query or body.

**Output** `200 OK` with [JobStatus](#jobstatus). Read `status`, `step`, `progress`, and `error` to monitor the job. `result` is a job-specific metadata object when available and `null` before a result exists. Use A5 for the generated lesson and WAVs.

### A5 `GET /v1/jobs/{job_id}/result`

**Scope** `jobs:read`. Fetch available draft output.

**Input** Required path `job_id` (UUID). Optional query `learner_level` (1–40 characters, a lowercase identifier that starts with a letter and may contain digits and underscores). No body.

**Output** `200 OK` with [LessonResult](#lessonresult) for a single-level job or selected level, or [AlignedLessonResult](#alignedlessonresult) with all available variants when requesting the full aligned result. Results contain ordered teaching chunks and audio URLs. `409` can mean output is still pending or its source is unavailable. `410` indicates expired or disposed output. [Results and expiry](a5.md)

### A5 `GET /v1/jobs/{job_id}/audio/{chunk_id}`

**Scope** `jobs:read`. Download one chunk's narration.

**Input** Required paths `job_id` (UUID) and `chunk_id` (UUID) from the A5 result. No query or body.

**Output** `200 OK` with binary `audio/wav`, `Content-Length`, an attachment filename, and an `ETag` containing the audio SHA-256. Availability follows the A5 result's lifecycle. [Audio download](a5.md)

### A6 `DELETE /v1/jobs/{job_id}`

**Scope** `jobs:write`. Cancel a job or acknowledge receipt and remove temporary output.

**Input** Required path `job_id` (UUID). Required query `reason` is `CANCEL` for a queued or running job, or `RECEIVED` for a done or failed job. No body.

**Output** `204 No Content`. Repeating the same authorized action can finish cleanup. An incompatible action or job state returns `409`. [Job control and retention](a6.md)

### A7 `POST /v1/audio:regenerate`

**Scope** `lessons:write`. Queue a revision of one teaching chunk and its narration.

**Input** JSON [ChunkRevisionInput](#chunkrevisioninput). Send `lesson_job_id` and `chunk_id` as UUIDs, and `teacher_review` as nonblank feedback of up to 8,000 characters. Optional `apply_to_all_levels` defaults to `false`. Set it to `true` for a corresponding aligned edit across available levels. Each submission starts a new revision.

**Output** `202 Accepted` with [AcceptedLesson](#acceptedlesson). `Location` points to the revision's A4 status URL. A successful revision updates the current teaching output. [Revision contract](a7.md)

### A8 `GET /v1/lesson-jobs`

**Scope** `jobs:read`. List available draft lessons and levels for the caller.

**Input** Optional query `course_id` (string, 1–120 characters), `limit` (integer 1–100, default 100), and `offset` (integer 0–1,000,000, default 0). No body.

**Output** `200 OK` with an array of [ReadyLesson](#readylesson) objects. Each entry identifies a job and learner level, with its chunk count, timestamps, and review metadata. [Catalog contract](a8.md)

### `GET /v1/lesson-reviews`

**Scope** `jobs:read`. List lessons with retained teaching text for teacher review.

**Input** Optional query `course_id` (string, 1–120 characters), `limit` (integer 1–100, default 100), and `offset` (integer 0–1,000,000, default 0). No body.

**Output** `200 OK` with an array of [ReviewLessonSummary](#reviewlessonsummary) objects. Each gives the lesson's levels, counts, and revision availability. [Teacher review contract](lesson-review.md)

### `GET /v1/lesson-reviews/{job_id}`

**Scope** `jobs:read`. Fetch retained teaching text, including text whose audio has expired.

**Input** Required path `job_id` (UUID). No query or body.

**Output** `200 OK` with [ReviewLessonText](#reviewlessontext). Its `variants` map contains the ordered text chunks, source references, and review findings for each level. [Teacher review contract](lesson-review.md)

### B7 `DELETE /v1/lesson-jobs/{lesson_job_id}`

**Scope** `lessons:write`. Remove generated teaching content while retaining the source material.

**Input** Required path `lesson_job_id` (UUID) from the generated lesson catalog. Optional query `learner_level` uses the same syntax as A5. Omit it to delete the whole generated lesson. No body.

**Output** `204 No Content` after cleanup. When the deleted level is the last one, the generated lesson is removed. A pending cleanup can return `503` for an authorized retry. [Generated content deletion](b7.md)

## Learning sessions

Each route below requires `sessions:write` and the [learner-session JWT](#learner-session-jwt) when using signed sessions. Start with B1 and reuse the returned `session_id` for B2, B4, and B5.

### B1 `POST /v1/sessions`

**Input** JSON [SessionInput](#sessioninput). Send `course_id`, `activity_id`, `lesson_id`, and `lesson_job_id` for the exact available lesson. Optional `learner_level` selects or confirms the level, `document_ids` narrows the bound source set, and `language` confirms the generated language. Supply `session_id` and `consent_scope` in the JSON only for explicit local draft preview without a signed assertion. Signed sessions take those claims from `X-UPRAISE-Session-JWT`.

**Output** `201 Created` for a new session, or `200 OK` for an unchanged authorized retry. Both return [StartedSession](#startedsession) with `session_id`, bound lesson and document IDs, `result_sha256`, `learner_level`, `preview`, `ai_notice`, and `expires_at`. [Session start contract](b1.md)

### B2 `POST /v1/sessions/{session_id}/chunk-end`

**Input** Required path `session_id` (UUID). JSON [ChunkEndInput](#chunkendinput) requires `lesson_id`, `chunk_id` (UUID), and `cognitive_load_score` (JSON number from 0 to 1). The IDs must match the open session and lesson.

**Output** `200 OK` with [ChunkEndResponse](#chunkendresponse), containing `decision`. With telemetry consent and adaptation enabled, a score above `0.5` returns `HIGH_COGNITIVE_LOAD`. Other accepted requests return `PROCEED`. [Chunk-end decision](b2.md)

### B4 `POST /v1/sessions/{session_id}/interrupts`

**Input** Required path `session_id` (UUID). The session needs `questions` consent. Use one of the two body formats below.

| Input | JSON text | Multipart WAV |
| --- | --- | --- |
| `request_id` | Required UUID. Reuse it for an identical retry | Same |
| `chunk_id` | Required UUID from the bound lesson | Same |
| `playback_offset_ms` | Optional integer, 0–86,400,000 | Optional form value containing the same integer |
| `text` | Required nonblank string of up to 6,000 characters | Omit |
| `audio` | Omit | Required file field. Mono 16-bit PCM WAV at 8–48 kHz, up to 60 seconds and 6,000,000 bytes by default |

**Output** `200 OK` with [InterruptResponse](#interruptresponse), including the request and chunk IDs and any teaching text, SSML, inline Base64 WAV, visemes, source references, playback action, or warnings. Many reply fields are nullable to support text-only or player-only answers. [Interruption contract](b4.md)

### B5 `POST /v1/sessions/{session_id}/end`

**Input** Required path `session_id` (UUID). JSON [EndSessionInput](#endsessioninput) with `reason` set to `COMPLETED`, `STOPPED_BY_LEARNER`, `TIMEOUT`, or `ERROR`.

**Output** `204 No Content`. Clears the session's temporary dialogue and reply data. An authorized repeat can return `204` while the end record is retained. [Session end contract](b5.md)

## Health, status, and administration

### `GET /health`

**Input** No authentication, path arguments, query, or body.

**Output** `200 OK` with `{"status":"alive"}`. This checks whether the API responds. [Health checks](c1-c2.md#c1)

### C1 `GET /ready`

**Input** No authentication, path arguments, query, or body.

**Output** [ReadinessStatus](#readinessstatus) with the most recent check time and per-dependency status. HTTP `200` means `READY` or `DEGRADED`. HTTP `503` means `NOT_READY`. [Readiness checks](c1-c2.md#c1)

### C2 `GET /v1/status`

**Scope** `jobs:read`. Read cached AI feature availability and effective controls.

**Input** Optional query `course_id` (authorized string, 1–120 characters, without leading or trailing spaces). Omitting it returns global status. No body.

**Output** `200 OK` with [ServiceStatus](#servicestatus), including per-feature details and availability. Availability can be `true`, `false`, or `null` for unknown. [Feature status](c1-c2.md#c2)

### C3 `PUT /v1/admin/kill-switch`

**Scope** `admin:operations`. Change a runtime disable switch. Course updates also require access to the target course.

**Input** JSON [KillSwitchInput](#killswitchinput). Set `scope` to `global` or `course_id`. Provide `course_id` for a course update. For a global update, omit `course_id` or set it to `null`. `feature` selects the control. Set `enabled: true` to activate the kill switch and disable that feature, or `enabled: false` to release it.

**Output** `200 OK` with [KillSwitchState](#killswitchstate), including the requested switch, effective feature flags, timestamp, and audit information. [Operator controls](c3.md)

### C5 `GET /v1/version`

**Scope** `jobs:read`. Read deployed version metadata.

**Input** No path arguments, query, or body.

**Output** `200 OK` with [VersionStatus](#versionstatus), including application, model, prompt, pipeline, cognitive-load policy, and configuration identifiers. [Version contract](c5.md)

## JSON schemas

These tables describe the API's named JSON bodies. For an input, **Required** means you must supply the field. For an output, it shows which fields the API declares as required in OpenAPI. A field with `null` in its type accepts a null value. Some responses omit null fields. Constraints in the endpoint entries apply in addition to the field types below.

### AcceptedDocument

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `document_id` | String | Yes | – |
| `job_id` | String | Yes | – |

### AcceptedLesson

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `job_id` | String | Yes | – |

### AlignedLessonInput

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `lesson_id` | String | Yes | 1–120 characters |
| `course_id` | String | Yes | 1–120 characters |
| `activity_id` | String | Yes | 1–120 characters |
| `document_ids` | Array of UUID string | Yes | 1–50 items |
| `language` | String | Yes | 2–35 characters. Pattern `^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$` |
| `voice` | String | Yes | 1–80 characters. Pattern `^[A-Za-z]+$` |
| `teaching_version` | `"v1"`, `"v2"`, `"v2.9"`, `"v2.9-light"`, `"v3"`, `"v3.1"` | No | Default `"v1"` |
| `allow_incomplete_source` | Boolean | No | Default `false` |
| `options` | [AlignedOptions](#alignedoptions) | No | – |
| `callback_url` | String or `null` | No | String constraints (At most 2048 characters) |
| `levels` | Array of String or `null` | No | Array of String constraints (1–8 items. Each item (Pattern `^[a-z][a-z0-9_]{0,39}$`)) |

### AlignedLessonResult

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `generation_version` | `"a3-v2"` | No | Default `"a3-v2"` |
| `job_id` | String | Yes | – |
| `lesson_id` | String | Yes | – |
| `course_id` | String | Yes | – |
| `activity_id` | String | Yes | – |
| `language` | String | Yes | – |
| `voice` | String | Yes | – |
| `status` | `"DRAFT"` | No | Default `"DRAFT"` |
| `label` | `"AI-generated"` | No | Default `"AI-generated"` |
| `chunk_plan_id` | UUID string | Yes | – |
| `content_unit_ids` | Array of UUID string | Yes | – |
| `chunk_count` | Integer | Yes | ≥ 1.0 |
| `variants` | Object with string keys and [LessonResult](#lessonresult) values | Yes | – |
| `reviewer_report` | [ReviewReport](#reviewreport) | Yes | – |
| `versions` | Object with string keys and String values | Yes | – |
| `source_attempts` | Object with string keys and String values | Yes | – |
| `expires_at` | String or `null` | No | – |

### AlignedOptions

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `source_window_pages` | Integer | No | ≥ 1.0. ≤ 50.0. Default `5` |
| `target_chunk_words` | Integer | No | ≥ 30.0. ≤ 1000.0. Default `200` |
| `max_chunk_words` | Integer | No | ≥ 50.0. ≤ 2000.0. Default `500` |

### ApplicationVersion

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `version` | String | Yes | – |
| `build_id` | String or `null` | Yes | – |
| `build_id_kind` | `"source_and_locks_sha256"`, `"source_sha256"`, `"UNAVAILABLE"` | Yes | – |
| `dependencies` | Object with string keys and String or `null` values | Yes | – |

### ChunkEndInput

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `lesson_id` | String | Yes | 1–120 characters |
| `chunk_id` | UUID string | Yes | – |
| `cognitive_load_score` | Number | Yes | ≥ 0.0. ≤ 1.0 |

### ChunkEndResponse

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `decision` | `"PROCEED"`, `"HIGH_COGNITIVE_LOAD"` | Yes | – |

### ChunkRevisionInput

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `lesson_job_id` | UUID string | Yes | – |
| `chunk_id` | UUID string | Yes | – |
| `teacher_review` | String | Yes | 1–8000 characters |
| `apply_to_all_levels` | Boolean | No | Default `false` |

### CognitiveLoadVersion

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `fusion_rule` | [FusionRule](#fusionrule) | Yes | – |
| `threshold_method` | [ThresholdMethod](#thresholdmethod) | Yes | – |

### DependencyStatus

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `state` | `"AVAILABLE"`, `"UNAVAILABLE"`, `"UNKNOWN"` | Yes | – |
| `reason_code` | String | Yes | – |
| `observed_at` | ISO 8601 date-time string or `null` | No | – |

### DocumentSummary

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `document_id` | String | Yes | – |
| `course_id` | String | Yes | – |
| `activity_id` | String | Yes | – |
| `language` | String | Yes | – |
| `source_ref` | String | Yes | – |
| `content_hash` | String | Yes | – |
| `version` | String or `null` | Yes | – |
| `filename` | String | Yes | – |
| `status` | String | Yes | – |
| `progress` | Number | Yes | ≥ 0.0. ≤ 1.0 |
| `chunk_count` | Integer | Yes | – |
| `ingestion_readiness` | [SourceReadiness](#sourcereadiness) or `null` | No | – |
| `created_at` | ISO 8601 date-time string | Yes | – |
| `updated_at` | ISO 8601 date-time string | Yes | – |

### EndSessionInput

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `reason` | `"COMPLETED"`, `"STOPPED_BY_LEARNER"`, `"TIMEOUT"`, `"ERROR"` | Yes | – |

### FeatureStatus

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `enabled` | Boolean | Yes | – |
| `available` | Boolean or `null` | Yes | – |
| `state` | `"AVAILABLE"`, `"UNAVAILABLE"`, `"UNKNOWN"` | Yes | – |
| `reason_codes` | Array of String | No | – |

### FusionRule

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `status` | `"NOT_IMPLEMENTED"` | No | Default `"NOT_IMPLEMENTED"` |
| `version` | `null` | No | – |

### HTTPValidationError

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `detail` | Array of [ValidationError](#validationerror) | No | – |

### InlineAudio

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `media_type` | `"audio/wav"` | No | Default `"audio/wav"` |
| `encoding` | `"base64"` | No | Default `"base64"` |
| `data` | String | Yes | – |

### InterruptResponse

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `request_id` | UUID string | Yes | – |
| `chunk_id` | UUID string | Yes | – |
| `answer_id` | UUID string or `null` | No | – |
| `text` | String or `null` | No | – |
| `ssml` | String or `null` | No | – |
| `audio` | [InlineAudio](#inlineaudio) or `null` | No | – |
| `visemes` | Array of [Viseme](#viseme) | No | – |
| `sources` | Array of [LessonSource](#lessonsource) | No | – |
| `grounded` | Boolean or `null` | No | – |
| `playback_action` | [PlaybackAction](#playbackaction) or `null` | No | – |
| `warnings` | Array of String | No | – |

### JobStatus

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `job_id` | String | Yes | – |
| `document_id` | String or `null` | No | – |
| `lesson_id` | String or `null` | No | – |
| `status` | String | Yes | – |
| `step` | String | Yes | – |
| `progress` | Number | Yes | – |
| `attempts` | Integer | Yes | – |
| `error` | Object or `null` | Yes | – |
| `result` | Object or `null` | Yes | – |
| `created_at` | ISO 8601 date-time string | Yes | – |
| `updated_at` | ISO 8601 date-time string | Yes | – |
| `cleanup_pending` | Boolean | No | Default `false` |
| `acknowledged` | Boolean | No | Default `false` |
| `expires_at` | ISO 8601 date-time string or `null` | No | – |
| `callback_status` | String or `null` | No | – |
| `callback_attempts` | Integer or `null` | No | – |
| `callback_error` | String or `null` | No | – |

### KillSwitchInput

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `scope` | `"global"`, `"course_id"` | Yes | – |
| `course_id` | String or `null` | No | String constraints (1–120 characters) |
| `feature` | `"answers"`, `"stt"`, `"tts"`, `"generation"`, `"adaptive_levels"`, `"all"` | Yes | – |
| `enabled` | Boolean | Yes | True activates this kill switch; false releases it |

### KillSwitchState

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `scope` | `"global"`, `"course_id"` | Yes | – |
| `course_id` | String or `null` | Yes | – |
| `feature` | `"answers"`, `"stt"`, `"tts"`, `"generation"`, `"adaptive_levels"`, `"all"` | Yes | – |
| `enabled` | Boolean | Yes | – |
| `kill_switch_active` | Boolean | Yes | – |
| `features_enabled` | Object with string keys and Boolean values | Yes | – |
| `updated_at` | ISO 8601 date-time string | Yes | – |
| `audit_event_id` | UUID string | Yes | – |
| `audit_pending` | Boolean | Yes | – |

### LessonChunk

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `chunk_id` | String | Yes | – |
| `content_unit_id` | UUID string or `null` | No | – |
| `index` | Integer | Yes | ≥ 0.0 |
| `text` | String | Yes | – |
| `ssml` | String | Yes | – |
| `ssml_parts` | Array of String | Yes | – |
| `audio_url` | String | Yes | – |
| `audio_media_type` | `"audio/wav"` | No | Default `"audio/wav"` |
| `audio_sha256` | String | Yes | – |
| `visemes` | Array of [Viseme](#viseme) | Yes | – |
| `duration_seconds` | Number | Yes | > 0.0 |
| `sources` | Array of [LessonSource](#lessonsource) | Yes | – |
| `reviewer_report` | [ReviewReport](#reviewreport) | Yes | – |
| `section_index` | Integer | Yes | – |
| `revisions` | Integer | Yes | ≥ 0.0. ≤ 1.0 |

### LessonInput

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `lesson_id` | String | Yes | 1–120 characters |
| `course_id` | String | Yes | 1–120 characters |
| `activity_id` | String | Yes | 1–120 characters |
| `document_ids` | Array of UUID string | Yes | 1–50 items |
| `language` | String | Yes | 2–35 characters. Pattern `^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$` |
| `voice` | String | Yes | 1–80 characters. Pattern `^[A-Za-z]+$` |
| `teaching_version` | `"v1"`, `"v2"`, `"v2.9"`, `"v2.9-light"`, `"v3"`, `"v3.1"` | No | Default `"v1"` |
| `allow_incomplete_source` | Boolean | No | Default `false` |
| `options` | [LessonOptions](#lessonoptions) | No | – |
| `callback_url` | String or `null` | No | String constraints (At most 2048 characters) |

### LessonOptions

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `source_window_pages` | Integer | No | ≥ 1.0. ≤ 50.0. Default `5` |
| `target_chunk_words` | Integer | No | ≥ 30.0. ≤ 1000.0. Default `200` |
| `learner_level` | `"beginner"`, `"intermediate"`, `"advanced"` | No | Default `"intermediate"` |

### LessonResult

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `job_id` | String | Yes | – |
| `lesson_id` | String | Yes | – |
| `course_id` | String | Yes | – |
| `activity_id` | String | Yes | – |
| `status` | `"DRAFT"` | No | Default `"DRAFT"` |
| `label` | `"AI-generated"` | No | Default `"AI-generated"` |
| `language` | String | Yes | – |
| `voice` | String | Yes | – |
| `learner_level` | String | Yes | – |
| `learner_level_applied` | Boolean | No | Default `false` |
| `chunks` | Array of [LessonChunk](#lessonchunk) | Yes | – |
| `reviewer_report` | [ReviewReport](#reviewreport) | Yes | – |
| `versions` | Object with string keys and String values | Yes | – |
| `expires_at` | String or `null` | No | – |
| `revision` | [LessonRevision](#lessonrevision) or `null` | No | – |
| `generation_version` | `"a3-v2"` or `null` | No | – |
| `chunk_plan_id` | UUID string or `null` | No | – |
| `source_attempts` | Object with string keys and String values | No | – |

### LessonRevision

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `parent_job_id` | UUID string | Yes | – |
| `chunk_id` | UUID string | Yes | – |
| `number` | Integer | Yes | ≥ 1.0 |

### LessonSource

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `document_id` | String | Yes | – |
| `document_version_id` | String | Yes | – |
| `pages` | Array of Integer | Yes | – |
| `source_chunk_ids` | Array of String | Yes | – |
| `quality_status` | String | Yes | – |
| `source_format` | String | No | Default `"pdf"` |
| `source_blocks` | Array of Integer | No | – |
| `row_ranges` | Array of Object with string keys and Integer values | No | – |
| `word_references` | Array of Object | No | – |
| `slide_numbers` | Array of Integer | No | – |
| `slide_references` | Array of Object | No | – |

### ModelVersion

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `provider` | String | Yes | – |
| `model_id` | String or `null` | No | – |
| `engine` | String or `null` | No | – |
| `version_status` | `"CONFIGURED"`, `"PROVIDER_MANAGED"` | No | Default `"CONFIGURED"` |

### PipelineVersion

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `version` | String | Yes | – |
| `parameters` | Object with string keys and String or Integer or Boolean values | Yes | – |

### PlaybackAction

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `action` | `"REPLAY"`, `"SET_SPEED"`, `"PAUSE"`, `"RESUME"` | Yes | – |
| `rate` | Number or `null` | No | Number constraints (≥ 0.5. ≤ 2.0) |

### ReadinessStatus

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `status` | `"READY"`, `"DEGRADED"`, `"NOT_READY"` | Yes | – |
| `checked_at` | ISO 8601 date-time string or `null` | Yes | – |
| `dependencies` | Object with string keys and [DependencyStatus](#dependencystatus) values | Yes | – |

### ReadyLesson

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `job_id` | String | Yes | – |
| `lesson_id` | String | Yes | – |
| `course_id` | String | Yes | – |
| `activity_id` | String | Yes | – |
| `language` | String | Yes | – |
| `voice` | String | Yes | – |
| `learner_level` | String | Yes | – |
| `chunk_count` | Integer | Yes | – |
| `status` | `"DRAFT"` | No | Default `"DRAFT"` |
| `label` | `"AI-generated"` | No | Default `"AI-generated"` |
| `requires_review` | Boolean | Yes | – |
| `review_issue_count` | Integer | Yes | – |
| `created_at` | ISO 8601 date-time string | Yes | – |
| `expires_at` | ISO 8601 date-time string or `null` | Yes | – |
| `revision` | [LessonRevision](#lessonrevision) or `null` | No | – |
| `generation_version` | `"a3-v2"` or `null` | No | – |
| `chunk_plan_id` | String or `null` | No | – |
| `teaching_version` | `"v1"`, `"v2"`, `"v2.9"`, `"v2.9-light"`, `"v3"`, `"v3.1"` or `null` | No | – |
| `teaching_policy` | String or `null` | No | – |

### ReviewIssue

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `category` | `"accuracy"`, `"citations"`, `"clarity"`, `"bias"`, `"coverage"`, `"source"`, `"speech"` | Yes | – |
| `message` | String | Yes | 1–4000 characters |
| `pages` | Array of Integer | No | – |
| `row_ranges` | Array of Object with string keys and Integer values | No | – |
| `source_blocks` | Array of Integer | No | – |

### ReviewLessonChunk

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `chunk_id` | String | Yes | – |
| `index` | Integer | Yes | ≥ 0.0 |
| `text` | String | Yes | – |
| `narration_text` | String or `null` | No | – |
| `sources` | Array of [LessonSource](#lessonsource) | Yes | – |
| `reviewer_report` | [ReviewReport](#reviewreport) | Yes | – |

### ReviewLessonLevel

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `learner_level` | String | Yes | – |
| `chunks` | Array of [ReviewLessonChunk](#reviewlessonchunk) | Yes | – |

### ReviewLessonSummary

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `job_id` | String | Yes | – |
| `lesson_id` | String | Yes | – |
| `course_id` | String | Yes | – |
| `activity_id` | String | Yes | – |
| `language` | String | Yes | – |
| `voice` | String | Yes | – |
| `teaching_version` | `"v1"`, `"v2"`, `"v2.9"`, `"v2.9-light"`, `"v3"`, `"v3.1"` | No | Default `"v1"` |
| `status` | `"DRAFT"` | No | Default `"DRAFT"` |
| `job_status` | String | Yes | – |
| `levels` | Array of String | Yes | – |
| `chunk_counts` | Object with string keys and Integer values | Yes | – |
| `created_at` | ISO 8601 date-time string | Yes | – |
| `audio_expires_at` | ISO 8601 date-time string or `null` | Yes | – |
| `revision_available` | Boolean | Yes | – |
| `revision_unavailable_reason` | String or `null` | No | – |

### ReviewLessonText

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `job_id` | String | Yes | – |
| `lesson_id` | String | Yes | – |
| `course_id` | String | Yes | – |
| `activity_id` | String | Yes | – |
| `language` | String | Yes | – |
| `voice` | String | Yes | – |
| `teaching_version` | `"v1"`, `"v2"`, `"v2.9"`, `"v2.9-light"`, `"v3"`, `"v3.1"` | No | Default `"v1"` |
| `status` | `"DRAFT"` | No | Default `"DRAFT"` |
| `job_status` | String | Yes | – |
| `levels` | Array of String | Yes | – |
| `chunk_counts` | Object with string keys and Integer values | Yes | – |
| `created_at` | ISO 8601 date-time string | Yes | – |
| `audio_expires_at` | ISO 8601 date-time string or `null` | Yes | – |
| `revision_available` | Boolean | Yes | – |
| `revision_unavailable_reason` | String or `null` | No | – |
| `variants` | Object with string keys and [ReviewLessonLevel](#reviewlessonlevel) values | Yes | – |

### ReviewReport

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `approved` | Boolean | Yes | – |
| `summary` | String | Yes | 1–4000 characters |
| `issues` | Array of [ReviewIssue](#reviewissue) | No | – |
| `revision_instructions` | Array of String | No | – |

### ServiceStatus

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `course_id` | String or `null` | Yes | – |
| `checked_at` | ISO 8601 date-time string or `null` | Yes | – |
| `answers_available` | Boolean or `null` | Yes | – |
| `stt_available` | Boolean or `null` | Yes | – |
| `tts_available` | Boolean or `null` | Yes | – |
| `generation_available` | Boolean or `null` | Yes | – |
| `kill_switch_active` | Boolean | Yes | – |
| `features` | Object with string keys and [FeatureStatus](#featurestatus) values | Yes | – |

### SessionInput

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `course_id` | String | Yes | 1–120 characters |
| `activity_id` | String | Yes | 1–120 characters |
| `lesson_id` | String | Yes | 1–120 characters |
| `lesson_job_id` | UUID string | Yes | – |
| `learner_level` | String or `null` | No | String constraints (1–40 characters. Pattern `^[a-z][a-z0-9_]*$`) |
| `document_ids` | Array of UUID string or `null` | No | Array of UUID string constraints (1–50 items) |
| `language` | String or `null` | No | String constraints (2–35 characters. Pattern `^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$`) |
| `session_id` | UUID string or `null` | No | – |
| `consent_scope` | Array of `"questions"`, `"telemetry"` or `null` | No | Array of `"questions"`, `"telemetry"` constraints (At most 2 items) |

### SourceReadiness

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `status` | `"COMPLETE"`, `"INCOMPLETE"` | Yes | – |
| `blocking_slides` | Array of Integer | No | – |
| `issues` | Array of String | No | – |

### StartedSession

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `session_id` | UUID string | Yes | – |
| `lesson_job_id` | UUID string | Yes | – |
| `lesson_id` | String | Yes | – |
| `course_id` | String | Yes | – |
| `activity_id` | String | Yes | – |
| `document_ids` | Array of UUID string | Yes | – |
| `language` | String | Yes | – |
| `learner_level` | String | Yes | – |
| `voice` | String | Yes | – |
| `result_sha256` | String | Yes | – |
| `preview` | Boolean | Yes | – |
| `ai_notice` | String | Yes | – |
| `expires_at` | ISO 8601 date-time string | Yes | – |

### ThresholdMethod

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `name` | `"fixed_threshold"` | No | Default `"fixed_threshold"` |
| `version` | String | Yes | – |
| `value` | Number | Yes | – |
| `comparison` | `">"` | No | Default `">"` |
| `telemetry_consent_required` | Boolean | No | Default `true` |

### ValidationError

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `loc` | Array of (String or Integer) | Yes | – |
| `msg` | String | Yes | – |
| `type` | String | Yes | – |
| `input` | Any JSON value | No | – |
| `ctx` | Object | No | – |

### VersionItem

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `version` | String or `null` | Yes | – |
| `sha256` | String or `null` | Yes | – |
| `hash_kind` | `"source_bundle_sha256"` | No | Default `"source_bundle_sha256"` |
| `status` | `"AVAILABLE"`, `"UNAVAILABLE"` | Yes | – |

### VersionStatus

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `schema_version` | `"c5-v1"` | No | Default `"c5-v1"` |
| `application` | [ApplicationVersion](#applicationversion) | Yes | – |
| `models` | Object with string keys and [ModelVersion](#modelversion) values | Yes | – |
| `prompts` | Object with string keys and [VersionItem](#versionitem) values | Yes | – |
| `pipelines` | Object with string keys and [PipelineVersion](#pipelineversion) values | Yes | – |
| `cognitive_load` | [CognitiveLoadVersion](#cognitiveloadversion) | Yes | – |
| `configuration_id` | String | Yes | – |

### Viseme

| Field | JSON type or allowed values | Required | Constraints and defaults |
| --- | --- | --- | --- |
| `time_ms` | Number | Yes | ≥ 0.0 |
| `value` | String | Yes | – |
