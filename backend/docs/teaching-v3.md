# Teaching and narration Version 3

Select **Version 3 · Continuous course teaching** in Frontend V2, or submit
`"teaching_version": "v3"` to either lesson-generation endpoint. Omitted/v1 keeps
the existing V1 generator; v2 retains V2.8. A version change creates a separate
request identity. Existing courses are not regenerated or replaced.

## Course generation

New V3 generation uses its **flag and continue** DRAFT policy
(`draft_review_recovery=flag-draft-for-human-review-v1`). Invalid/unreadable
automated reviews, exhausted factual or narration corrections, and unresolved
course conflicts/continuity add explicit human-review findings to the affected
parts. Every later part still receives its review. Structurally valid current
text can remain unverified; known rejected material uses exact primary excerpts
or a visible gap. A usable spoken script may remain flagged; without one, a
short review notice is spoken. Flags survive retries and are persisted with
durable teaching text before TTS, as well as in course manifests and protected
checkpoints. The Review page marks affected parts and expands their findings.
V2.8 now has a separate first-draft retention policy; V3 retains its own
bounded correction and source-excerpt/gap behavior described here.
Unavailable review is never recorded as approval. Source/citation/identity/
format/size bounds, actual storage/TTS failures and A7's strict gates remain.
The shared recovery also handles malformed generated writing before review;
source/gap replacement still passes local text-format validation.

1. Authorized, active-attempt sources are selected before writing. Every source
   window receives a plan with a descriptive topic and selected original concept/
   definition excerpts. The model selects explicit source indices; software copies
   the original text, including mathematical notation. It does not retype formulas.
2. The outline follows document order. Adjacent windows can be grouped when they
   continue the same idea, within 24,000 characters of teaching text and the
   configured full-payload source limit. Extraction geometry and provenance do
   not consume the teaching-text budget. There is no fixed window-count cutoff that separates a formula heading
   from its following equation. Different documents, versions, languages and formats cannot be merged.
   Empty incidental windows do not become empty student-facing lessons; their
   source warnings remain in the DRAFT review.
3. Each writer gets its position, bounded ordered outline navigation, previous
   teaching, upcoming topics and relevant exact source definitions. The whole plan
   is persisted; calls use bounded views (recent three sections/12,000 characters
   of complete teaching paragraphs, and 16,000 characters of whole definition
   excerpts). Shared definitions stay within the same document/version.
4. Necessary supporting definitions may carry their actual citations. At most six
   supporting original chunks/12,000 characters extend a section's evidence, within
   the configured source limit. Primary topic order and mandatory coverage remain
   separate. Supporting chunks do not become additional required topics. Word/CSV
   source references retain real source IDs and can be resolved by A7.
5. Authored paragraphs are complete teaching units: keep an equation, its variables
   and explanation together. Playback groups complete units to the target length.
   It does not split an authored paragraph at an arbitrary word count. Corrections
   enforce the maximum word count and 16,000-character aligned-text bound. Plan
   refinement splits only between complete paragraphs. An unsplittable oversized
   thought fails instead of silently separating a formula from its explanation.
6. Source coverage is checked one host-bound required excerpt at a time; independent
   generation-model entailment checks every candidate paragraph. Reviews return
   omissions/language warnings or support verdicts, not model-retyped evidence
   quotes/indices. Numeric question givens, citation bounds and unit IDs remain
   checked. Actual material, citation and formatting problems receive at most two
   corrections using the actual current candidate; the last factual editor uses
   the configured review model.
7. Exhausted new-generation factual corrections can retain exact PRIMARY source
   excerpts, flagged for teacher editing, as approved for DRAFT recovery. Supporting
   context is not copied as new topics. Excerpts are grouped by model-selected,
   strictly increasing contiguous paragraph boundaries; the host preserves every
   original fragment once and preserves source order. Failed level expansion can
   retain reviewed canonical text in the correct language, flagged for level editing.
