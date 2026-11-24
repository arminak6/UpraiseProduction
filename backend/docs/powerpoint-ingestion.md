# PowerPoint ingestion

A1 `POST /v1/documents` accepts `.pptx` multipart uploads using the existing
authentication, idempotency and `202 {document_id, job_id}` contract. Legacy
`.ppt`, macro-enabled presentations and narrated/animated slide playback are not
included. Signed Moodle URL submissions remain PDF-only.

Optional multipart booleans, encoded as `true` or `false`, apply only to PPTX:

- `include_speaker_notes`: default false; include presenter notes as separately
  labeled teaching material. Comments are excluded.
- `include_hidden_slides`: default false; include hidden slides while preserving
  original slide numbers.

Both choices are saved in metadata and the request fingerprint. Changed choices
require a new idempotency key. The main UI exposes them when a PPTX is selected.

## Extraction and source checks

`PowerPointProcessor` reads native text boxes, paragraphs, groups and tables with
python-pptx, and retains independent Docling extraction as additional evidence.
Geometric XY cuts order full-width headings, columns and their contents rather
than using XML drawing order. Explicit groups remain together; their transforms
are applied to child positions. Right-to-left slides use right-to-left column
order. Ordinary text-box titles, list depth, merged table cells and normalized
shape bounds are retained. Native paragraphs/table cells remain the reference
for deterministic word/number coverage. Notes are read separately; comments and
unselected presenter notes cannot silently enter teaching material.

LibreOffice Impress renders all slides, including hidden ones, with notes pages
disabled. A slide/page count mismatch fails instead of shifting citations. Only
included slides receive PNG previews and enter retrieval. Native/rendered token
comparison flags possible omissions; native text is never silently replaced.

With `UPRAISE_DESCRIBE_IMAGES=true`, text-only candidates receive a complete-slide
comparison. Visual slides, incomplete native extraction and failed comparisons
use a complete-slide transcription followed by an independent source comparison.
This checks screenshot text, exclusions, conditions, prices, tables, chart labels
and diagram relationships, rather than accepting a brief figure summary. Visible
native picture crops supplement the rendering at original resolution; cropped-out
picture content is excluded. Evidence is bounded and never silently truncated or
downsampled to obtain a passing comparison. Unsupported picture encodings may
use a readable full-slide rendering; unavailable supplemental evidence is warned.

The verifier has an explicit `slide` mapping (`page_numbers=[]`, ordered positive
`slide_numbers` and matching `slide:` references). PDF and Word mappings remain
separate. PowerPoint-specific prompts/cache identities prevent reuse of legacy
short descriptions or incompatible checks. Truncated/unreadable candidates are
rejected. An accepted transcription must retain all native normalized words and
exact numeric literals, including decimal separators, and pass the complete
visual comparison. It replaces the retrieval view of the slide, avoiding duplicate
native/visual accounts; original native elements remain in audit artifacts.
Failed candidates never enter retrieval.

An unavailable Docling conversion can fall back to native XML plus the same full
visual checks. Embedded media/OLE objects and off-slide content require review.
Audio/video is not transcribed and animations are not interpreted as a sequence.
Impress can render fonts/complex objects differently from PowerPoint; unresolved
clipping, unreadable content and ambiguous relationships require review. Parsing
and model comparison do not prove factual accuracy or human approval.

Artifacts under `powerpoint-artifacts/` include available `docling.json`,
`native-elements.json`, the rendered PDF, included `slide-N.png` files, visible
picture evidence, `render-audit.json` and source/version/hash-bound
`slides.json`. Existing prepared checkpoints, leases, attempt fencing and scoped
Qdrant publication are reused.

## Completeness and generation admission

PPTX A4 results and A2 document summaries include `ingestion_readiness`:
`{status: COMPLETE|INCOMPLETE, blocking_slides: [...], issues: [...]}`. `DONE`
continues to mean that processing/index publication completed; it does not mean
that every source element was successfully verified. Complete coverage is an
automated result, distinct from human approval. Disabling image/source checks
leaves nonempty slides incomplete.

By default, both A3 endpoints reject incomplete or legacy unchecked PPTX with
`409 POWERPOINT_INGESTION_REVIEW_REQUIRED`. A request may explicitly set the
top-level strict boolean `allow_incomplete_source: true` to generate a draft from
the available indexed content. The choice is saved in job metadata and retry
identity; omitting it or setting false preserves existing default fingerprints.
Workers honor it after a queued source finishes. Ownership, deletion, failed
ingestion, active-attempt, source-hash and index-completeness checks still apply.

The option does not accept rejected descriptions, clear source warnings, change
source readiness or assert complete coverage. A shared model policy forbids
inventing unavailable visual content; affected course sections/levels retain
explicit source-coverage warnings in their review reports. Human review remains
required. No course is generated automatically by enabling the feature.

Both UIs show source coverage. Processed material with content can be selected
even when it needs review; choosing **Generate from the available content** is
required before submission for that source. The main UI resets this choice when
switching sources or preparing another course, and preserves it for unchanged
retries. A fresh upload remains the way to run updated ingestion checks on legacy
material. Previously generated courses stay readable/reviewable.

## References, review and retention

Chunks stay within an original slide; large slides may have multiple chunks.
They carry `source_format=pptx`, `slide_numbers`, `slide_references`, and numeric
`source_blocks` equal to slide ordinals. References preserve title, hidden status,
native element locations and notes inclusion. Public PDF page fields stay empty;
`page_count=0`. A4's `slide_count` and `source_block_count` report the original
total, so excluded slide slots may leave gaps in indexed content.

A3 groups slides through its existing source windows; one slide is not forced
to become one course part. A3/A7 publish `[Slide N]` or `[Slides N-M]`, preserve
structured references across levels/revisions, and remove citation markers from
speech. B4 retains slide references and distinguishes them from PDF pages.

Teacher review explicitly loads original slide PNGs through
`GET /v1/documents/{document_id}/slides/{slide_number}?document_version_id=UUID`.
This requires `documents:read`, current document ownership/course access, a DONE
source and no deletion request. Version, included-slide membership and image
hash are checked under artifact/source locks. PNG responses use
`Cache-Control: no-store`. The bridge preserves authentication; previews are
loaded only when clicked and late responses are discarded after navigation.

Previews follow ingestion artifact retention. A6 removes derived artifacts and
retains `source.pptx`; unavailable previews do not hide retained course text. A2
revokes source/preview access and removes dependent teaching content under the
existing original-source retention policy. Existing PDF/CSV/Word flows remain.

## Limits and deployment

- `UPRAISE_MAX_POWERPOINT_SIZE_MB=100`
- `UPRAISE_MAX_POWERPOINT_UNCOMPRESSED_MB=1000`
- `UPRAISE_MAX_POWERPOINT_SLIDES=1000`
- `UPRAISE_POWERPOINT_CONVERSION_TIMEOUT_SECONDS=180`

Validation bounds archives to 10,000 unique entries and rejects invalid/encrypted
OOXML, macro payloads, unsafe XML and external non-hyperlink content. Conversions
use separate headless profiles and never modify the original. Runtime errors use
`POWERPOINT_*` codes. Settings/parser identity appear in the ingestion signature
and C5 provenance (ingestion v1.8, parser/slide checks v2).

The backend image includes `libreoffice-impress`; `python-pptx==1.0.2` is an
explicit locked dependency. Drain active jobs before refreshing shared backend
services and rebuild both frontends. No database migration or regeneration of
existing courses is performed automatically. Existing incomplete PPTX material
needs fresh ingestion and a new course generation to benefit from recovered content.
