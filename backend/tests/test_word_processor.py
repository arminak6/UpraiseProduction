"""Word structure, rendering, immutable source and source-block citations."""
import io
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from docx import Document
from test_a1 import FakeEmbedder, pdf_bytes

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.documents import DocumentElement, ElementType, IngestionMetadata
from upraise_ai.schemas.lessons import ReviewIssue, ReviewReport
from upraise_ai.services.formats import FORMAT_MIMES, get_processor, upload_format
from upraise_ai.services.lesson_pipeline import plan_sections
from upraise_ai.services.source_references import (
    display_citations,
    display_review,
    lesson_source,
    model_citations,
)
from upraise_ai.services.word_processor import (
    WordProcessor,
    convert_word,
    recover_native_paragraphs,
    validate_word_input,
)
from upraise_ai.storage.files import StagingStore


def word_bytes():
    doc = Document()
    doc.add_heading('Random graphs', level=1)
    doc.add_paragraph('Each potential edge is present independently with probability p.')
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = 'Symbol', 'Meaning'
    table.cell(1, 0).text, table.cell(1, 1).text = 'p', 'Edge probability'
    stream = io.BytesIO()
    doc.save(stream)
    return stream.getvalue()


def word_metadata(source_format='docx'):
    return IngestionMetadata(course_id='course-1', activity_id='activity-1', language='en',
        source_ref='word-fixture', content_hash='source-revision', source_filename='source.' + source_format,
        source_format=source_format)


def fake_renderer(source, destination, target, settings):
    assert target == 'pdf'
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / (source.stem + '.pdf')
    path.write_bytes(pdf_bytes('Random graphs Each potential edge is present independently with probability p.'))
    return path


def test_native_word_structure_checkpoint_and_citations(tmp_path):
    path = tmp_path / 'source.docx'
    raw = word_bytes()
    path.write_bytes(raw)
    embedder = FakeEmbedder(1024)
    processor = WordProcessor(Settings(), embedder=embedder, renderer=fake_renderer)
    doc, vectors = processor.prepare(path, word_metadata(), 'document', tmp_path, uuid4(), lambda *_: None)
    assert path.read_bytes() == raw and len(vectors) == len(doc.chunks)
    assert any(e.element_type.value == 'heading' for e in doc.elements)
    assert any(e.element_type.value == 'table' and 'Edge probability' in e.original_text for e in doc.elements)
    assert all(c.page_numbers == [] and c.source_format == 'docx' and c.word_references for c in doc.chunks)
    assert doc.page_count == 0 and doc.source_block_count == len(doc.chunks)
    assert (tmp_path / 'word-artifacts/render-audit.json').is_file()
    row = dict(document_id='document', job_id='job', active_attempt='attempt', metadata=word_metadata().model_dump(),
               result={'page_count': 0, 'source_block_count': len(doc.chunks)})
    sections = plan_sections([(row, [c.model_dump(mode='json') for c in doc.chunks])], 5)
    assert sum(len(s['chunks']) for s in sections) == len(doc.chunks)
    source = lesson_source(sections[0], sections[0]['pages'])
    shown = display_citations('Explanation [p. 1]', source)
    assert shown == 'Explanation [Word blocks 1-1]' and model_citations(shown, source) == 'Explanation [p. 1]'
    report = display_review(ReviewReport(approved=False, summary='Check',
        issues=[ReviewIssue(category='accuracy', message='Check [p. 1]', pages=[1])]), source)
    assert report.issues[0].pages == [] and report.issues[0].source_blocks == [1]
    assert '[Word blocks' in report.issues[0].message
    assert processor.prepare(path, word_metadata(), 'document', tmp_path, uuid4(), lambda *_: None)[0] == doc
    assert embedder.calls == 1
    with pytest.raises(IngestionError, match='LESSON_CITATIONS_INVALID'):
        display_citations('Outside [p. 999]', source)


@pytest.mark.parametrize('extension', ['doc', 'docx'])
def test_word_dispatch_and_receipt_retains_original(tmp_path, extension):
    assert upload_format('source.' + extension.upper(), 'application/octet-stream') == extension
    assert isinstance(get_processor(FORMAT_MIMES[extension], Settings()), WordProcessor)
    store, job = StagingStore(tmp_path), str(uuid4())
    source = store.stage(job, io.BytesIO(b'original'), 100, source_format=extension)
    (store.job_dir(job) / 'temporary.json').write_text('{}')
    store.purge_output(job)
    assert source.read_bytes() == b'original' and store.source_path(job) == source


