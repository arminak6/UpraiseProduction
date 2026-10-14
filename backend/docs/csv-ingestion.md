# CSV ingestion

A1 `POST /v1/documents` accepts a CSV upload using the existing multipart metadata
and response: `202 {document_id, job_id}` plus A4 Location. Select CSV with a
`.csv` filename or `text/csv` / `application/csv` MIME. Signed Moodle URL mode
continues to accept PDFs; CSV support in this release is for uploads.

## Supported input

- UTF-8, with an optional UTF-8 BOM (Excel's CSV UTF-8 export).
- First record is a header; names must be nonempty and unique.
- Comma, semicolon or tab separator, inferred from the header. An ambiguous
  separator is rejected instead of guessing. One-column CSV is supported.
- Standard quoted cells, doubled quotes and embedded newlines. Strings are
  preserved, including leading zeros, empty cells, whitespace and decimal text;
  no numeric conversion, expression execution or AI rewriting.
- Every data record must have the same column count as its header. Blank records
  are skipped; at least one data record is required.
- Default limits: 100 MB, 100,000 data records, 256 columns. Override with
  UPRAISE_MAX_CSV_SIZE_MB / ROWS / COLUMNS. These affect the ingestion signature.
  A complete header or individual record exceeding the chunk budget fails with
  CSV_HEADER_TOO_LARGE / CSV_ROW_TOO_LARGE; values are never truncated.

## Processing

Submission validates every record before acceptance. CsvProcessor uses Python's
standard csv.reader, bypasses Docling/OCR/vision, groups complete records within
the configured chunk budget and repeats headers. Original source.csv is retained.
Prepared JSON checkpoints bind actual SHA-256, metadata, pipeline and version
references, allowing safe embedding/publication retry. Titan embeddings, Qdrant
publication, owner/course isolation, leases, recovery and A4/A6/A2 remain shared.
Parsing success is not semantic/human verification; CSV quality is UNVERIFIED.

A4 result adds source_format=csv, row_count (data records), column_count and
source_block_count (row groups); page_count=0. Qdrant chunks use source_format=csv,
source_blocks and row_ranges, with page_numbers=[] and page=null. Record numbering
includes the header as record1; quoted newlines do not create extra records.
Blank records retain their position in numbering. References denote record
ranges, not physical file lines.

## Lesson generation and citations

A3 v1/v2 consume every CSV row group in order; source_window_pages applies to row
groups for CSV and pages for PDF. Mixed PDF/CSV requests are supported. Legacy
teacher numeric citation slots represent CSV blocks internally, with an explicit
CSV policy and exact row context. Public lesson sources expose pages=[],
source_format=csv, source_blocks and row_ranges. Display text uses
`[CSV rows 2-10]`, never fabricated PDF page citations. Row ranges identify the
source group supporting the generated text; they are not per-cell grounding proof.
Speech omits row citation markers, matching the existing PDF citation treatment.
All-level generation and A7 replacement preserve this provenance. B4 retrieval
receives original header/row text and contextual row metadata.

A6 retains source.csv while removing temporary preparation output. A2 removes
the indexed CSV material and dependent generated lessons; original files retain
the existing independent artifact-retention policy. B7 removes generated content
while preserving the ingested CSV. Existing PDF idempotency identities and
prepared/active sources remain compatible; drain old-signature queued work before
upgrading the ingestion worker. Existing documents do not need re-ingestion.

## Errors and UI

CSV_INVALID_TEXT, CSV_ENCODING_UNSUPPORTED, CSV_HEADER_INVALID,
CSV_DELIMITER_AMBIGUOUS, CSV_PARSE_INVALID, CSV_COLUMN_COUNT_MISMATCH,
CSV_NO_DATA_ROWS, CSV_TOO_MANY_ROWS/COLUMNS and chunk-budget errors return422.
CSV_TOO_LARGE returns413. Responses/logs do not expose cell text or parser details.

The ingestion workspace and standalone Ingest material page accept PDF/CSV,
set the CSV MIME and compute the same source hash. Submission stays explicit;
navigation/reruns do not send extra requests. The Learning page shows CSV row
ranges. Existing progress, ready lessons, revision and deletion controls are reused.
