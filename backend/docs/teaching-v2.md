# Optional teaching and narration V2

This is independent of the `/v2/lesson-jobs` aligned-level API and the `frontend2`
application name. Both generation APIs accept a new top-level field:

```json
{"teaching_version": "v2"}
```

Omitting it or sending `"v1"` uses the original generator, prompts and speech
converter. Frontend V2 defaults to **Version 1 · Current**. Choose **Version 2 ·
Natural formula explanations** in Generate course to opt in. Version choices
survive navigation and participate in request identity; changing versions after
an uncertain submission creates a new idempotency key. Existing V1 request
fingerprints retain their original metadata shape.

V2 is isolated in `src/upraise_ai/services/teaching_v2/`. The worker imports its
model/speech classes only for a V2 job. Original `lesson_models.py`,
`lesson_prompts.py`, `lesson_speech.py`, and `services/v3/` remain independent.

## Behavior

V2.8 now uses `draft_review_recovery=generator-text-with-review-tags-v2`.
The generator owns the teaching text; reviewers provide annotations only. Keep
the first usable explanation, tag failed checks and let a human edit it later.
V1 itself is unchanged. V3 retains its separate bounded correction/source-excerpt-or-gap
recovery policy.

- The writer receives exact selected primary evidence and bounded same-document
  definitions. Source selection cannot invent quotations, definitions or repaired
  formulas. Failed selection uses the original primary source with a review flag.
  Optional context defines existing terms; it does not introduce unrelated topics.
- The first parsed written draft, difficulty adaptation, translation and narration
  are checkpointed in job staging BEFORE their respective reviews.
  Source/request/profile/model identity binds this checkpoint. Interrupted review
  can reuse that first draft without paying for a different explanation.
- Independent material, finding and paragraph entailment checks still identify
  changed facts, invented claims, missing coverage, wrong language and source gaps.
  Reviewers have bounded quote/index/JSON recovery. A failed verdict or unreadable
  reviewer creates an explicit **Human review required** finding. Provider errors
  and unexpected reviewer-response failures also become findings when a usable
  generator draft exists. V2.8 retains the original explanation; it does not run
  factual correction rewrites or replace
  available prose with raw source excerpts or generic gap notices.
- Formatting problems such as literal newline/Unicode escapes and doubled math
  delimiters stay visible in that first text and receive findings. Only actual
  characters that cannot travel safely through UTF-8/PostgreSQL/XML are replaced
  with spaces. Invalid out-of-window citation tags remain visible in the original
  text with citation findings; paragraph reference metadata is restricted to actual
  source numbers. This also handles a paragraph containing only a bad citation.
  Meaning, equations, values and available teaching prose are not reconstructed.
- Same-level generation reuses canonical text and its warnings. Other levels keep
  their first adapted candidate when a review fails. An unavailable adaptation
  can retain the existing canonical teaching, explicitly flagged. Exceeding the
  requested word target is an editing finding, including for same-level canonical
  reuse. Oversized canonical display units are split without rewording. Unit
  identity and representable schema bounds remain requirements. The shared plan still links corresponding
  chunks across levels.
- Translation protects paragraph identity, mathematical notation, code and source
  references. A failed translation review retains the first structurally valid
  translation. If none exists, keep the original teaching and flag the unfinished
  translation. The required target language is not falsely marked verified.
- Voice preparation keeps the first narration if its format/meaning/language/
  completeness review fails, with speech findings. V2 does not repeatedly rewrite
  that narration to satisfy the reviewer. If no usable narration was returned,
  read the existing written teaching through the shared V1 speech renderer and
  mark it for review. Rendering failures are preflighted and tagged before saving
  text; if narration cannot be rendered, try the existing writing, then an explicit
  review notice. The written teaching remains unchanged. If the writer never
  returned any usable teaching at all, an explicit human-review notice is the last resort.
- All parts are preflighted before synthesis. Review flags persist with durable
  PostgreSQL teaching text BEFORE TTS, as well as in manifests and narration
  checkpoints. Passed and needs-human-review statuses remain distinct. Cache
  identity includes retention policy/mode, written text, language and model IDs;
  retrying speech does not silently discard findings.
