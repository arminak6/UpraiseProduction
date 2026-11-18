"""General layout/evidence regressions, using the production verifier contract."""

import io
import json
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt
from test_a1 import FakeEmbedder
from test_a3 import request_data
from test_powerpoint_processor import powerpoint_metadata
from test_source_verifier import Client, crop, response, verifier

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.services.lesson_pipeline import LessonPipeline
from upraise_ai.services.powerpoint_layout import slide_inventory
from upraise_ai.services.powerpoint_processor import PowerPointProcessor, native_elements
from upraise_ai.services.powerpoint_vision import PowerPointImageDescriber
from upraise_ai.services.source_readiness import require_complete_powerpoint


def box(slide, text, x, y, w=3, h=.4):
    shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    shape.text = text
    return shape


def test_ordinary_heading_columns_and_scrambled_xml_order_are_preserved(tmp_path):
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, deck.slide_width, deck.slide_height)
    box(slide, 'Right body: 200', 5.5, 2)
    box(slide, 'Left body: 100', .5, 2)
    box(slide, 'Right heading', 5.5, 1.4)
    box(slide, 'General coverage', .5, .3, 9).text_frame.paragraphs[0].runs[0].font.size = Pt(28)
    box(slide, 'Left heading', .5, 1.4)
    source = tmp_path / 'columns.pptx'
    deck.save(source)
    inventory = slide_inventory(source)[0]
    assert inventory['title'] == 'General coverage'
    elements = native_elements(inventory, 'hash')
    assert [e.original_text for e in elements] == [
        'General coverage', 'Left heading', 'Left body: 100', 'Right heading', 'Right body: 200']
    assert all(e.bounding_box and e.bounding_box.coordinate_origin == 'TOPLEFT' for e in elements)


def test_separate_headings_keep_their_values_despite_xml_z_order(tmp_path):
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    box(slide, 'First coverage', .5, 1, 9)
    box(slide, 'Second coverage', .5, 3, 9)
    box(slide, 'Annual limit 9000 per member', .5, 1.5, 9)
    box(slide, 'Annual limit 500 per member', .5, 3.5, 9)
    source = tmp_path / 'headings.pptx'
    deck.save(source)
    text = slide_inventory(source)[0]['native_text']
    assert text.index('9000') < text.index('Second coverage') < text.index('500')


def test_merged_table_and_scaled_group_keep_native_structure(tmp_path):
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    group = slide.shapes.add_group_shape()
    box(group, 'Lower paragraph', 1, 3)
    box(group, 'Upper paragraph', 1, 2)
    group.left, group.top = Inches(1), Inches(1)
    group.width = Inches(4)
    table = slide.shapes.add_table(3, 2, Inches(1), Inches(4), Inches(7), Inches(1.5)).table
    table.cell(0, 0).merge(table.cell(0, 1))
    table.cell(0, 0).text = 'Packages'
    table.cell(1, 0).text, table.cell(1, 1).text = 'Basic', '12.50'
    table.cell(2, 0).text, table.cell(2, 1).text = 'Extra', '21.75'
    source = tmp_path / 'group-table.pptx'
    deck.save(source)
    inventory = slide_inventory(source)[0]
    assert inventory['has_visual']
    assert inventory['native_text'].index('Upper') < inventory['native_text'].index('Lower')
    elements = native_elements(inventory, 'hash')
    table_element = next(e for e in elements if e.structured_content)
    assert 'colspan="2"' in table_element.structured_content
    assert table_element.original_text.count('Packages') == 1
    assert '12.50' in table_element.structured_content and '21.75' in table_element.structured_content


def test_picture_evidence_uses_native_resolution_and_only_visible_crop(tmp_path):
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    raw = io.BytesIO()
    image = Image.new('RGB', (200, 100), 'blue')
    image.paste('red', (0, 0, 100, 100))
    image.save(raw, format='PNG')
    picture = slide.shapes.add_picture(raw, Inches(1), Inches(1), width=Inches(3))
    picture.crop_left = .5
    source = tmp_path / 'picture.pptx'
    deck.save(source)
    inventory = slide_inventory(source, tmp_path)[0]
    assert inventory['has_visual'] and not inventory['issues']
    with Image.open(inventory['pictures'][0]['path']) as visible:
        assert visible.size == (100, 100) and visible.getpixel((0, 0)) == (0, 0, 255)


