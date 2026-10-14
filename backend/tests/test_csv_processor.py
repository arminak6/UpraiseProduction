"""CSV fidelity, bounded records, provenance and resumable preparation."""

import json
from uuid import uuid4

import pytest
from test_a1 import FakeEmbedder

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.documents import IngestionMetadata
from upraise_ai.services.csv_processor import CsvProcessor, csv_blocks, validate_csv_input
from upraise_ai.services.lesson_pipeline import plan_sections
from upraise_ai.services.source_references import display_citations, lesson_source, model_citations
from upraise_ai.storage.files import StagingStore


def csv_metadata(**updates):
    return IngestionMetadata(course_id="course-1", activity_id="activity-1", language="en",
        source_ref="csv-source", content_hash="upstream-revision", source_filename="source.csv",
        source_format="csv", **updates)


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
def test_cells_preserved_with_bom_quotes_empty_values_and_multiline(tmp_path, delimiter):
    import csv
    import io

    raw = io.StringIO(newline="")
    writer = csv.writer(raw, delimiter=delimiter)
    writer.writerow(["ID", "Description", "Value"])
    cells = ["001", 'English, Italiano; "quoted"\nsecond line', ""]
    writer.writerow(cells)
    path = tmp_path / "source.csv"
    path.write_bytes(b"\xef\xbb\xbf" + raw.getvalue().encode("utf-8"))
    settings = Settings()
    embedder = FakeEmbedder(settings.embedding_dimension)
    result, vectors = CsvProcessor(settings, embedder=embedder).prepare(
        path, csv_metadata(), "document", tmp_path, uuid4(), lambda *_: None)
    structured = json.loads(result.elements[0].structured_content)
    assert structured == {"columns": ["ID", "Description", "Value"], "rows": [cells]}
    assert result.page_count == 0 and result.row_count == 1 and result.column_count == 3
    assert result.chunks[0].page_numbers == [] and result.chunks[0].source_blocks == [1]
    assert result.chunks[0].row_ranges[0].model_dump() == {"start": 2, "end": 2}
    assert result.quality.status.value == "UNVERIFIED"
    assert len(vectors) == 1


def test_chunks_repeat_headers_never_split_or_drop_rows_and_use_real_row_citations(tmp_path):
    path = tmp_path / "source.csv"
    path.write_text("ID,Definition\n" + "\n".join(f"{index:03}," + "short definition " * 14
                                                for index in range(25)), encoding="utf-8")
    settings = Settings(chunk_max_tokens=100)
    result, _ = CsvProcessor(settings, embedder=FakeEmbedder(1024)).prepare(
        path, csv_metadata(), "document", tmp_path, uuid4(), lambda *_: None)
    assert len(result.chunks) > 1
    actual = [row for element in result.elements for row in json.loads(element.structured_content)["rows"]]
    assert [row[0] for row in actual] == [f"{index:03}" for index in range(25)]
    assert all(chunk.token_count <= 100 and 'Columns: ["ID", "Definition"]' in chunk.text for chunk in result.chunks)
    row = {"document_id": "document", "job_id": "job", "active_attempt": "attempt",
           "metadata": csv_metadata().model_dump(), "result": {"page_count": 0, "row_count": 25,
                                                              "source_block_count": len(result.chunks)}}
    sections = plan_sections([(row, [chunk.model_dump(mode="json") for chunk in result.chunks])], 5)
    assert sum(len(section["chunks"]) for section in sections) == len(result.chunks)
    source = lesson_source(sections[0], sections[0]["pages"])
    text = f"Explanation [p. {source.source_blocks[0]}]"
    shown = display_citations(text, source)
    assert "[CSV rows " in shown and "[p." not in shown and source.pages == []
    assert model_citations(shown, source) == text
    assert result.chunks[0].page_numbers == []  # Compatibility adapter never mutates the stored source.
    with pytest.raises(IngestionError, match="LESSON_CITATIONS_INVALID"):
        display_citations("Wrong [p. 999]", source)


@pytest.mark.parametrize("raw,code", [
    (b"", "CSV_HEADER_INVALID"), (b"Name\n", "CSV_NO_DATA_ROWS"),
    (b"Name,Value\nfirst\n", "CSV_COLUMN_COUNT_MISMATCH"),
    (b"Name;Value\nfirst;second;third\n", "CSV_COLUMN_COUNT_MISMATCH"),
    (b'Name,Value\nfirst,"unterminated\n', "CSV_PARSE_INVALID"),
    (b"Name,Value\nfirst,\xff\n", "CSV_ENCODING_UNSUPPORTED"),
    (b"Name,Value\nfirst,\x00\n", "CSV_INVALID_TEXT"),
    (b"Name,Name\nfirst,second\n", "CSV_HEADER_INVALID"),
    (b"Name,\nfirst,second\n", "CSV_HEADER_INVALID"),
    (b"A,B;C\n1,2;3\n", "CSV_DELIMITER_AMBIGUOUS"),
])
def test_invalid_csv_rejected_without_content_in_error(tmp_path, raw, code):
    path = tmp_path / "source.csv"
    path.write_bytes(raw)
    with pytest.raises(IngestionError, match=code) as caught:
        validate_csv_input(path, Settings(), csv_metadata())
    assert caught.value.status == 422 and str(caught.value) == code


