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

`PowerPointProcessor` uses native Docling structure for titles, lists, text,
formulas and tables. Original source bytes/hash, list depth and Docling JSON are
retained. Speaker notes are read separately, so comments and presenter notes
cannot silently enter the teaching material.

LibreOffice Impress renders all slides, including hidden ones, with notes pages
disabled. A slide/page count mismatch fails instead of shifting citations. Only
included slides receive PNG previews and enter retrieval. Native/rendered token
comparison flags possible omissions; native text is never silently replaced.

With `UPRAISE_DESCRIBE_IMAGES=true`, the existing vision verifier compares native
slide content against the matching rendering. Visual slides also receive a
candidate description; only a separate passing source comparison permits it
into retrieval. Failed candidates remain separate from accepted content and
flag the source. Disabling image description leaves visual content explicitly
unverified. Sparse slides are flagged when they lack sufficient text or a
verified description. Embedded audio/video is not transcribed; animations are
not interpreted as a sequence. Impress can render fonts or complex objects
differently from PowerPoint. Parsing and model comparison do not prove factual
accuracy or human approval.

Artifacts under `powerpoint-artifacts/` include `docling.json`, the rendered PDF,
included `slide-N.png` files, `render-audit.json` and source/version/hash-bound
`slides.json`. Existing prepared checkpoints, leases, attempt fencing and scoped
Qdrant publication are reused.

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
- `UPRAISE_MAX_POWERPOINT_UNCOMPRESSED_MB=500`
- `UPRAISE_MAX_POWERPOINT_SLIDES=500`
- `UPRAISE_POWERPOINT_CONVERSION_TIMEOUT_SECONDS=180`

Validation bounds archives to 10,000 unique entries and rejects invalid/encrypted
OOXML, macro payloads, unsafe XML and external non-hyperlink content. Conversions
use separate headless profiles and never modify the original. Runtime errors use
`POWERPOINT_*` codes. Settings/parser identity appear in the ingestion signature
and C5 provenance (ingestion v1.7).

The backend image includes `libreoffice-impress`; `python-pptx==1.0.2` is an
explicit locked dependency. Drain active jobs before refreshing shared backend
services and rebuild both frontends. No database migration or regeneration of
existing courses is required.
