"""Real PPTX extraction, slide policies, retained rendering and citation round trips."""

import io
import json
import zipfile
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pptx import Presentation
from pptx.util import Inches
from reportlab.pdfgen.canvas import Canvas
from test_a1 import FakeEmbedder
from test_source_verifier import PASSING, Client, response, verifier

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.documents import IngestionMetadata
from upraise_ai.schemas.lessons import ReviewIssue, ReviewReport
from upraise_ai.services.formats import FORMAT_MIMES, get_processor, upload_format
from upraise_ai.services.lesson_pipeline import plan_sections
from upraise_ai.services.powerpoint_processor import (
    PowerPointProcessor,
    render_powerpoint,
    validate_powerpoint_input,
)
from upraise_ai.services.source_references import (
    display_citations,
    display_review,
    lesson_source,
    model_citations,
)
from upraise_ai.storage.files import StagingStore


def powerpoint_bytes(*, visual=False):
    presentation = Presentation()
    for number in range(1, 4):
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = f'Graph lesson {number}'
        slide.placeholders[1].text = f'Each potential edge on slide {number} is present independently with probability p.'
        slide.notes_slide.notes_text_frame.text = f'Private speaker explanation for slide {number}.'
        if number == 2:
            slide._element.set('show', '0')
        if number == 3:
            table = slide.shapes.add_table(2, 2, Inches(1), Inches(4), Inches(6), Inches(1)).table
            table.cell(0, 0).text, table.cell(0, 1).text = 'Symbol', 'Meaning'
            table.cell(1, 0).text, table.cell(1, 1).text = 'p', 'Edge probability'
        if visual and number == 1:
            group = slide.shapes.add_group_shape()
            box = group.shapes.add_textbox(Inches(1), Inches(5), Inches(2), Inches(1))
            box.text = 'Visible group label'
    stream = io.BytesIO()
    presentation.save(stream)
    return stream.getvalue()


def powerpoint_metadata(**options):
    return IngestionMetadata(course_id='course-1', activity_id='activity-1', language='en',
        source_ref='pptx-fixture', content_hash='source-revision', source_filename='source.pptx',
        source_format='pptx', **options)


def fake_powerpoint_renderer(source, destination, settings):
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / 'source.pdf'
    canvas = Canvas(str(path))
    for number in range(1, 4):
        canvas.drawString(50, 750, f'Graph lesson {number}')
        canvas.drawString(50, 720, f'Each potential edge on slide {number} is present independently with probability p.')
        canvas.showPage()
    canvas.save()
    return path


def prepare(tmp_path, *, visual=False, **options):
    source = tmp_path / 'source.pptx'
    source.write_bytes(powerpoint_bytes(visual=visual))
    processor = PowerPointProcessor(Settings(describe_images=False), embedder=FakeEmbedder(1024),
                                    renderer=fake_powerpoint_renderer)
    document, vectors = processor.prepare(source, powerpoint_metadata(**options), 'document', tmp_path,
                                         uuid4(), lambda *_: None)
    return processor, document, vectors


def test_native_powerpoint_preserves_original_slide_numbers_and_checkpoint(tmp_path):
    processor, document, vectors = prepare(tmp_path)
    text = '\n'.join(c.text for c in document.chunks)
    assert 'Graph lesson 1' in text and 'Graph lesson 3' in text
    assert 'Graph lesson 2' not in text and 'Private speaker' not in text
    assert 'Edge probability' in text
    assert document.slide_count == 3 and document.page_count == 0
    assert {n for c in document.chunks for n in c.slide_numbers} == {1, 3}
    assert all(c.page_numbers == [] and c.source_format == 'pptx' for c in document.chunks)
    assert len(vectors) == len(document.chunks) and len({c.chunk_id for c in document.chunks}) == len(document.chunks)
    assert [c.chunk_index for c in document.chunks] == list(range(len(document.chunks)))
    manifest = json.loads((tmp_path / 'powerpoint-artifacts/slides.json').read_bytes())
    assert [s['slide_number'] for s in manifest['slides']] == [1, 3]
    assert (tmp_path / 'powerpoint-artifacts/slide-1.png').read_bytes().startswith(b'\x89PNG')
    assert not (tmp_path / 'powerpoint-artifacts/slide-2.png').exists()
    calls = processor.embedder.calls
    cached, _ = processor.prepare(tmp_path / 'source.pptx', powerpoint_metadata(), 'document', tmp_path,
                                  uuid4(), lambda *_: None)
    assert cached == document and processor.embedder.calls == calls


@pytest.mark.parametrize('notes,hidden', [(True, False), (False, True), (True, True)])
def test_notes_and_hidden_slides_are_explicit_source_choices(tmp_path, notes, hidden):
    _, document, _ = prepare(tmp_path, include_speaker_notes=notes, include_hidden_slides=hidden)
    text = '\n'.join(c.text for c in document.chunks)
    assert ('Private speaker explanation' in text) == notes
    assert ('Graph lesson 2' in text) == hidden
    if notes:
        assert 'Speaker notes:' in text
        assert any(ref['includes_speaker_notes'] for c in document.chunks for ref in c.slide_references)


