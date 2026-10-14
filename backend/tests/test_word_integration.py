"""Word uploads reuse durable A1/A3/A5 and deletion flows with isolated stores."""
from types import SimpleNamespace

import pytest
from test_a1 import FakeEmbedder
from test_a3 import request_data
from test_a3_integration import api, process
from test_csv_integration import upload_api
from test_word_processor import fake_renderer, word_bytes, word_metadata

from upraise_ai.schemas.aligned_lessons import AlignedReview, AlignedTexts
from upraise_ai.schemas.lessons import ReviewReport, SectionDraft
from upraise_ai.services.word_processor import WordProcessor

pytest_plugins = ['test_a1_integration', 'test_a3_integration']


@pytest.mark.parametrize('aligned', [False, True])
def test_word_upload_ingestion_generation_revision_and_deletion(integration, lesson, monkeypatch, aligned):
    raw = word_bytes()
    with upload_api(integration, lesson) as client:
        def submit(content=raw):
            return client.post('/v1/documents', data=word_metadata().model_dump(
                exclude={'source_filename', 'source_format'}, exclude_none=True),
                files={'file': ('lesson.docx', content, 'application/octet-stream')},
                headers={'Idempotency-Key': 'word-one'})
        response = submit()
        assert response.status_code == 202, response.text
        doc = response.json()
        assert submit().json() == doc
        assert submit(b'wrong bytes').status_code == 422
    settings, jobs, _, vectors, worker, _ = integration
    worker.processor = None
    monkeypatch.setattr('upraise_ai.workers.ingestion.get_processor', lambda mime, cfg:
        WordProcessor(cfg, embedder=FakeEmbedder(cfg.embedding_dimension), renderer=fake_renderer))
    worker.process(jobs.claim(settings.lease_seconds, settings.pipeline_signature))
    source = jobs.get(doc['job_id'])
    assert source['status'] == 'DONE', source['error_code']
    assert source['result']['source_format'] == 'docx' and source['result']['page_count'] == 0
    indexed = vectors.document_sections(doc['document_id'], 'course-1', source['active_attempt'])
    assert indexed and all(c['word_references'] and c['page_numbers'] == [] for c in indexed)
    def generate(section, language, *args, **kwargs):
        assert section['source_format'] == 'docx'
        return SectionDraft(paragraphs=[{'text': 'Each potential edge is present independently. [p. 1]',
                                         'pages': [1]}]), ReviewReport(approved=True, summary='Checked.'), 0
    monkeypatch.setattr(lesson[5], 'generate_section', generate)
    monkeypatch.setattr(lesson[5], 'adapt_units', lambda section, units, language, **kwargs:
        (AlignedTexts(units=[{'content_unit_id': u.content_unit_id, 'text': u.material_text} for u in units]),
         {str(u.content_unit_id): ReviewReport(approved=True, summary='Checked.') for u in units}), raising=False)
    with api(integration, lesson) as client:
        response = client.post('/v2/lesson-jobs' if aligned else '/v1/lesson-jobs',
                               json=request_data(document_ids=[doc['document_id']]))
        assert response.status_code == 202, response.text
    generation = lesson[1].claim(settings.lease_seconds, [lesson[0].lesson_signature, lesson[0].lesson_v2_signature])
    lesson[2].process(generation)
    root = lesson[1].get(generation['job_id'])
    assert root['status'] == 'DONE', root['error_code']
    result = lesson[3].result(root)
    variants = result.variants if aligned else {'intermediate': result}
    for variant in variants.values():
        chunk = variant.chunks[0]
        assert '[Word blocks 1-1]' in chunk.text and '[p.' not in chunk.text
        assert chunk.sources[0].pages == [] and chunk.sources[0].word_references
        assert '[Word blocks' not in chunk.ssml
    models = lesson[5]
    def revise(section, original, feedback, neighbours, **kwargs):
        assert section['source_format'] == 'docx' and '[p. 1]' in original
        return 'A clearer Word explanation. [p. 1]', ReviewReport(approved=True, summary='Checked.'), 0
    monkeypatch.setattr(models, 'revise_chunk', revise, raising=False)
    if aligned:
        models.transport = SimpleNamespace(language='en', teaching_prompt=None)
        monkeypatch.setattr('upraise_ai.services.revision_pipeline.reviewed', lambda models, section, units, *args:
            AlignedReview(units=[{'content_unit_id': u.content_unit_id, 'material_preserved': True,
                'level_suitable': True, 'in_language': True, 'issues': [], 'revision_instructions': []} for u in units]))
    with api(integration, lesson) as client:
        response = client.post('/v1/audio:regenerate', json={'lesson_job_id': str(root['job_id']),
            'chunk_id': variants['intermediate'].chunks[0].chunk_id, 'teacher_review': 'Explain clearly.',
            'apply_to_all_levels': aligned})
        assert response.status_code == 202, response.text
    revised = process(lesson)
    assert revised['status'] == 'DONE', revised['error_code']
    current = lesson[3].result(lesson[1].get(root['job_id']))
    for variant in (current.variants.values() if aligned else [current]):
        assert variant.chunks[0].text == 'A clearer Word explanation. [Word blocks 1-1]'
    with upload_api(integration, lesson) as client:
        assert client.delete('/v1/documents/' + doc['document_id']).status_code == 204
        assert vectors.document_count(doc['document_id'], 'course-1') == 0
    assert worker.staging.source_path(doc['job_id']).read_bytes() == raw