@pytest.mark.parametrize('raw,extension', [(b'%PDF-1.7', 'docx'), (b'not-a-word-document', 'doc')])
def test_reject_wrong_word_bytes(tmp_path, raw, extension):
    path = tmp_path / ('source.' + extension)
    path.write_bytes(raw)
    with pytest.raises(IngestionError, match='WORD_INVALID_DOCUMENT'):
        validate_word_input(path, Settings(), extension)


def test_word_archive_limits(tmp_path):
    path = tmp_path / 'source.docx'
    path.write_bytes(word_bytes())
    with pytest.raises(IngestionError, match='WORD_ARCHIVE_LIMIT_EXCEEDED'):
        validate_word_input(path, Settings().model_copy(update={'max_word_uncompressed_mb': 0}), 'docx')


def test_real_word_rendering_and_legacy_conversion(tmp_path):
    source = tmp_path / 'source.docx'
    source.write_bytes(word_bytes())
    pdf = convert_word(source, tmp_path / 'pdf', 'pdf', Settings())
    assert pdf.read_bytes().startswith(b'%PDF-')
    legacy = convert_word(source, tmp_path / 'legacy', 'doc', Settings())
    validate_word_input(legacy, Settings(), 'doc')
    restored = convert_word(legacy, tmp_path / 'restored', 'docx', Settings())
    validate_word_input(restored, Settings(), 'docx')
    assert Document(restored).paragraphs[0].text == 'Random graphs'
    doc, _ = WordProcessor(Settings(), embedder=FakeEmbedder(1024)).prepare(
        legacy, word_metadata('doc'), 'document', tmp_path, uuid4(), lambda *_: None)
    assert doc.metadata.source_format == 'doc' and all(c.source_format == 'doc' for c in doc.chunks)
    assert json.loads((tmp_path / 'word-artifacts/render-audit.json').read_text())['rendered_page_count'] > 0


def test_recover_clipped_native_definition_without_duplicate_existing_text(tmp_path):
    source = tmp_path / 'source.docx'
    source.write_bytes(word_bytes())
    rendered = [DocumentElement(element_id='heading', element_index=0, element_type=ElementType.HEADING,
                               original_text='Random graphs'),
                DocumentElement(element_id='clipped', element_index=1, element_type=ElementType.TEXT,
                               original_text='Each potential edge is present')]
    elements, recovered = recover_native_paragraphs(source, rendered, 'source-hash')
    assert recovered == 1
    extra = elements[-1]
    assert extra.original_text == 'Each potential edge is present independently with probability p.'
    assert extra.quality.status.value == 'NEEDS_REVIEW' and extra.source_reference.endswith('p[2]')
    assert [e.element_index for e in elements] == list(range(len(elements)))


def test_native_word_figure_enters_retrieval_only_after_bound_picture_verification(tmp_path):
    from PIL import Image

    image = tmp_path / 'diagram.png'
    Image.new('RGB', (80, 60), 'blue').save(image)
    doc = Document(io.BytesIO(word_bytes()))
    doc.add_picture(str(image))
    source = tmp_path / 'source.docx'
    doc.save(source)
    class Describer:
        def describe(self, path, **kwargs):
            return SimpleNamespace(description='A blue rectangular diagram.')
    calls = []
    class Verifier:
        def verify_source(self, paths, **kwargs):
            assert len(paths) == 1 and paths[0].is_file()
            assert kwargs['page_numbers'] == []
            assert kwargs['source_references'][0].startswith('#/pictures/')
            calls.append(kwargs)
            return SimpleNamespace(passed=True)
    result, _ = WordProcessor(Settings(word_visual_fallback=False), embedder=FakeEmbedder(1024),
        renderer=fake_renderer, describer=Describer(), verifier=Verifier()).prepare(
            source, word_metadata(), 'document', tmp_path, uuid4(), lambda *_: None)
    assert calls and any('blue rectangular diagram' in c.text for c in result.chunks)
    assert all(c.page_numbers == [] for c in result.chunks)
