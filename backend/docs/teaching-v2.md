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

- Teaching policy focuses on concepts, notation, reasoning and source-supported
  examples. Incidental fonts/icons/headings do not count as mandatory teaching
  facts. Meaningful diagrams/tables and substantive source coverage remain.
- V2.6 writes canonical teaching text with its own concept-focused structured
  author rather than inheriting V1's exhaustive source-description workflow.
  Isolated labels, unconnected numerals, decorative captions and publication
  metadata may be omitted without narrating each fragment. Actual equations,
  table values, meaningful figures and substantive relationships remain required.
  Qualitative intuition stays qualitative; it does not need an invented formal
  threshold. Source-supported learner questions need not occur verbatim in the
  source. Essential missing equations/definitions receive brief honest limitations.
  V2.7 attributes ambiguous table labels as 'the row labelled ...', retaining the
  printed label and its attached values without guessing a real-world identity,
  definitions for unexplained columns, causal explanations or missing benchmarks.
- Before authoring, a separate exact-excerpt selection pass identifies substantive
  evidence in primary chunks and bounded clarifying context. Every primary chunk
  must receive a disposition; unused optional context defaults to exclusion.
  Selected quotes must occur verbatim. Local guards restore original paragraphs
  containing omitted math and complete table data, then validate preservation.
  These are exact original spans, never model-proposed formula repairs.
  Exact quotes supplied with an incidental classification are treated as reasons
  for exclusion, not lesson evidence. Protected original equations/tables are
  restored afterward; fabricated quotes still fail validation.
  Incidental captions and standalone unexplained fragments do not reach the
  writer as teaching material. Selection is cached within the job and shared by
  canonical/aligned/A7 authoring; primary citation boundaries remain unchanged.
  Empty primary evidence yields an honest limitation, never a neighbouring topic.
  Optional context supplies only definitions of terms/symbols explicitly present
  in the primary material; neighbouring scenarios and new topics are excluded.
- Written final text receives an independent source/meaning/language review.
  V2.8 retains reviewed canonical units unchanged when the requested level is the
  plan level and units fit the word limit. It carries their review warnings and
  avoids a redundant AI rewrite and V1 math-markup repair. Other levels,
  translations and oversized units retain their adaptation/validation flow.
  For new canonical text
  and aligned variants, a rejected final candidate receives up to two corrections
  using the actual independent findings; each corrected candidate is reviewed
  again. Unit IDs, source citations and material boundaries remain checked.
  Word/CSV reference rules are supplied to this review and correction too.
  Acknowledging a source gap is acceptable; inventing its meaning is not.
  After two unsuccessful factual corrections in new generation, V2 discards the
  rejected explanation and copies the selected primary source excerpts exactly.
  It marks the section **Source-excerpt DRAFT; teacher editing is required** and
  keeps an explicit source issue in its saved review. Source values, formulas and
  extraction damage remain visible; the fallback does not invent repairs or use
  neighbouring topics. Successfully reviewed sections keep their teaching text.
  For a failed difficulty adaptation, V2 can retain unchanged, reviewed canonical
  material with its unit IDs, flagged for editing at the requested level.
  It never truncates that material to fit: oversized units use common-plan refinement.
  Copies require matching source/requested languages, valid source bounds and
  valid text. Wrong-language findings, invalid evidence/provider output and local
  citation/format failures still fail closed. Translation keeps its separate checks.
  This recovery does not approve the rejected candidate or relax spoken review.
  A7 retains its existing correction flow and strict final V2 material gate;
  teacher edits cannot be replaced automatically with source excerpts.
  Invalid canonical citations enter the same bounded correction flow and still
  cannot publish unresolved. The final permitted material correction uses the
  configured reviewer model as editor, followed by the normal final review.
- V2 separates blocking written errors from teacher warnings. Changed facts,
  mathematical meaning, values/signs/conditions, missing available substantive
  content, invalid citations and wrong language still reject the candidate after
  bounded correction. Only new generation can replace rejected factual prose with
  the flagged copies described above. Honestly acknowledged source gaps and style/naturalness findings
  remain visible in the saved DRAFT review rather than failing the whole course.
  This applies to canonical text, aligned variants and A7's final material check.
  An incorrect claim cannot pass by labelling it a warning: correctness,
  completeness, language and blocking issues remain independent required checks.