@pytest.mark.parametrize('mode', ['masked', 'covered', 'rotated', 'flipped'])
def test_native_picture_evidence_never_reveals_masked_or_covered_pixels(tmp_path, mode):
    from pptx.oxml.xmlchemy import OxmlElement
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    raw = io.BytesIO()
    Image.new('RGB', (200, 100), 'red').save(raw, format='PNG')
    picture = slide.shapes.add_picture(raw, Inches(1), Inches(1), width=Inches(3))
    if mode == 'masked':
        geometry = picture._element.spPr.find('{http://schemas.openxmlformats.org/drawingml/2006/main}prstGeom')
        if geometry is None:
            geometry = OxmlElement('a:prstGeom')
            picture._element.spPr.append(geometry)
        geometry.set('prst', 'ellipse')
    elif mode == 'covered':
        slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(1), Inches(1), Inches(1), Inches(1))
    elif mode == 'rotated':
        picture.rotation = 90
    else:
        picture._element.spPr.xfrm.set('flipH', '1')
    source = tmp_path / 'masked.pptx'
    deck.save(source)
    inventory = slide_inventory(source, tmp_path)[0]
    assert inventory['has_visual'] and not inventory['pictures']
    assert 'POWERPOINT_PICTURE_RENDER_ONLY' in inventory['evidence_warnings']


def test_complete_slide_mapping_reaches_actual_verifier_and_is_cached_by_source(tmp_path):
    client = Client()
    subject = verifier(tmp_path, client)
    paths = [crop(tmp_path), crop(tmp_path, 'picture.png', color='blue')]
    kwargs = dict(candidate='Package Basic: 12.50', content_type='slide', page_numbers=[],
                  slide_numbers=[7, 7], source_references=['slide:7', 'slide:7:shape:/4'])
    assert subject.verify_source(paths, **kwargs).passed
    assert subject.verify_source(paths, **kwargs).from_cache
    assert len(client.calls) == 1
    request = client.calls[0]
    context = json.loads(request['messages'][0]['content'][-1]['text'])
    assert context['ordered_slide_numbers'] == [7, 7] and context['ordered_source_pages'] == []
    assert 'exclusion' in request['system'][0]['text'] and 'heading/value' in request['system'][0]['text']
    kwargs['source_references'][-1] = 'slide:7:shape:/5'
    assert subject.verify_source(paths, **kwargs).passed and len(client.calls) == 2


@pytest.mark.parametrize('mapping', [
    {'page_numbers': [1], 'source_references': ['slide:1']},  # original integration defect
    {'page_numbers': [], 'slide_numbers': [True], 'source_references': ['slide:True']},
    {'page_numbers': [], 'slide_numbers': [2], 'source_references': ['slide:1']},
    {'page_numbers': [], 'slide_numbers': [1], 'source_references': []},
    {'page_numbers': [1], 'slide_numbers': [1], 'source_references': ['slide:1']},
])
def test_invalid_slide_mapping_fails_before_provider(tmp_path, mapping):
    client = Client()
    result = verifier(tmp_path, client).verify_source([crop(tmp_path)], candidate='Text', content_type='slide', **mapping)
    assert not result.passed and result.issues == ('incomplete_source_mapping',) and not client.calls


@pytest.mark.parametrize('text,stop_reason,accepted', [
    ('Package Basic: 12.50\nExclusion: item X.', 'end_turn', True),
    ('Package Basic: 12.50', 'max_tokens', False),
    ('Package Basic: [UNREADABLE]', 'end_turn', False),
])
def test_slide_transcription_is_complete_bounded_and_lossless(tmp_path, text, stop_reason, accepted):
    client = Client(response(text, stop_reason=stop_reason))
    subject = PowerPointImageDescriber(session=None, model_id='test', cache_dir=tmp_path / 'cache', client=client)
    path = crop(tmp_path, size=(2200, 900))
    outcome = subject.transcribe_slide([path], slide_number=1, source_references=['slide:1'], native_text='Basic')
    assert bool(outcome.description) == accepted
    data = client.calls[0]['messages'][0]['content'][0]['image']['source']['bytes']
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (2200, 900)
    assert client.calls[0]['inferenceConfig']['maxTokens'] == 4096
    if accepted:
        assert subject.transcribe_slide([path], slide_number=1, source_references=['slide:1'], native_text='Basic').from_cache


@pytest.mark.parametrize('result', [None, [], {'output': None}, {'output': {'message': None}},
    {'stopReason': 'end_turn', 'output': {'message': {'content': [{'toolUse': {}}]}}}])
def test_malformed_slide_transcription_stays_incomplete(tmp_path, result):
    client = Client()
    client.result = result
    subject = PowerPointImageDescriber(session=None, model_id='test', cache_dir=tmp_path / 'cache', client=client)
    outcome = subject.transcribe_slide([crop(tmp_path)], slide_number=1, source_references=['slide:1'], native_text='Basic')
    assert outcome.description is None and outcome.warning == 'POWERPOINT_TRANSCRIPTION_INCOMPLETE'


