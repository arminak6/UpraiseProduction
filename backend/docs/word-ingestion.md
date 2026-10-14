# Word ingestion

A1 `POST /v1/documents` accepts `.docx` and legacy `.doc` uploads using the existing
multipart fields, idempotency key and `202 {document_id, job_id}` response. Filename
extension or Word MIME selects the format; staged bytes are validated before
acceptance. Signed Moodle URL submissions remain PDF-only.

`WordProcessor` uses native Docling DOCX extraction for headings, lists, text,
tables, formulas and pictures. Legacy DOC is converted into an intermediate DOCX
with LibreOffice. The original source bytes and hash remain authoritative.
Headings and Docling element references are retained in ordered source blocks.
`page_count=0`, `pages=[]`, `source_format=docx|doc`, `source_block_count` and
`word_references` distinguish logical blocks from physical pages.

A rendered PDF is retained at `word-artifacts/rendered/source.pdf` (intermediate
DOC names may differ). `render-audit.json` compares native and rendered word-token
counts to identify possible omissions; this is not semantic or visual proof.
Disagreement over 5% flags extraction for review. Pictures are saved and described
using existing vision providers; a separate passing source comparison is required
before a description enters retrieval. Native text remains UNVERIFIED unless
further evidence justifies a review flag. Successful parsing is not human approval.

When native text is highly fragmented (over 100 text elements, more than half
under four words) or the rendered/native token mismatch exceeds 5%, the default
visual fallback parses the rendered PDF with the existing Docling PDF parser and
source-fidelity enrichment. The native Docling JSON/elements remain retained for
comparison. Published references state `extraction_path=rendered_pdf_fallback`
and retain `rendered_pages` separately from empty public PDF `pages`.
Native paragraphs absent from the rendering are retained next to their closest
rendered text, with native XML paragraph locators and NEEDS_REVIEW flags. This
prevents clipping in a draft's layout from silently deleting source statements;
recovered linear text does not establish the correctness of mathematical layout.
`UPRAISE_WORD_VISUAL_FALLBACK=false` disables this recovery; its setting is part
of pipeline identity. A fallback flag and any fidelity findings remain visible.

Prepared JSON binds original SHA-256, metadata, parser/configuration versions and
embeddings for retries. The existing worker lease, attempt fencing, owner/course
isolation, Qdrant publication, status, cancellation and deletion flows are reused.
A6 retains `source.docx`/`source.doc`; temporary Word/render/vision artifacts are
removed with other preparation output. A2 removes indexed sources and lessons
under the existing independent original-file retention policy.

A3 v1/v2 and A7 consume Word blocks through the numeric-citation adapter.
Internal `[p. N]` fields are logical source slots; public text uses
`[Word blocks N-M]`, with structured native references and empty PDF pages.
Speech removes these markers. B4 retains Word block/reference metadata. Mixed
PDF/CSV/Word source requests use each format's own windows and citations.

Limits: 100 MB upload, 500 MB total expanded DOCX data, 10,000 archive entries,
180 seconds per conversion and the configured maximum rendered PDF pages.
`UPRAISE_MAX_WORD_SIZE_MB`, `UPRAISE_MAX_WORD_UNCOMPRESSED_MB` and
`UPRAISE_WORD_CONVERSION_TIMEOUT_SECONDS` are included in pipeline identity/C5.
Encrypted archives, macro payloads and invalid OOXML are rejected. Archive
contents are inspected in place rather than extracted into arbitrary paths.
Conversion uses a per-job headless LibreOffice profile and preserves the upload.

Expected failure codes include WORD_TOO_LARGE (413), WORD_INVALID_DOCUMENT,
WORD_ARCHIVE_LIMIT_EXCEEDED, WORD_ENCRYPTED_UNSUPPORTED, WORD_MACROS_UNSUPPORTED,
WORD_CONVERSION_FAILED/TIMEOUT/INCOMPLETE, WORD_TOO_MANY_RENDERED_PAGES (422), and
WORD_CONVERTER_UNAVAILABLE (retryable 503). CSV/PDF contracts remain unchanged.

Limitations: complex drawings, text boxes, equation images, footnotes, tracked
changes and comments need corpus-specific fidelity checks. The native/render token
comparison cannot detect changed operators, reading order or meaning. References
are Word blocks with native element locations, not Word pagination. Final edited
course text retains the existing draft review policy; adding Word ingestion does
not introduce a separate final semantic judge.
