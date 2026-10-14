"""UTF-8 CSV extraction with bounded complete records and exact cell values."""

import csv
import json
import os
from io import StringIO
from pathlib import Path

from upraise_ai.core.errors import IngestionError
from upraise_ai.core.utils import deterministic_uuid, sha256_file, sha256_text
from upraise_ai.schemas.documents import Chunk, DocumentElement, ElementType, ProcessedDocument
from upraise_ai.services.aws import aws_session
from upraise_ai.services.chunking import StructureAwareChunker, TokenCodec
from upraise_ai.services.embeddings import BedrockTitanEmbeddingProvider
from upraise_ai.services.quality import aggregate_quality, quality_summary
from upraise_ai.services.versions import VersionCatalog
from upraise_ai.workers.budgets import notify_workload

CSV_PARSER_VERSION = "csv-records-v1"


def csv_blocks(source, settings, metadata):
    """Stream blocks; each includes its header and whole numbered CSV records."""
    if source.stat().st_size > settings.max_csv_size_mb * 1024**2:
        raise IngestionError("CSV_TOO_LARGE", status=413)
    codec = TokenCodec()
    prefix = StructureAwareChunker._context_prefix(metadata, [])
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            sample = stream.read(65536)
            if "\x00" in sample:
                raise IngestionError("CSV_INVALID_TEXT", status=422)
            candidates = []
            for delimiter in (",", ";", "\t"):
                try:
                    first = next(csv.reader(StringIO(sample), delimiter=delimiter, strict=True), [])
                    candidates.append((len(first), delimiter))
                except csv.Error:
                    pass
            if not candidates:
                raise IngestionError("CSV_PARSE_INVALID", status=422)
            width = max(size for size, _ in candidates)
            choices = [delimiter for size, delimiter in candidates if size == width]
            if width > 1 and len(choices) != 1:
                raise IngestionError("CSV_DELIMITER_AMBIGUOUS", status=422)
            delimiter = choices[0] if width > 1 else ","
            stream.seek(0)
            reader = csv.reader(stream, delimiter=delimiter, doublequote=True, strict=True)
            header = next(reader, None)
            if (not header or any(not cell.strip() for cell in header)
                    or len(set(header)) != len(header)):
                raise IngestionError("CSV_HEADER_INVALID", status=422)
            if len(header) > settings.max_csv_columns:
                raise IngestionError("CSV_TOO_MANY_COLUMNS", status=422)
            heading = "[CSV table]\nColumns: " + json.dumps(header, ensure_ascii=False)
            if codec.count(prefix + "\n\n" + heading) + 10 >= settings.chunk_max_tokens:
                raise IngestionError("CSV_HEADER_TOO_LARGE", status=422)
            rows, lines, count = [], [], 0
            for record, cells in enumerate(reader, 2):
                if not cells:
                    continue
                count += 1
                if count > settings.max_csv_rows:
                    raise IngestionError("CSV_TOO_MANY_ROWS", status=422)
                if len(cells) != len(header):
                    raise IngestionError("CSV_COLUMN_COUNT_MISMATCH", status=422)
                if any("\x00" in cell for cell in cells):
                    raise IngestionError("CSV_INVALID_TEXT", status=422)
                line = f"Row {record}: " + json.dumps(cells, ensure_ascii=False)
                if codec.count(prefix + "\n\n" + heading + "\n" + line) > settings.chunk_max_tokens:
                    raise IngestionError("CSV_ROW_TOO_LARGE", status=422)
                candidate = heading + "\n" + "\n".join([*lines, line])
                if lines and codec.count(prefix + "\n\n" + candidate) > settings.chunk_max_tokens:
                    yield header, rows, heading + "\n" + "\n".join(lines)
                    rows, lines = [], []
                rows.append((record, cells))
                lines.append(line)
            if rows:
                yield header, rows, heading + "\n" + "\n".join(lines)
            if count == 0:
                raise IngestionError("CSV_NO_DATA_ROWS", status=422)
    except UnicodeDecodeError:
        raise IngestionError("CSV_ENCODING_UNSUPPORTED", status=422) from None
    except csv.Error:
        raise IngestionError("CSV_PARSE_INVALID", status=422) from None


def validate_csv_input(source, settings, metadata):
    # Validate every record before accepting the durable job; no embeddings here.
    for _ in csv_blocks(source, settings, metadata):
        pass