def test_verified_transcription_cannot_drop_native_numbers_or_use_stale_checkpoint(tmp_path):
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    box(slide, 'Coverage limit 500 per member', 1, 1)
    slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(1), Inches(2), Inches(2), Inches(1))
    source = tmp_path / 'numbers.pptx'
    deck.save(source)
    client = Client()
    processor = PowerPointProcessor(Settings(), embedder=FakeEmbedder(1024), verifier=verifier(tmp_path, client),
        describer=SimpleNamespace(transcribe_slide=lambda *args, **kwargs:
            SimpleNamespace(description='Coverage limit 50 per member')))
    result, _ = processor.prepare(source, powerpoint_metadata(), 'document', tmp_path, uuid4(), lambda *_: None)
    assert result.ingestion_readiness.status == 'INCOMPLETE'
    assert result.ingestion_readiness.blocking_slides == [1]
    assert 'POWERPOINT_TRANSCRIPTION_NATIVE_TEXT_MISSING' in result.warnings
    assert not client.calls  # reject dropped/changed native tokens before trusting a permissive model
    assert all('limit 50 per member' not in c.text for c in result.chunks)
    processor.describer = SimpleNamespace(transcribe_slide=lambda *args, **kwargs:
        SimpleNamespace(description=kwargs['native_text']))
    # New parser/pipeline identity must invalidate the old prepared snapshot.
    saved = json.loads((tmp_path / 'prepared.json').read_bytes())
    saved['document']['parser_version'] = 'docling-powerpoint-slides-v1'
    (tmp_path / 'prepared.json').write_text(json.dumps(saved))
    corrected, _ = processor.prepare(source, powerpoint_metadata(), 'document', tmp_path, uuid4(), lambda *_: None)
    assert corrected.ingestion_readiness.status == 'COMPLETE' and client.calls


@pytest.mark.parametrize('readiness', [None, {'status': 'INCOMPLETE', 'blocking_slides': [2], 'issues': []}])
def test_legacy_or_incomplete_pptx_blocks_new_generation_without_touching_other_formats(readiness):
    row = {'metadata': {'source_format': 'pptx'}, 'status': 'DONE', 'result': {'ingestion_readiness': readiness}}
    with pytest.raises(IngestionError, match='POWERPOINT_INGESTION_REVIEW_REQUIRED'):
        require_complete_powerpoint(row)
    row['result']['ingestion_readiness'] = {'status': 'COMPLETE'}
    require_complete_powerpoint(row)
    for format in ('pdf', 'csv', 'docx'):
        row['metadata']['source_format'] = format
        row['result'] = {}
        require_complete_powerpoint(row)


def test_generation_waiting_for_a1_rechecks_readiness_before_loading_vectors():
    data = request_data()
    row = {'metadata': {'source_format': 'pptx', 'course_id': 'course-1'}, 'client_id': 'owner',
           'status': 'DONE', 'active_attempt': 'attempt', 'deletion_requested_at': None, 'result': {}}
    @contextmanager
    def transaction(_):
        yield None, row
    pipeline = LessonPipeline(None, SimpleNamespace(document_transaction=transaction), None, None, None, None)
    with pytest.raises(IngestionError, match='POWERPOINT_INGESTION_REVIEW_REQUIRED'):
        pipeline.load_documents({'metadata': data, 'client_id': 'owner'})


def test_native_and_visual_fallback_can_recover_when_docling_cannot_parse_a_valid_deck(tmp_path):
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    box(slide, 'Native coverage 17,90 per month', 1, 1, 7)
    source = tmp_path / 'fallback.pptx'
    deck.save(source)
    class Unavailable:
        def convert(self, _):
            raise ValueError('unsupported object in the auxiliary parser')
    client = Client()
    processor = PowerPointProcessor(Settings(), embedder=FakeEmbedder(1024), converter=Unavailable(),
        verifier=verifier(tmp_path, client), describer=SimpleNamespace(transcribe_slide=lambda *args, **kwargs:
            SimpleNamespace(description=kwargs['native_text'])))
    result, _ = processor.prepare(source, powerpoint_metadata(), 'document', tmp_path, uuid4(), lambda *_: None)
    assert result.ingestion_readiness.status == 'COMPLETE'
    assert 'POWERPOINT_DOCLING_FALLBACK' in result.warnings and client.calls
    assert any('17,90' in c.text for c in result.chunks)


def test_rtl_columns_follow_the_source_reading_direction(tmp_path):
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    box(slide, 'العمود الأيسر', .5, 1)
    box(slide, 'العمود الأيمن', 5.5, 1)
    source = tmp_path / 'rtl.pptx'
    deck.save(source)
    inventory = slide_inventory(source)[0]
    assert inventory['reading_direction'] == 'rtl'
    assert inventory['native_text'].startswith('العمود الأيمن')