8. Every final written part and neighbouring boundary receives continuity review:
   opening only at the start, stable notation, meaningful transitions, questions
   and a supported ending. Suspected cross-part contradictions receive independent
   verification: comparing different models, grammar and faithful source ambiguity
   are not automatically factual contradictions. Confirmed unresolved contradictions
   flag the affected DRAFT parts; source/presentation concerns remain warnings. Completeness
   accusations also receive independent verification: absent derivations or
   distributional assumptions are source warnings, not new teaching requirements. Normal prose
   with unresolved continuity failures is flagged after bounded correction. Exact-source
   DRAFTs can retain editing warnings rather than inventing unsupported repairs.

## Text and voice

All selected levels' spoken scripts are prepared BEFORE the first real synthesis
request. Each narrator receives its actual part index/count, ordered topic
navigation and the actual previous written part. Wider source definitions and
future teaching stay out of narration prompts, preventing premature new lectures.
It continues the same teacher's explanation,
preserves questions as questions, and cannot add new examples, answers, distributional
assumptions, significance thresholds or repaired formulas.

An independent meaning/language/completeness/naturalness review, per-written-
paragraph spoken coverage checks AND host-bound entailment against the CURRENT written text are attempted. Unresolved checks flag new DRAFT narration for human review. Context helps interpretation
and transitions; it cannot authorize new scientific facts in the voice. Binomial
coefficients use their actual operands ("a choose b"), and matrices, subscripts,
conditioning, cardinalities and fixed/expected quantities retain their meaning.

Alleged spoken omissions receive independent verification against the whole written
teaching unit, preserving alternative-case framing across paragraphs. Typed findings
separate genuinely missing available meaning from equivalent wording, source damage
and source ambiguity. Repeated headings and unexplained extraction fragments are
not required speech. Source gaps become teacher-visible warnings; missing available
equations, operators, conditions or assertions require correction or a visible DRAFT flag. Forward
entailment always runs first, so this resolution cannot authorize new spoken claims.

The first narration uses the generation model. Bounded corrections use the separate
review model as a narrow editor of the actual candidate, removing unsupported
additions rather than generating another creative explanation. Historical credit
does not establish the credited author's intent. Every repair repeats the ordinary
precision, format, meaning and coverage gates.

Decimal quantities retain their exact numeric values, including leading zeroes.
Conservative English number-word normalization only applies when the parsed value
matches a value in the current written teaching. Missing values receive a narrow
numeric-format repair with no wider course context; unresolved precision uses a flagged review notice if no usable script is available.
Integer operands can use words. Other mathematical notation retains independent
meaning checks rather than document-specific substitution rules.

The full spoken sequence receives the same boundary/consistency review after all
scripts exist. Actual spoken continuity problems retain human-review flags after bounded
corrections. Independently verified source-ambiguity warnings remain in the saved
teacher report; source warnings do not approve changed mathematical meaning.
Verified scripts are cached by complete course text/order, policy, plan, language,
level and model IDs. Individual approved scripts are also checkpointed with their
full input context, policy/model identity, output hash and source warnings. A later
failure does not discard approved parts. Changed neighbouring text invalidates the
checkpoint, and corrupt scripts are regenerated. Reusing a script never skips the
full-course spoken review. Lower shared
audio preparation uses the reviewed plain narration, bypassing V1 symbolic formula
conversion. Script caches/plans follow ordinary job-staging erasure and TTL.
Changing only sequence-review policy repeats the whole-course review while keeping
otherwise identical individual script approvals valid.

## Revisions, compatibility and limits

A7 inherits v3 from the parent. It changes only the requested unit/selected levels,
retains the existing source/final material gates and reviews the new spoken script
against the stored neighbouring narration. It does not regenerate unrelated audio
or silently replace a teacher edit with source excerpts. Revision caches include
the surrounding written course. Original V1/V2 implementations remain unchanged;
V3 reuses their shared source-selection, validation and transport utilities.