- A7 inherits V2 from its parent and edits only the requested chunk/levels. It
  keeps the first requested update on review/alignment failure and carries the
  findings into the replacement DRAFT. Unreadable reviewers cannot silently
  approve an edit. Source ownership, unit identity, local reference/encoding
  bounds, leases and atomic replacement still apply; other chunks stay intact.
- The Review page marks affected parts with a visible banner and section tags
  (for example **Citations**, **Review unavailable**, **Text formatting**, **Length**),
  expands findings, labels the part selector **Needs review**, and presents the actual text, source
  evidence and narration for the human reviewer. Existing A7 feedback controls
  allow correction. All results remain DRAFT; external approval stays external.
- Actual storage, source/access/lease and TTS availability failures remain real
  operational failures. Cancellation, memory exhaustion and ownership loss also
  retain their worker handling. They cannot create nonexistent audio or authorize
  another owner's material. Prepared teaching text and its flags survive a TTS outage.

V2 uses `services/teaching_v2/`, especially `first_draft.py`, `models.py`,
`speech.py` and `review_recovery.py`. The worker enables this policy for V2
generation and V2 teacher updates. Original V1 model/prompt/speech code remains
independent. Historical V2 correction/source-excerpt utilities remain available
for strict compatibility paths; they are not the active first-draft policy.

## Rollback and removal

**Immediate rollback:** select Version 1 for new generations. It is already the
default, and existing V1 text/audio remains intact. Existing V2 courses remain
readable/playable because their stored result uses the same A5/audio schemas.
Stopping V2 generation does not require database migration or deleting courses.

**Removing the feature later:** first drain/cancel any queued or running V2 jobs.
Remove the conditional worker factory, V2-specific version hooks and selector/
narration-review UI, then delete the isolated `teaching_v2/` package and its tests.
The additive request/review metadata can remain for historical compatibility.
Historical V2 revisions need the V2 implementation; either retain it for those
courses or explicitly disable their revision rather than silently applying V1.
Deleting V2 courses, if desired, uses the existing course/lesson deletion APIs.

Pre-V2 integration files and SHA-256 hashes are saved under ignored
`codex_helper/teaching-v2-baseline/`, including earlier uncommitted chat-history
work. Compare individual changes; do not reset the repository or overwrite later
unrelated edits. `codex_helper/teaching-v2-runtime.patch` contains only V2 runtime
changes; its reverse application was checked without applying it. Recheck it
before any later rollback, and retain historical-metadata compatibility as above.
No Git commit or staging is performed by this task.

Validation: `tests/test_teaching_v2.py`, `tests/test_teaching_v2_material_scope.py`,
`tests/test_teaching_v2_context.py`, `tests/test_teaching_v2_integration.py`,
existing A3/A7/review/version regressions, and Frontend V2 DOM/browser checks.
Live written-only checks require authorization for the source payload and provider;
they create no audio or course replacement. Results are recorded under ignored
`codex_helper/`; test results for a source window do not verify an entire course.

## Historical validation before first-draft retention

Verified 2026-10-19 V2.8: 341 selected A3/A7/review/backend regressions passed.
The final V2 run passed 135 tests, and the subsequently added adaptation-recovery
test passed separately (136 V2 tests total). Ruff/build/diff checks passed.
Written-only course verification completed all 23 source windows: 16 retained
reviewed teaching text and 7 used flagged exact-source excerpts after exhausted
corrections. Previously reviewed V2.8 windows were checkpointed; this was not a
fresh whole-course worker submission. A fresh live check of one exhausted window
independently exercised authoring, both factual corrections and deterministic
source-copy recovery; every returned paragraph matched its selected primary
source exactly. No real audio or course replacement was generated by these checks.
The strict spoken gate was exercised with fake providers, not by listening to
new audio. Both configured Bedrock models received only the user-authorized
course debugging inputs. See ignored `codex_helper/teaching-v28-*` evidence.
All eight services are healthy. All 17 checked runtime file hashes match the host
in backend, worker, lesson-worker and lesson-delivery. Two existing completed V1
result hashes are unchanged; the failed job remains failed, requiring a fresh
user-triggered generation. V1 model/prompt/speech source files are unchanged.
The V2-only removal patch was refreshed and reverse-checked without applying it.
