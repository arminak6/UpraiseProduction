# Backend API reference

PowerShell examples for the APIs shown at http://127.0.0.1:8000/docs.

- `$jwt` is the service token. Get it using [README-JWT.md](README-JWT.md).
- Replace example IDs and file paths with your values.
- Required means the field must be sent. Conditional means it depends on the request.
- Requests that create, change or delete data perform those actions.
- Learning APIs also need `$sessionJwt`, a signed learner-session token.
- The local service token is limited to `demo-course`. The admin API needs separate admin authorization.
- Lists and objects can contain nested fields. See [full JSON schemas](backend/docs/api-reference.md#json-schemas).

## Documents

### 1. Upload a document

**API** `POST /v1/documents`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `course_id` | Yes | Authorized course ID; 1?120 characters |
| `activity_id` | Yes | Activity ID; 1?120 characters |
| `language` | Yes | Language tag, e.g. en, it, en-GB; 2?35 characters |
| `source_ref` | Yes | Source reference; 1?200 characters |
| `content_hash` | Yes | Source revision or hash; 1?200 characters |
| `file` | Conditional | Required for multipart upload: PDF, DOCX, DOC, PPTX or CSV |
| `moodle_url` | Conditional | Required instead of file for JSON submission: HTTPS PDF URL |
| `version` | No | Source version; 1?120 characters |
| `include_speaker_notes` | No | true or false; PowerPoint upload only; default false |
| `include_hidden_slides` | No | true or false; PowerPoint upload only; default false |
| `Idempotency-Key (header)` | No | Key for retrying the same request |

**Output** `202`, for example `{"document_id":"...","job_id":"..."}`.

```powershell
curl.exe -i -H "Authorization: Bearer $jwt" -F "course_id=demo-course" -F "activity_id=activity-1" -F "language=en" -F "source_ref=lesson.pdf" -F "content_hash=revision-1" -F "file=@C:\path\lesson.pdf" http://127.0.0.1:8000/v1/documents
```

### 2. List documents

**API** `GET /v1/documents`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `course_id (query)` | No | Authorized course ID; 1?120 characters |

**Output** `200`, a list of documents. Each includes `document_id`, `course_id`, `activity_id`, `language`, `source_ref`, `content_hash`, `version`, `filename`, `status`, `progress`, `chunk_count`, `ingestion_readiness`, `created_at` and `updated_at`.

```powershell
curl.exe -i -H "Authorization: Bearer $jwt" "http://127.0.0.1:8000/v1/documents"
```

### 3. Delete a document

**API** `DELETE /v1/documents/{document_id}`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `document_id (path)` | Yes | Document UUID |

**Output** `204`, empty response. Dependent lessons are also removed.

```powershell
curl.exe -i -X DELETE -H "Authorization: Bearer $jwt" "http://127.0.0.1:8000/v1/documents/DOCUMENT_ID"
```

### 4. Get a PowerPoint slide image

**API** `GET /v1/documents/{document_id}/slides/{slide_number}`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `document_id (path)` | Yes | Document UUID |
| `slide_number (path)` | Yes | Integer 1?5000 |
| `document_version_id (query)` | Yes | Document version UUID |

**Output** `200`, PNG image.

```powershell
curl.exe -H "Authorization: Bearer $jwt" -o slide.png "http://127.0.0.1:8000/v1/documents/DOCUMENT_ID/slides/1?document_version_id=DOCUMENT_VERSION_ID"
```

### 5. Delete a course

**API** `DELETE /v1/courses/{course_id}`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `course_id (path)` | Yes | Authorized course ID |

**Output** `204`, empty response. The course's documents and lessons are removed.

```powershell
curl.exe -i -X DELETE -H "Authorization: Bearer $jwt" "http://127.0.0.1:8000/v1/courses/demo-course"
```

## Lessons and jobs

### 6. Generate a lesson at one level

**API** `POST /v1/lesson-jobs`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `course_id` | Yes | Authorized course ID; 1?120 characters |
| `activity_id` | Yes | Activity ID; 1?120 characters |
| `lesson_id` | Yes | Lesson ID; 1?120 characters |
| `document_ids` | Yes | Array of 1?50 distinct document UUIDs |
| `language` | Yes | Language tag, e.g. en, it, en-GB; 2?35 characters |
| `voice` | Yes | Letters only, e.g. Joanna; voice must support the selected language and speech engine |
| `teaching_version` | No | v1, v2, v2.9, v2.9-light, v3, v3.1; default v1 |
| `allow_incomplete_source` | No | true or false; default false |
| `callback_url` | No | Configured callback URL |
| `Idempotency-Key (header)` | No | Key for retrying the same request |
| `options` | No | source_window_pages: 1?50; target_chunk_words: 30?1000; learner_level: beginner, intermediate, advanced |

**Output** `202`, for example `{"job_id":"..."}`.

```powershell
$body = @{course_id='demo-course'; activity_id='activity-1'; lesson_id='lesson-1'; document_ids=@('DOCUMENT_ID'); language='en'; voice='Joanna'; teaching_version='v2.9-light'; options=@{learner_level='beginner'}} | ConvertTo-Json -Depth 5 -Compress; $body | curl.exe -i -H "Authorization: Bearer $jwt" --json '@-' http://127.0.0.1:8000/v1/lesson-jobs
```

### 7. Generate a lesson at one or more levels

**API** `POST /v2/lesson-jobs`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `course_id` | Yes | Authorized course ID; 1?120 characters |
| `activity_id` | Yes | Activity ID; 1?120 characters |
| `lesson_id` | Yes | Lesson ID; 1?120 characters |
| `document_ids` | Yes | Array of 1?50 distinct document UUIDs |
| `language` | Yes | Language tag, e.g. en, it, en-GB; 2?35 characters |
| `voice` | Yes | Letters only, e.g. Joanna; voice must support the selected language and speech engine |
| `teaching_version` | No | v1, v2, v2.9, v2.9-light, v3, v3.1; default v1 |
| `allow_incomplete_source` | No | true or false; default false |
| `callback_url` | No | Configured callback URL |
| `Idempotency-Key (header)` | No | Key for retrying the same request |
| `levels` | No | Array of configured level names; defaults: beginner, intermediate, advanced; omitted means all configured levels |
| `options` | No | source_window_pages: 1?50; target_chunk_words: 30?1000; max_chunk_words: 50?2000; target must not exceed max |

**Output** `202`, for example `{"job_id":"..."}`.

```powershell
$body = @{course_id='demo-course'; activity_id='activity-1'; lesson_id='lesson-1'; document_ids=@('DOCUMENT_ID'); language='en'; voice='Joanna'; teaching_version='v2.9-light'; levels=@('beginner')} | ConvertTo-Json -Depth 5 -Compress; $body | curl.exe -i -H "Authorization: Bearer $jwt" --json '@-' http://127.0.0.1:8000/v2/lesson-jobs
```

### 8. Check job progress

**API** `GET /v1/jobs/{job_id}`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `job_id (path)` | Yes | Job UUID |

**Output** `200` with `job_id`, `document_id`, `lesson_id`, `status`, `step`, `progress`, `attempts`, `error`, `result`, `created_at`, `updated_at`, `cleanup_pending`, `acknowledged`, `expires_at`, `callback_status`, `callback_attempts` and `callback_error`.

```powershell
curl.exe -i -H "Authorization: Bearer $jwt" "http://127.0.0.1:8000/v1/jobs/JOB_ID"
```

### 9. Get a lesson result

**API** `GET /v1/jobs/{job_id}/result`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `job_id (path)` | Yes | Job UUID |
| `learner_level (query)` | No | Level in the result, e.g. beginner |

**Output** `200` with lesson details and chunks. A multi-level result contains `variants`. Fields include `job_id`, `lesson_id`, `course_id`, `activity_id`, `language`, `voice`, `status`, `label`, `versions`, `reviewer_report`, `expires_at` and `source_attempts`.

```powershell
curl.exe -i -H "Authorization: Bearer $jwt" "http://127.0.0.1:8000/v1/jobs/JOB_ID/result?learner_level=beginner"
```

### 10. Download lesson audio

**API** `GET /v1/jobs/{job_id}/audio/{chunk_id}`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `job_id (path)` | Yes | Job UUID |
| `chunk_id (path)` | Yes | Chunk UUID |

**Output** `200`, WAV audio file.

```powershell
curl.exe -H "Authorization: Bearer $jwt" -o lesson.wav "http://127.0.0.1:8000/v1/jobs/JOB_ID/audio/CHUNK_ID"
```

### 11. Cancel or acknowledge a job

**API** `DELETE /v1/jobs/{job_id}`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `job_id (path)` | Yes | Job UUID |
| `reason (query)` | Yes | CANCEL or RECEIVED |

**Output** `204`, empty response.

```powershell
curl.exe -i -X DELETE -H "Authorization: Bearer $jwt" "http://127.0.0.1:8000/v1/jobs/JOB_ID?reason=CANCEL"
```

### 12. Revise lesson text and audio

**API** `POST /v1/audio:regenerate`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `lesson_job_id` | Yes | Lesson job UUID |
| `chunk_id` | Yes | Chunk UUID |
| `teacher_review` | Yes | Feedback; 1?8,000 characters |
| `apply_to_all_levels` | No | true or false; default false |

**Output** `202` with a revision `job_id`.

```powershell
$body = @{lesson_job_id='LESSON_JOB_ID'; chunk_id='CHUNK_ID'; teacher_review='Explain the example more clearly.'; apply_to_all_levels=$false} | ConvertTo-Json -Compress; $body | curl.exe -i -H "Authorization: Bearer $jwt" --json '@-' http://127.0.0.1:8000/v1/audio:regenerate
```

### 13. List generated lessons

**API** `GET /v1/lesson-jobs`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `course_id (query)` | No | Authorized course ID; 1?120 characters |
| `limit (query)` | No | Integer 1?100; default 100 |
| `offset (query)` | No | Integer 0?1,000,000; default 0 |

**Output** `200`, a list. Each entry includes `job_id`, `lesson_id`, `course_id`, `activity_id`, `language`, `voice`, `learner_level`, `chunk_count`, `status`, `label`, `requires_review`, `review_issue_count`, `created_at`, `expires_at`, `revision`, `generation_version`, `chunk_plan_id`, `teaching_version` and `teaching_policy`.

```powershell
curl.exe -i -H "Authorization: Bearer $jwt" "http://127.0.0.1:8000/v1/lesson-jobs"
```

### 14. List lessons for review

**API** `GET /v1/lesson-reviews`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `course_id (query)` | No | Authorized course ID; 1?120 characters |
| `limit (query)` | No | Integer 1?100; default 100 |
| `offset (query)` | No | Integer 0?1,000,000; default 0 |

**Output** `200`, a list. Each entry includes `job_id`, `lesson_id`, `course_id`, `activity_id`, `language`, `voice`, `teaching_version`, `status`, `job_status`, `levels`, `chunk_counts`, `created_at`, `audio_expires_at`, `revision_available` and `revision_unavailable_reason`.

```powershell
curl.exe -i -H "Authorization: Bearer $jwt" "http://127.0.0.1:8000/v1/lesson-reviews"
```

### 15. Get lesson review

**API** `GET /v1/lesson-reviews/{job_id}`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `job_id (path)` | Yes | Job UUID |

**Output** `200` with the review summary fields from API 14 and `variants` containing lesson text, sources and review findings.

```powershell
curl.exe -i -H "Authorization: Bearer $jwt" "http://127.0.0.1:8000/v1/lesson-reviews/JOB_ID"
```

### 16. Delete a generated lesson

**API** `DELETE /v1/lesson-jobs/{lesson_job_id}`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `lesson_job_id (path)` | Yes | Lesson job UUID |
| `learner_level (query)` | No | Level to delete; omit to delete all levels |

**Output** `204`, empty response. Source documents are retained.

```powershell
curl.exe -i -X DELETE -H "Authorization: Bearer $jwt" "http://127.0.0.1:8000/v1/lesson-jobs/LESSON_JOB_ID"
```

## Learning sessions

### 17. Start a session

**API** `POST /v1/sessions`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `X-UPRAISE-Session-JWT (header)` | Conditional | Learner-session JWT for signed sessions |
| `course_id` | Yes | Authorized course ID |
| `activity_id` | Yes | Activity ID |
| `lesson_id` | Yes | Lesson ID |
| `lesson_job_id` | Yes | Lesson job UUID |
| `learner_level` | No | Level in the selected lesson |
| `document_ids` | No | Array of 1?50 source document UUIDs |
| `language` | No | Language tag, e.g. en, it, en-GB; 2?35 characters |
| `session_id` | Conditional | Optional UUID for local draft preview only |
| `consent_scope` | Conditional | Local draft preview only; array may contain questions and telemetry |

**Output** `201` for a new session or `200` for a retry. Fields include `session_id`, `lesson_job_id`, `lesson_id`, `course_id`, `activity_id`, `document_ids`, `language`, `learner_level`, `voice`, `result_sha256`, `preview`, `ai_notice` and `expires_at`.

```powershell
$body = @{course_id='demo-course'; activity_id='activity-1'; lesson_id='lesson-1'; lesson_job_id='LESSON_JOB_ID'; learner_level='beginner'} | ConvertTo-Json -Compress; $body | curl.exe -i -H "Authorization: Bearer $jwt" -H "X-UPRAISE-Session-JWT: $sessionJwt" --json '@-' http://127.0.0.1:8000/v1/sessions
```

### 18. Report the end of a chunk

**API** `POST /v1/sessions/{session_id}/chunk-end`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `X-UPRAISE-Session-JWT (header)` | Conditional | Learner-session JWT for signed sessions |
| `session_id (path)` | Yes | Session UUID |
| `lesson_id` | Yes | Lesson ID |
| `chunk_id` | Yes | Chunk UUID |
| `cognitive_load_score` | Yes | Number from 0 to 1 |

**Output** `200` with `decision`, either `PROCEED` or `HIGH_COGNITIVE_LOAD`.

```powershell
$body = @{lesson_id='lesson-1'; chunk_id='CHUNK_ID'; cognitive_load_score=0.4} | ConvertTo-Json -Compress; $body | curl.exe -i -H "Authorization: Bearer $jwt" -H "X-UPRAISE-Session-JWT: $sessionJwt" --json '@-' "http://127.0.0.1:8000/v1/sessions/SESSION_ID/chunk-end"
```

### 19. Ask a question

**API** `POST /v1/sessions/{session_id}/interrupts`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `X-UPRAISE-Session-JWT (header)` | Conditional | Learner-session JWT for signed sessions |
| `session_id (path)` | Yes | Session UUID |
| `request_id` | Yes | UUID; reuse for an identical retry |
| `chunk_id` | Yes | Chunk UUID |
| `text` | Conditional | Required for JSON text input; 1?6,000 characters |
| `audio` | Conditional | Required for multipart audio input; mono 16-bit PCM WAV, 8?48 kHz; at most 60 seconds |
| `playback_offset_ms` | No | Integer 0?86,400,000 |

**Output** `200` with `request_id`, `chunk_id`, `answer_id`, `text`, `ssml`, `audio`, `visemes`, `sources`, `grounded`, `playback_action` and `warnings`.

```powershell
$body = @{request_id=[guid]::NewGuid().ToString(); chunk_id='CHUNK_ID'; text='Can you explain this?'; playback_offset_ms=0} | ConvertTo-Json -Compress; $body | curl.exe -i -H "Authorization: Bearer $jwt" -H "X-UPRAISE-Session-JWT: $sessionJwt" --json '@-' "http://127.0.0.1:8000/v1/sessions/SESSION_ID/interrupts"
```

### 20. End a session

**API** `POST /v1/sessions/{session_id}/end`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `X-UPRAISE-Session-JWT (header)` | Conditional | Learner-session JWT for signed sessions |
| `session_id (path)` | Yes | Session UUID |
| `reason` | Yes | COMPLETED, STOPPED_BY_LEARNER, TIMEOUT or ERROR |

**Output** `204`, empty response.

```powershell
$body = @{reason='COMPLETED'} | ConvertTo-Json -Compress; $body | curl.exe -i -H "Authorization: Bearer $jwt" -H "X-UPRAISE-Session-JWT: $sessionJwt" --json '@-' "http://127.0.0.1:8000/v1/sessions/SESSION_ID/end"
```

## Status and administration

### 21. Check health

**API** `GET /health`

**Input** None.

**Output** `200` with `status`.

```powershell
curl.exe -i http://127.0.0.1:8000/health
```

### 22. Check readiness

**API** `GET /ready`

**Input** None.

**Output** `200` or `503` with `status`, `checked_at` and `dependencies`.

```powershell
curl.exe -i http://127.0.0.1:8000/ready
```

### 23. Check feature status

**API** `GET /v1/status`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |
| `course_id (query)` | No | Authorized course ID; 1?120 characters |

**Output** `200` with `course_id`, `checked_at`, `answers_available`, `stt_available`, `tts_available`, `generation_available`, `kill_switch_active` and `features`.

```powershell
curl.exe -i -H "Authorization: Bearer $jwt" "http://127.0.0.1:8000/v1/status"
```

### 24. Update a kill switch

**API** `PUT /v1/admin/kill-switch`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer admin JWT with admin:operations |
| `scope` | Yes | global or course_id |
| `course_id` | Conditional | Required when scope is course_id; omit for global |
| `feature` | Yes | answers, stt, tts, generation, adaptive_levels or all |
| `enabled` | Yes | true activates the kill switch; false releases it |

**Output** `200` with `scope`, `course_id`, `feature`, `enabled`, `kill_switch_active`, `features_enabled`, `updated_at`, `audit_event_id` and `audit_pending`.

```powershell
$body = @{scope='global'; feature='generation'; enabled=$false} | ConvertTo-Json -Compress; $body | curl.exe -i -X PUT -H "Authorization: Bearer $adminJwt" --json '@-' http://127.0.0.1:8000/v1/admin/kill-switch
```

### 25. Get version information

**API** `GET /v1/version`

**Input**

| Input | Required | Accepted values |
| --- | --- | --- |
| `Authorization (header)` | Yes | Bearer service JWT |

**Output** `200` with `schema_version`, `application`, `models`, `prompts`, `pipelines`, `cognitive_load` and `configuration_id`.

```powershell
curl.exe -i -H "Authorization: Bearer $jwt" "http://127.0.0.1:8000/v1/version"
```