def test_slide_windows_citations_and_review_keep_slide_provenance(tmp_path):
    _, document, _ = prepare(tmp_path)
    row = dict(document_id='document', job_id='job', active_attempt='attempt', metadata=powerpoint_metadata().model_dump(),
               result={'page_count': 0, 'source_block_count': 3, 'slide_count': 3})
    sections = plan_sections([(row, [c.model_dump(mode='json') for c in document.chunks])], 1)
    assert [s['pages'] for s in sections] == [[1], [3]]
    source = lesson_source(sections[-1], [3])
    assert source.pages == [] and source.slide_numbers == [3] and source.slide_references
    assert source.model_dump()['slide_numbers'] == [3]
    text = display_citations('Explanation [p. 3]', source)
    assert text == 'Explanation [Slide 3]'
    assert model_citations(text, source) == 'Explanation [p. 3]'
    with pytest.raises(IngestionError, match='LESSON_CITATIONS_INVALID'):
        display_citations('Unsupported [p. 2]', source)
    report = display_review(ReviewReport(approved=False, summary='Check',
        issues=[ReviewIssue(category='accuracy', message='Check [p. 3]', pages=[3])]), source)
    assert report.issues[0].pages == [] and report.issues[0].source_blocks == [3]
    assert report.issues[0].message == 'Check [Slide 3]'


@pytest.mark.parametrize('passed', [True, False])
def test_visual_description_requires_a_passing_comparison_against_the_rendered_slide(tmp_path, passed):
    source = tmp_path / 'source.pptx'
    source.write_bytes(powerpoint_bytes(visual=True))
    class Describer:
        def transcribe_slide(self, paths, **kwargs):
            return SimpleNamespace(description=kwargs['native_text'] + '\nThe group contains a visible group label.')
    client = Client(response({**PASSING, 'complete': passed}))
    document, _ = PowerPointProcessor(Settings(), embedder=FakeEmbedder(1024), renderer=fake_powerpoint_renderer,
        describer=Describer(), verifier=verifier(tmp_path, client)).prepare(source, powerpoint_metadata(), 'document', tmp_path,
                                                           uuid4(), lambda *_: None)
    text = '\n'.join(c.text for c in document.chunks)
    assert ('group contains a visible group label' in text) == passed
    contexts = [json.loads(c['messages'][0]['content'][-1]['text']) for c in client.calls]
    assert any(c['content_type'] == 'slide' and c['ordered_slide_references'] == ['slide:1'] for c in contexts)
    assert all(c['ordered_source_pages'] == [] for c in contexts)
    assert document.ingestion_readiness.status == ('COMPLETE' if passed else 'INCOMPLETE')
    assert 'Each potential edge' in text
    if not passed:
        assert 'POWERPOINT_VISUAL_REQUIRES_REVIEW' in document.warnings


def test_powerpoint_dispatch_archive_validation_and_receipt_retention(tmp_path):
    assert upload_format('SOURCE.PPTX', 'application/octet-stream') == 'pptx'
    assert isinstance(get_processor(FORMAT_MIMES['pptx'], Settings()), PowerPointProcessor)
    store, job = StagingStore(tmp_path), str(uuid4())
    source = store.stage(job, io.BytesIO(powerpoint_bytes()), 1000000, source_format='pptx')
    validate_powerpoint_input(source, Settings())
    with pytest.raises(IngestionError, match='POWERPOINT_TOO_MANY_SLIDES'):
        validate_powerpoint_input(source, Settings(max_powerpoint_slides=2))
    with pytest.raises(IngestionError, match='POWERPOINT_ARCHIVE_LIMIT_EXCEEDED'):
        validate_powerpoint_input(source, Settings().model_copy(update={'max_powerpoint_uncompressed_mb': 0}))
    folder = store.job_dir(job) / 'powerpoint-artifacts'
    folder.mkdir()
    (folder / 'slide-1.png').write_bytes(b'preview')
    store.purge_output(job)
    assert store.source_path(job) == source and source.is_file() and not folder.exists()


def test_invalid_or_macro_powerpoint_is_rejected_before_ingestion(tmp_path):
    source = tmp_path / 'source.pptx'
    source.write_bytes(b'%PDF-invalid')
    with pytest.raises(IngestionError, match='POWERPOINT_INVALID_DOCUMENT'):
        validate_powerpoint_input(source, Settings())
    source.write_bytes(powerpoint_bytes())
    with zipfile.ZipFile(source, 'a') as archive:
        archive.writestr('ppt/vbaProject.bin', b'macro')
    with pytest.raises(IngestionError, match='POWERPOINT_MACROS_UNSUPPORTED'):
        validate_powerpoint_input(source, Settings())


def test_real_impress_rendering_keeps_hidden_slide_ordinals(tmp_path):
    source = tmp_path / 'source.pptx'
    raw = powerpoint_bytes()
    source.write_bytes(raw)
    from pypdf import PdfReader
    rendered = render_powerpoint(source, tmp_path / 'rendered', Settings())
    pages = PdfReader(rendered).pages
    assert len(pages) == 3 and 'Graph lesson 2' in pages[1].extract_text()
    assert source.read_bytes() == raw


def test_real_chart_deck_renders_and_uses_a_source_checked_visual_description(tmp_path):
    from pptx.chart.data import ChartData
    from pptx.enum.chart import XL_CHART_TYPE

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.title.text = 'Measured edges'
    data = ChartData()
    data.categories = ['low', 'high']
    data.add_series('Count', [10, 20])
    slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(2), Inches(7), Inches(4), data)
    source = tmp_path / 'chart.pptx'
    presentation.save(source)
    client = Client()
    describer = SimpleNamespace(transcribe_slide=lambda *args, **kwargs:
        SimpleNamespace(description=kwargs['native_text'] + '\nThe Count chart shows low: 10 and high: 20.'))
    result, _ = PowerPointProcessor(Settings(), embedder=FakeEmbedder(1024), describer=describer,
        verifier=verifier(tmp_path, client)).prepare(source, powerpoint_metadata(), 'document', tmp_path, uuid4(), lambda *_: None)
    assert result.slide_count == 1 and client.calls and result.ingestion_readiness.status == 'COMPLETE'
    assert any('low: 10 and high: 20' in c.text for c in result.chunks)
    assert (tmp_path / 'powerpoint-artifacts/slide-1.png').read_bytes().startswith(b'\x89PNG')