class CsvProcessor:
    def __init__(self, settings, *, embedder=None):
        self.settings, self.embedder = settings, embedder

    def prepare(self, source: Path, metadata, document_id, job_dir, token, progress):
        settings = self.settings
        source_hash = sha256_file(source)
        refs = VersionCatalog(settings).references()
        checkpoint = job_dir / "prepared.json"
        if checkpoint.is_file():
            try:
                saved = json.loads(checkpoint.read_text(encoding="utf-8"))
                processed = ProcessedDocument.model_validate(saved["document"])
                if (processed.document_id == document_id and processed.metadata == metadata
                        and processed.pipeline_version == settings.pipeline_signature
                        and processed.source_hash == source_hash and saved.get("versions") == refs
                        and processed.parser_version == CSV_PARSER_VERSION):
                    progress("store", 0.93, "Resuming prepared CSV.")
                    return processed, saved["vectors"]
            except (OSError, ValueError, KeyError):
                pass
        notify_workload(progress, "parse", max(1, source.stat().st_size // (1024 * 1024)))
        progress("parse", 0.1, "Reading CSV records.")
        version_id = deterministic_uuid(f"{document_id}:version:{source_hash}:{settings.pipeline_signature}")
        prefix = StructureAwareChunker._context_prefix(metadata, [])
        codec = TokenCodec()
        elements, chunks, row_count, column_count = [], [], 0, 0
        for index, (header, rows, text) in enumerate(csv_blocks(source, settings, metadata)):
            progress("parse", 0.35, "Reading CSV records.")
            row_count += len(rows)
            column_count = len(header)
            row_range = {"start": rows[0][0], "end": rows[-1][0]}
            element_id = deterministic_uuid(f"{version_id}:csv:{index}:{row_range}")
            # Successful parsing is structural validation, not factual verification.
            element = DocumentElement(element_id=element_id, element_index=index,
                element_type=ElementType.TABLE, original_text=text, source_text=text,
                structured_content=json.dumps({"columns": header, "rows": [cells for _, cells in rows]},
                                              ensure_ascii=False),
                source_reference=f"CSV rows {row_range['start']}-{row_range['end']}",
                metadata={"source_format": "csv", "source_block": index + 1, "row_range": row_range})
            element.source_structured_content = element.structured_content
            elements.append(element)
            embedding_text = prefix + "\n\n" + text
            chunks.append(Chunk(chunk_id=deterministic_uuid(f"{version_id}:chunk:{index}:{sha256_text(embedding_text)}"),
                chunk_index=index, document_id=document_id, document_version_id=version_id, source_hash=source_hash,
                course_id=metadata.course_id, activity_id=metadata.activity_id, language=metadata.language,
                source_ref=metadata.source_ref, version=metadata.version, document_title=metadata.document_title,
                text=text, embedding_text=embedding_text, token_count=codec.count(embedding_text),
                element_ids=[element_id], element_types=[ElementType.TABLE], source_format="csv",
                source_blocks=[index + 1], row_ranges=[row_range],
                element_quality={element_id: element.quality.model_copy(deep=True)}))
        notify_workload(progress, "chunk", len(chunks))
        progress("chunk", 0.64, "CSV row chunks ready.")
        notify_workload(progress, "embed", len(chunks))
        embedder = self.embedder or BedrockTitanEmbeddingProvider(session=aws_session(settings),
            model_id=settings.embedding_model_id, dimension=settings.embedding_dimension)
        vectors = []
        for start in range(0, len(chunks), 8):
            progress("embed", 0.7 + 0.2 * start / len(chunks), "Embedding CSV chunks.")
            vectors.extend(embedder.embed_texts([chunk.embedding_text for chunk in chunks[start:start + 8]]))
        processed = ProcessedDocument(document_id=document_id, document_version_id=version_id,
            source_hash=source_hash, metadata=metadata, parser_name="python-csv", parser_version=CSV_PARSER_VERSION,
            pipeline_version=settings.pipeline_signature, embedding_model_id=settings.embedding_model_id,
            embedding_dimension=settings.embedding_dimension, page_count=0, row_count=row_count,
            column_count=column_count, source_block_count=len(chunks), elements=elements, chunks=chunks,
            warnings=[], quality=aggregate_quality(elements), quality_summary=quality_summary(elements))
        temporary = checkpoint.with_name(f"prepared.{token}.part")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump({"document": processed.model_dump(mode="json"), "vectors": vectors, "versions": refs},
                      stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(checkpoint)
        return processed, vectors