- V2.6 replaces the contradictory global-booleans/scope-recheck contract with
  typed candidate findings: changed fact, unsupported claim, omitted available
  content, wrong language or invalid citation. Correctness/completeness/language
  flags are derived locally from these findings. Blocking assertions must quote
  actual candidate text; omissions must quote substantive information actually
  present in required material. Local checks verify indices, exact quotes (only
  whitespace normalization), per-unit boundaries and whether alleged missing
  excerpts already appear in the candidate. Invalid evidence receives one quote-based
  review correction followed by indexed original-span recovery; persistent invalid evidence fails with
  `TEACHING_V2_REVIEW_EVIDENCE_INVALID` rather than instructing the author to
  invent missing source content. Source/style warnings remain separate. The
  automated reviewer can still make semantic mistakes; teacher review is required.
  Every candidate also receives a separate textual-entailment check using the
  configured generation model, in a fresh source-only audit task, even when the
  broad reviewer has already reported findings.
  It must check every candidate, with the host binding the paragraph and evidence
  scope. Scientifically plausible extra facts remain unsupported when absent
  from the evidence. This gate caught additions that the general reviewer had
  incorrectly classified as harmless explanatory style in the live test.
  In V2.7, factual accusations also receive a narrow independent check using the
  generation model. An accusation solely about faithful source transcription or
  harmless presentation becomes a teacher warning. Actual altered values,
  guessed identities, unsupported explanations and benchmark conclusions remain
  blocking. Wrong-language and citation findings cannot be reclassified. Missing
  substantive content remains blocking; a supposed omission only of repeated
  table-title prefixes/layout can become a presentation warning when all actual
  data, labels and relationships remain. Every supplied finding index must be assessed exactly once; invalid
  coverage receives one repair and then fails closed. Reclassification does not
  bypass the whole-draft entailment or local numerical-question checks.
  V2.8 adds structural recovery after two quote-based broad-review attempts. The
  broad material audit can select explicitly numbered immutable source/candidate excerpts;
  the code resolves the exact quotes and checks indices, unit scope and omissions.
  The entailment audit receives each exact candidate paragraph separately with
  its allowed material: the full source pool for canonical text, or only the
  corresponding canonical unit for an aligned variant. The host binds every
  paragraph's location and checks all of them; the model returns only a strict
  support verdict and reason. Invented facts still require correction, and
  malformed recovery cannot pass. This avoids rejecting a course solely because
  the model removed Markdown/LaTeX while quoting its draft or quoted the source
  instead of the candidate. No approximate string matching is used.
  Valid supplied reference tags are excluded only from entailment input because
  they are application metadata, not scientific claims. Out-of-window tags stay
  visible, and local/broad citation checks remain; published citations are intact.
  A separate local check rejects invented numerical inputs in learner questions,
  even when the AI reviewer approves them. Questions reuse supplied data/variables
  or stay qualitative; equivalent numeric formatting is accepted. Ordinary
  explanation paragraphs can still use equivalent mathematical notation.
  V2 policy metadata participates in canonical/aligned/A7 checkpoint signatures,
  so a policy change cannot reuse text accepted under an earlier V2 policy.
- V2 authoring, correction and material review can receive up to six whole
  clarifying references (12,000 text characters) from the same already authorized
  document/version/active ingestion. Neighbouring references, case-sensitive
  mathematical identifiers and generic Unicode term overlap select context;
  definitions are prioritized over repeated headings. There are no document IDs, subject-specific lookup
  rules, file names or special block numbers in the algorithm. Context clarifies
  existing terminology; it is not additional required coverage or permission to
  introduce topics or expand citation scope. Original unit IDs and citations
  remain bounded. Extraction repair suggestions/private asset metadata are
  excluded; source quality status remains visible. No extra retrieval/model calls
  are needed to select this context. Selection is cached within the job.
  The worker binds these validated sources after creating the actual job model.
  This fixes the earlier handoff that attempted binding while the model was still
  absent, leaving live canonical/aligned/A7 reviews without clarifying references.
- Spoken narration keeps its separate strict contract: meaning, completeness,
  requested language and naturalness must all pass before TTS. Nonblocking
  written warnings do not relax this narration gate. V1 is unchanged.
- Material review/correction use extracted text, source references and headings
  as factual evidence. Extraction-quality flags are supplied separately; proposed
  repairs inside extraction diagnostics are not course facts. Normal source-quality
  warnings and DRAFT status remain in the published review. Equivalent typography
  or label wording is accepted only when entity/value/relationship meaning stays
  intact; unsupported numbers, titles, definitions and formula repairs still fail.
- Material findings are kept under job staging `teaching-v2-material/`. Terminal
  failures also expose bounded reasons, review flags, source window and correction
  count in A4's `error` and `result.teaching_failure`; the existing frontend shows
  those reasons. Ownership/course checks apply. These are temporary output, erased
  by A6/TTL/source deletion; model findings never enter logs or audit records.
  Local diagnostics retain at most five evidence pairs (300 characters per quote)
  and, for rejection, three parsed candidate excerpts (1,200 characters each).
  These aid diagnosis without storing raw provider responses or exposing rejected
  candidates in the published course.
  Selection failures retain bounded typed diagnostic details for debugging too;
  they do not authorize publication or guessing at the missing content.
  Invalid-evidence failures now also expose content-free stage/problem codes,
  source window, level and correction round through owner-only A4 temporary
  output. A6/TTL/source deletion clears them; none enter audit records.
- The written text and equations are retained. A separate complete spoken script
  explains their operations and meaning in natural language. Context distinguishes
  binomial coefficients/matrices, conditioning/division, set counts/absolute
  values and named statistics/nested functions. V2 does not send equations to
  the V1 symbolic speech converter.
- A separate configured reviewer checks the spoken script against the written
  lesson. At most two corrections follow the first candidate; unresolved meaning,
  coverage, language or naturalness fails with a sanitized V2 error. V2 never
  silently falls back to V1 narration.
- Verified scripts are cached in job staging by language, written text, prompt
  policies/version and generator/reviewer IDs. TTS retries reuse them. Normal
  artifact cleanup removes these caches. Published narration is the existing
  SSML/SSML-parts in PostgreSQL and course manifests; WAV/viseme behavior is shared.
- Review courses shows the V2 spoken script and checksum-verified audio for the
  selected part, with native pause/stop/seeking controls. Changing parts or leaving
  the page stops and releases that audio. Teacher feedback uses existing A7.
- A7 inherits the parent's version. A V2 revision cannot switch a V1 course into
  V2. To compare approaches, generate another course with a fresh request.
- All results remain DRAFT. Automated review is not proof of mathematical accuracy
  or human approval. Source extraction gaps still require review, and actual
  voice quality requires listening. V2 uses additional model calls and can take
  longer than V1. Tests use fake providers; no existing course is regenerated.

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
