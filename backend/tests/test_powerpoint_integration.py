"""PPTX uses the real ingestion, source-index, generation, revision and removal lifecycle."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from test_a1 import FakeEmbedder
from test_a3 import request_data
from test_a3_integration import api, process
from test_b1_integration import b1_api
from test_b4 import Speech, Teacher
from test_csv_integration import upload_api
from test_powerpoint_processor import fake_powerpoint_renderer, powerpoint_bytes, powerpoint_metadata
from test_source_verifier import Client, verifier

from upraise_ai.main import app
from upraise_ai.schemas.aligned_lessons import AlignedReview, AlignedTexts
from upraise_ai.schemas.lessons import ReviewReport, SectionDraft
from upraise_ai.services.powerpoint_processor import PowerPointProcessor
from upraise_ai.workflows.interrupts import InterruptService

pytest_plugins = ['test_a1_integration', 'test_a3_integration', 'test_b1']


@pytest.mark.parametrize('aligned', [False, True])
@pytest.mark.parametrize('partial', [False, True])
def test_powerpoint_upload_preview_generation_revision_and_delete(integration, lesson, monkeypatch, aligned, partial):
    raw = powerpoint_bytes()
    fields = powerpoint_metadata(include_speaker_notes=True).model_dump(
        exclude={'source_filename', 'source_format', 'include_speaker_notes', 'include_hidden_slides'}, exclude_none=True)
    fields.update(include_speaker_notes='true', include_hidden_slides='false')
    with upload_api(integration, lesson) as client:
        def submit(values=fields):
            return client.post('/v1/documents', data=values, files={'file': ('lesson.pptx', raw, 'application/octet-stream')},
                               headers={'Idempotency-Key': 'powerpoint-one'})
        response = submit()
        assert response.status_code == 202, response.text
        doc = response.json()
        assert submit().json() == doc
        assert submit({**fields, 'include_speaker_notes': 'false'}).status_code == 409
        assert submit({**fields, 'include_speaker_notes': 'maybe'}).status_code == 422
    settings, jobs, _, vectors, worker, _ = integration
    worker.processor = None
    monkeypatch.setattr('upraise_ai.workers.ingestion.get_processor', lambda mime, cfg:
        PowerPointProcessor(cfg, embedder=FakeEmbedder(cfg.embedding_dimension), renderer=fake_powerpoint_renderer,
            describer=SimpleNamespace(transcribe_slide=lambda *args, **kwargs: SimpleNamespace(description=kwargs['native_text'])),
            verifier=verifier(settings.data_dir, Client())))
    worker.process(jobs.claim(settings.lease_seconds, settings.pipeline_signature))
    source = jobs.get(doc['job_id'])
    assert source['status'] == 'DONE', source['error_code']
    assert source['result']['slide_count'] == 3 and source['result']['page_count'] == 0
    assert source['result']['ingestion_readiness']['status'] == 'COMPLETE'
    indexed = vectors.document_sections(doc['document_id'], 'course-1', source['active_attempt'])
    assert indexed and {n for c in indexed for n in c['slide_numbers']} == {1, 3}
    version = indexed[0]['document_version_id']
    with upload_api(integration, lesson) as client:
        path = f"/v1/documents/{doc['document_id']}/slides/1?document_version_id={version}"
        preview = client.get(path)
        assert preview.status_code == 200, preview.text if preview.status_code != 200 else ''
        assert preview.headers['content-type'] == 'image/png' and preview.content.startswith(b'\x89PNG')
        assert client.get(path.replace('/slides/1', '/slides/2')).status_code == 404
        assert client.get(path.replace(version, str(uuid4()))).status_code == 404
        listing = client.get('/v1/documents').json()
        assert next(d for d in listing if d['document_id'] == doc['document_id'])['ingestion_readiness']['status'] == 'COMPLETE'

    # Admission must reject incomplete and legacy source results for both APIs.
    for readiness in (None, {'status': 'INCOMPLETE', 'blocking_slides': [3], 'issues': ['POWERPOINT_SOURCE_REQUIRES_REVIEW']}):
        changed = {**source['result'], 'ingestion_readiness': readiness}
        with jobs.connect() as conn:
            conn.execute('UPDATE ingestion_jobs SET result=%s WHERE job_id=%s', (Jsonb(changed), doc['job_id']))
        with api(integration, lesson) as client:
            rejected = client.post('/v2/lesson-jobs' if aligned else '/v1/lesson-jobs',
                                   json=request_data(document_ids=[doc['document_id']]))
            assert rejected.status_code == 409 and rejected.json()['detail'] == 'POWERPOINT_INGESTION_REVIEW_REQUIRED'
    generation_source = {**source['result']}
    if partial:
        generation_source['ingestion_readiness'] = {'status': 'INCOMPLETE', 'blocking_slides': [1], 'issues': []}
    with jobs.connect() as conn:
        conn.execute('UPDATE ingestion_jobs SET result=%s WHERE job_id=%s', (Jsonb(generation_source), doc['job_id']))

    def generate(section, language, *args, **kwargs):
        assert section['source_format'] == 'pptx' and section['pages'] == [1, 3]
        assert bool(section.get('ingestion_readiness')) == partial
        return SectionDraft(paragraphs=[{'text': 'Each potential edge is present independently. [p. 1]', 'pages': [1]}]), ReviewReport(approved=True, summary='Checked.'), 0
    monkeypatch.setattr(lesson[5], 'generate_section', generate)
    monkeypatch.setattr(lesson[5], 'adapt_units', lambda section, units, language, **kwargs:
        (AlignedTexts(units=[{'content_unit_id': u.content_unit_id, 'text': u.material_text} for u in units]),
         {str(u.content_unit_id): ReviewReport(approved=True, summary='Checked.') for u in units}), raising=False)
    with api(integration, lesson) as client:
        body = request_data(document_ids=[doc['document_id']])
        if partial:
            body['allow_incomplete_source'] = True
        generation_path = '/v2/lesson-jobs' if aligned else '/v1/lesson-jobs'
        response = client.post('/v2/lesson-jobs' if aligned else '/v1/lesson-jobs',
                               json=body, headers={'Idempotency-Key': 'pptx-generate'})
        assert response.status_code == 202, response.text
        retry = client.post(generation_path, json={**body, 'allow_incomplete_source': partial},
                            headers={'Idempotency-Key': 'pptx-generate'})
        assert retry.status_code == 202 and retry.json() == response.json()
        if not partial:
            changed = client.post(generation_path, json={**body, 'allow_incomplete_source': True},
                                  headers={'Idempotency-Key': 'pptx-generate'})
            assert changed.status_code == 409 and changed.json()['detail'] == 'IDEMPOTENCY_KEY_CONFLICT'
    generation = lesson[1].claim(settings.lease_seconds, [lesson[0].lesson_signature, lesson[0].lesson_v2_signature])
    lesson[2].process(generation)
    root = lesson[1].get(generation['job_id'])
    assert root['status'] == 'DONE', root['error_code']
    assert root['metadata'].get('allow_incomplete_source', False) == partial
    result = lesson[3].result(root)
    variants = result.variants if aligned else {'intermediate': result}
    for variant in variants.values():
        chunk = variant.chunks[0]
        assert '[Slide 1]' in chunk.text and '[p.' not in chunk.text
        assert chunk.sources[0].pages == [] and chunk.sources[0].slide_numbers == [1]
        assert chunk.sources[0].slide_references and '[Slide' not in chunk.ssml
        if partial:
            assert not chunk.reviewer_report.approved
            assert any(i.category == 'source' and 'available ingested content' in i.message
                       for i in chunk.reviewer_report.issues)
    teacher = Teacher()
    teacher.answer = 'An explanation supported by the slide. [Slide 1]'
    first = variants['intermediate']
    with b1_api(integration, lesson) as client:
        started = client.post('/v1/sessions', json={'course_id': first.course_id, 'activity_id': first.activity_id,
            'lesson_id': first.lesson_id, 'lesson_job_id': str(root['job_id']), 'learner_level': first.learner_level,
            'consent_scope': ['questions']})
        assert started.status_code == 201, started.text
        app.state.interrupts = InterruptService(app.state.lessons.settings.model_copy(update={
            'environment': 'development', 'local_test_mode': True}), app.state.lessons, app.state.sessions, vectors,
            teacher_factory=lambda *args: teacher, speech_factory=Speech,
            embeddings=SimpleNamespace(embed_query=lambda query: [1.0] + [0.0] * (settings.embedding_dimension - 1)))
        reply = client.post(f"/v1/sessions/{started.json()['session_id']}/interrupts", json={
            'request_id': str(uuid4()), 'chunk_id': first.chunks[0].chunk_id, 'text': 'Explain this slide.'})
        assert reply.status_code == 200, reply.text
        assert reply.json()['sources'][0]['slide_numbers'] == [1]
        assert reply.json()['sources'][0]['pages'] == []
        assert reply.json()['sources'][0]['slide_references']
    def revise(section, original, feedback, neighbours, **kwargs):
        assert section['source_format'] == 'pptx' and '[p. 1]' in original
        return 'A clearer slide explanation. [p. 1]', ReviewReport(approved=True, summary='Checked.'), 0
    monkeypatch.setattr(lesson[5], 'revise_chunk', revise, raising=False)
    if aligned:
        lesson[5].transport = SimpleNamespace(language='en', teaching_prompt=None)
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
        assert variant.chunks[0].text == 'A clearer slide explanation. [Slide 1]'
        assert variant.chunks[0].sources[0].slide_numbers == [1]
        if partial:
            assert any(i.category == 'source' and 'available ingested content' in i.message
                       for i in variant.chunks[0].reviewer_report.issues)
    with upload_api(integration, lesson) as client:
        assert client.delete('/v1/documents/' + doc['document_id']).status_code == 204
        assert client.get(path).status_code == 404
        assert vectors.document_count(doc['document_id'], 'course-1') == 0
    assert worker.staging.source_path(doc['job_id']).read_bytes() == raw