@pytest.mark.parametrize("update,raw,code", [
    ({"max_csv_rows": 1}, b"Name\nfirst\nsecond\n", "CSV_TOO_MANY_ROWS"),
    ({"max_csv_columns": 1}, b"Name,Value\nfirst,second\n", "CSV_TOO_MANY_COLUMNS"),
    ({"chunk_max_tokens": 100}, b"Name\n" + b"x" * 2000, "CSV_ROW_TOO_LARGE"),
    ({"max_csv_size_mb": 0}, b"Name\nfirst\n", "CSV_TOO_LARGE"),
])
def test_csv_limits(tmp_path, update, raw, code):
    path = tmp_path / "source.csv"
    path.write_bytes(raw)
    with pytest.raises(IngestionError, match=code):
        list(csv_blocks(path, Settings().model_copy(update=update), csv_metadata()))


def test_checkpoint_reuses_embeddings_and_changed_bytes_rebuild(tmp_path):
    path = tmp_path / "source.csv"
    path.write_text("Name\nfirst\n", encoding="utf-8")
    embedder = FakeEmbedder(1024)
    processor = CsvProcessor(Settings(), embedder=embedder)
    def prepare():
        return processor.prepare(path, csv_metadata(), "document", tmp_path, uuid4(), lambda *_: None)[0]
    first = prepare()
    assert prepare() == first and embedder.calls == 1
    path.write_text("Name\nsecond\n", encoding="utf-8")
    assert prepare().source_hash != first.source_hash and embedder.calls == 2


def test_receipt_retains_csv_source_and_removes_output(tmp_path):
    store, job = StagingStore(tmp_path), str(uuid4())
    import io
    source = store.stage(job, io.BytesIO(b"Name\nvalue\n"), 100, source_format="csv")
    (store.job_dir(job) / "prepared.json").write_text("temporary")
    store.purge_output(job)
    assert source.name == "source.csv" and store.source_path(job).read_bytes() == b"Name\nvalue\n"
    assert not (store.job_dir(job) / "prepared.json").exists()


def test_native_teacher_receives_csv_policy_and_speech_omits_row_citations(tmp_path):
    from test_a3 import MathModels, Polly
    from test_a3_model_compat import Converse, review_data

    from upraise_ai.services.lesson_models import LessonModels
    from upraise_ai.services.lesson_speech import LessonSpeech

    path = tmp_path / "source.csv"
    path.write_bytes(b"Concept,Definition\nTape,Read write memory\n")
    settings = Settings()
    processed, _ = CsvProcessor(settings, embedder=FakeEmbedder(1024)).prepare(
        path, csv_metadata(), "document", tmp_path, uuid4(), lambda *_: None)
    row = {"document_id": "document", "job_id": "job", "active_attempt": "attempt",
           "metadata": csv_metadata().model_dump(), "result": {"page_count": 0, "row_count": 1,
                                                              "source_block_count": 1}}
    section = plan_sections([(row, [chunk.model_dump(mode="json") for chunk in processed.chunks])], 5)[0]
    client = Converse(["Tape provides read write memory [p. 1].", json.dumps(review_data())])
    models = LessonModels(settings, client)
    draft, _, _ = models.generate_section(section, "en")
    assert all(any("CSV SOURCE BLOCK" in block["text"] for block in call["system"]) for call in client.calls)
    assert models.transport.source_policy == ""
    source = lesson_source(section, draft.paragraphs[0].pages)
    text = display_citations(draft.paragraphs[0].text, source)
    full, parts, _ = LessonSpeech(settings, MathModels(), client=Polly(), converter=lambda *a: "formula").prepare(text, "en")
    assert "[CSV rows" in text and "CSV rows" not in full and "CSV rows" not in "".join(parts)


def test_format_dispatch_and_mixed_pdf_csv_source_order(tmp_path):
    from upraise_ai.services.formats import get_processor
    from upraise_ai.services.pdf.processor import PdfProcessor

    settings = Settings()
    assert isinstance(get_processor("text/csv", settings), CsvProcessor)
    assert isinstance(get_processor("application/pdf", settings), PdfProcessor)
    pdf = ({"document_id": "pdf", "job_id": "pdf-job", "active_attempt": "pdf-attempt",
            "metadata": {"language": "en"}, "result": {"page_count": 1}},
           [{"chunk_id": "pdf-chunk", "document_version_id": "pdf-v", "page_numbers": [1]}])
    csv = ({"document_id": "csv", "job_id": "csv-job", "active_attempt": "csv-attempt",
            "metadata": csv_metadata().model_dump(),
            "result": {"page_count": 0, "row_count": 3, "source_block_count": 2}},
           [{"chunk_id": f"csv-{index}", "document_version_id": "csv-v", "page_numbers": [],
             "source_format": "csv", "source_blocks": [index],
             "row_ranges": [{"start": index + 1, "end": index + 1}]} for index in (1, 2)])
    sections = plan_sections([csv, pdf], 5)
    assert [section["document_id"] for section in sections] == ["csv", "pdf"]
    assert lesson_source(sections[0], [1, 2]).pages == []
    assert lesson_source(sections[1], [1]).pages == [1]
    assert csv[1][0]["page_numbers"] == []


def test_existing_pdf_metadata_sources_and_reviews_keep_their_serialized_shape():
    from upraise_ai.schemas.lessons import LessonSource, ReviewIssue

    source = {"document_id": "pdf", "document_version_id": "v1", "pages": [1],
              "source_chunk_ids": ["chunk"], "quality_status": "UNVERIFIED"}
    issue = {"category": "source", "message": "Review required.", "pages": [1]}
    assert LessonSource.model_validate(source).model_dump() == source
    assert ReviewIssue.model_validate(issue).model_dump() == issue
    metadata = csv_metadata().model_copy(update={"source_format": "pdf", "source_filename": "source.pdf"})
    assert "source_format" not in metadata.model_dump()