All output remains **DRAFT**. Planning and AI review do not establish human approval
or guarantee mathematical correctness. Damaged extraction can still require teacher
editing or prevent faithful narration. This change does not repair ingestion.
Long courses use bounded context and per-part/boundary review rather than one
unbounded prompt; distant errors can still escape automated checks. Source-excerpt
recovery is less polished than reviewed teaching prose. Actual voice quality requires
listening. More model calls can increase generation time.

New runtime stages use content-free `TEACHING_V3_*` errors. Reused low-level gates
can retain `TEACHING_V2_*` codes; the job's teaching_version identifies its mode.
No raw provider responses or source text enter logs/audit. Full plans/scripts remain
inside protected job output; bounded source/material findings use existing owner-only
A4 behavior. Normal ownership, source/version, lease, deletion and publication fences
continue to apply. No database migration or automatic job submission is required.

## Rollback

Choose V1 or V2 for new jobs to stop using V3 immediately. Before removing its
implementation, drain queued/running V3 jobs and account for historical V3 A7 edits.
The ignored `codex_helper/teaching-v3-baseline/` snapshots and
`codex_helper/teaching-v3-runtime.patch` preserve the pre-V3 integration. Its reverse
application is checked without applying it or touching the Git index. Do not reset
the repository or remove V2 shared utilities while V3 still depends on them.

Tests: `test_teaching_v3.py`, `test_teaching_v3_integration.py`, existing V1/V2/A3/A7/
review regressions, Frontend V2 DOM and bridge tests. Live written-only checks use
the explicitly authorized Random Graphs debugging inputs and create no actual audio,
course job or replacement. Evidence and scope are recorded under ignored
`codex_helper/teaching-v3-*`.

## Review repairs and validation (2026-10-19)

V3 gathers bounded same-page equation evidence with definitions. Independent
entailment accepts elementary consequences of explicitly supplied formulas,
without requiring a separate verbatim interpretation. It does not permit guessed
formulas, symbol meanings, distributions or thresholds. Written coverage/language
accusations receive independent verification. Real missing baseline table cells
remain blocking. Recovery checks the actual replacement prose language rather
than inheriting a verdict about the discarded draft.

The narration task transforms the source into speech; source questions are
content to read, never instructions to answer. All available table columns retain
their row/column relationships, including those outside an exercise's focus.
Printed undefined labels are not expanded using outside knowledge. Visibly
truncated equations/conditionals are not read as complete assertions or repaired
by guessing. Intact available meaning remains required; source gaps need teacher
review. Alleged language errors receive one independent check of actual script
prose. Source spelling mistakes are not foreign-language teaching; substantial
foreign passages still block. These are general source-handling rules.

Spoken entailment also permits mathematical type consequences of explicit source
properties: a quantity explicitly allowed to be negative is not constrained to
the probability range. This cannot be inferred merely from a statistic's name.
The live source-bound check accepts that consequence while rejecting an unstated
distribution and a guessed missing condition.

Spoken completeness findings receive a typed, host-bound check against current
writing and actual neighbours. Intentional exercise/motivating questions need not
be answered immediately. Introducing a concept before its definition in the next
part does not require duplicating that definition. Derivations absent from the
current writing are source concerns, even when a table provides the resulting
values. Genuine dangling sentences, missing current meaning, split equations and
necessary missing transitions still require repair. Exhausted continuity failures
save bounded protected findings before blocking audio.

All 88 V3 unit/integration cases pass, including actual-language mismatch rejection,
source questions, decimal precision, source gaps, cache integrity, all-level
preflight and isolated A7 edits. Ruff, image builds, whitespace and the V3-only
rollback reverse-check pass. Earlier validation covered 368 selected backend
regressions, 40 UI cases and 15 bridge cases; overlapping counts are not additive.

