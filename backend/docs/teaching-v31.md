# Teaching and narration Version 3.1

Choose **Version 3.1 · Source-aware teaching and level review** in the Generate
screen at localhost:8502. Both lesson endpoints accept `teaching_version: v3.1`.
The default remains V1; V2.8 (`v2`), V2.9 and V3 remain separate options.
Requests have distinct identities; selecting V3.1 creates a new course.

V3.1 inherits V3's document-order planning, complete thought units, course
continuity, math narration checks, all-level narration preflight, publication
fences and strict teacher-revision behavior. Its implementation lives in
`services/teaching_v31/`; historical teaching implementations are unchanged.

## Changes

- Selection retains the original text and structurally linked heading metadata
  of each selected substantive chunk. The clipped selection is kept separately
  as `selected_quotes`. Original stored ingestion text is never changed.
  Incidental-only windows are skipped. Reference scope is recomputed from actual
  remaining chunks, including explicitly authorized planning definitions.
- Writers select real evidence IDs for each paragraph or fixed unit. Software
  resolves IDs and renders references. Unknown IDs, guessed numeric citations,
  absent references and out-of-unit evidence fail local gates. Factual support
  checks use the chosen records, including headings and source quality; being
  inside an allowed numeric window does not establish support.
- Individual support and coverage verdicts are independently verified when
  negative. The host selects each affected paragraph; each repair returns only
  replacement text and evidence IDs. Models cannot choose paragraph indexes.
  Malformed edits consume the bounded correction budget without being applied;
  new generation then uses flagged source recovery, while strict revisions reject.
  Other paragraphs remain byte-for-byte unchanged. Missing material can append
  a complete thought. After two corrections, new-generation recovery replaces
  affected paragraphs only with exact source records or previously reviewed
  canonical text, within language/size limits, and flags teacher editing.
  Separate missing source records get separate targets. Canonical recovery may
  expand a rejected paragraph into several original-source paragraphs before
  content-unit IDs are assigned, keeping each within the word limit. Headings
  remain subject context; standalone headings are not required teaching facts.
  Findings use concise reference labels; internal source records stay structured.
  Original extraction labels are removed from presented source excerpts.
  Unavailable/unusable checks retry or fail; no unavailable verdict is approval.
- Level tasks guide writing and receive a separate final review. Similarity
  triggers explicit comparison with canonical/other-level teaching. Shared
  facts are allowed; unresolved level suitability is labeled **Difficulty
  adaptation incomplete** in the existing teacher review UI. This check flags
  weak adaptation; it does not run an unbounded rewrite loop.
- Narration retains V3's numeric/math, factual and reverse-coverage checks and
  adds a bounded question-role and named-subject check. It cannot publish a new
  answer to a written question after that check rejects it. Unresolved task
  changes stop publication and retry through normal job handling.
- Individual verdicts are cached by exact candidate, complete scoped evidence,
  schema, policies, strict/recovery mode and model identities. Changed evidence
  or policy requires new checks. Parsed cache entries carry integrity hashes.
  No shared global cache or new parallel model state is introduced. The existing
  job TTL/deletion lifetime applies. `teaching-v31-review-profile.json` records
  per-stage check counts, cache hits and elapsed provider-check time, with no
  teaching text. It is not a billing-token or whole-job latency measurement.
  Parsed author outputs are checkpointed before downstream checks so resuming
  an unavailable review reuses the same draft and its already-passing verdicts.

## Validation and limitations

Deterministic fake-provider tests cover the heading-only subject failure,
wrong-but-numerically-allowed citations, missing evidence, targeted correction,
source recovery, cache invalidation, task preservation and strict A7 behavior.
Storage integration exercises both APIs, three levels, narration preflight,
catalog/review provenance and revision isolation. Frontend tests exercise
selection across navigation, request identity, labels and spoken-script display.

These checks establish implementation behavior, not a course-quality score.
No new paid course is automatically generated during rollout. Benchmark V3.1
against the saved V2.9/V3 courses and varied Word/PDF/CSV sources before claiming
better quality, lower cost or faster generation. Reviewers are still models;
all generated courses remain DRAFTs requiring human review.

Rollback: choose V3 or V2.9 for a new generation. Existing courses retain their
recorded versions and content; they are not relabeled or regenerated.