The user explicitly requested fixing and fully generating the intermediate Random
Graphs PDF course with configured Bedrock and Polly, including retries. Private
written replays create no audio or course replacement. Full generation uses the
normal API/worker lifecycle. Authoritative job outcomes, scope and final actual
audio checks are recorded under ignored
`codex_helper/random-graphs-v3-generation/`.

The earlier intermediate V3 validation course completed with six parts and 9.8 minutes of
Brian narration. All six actual WAV hashes/durations/formats, saved text/narration,
the previously omitted baseline values, ready/review catalogs and persistent
archive were verified. Five parts retain source-excerpt teacher-editing flags;
completion does not make this a polished or teacher-approved course. No direct
listening assessment was performed. All eight services are healthy; four backend
services match 31 host-file hashes each, and three existing V1/V2 results are unchanged.

## Grounded consistency and planning budget repair

The course conflict checker classifies each allegation separately. Confirmed
conflicts must select valid host-supplied assertion records; model-retyped claims
never become rejection evidence. A separate reviewer sees the actual selected
passages and local context, without the first reviewer's allegation/paraphrase.
Both conflicting assertions must be in the selected passages; context only resolves
subjects and conditions. Source ambiguity, different model assumptions and missing
explanations are distinguished from genuine contradictory assertions.

For immutable source excerpts, separate fragments must explicitly name the same
subject before their assertions can be treated as a confirmed conflict. A complete
paragraph may coordinate predicates with one subject. An unlabeled fragment cannot
gain a subject solely through adjacency or a model's paraphrase. Such uncertainty
stays a teacher source warning; genuine explicit conflicts still block generation.
The rule has no document-specific terms. `course_conflict_review` identifies the
policy and invalidates dependent checkpoints.

Every written/spoken review round saves bounded, owner-protected findings with
part indices, content hashes and conflict evidence references in job staging.
It does not log source text or raw provider responses. These diagnostics follow
the usual A6/A2/TTL lifetime, allowing a later rejection to be investigated.

V3 announces its source-window workload before planning's source-selection/model
calls. Planning therefore receives its bounded lesson allowance rather than the
five-minute initialization allowance. The absolute attempt deadline, ownership
checks and non-resetting phase timers remain enforced. V1/V2 are unchanged.

The reported failed job's original conflicting claim was not retained. Its saved
writing passed one replay; a later replay reproduced the source-fragment problem
above. Four live probes verify source ambiguity and distinct assumptions are
allowed, while real contradictions remain blocked. Fresh normal-API generation
and its final verification are recorded in `codex_helper/v3-inconsistency-repair/`.

## V3 response recovery

V3 normalizes complete JSON objects using the existing bounded response parser.
One complete object inside a code fence or prose wrapper retains its actual fields
and verdict; duplicate keys, multiple objects and truncated objects are rejected.
The expected task schema is still validated after normalization. An unreadable or
token-limited response gets one format retry with the same source and factual
criteria and a bounded larger output allowance. Exhaustion is a retryable model
response failure, never a positive review verdict.

Paragraph entailment uses a V3-local auditor: host paragraph-index mismatches
remain invalid evidence, while model/provider response failures retain their own
retryability. Previously the shared auditor wrapped both as invalid evidence and
could terminate a course for a formatting failure. Protected diagnostics record
the stage and format failure without raw model/source content. V1/V2 retain their
existing code. `review_response_contract=complete-json-retryable-paragraphs-v1`
invalidates dependent V3 checkpoints.

After these consistency, planning and response fixes, a fresh same-document
intermediate course completed on its first attempt: five parts and 8.14 minutes
of Joanna narration. Actual archived WAV hashes/durations/formats, stored text/
SSML, the missing table baselines and ready/review listings passed verification.
All five parts retain source-excerpt teacher-editing flags; successful generation
does not establish polished teaching quality. No direct listening assessment was
performed. All 19 existing completed results were preserved. Evidence is under
`codex_helper/v3-inconsistency-repair/completed-course/`.
