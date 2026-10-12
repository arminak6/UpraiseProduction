"""Bundle persistence, selected-level runtime and revisions with invented course data."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_a2_integration import submit
from test_a3 import request_data
from test_a3_integration import api, process
from test_a3_model_compat import Converse
from test_b1_integration import b1_api

from upraise_ai.core.errors import IngestionError
from upraise_ai.main import app
from upraise_ai.schemas.aligned_lessons import AlignedLessonInput, AlignedTexts
from upraise_ai.schemas.lessons import ReviewReport, SectionDraft
from upraise_ai.services.aligned_models import UnitSizeError
from upraise_ai.services.lesson_models import LessonModels

pytest_plugins = ['test_a1_integration', 'test_a3_integration', 'test_b1']


def accept_bundle(integration, lesson, monkeypatch, *, oversized=False, profiles=None, fail_level=None, language='en'):
    doc = submit(integration)
    integration[4].process(integration[1].claim(integration[0].lease_seconds))
    original = lesson[5].generate_section
    monkeypatch.setattr(lesson[5], 'generate_section', lambda *args, level_profiles=None, **kwargs: original(*args, **kwargs))
    adapted = []

    def adapt(section, units, language, *, learner_level, profiles, **kwargs):
        adapted.append((learner_level, [str(unit.content_unit_id) for unit in units]))
        if learner_level == fail_level:
            raise IngestionError('LESSON_ALIGNMENT_IDS_INVALID', retryable=True)
        if oversized and any(len(unit.material_text.split()) > 20 for unit in units):
            raise UnitSizeError(next(unit.content_unit_id for unit in units if len(unit.material_text.split()) > 20))
        return AlignedTexts(units=[{'content_unit_id': unit.content_unit_id,
            'text': unit.material_text + (' This means the same thing.' if learner_level == 'beginner' else '')}
            for unit in units]), {str(unit.content_unit_id): ReviewReport(approved=True, summary='Synthetic material checked.')
                                 for unit in units}

    monkeypatch.setattr(lesson[5], 'adapt_units', adapt, raising=False)
    if language == 'it':
        from test_a3_translation import TranslationClient
        from test_a3_translation_integration import ItalianPolly

        from upraise_ai.services.lesson_translation import LessonTranslator
        translator = LessonTranslator(lesson[0], TranslationClient())
        monkeypatch.setattr(lesson[5], 'translate_section', lambda draft, source, target, *, learner_level:
                            translator.translate(draft, source, target, learner_level), raising=False)
        lesson[2].speech.client = ItalianPolly()
    if profiles:
        settings = lesson[0].model_copy(update={'lesson_level_profiles': profiles, 'lesson_plan_level': next(iter(profiles))})
        lesson[2].settings = lesson[3].settings = settings
    data = AlignedLessonInput.model_validate(request_data(document_ids=[doc['document_id']], language=language,
        voice='Bianca' if language == 'it' else 'Joanna',
        options={'target_chunk_words': 30, 'max_chunk_words': 50}))
    with api(integration, lesson) as client:
        response = client.post('/v2/lesson-jobs', json=data.model_dump(mode='json'), headers={'Idempotency-Key': 'bundle-one'})
        assert response.status_code == 202, response.text
    settings = lesson[2].settings
    synthesize = lesson[2].speech.synthesize
    def checked_synthesize(parts, voice, code):
        saved = lesson[1].prepared_chunks(response.json()['job_id'])
        levels = lesson[3].settings.lesson_level_profiles
        assert {chunk['level_key'] for chunk in saved} == set(levels)
        assert lesson[1].get_plan(response.json()['job_id'])
        assert any(chunk['ssml_parts'] == parts for chunk in saved)
        return synthesize(parts, voice, code)
    monkeypatch.setattr(lesson[2].speech, 'synthesize', checked_synthesize)
    row = lesson[1].claim(settings.lease_seconds, [settings.lesson_signature, settings.lesson_v2_signature])
    lesson[2].process(row)
    monkeypatch.setattr(lesson[2].speech, 'synthesize', synthesize)
    row = lesson[1].get(row['job_id'])
    if fail_level is None:
        assert row['status'] == 'DONE', row['error_code']
    return row, data, adapted


def test_bundle_has_equal_count_units_distinct_audio_and_durable_pg(integration, lesson, monkeypatch):
    row, data, adapted = accept_bundle(integration, lesson, monkeypatch)
    bundle = lesson[3].result(row)
    assert list(bundle.variants) == ['beginner', 'intermediate', 'advanced']
    assert len(bundle.chunks) == bundle.chunk_count * 3
    assert row['result']['total_chunk_count'] == len(bundle.chunks)
    assert row['result']['chunk_count'] == bundle.chunk_count
    assert lesson[1].get_plan(row['job_id'])['chunk_plan_id'] == str(bundle.chunk_plan_id)
    saved = lesson[1].prepared_chunks(row['job_id'])
    assert len(saved) == len(bundle.chunks)
    for level, variant in bundle.variants.items():
        assert [chunk.content_unit_id for chunk in variant.chunks] == bundle.content_unit_ids
        stored = lesson[1].prepared_chunks(row['job_id'], level)
        assert [chunk['text'] for chunk in stored] == [chunk.text for chunk in variant.chunks]
        assert all(chunk['ssml'] and chunk['content_unit_id'] for chunk in stored)
    assert len({chunk.chunk_id for chunk in bundle.chunks}) == len(bundle.chunks)
    assert bundle.variants['beginner'].chunks[0].text != bundle.variants['advanced'].chunks[0].text
    with api(integration, lesson) as client:
        path = f"/v1/jobs/{row['job_id']}"
        assert client.get(path + '/result').json()['chunk_count'] == bundle.chunk_count
        selected = client.get(path + '/result', params={'learner_level': 'beginner'})
        assert selected.status_code == 200 and selected.json()['learner_level'] == 'beginner'
        assert 'variants' not in selected.json()
        assert client.get(path + '/result', params={'learner_level': 'other'}).status_code == 404
        catalog = client.get('/v1/lesson-jobs', params={'limit': 1}).json()
        assert len(catalog) == 1
        page2 = client.get('/v1/lesson-jobs', params={'limit': 1, 'offset': 1}).json()
        assert catalog[0]['learner_level'] != page2[0]['learner_level']
        for chunk in bundle.chunks:
            assert client.get(chunk.audio_url).content.startswith(b'RIFF')
        assert client.post('/v2/lesson-jobs', json=data.model_dump(mode='json'),
                           headers={'Idempotency-Key': 'bundle-one'}).json()['job_id'] == str(row['job_id'])
        assert client.delete(path, params={'reason': 'RECEIVED'}).status_code == 204
        assert client.get(path + '/result').status_code == 410
    assert len(lesson[1].prepared_chunks(row['job_id'])) == len(saved)
    assert lesson[1].get_plan(row['job_id'])


def test_session_requires_level_and_binds_only_its_chunks(integration, lesson, monkeypatch):
    row, data, _ = accept_bundle(integration, lesson, monkeypatch)
    bundle = lesson[3].result(row)
    body = {key: data.model_dump(mode='json')[key] for key in ('course_id', 'activity_id', 'lesson_id')}
    body.update(lesson_job_id=str(row['job_id']), session_id=str(uuid4()), consent_scope=['telemetry'])
    with b1_api(integration, lesson) as client:
        assert client.post('/v1/sessions', json=body).status_code == 422
        body['learner_level'] = 'beginner'
        response = client.post('/v1/sessions', json=body)
        assert response.status_code == 201, response.text
        session = app.state.sessions.get(body['session_id'], row['client_id'])
        assert session.chunk_ids == tuple(chunk.chunk_id for chunk in bundle.variants['beginner'].chunks)
        assert session.binding.level_claim_required
        from fastapi import HTTPException

        from upraise_ai.schemas.interrupts import InterruptInput
        from upraise_ai.workflows.interrupts import InterruptService
        interrupts = InterruptService(lesson[0], lesson[3], app.state.sessions, integration[3], stt=object())
        request = InterruptInput(request_id=uuid4(), chunk_id=session.chunk_ids[0], text='Explain this more simply.')
        _, selected, stored_text, _ = interrupts._lesson(session, request, lesson[4])
        assert selected.chunk_id == session.chunk_ids[0] and stored_text == bundle.variants['beginner'].chunks[0].text
        with pytest.raises(HTTPException) as denied:
            interrupts._lesson(session, request.model_copy(update={'chunk_id': bundle.variants['advanced'].chunks[0].chunk_id}), lesson[4])
        assert denied.value.status_code == 404
        payload = {'lesson_id': data.lesson_id, 'chunk_id': bundle.variants['advanced'].chunks[0].chunk_id,
                   'cognitive_load_score': 0.3}
        assert client.post(f"/v1/sessions/{body['session_id']}/chunk-end", json=payload).status_code == 404
        assert client.post(f"/v1/sessions/{body['session_id']}/end", json={'reason': 'COMPLETED'}).status_code == 204


def test_shared_size_refinement_rebuilds_every_level(integration, lesson, monkeypatch):
    row, _, _ = accept_bundle(integration, lesson, monkeypatch, oversized=True)
    bundle = lesson[3].result(row)
    assert all(len(chunk.text.split()) <= 50 for chunk in bundle.chunks)
    assert all([chunk.content_unit_id for chunk in variant.chunks] == bundle.content_unit_ids
               for variant in bundle.variants.values())


def test_future_level_profiles_are_snapshot_configuration(integration, lesson, monkeypatch):
    row, _, _ = accept_bundle(integration, lesson, monkeypatch,
                             profiles={'foundation': 'Plain steps.', 'expert': 'Formal reasoning.'})
    assert list(lesson[3].result(row).variants) == ['foundation', 'expert']


def test_translation_preserves_units_and_target_language_audio(integration, lesson, monkeypatch):
    row, _, _ = accept_bundle(integration, lesson, monkeypatch, language='it')
    bundle = lesson[3].result(row)
    assert bundle.language == 'it' and bundle.voice == 'Bianca'
    assert bundle.versions['translation_applied'] == 'true'
    assert all('Una frase didattica supportata.' in chunk.text for chunk in bundle.chunks)
    assert all('LanguageCode' in call and call['LanguageCode'] == 'it-IT' for call in lesson[2].speech.client.calls)


def test_signed_sessions_bind_level_for_start_and_followup(integration, lesson, monkeypatch, signing):
    from test_b1 import assertion_settings
    from test_b1_integration import headers
    row, data, _ = accept_bundle(integration, lesson, monkeypatch)
    body = {key: data.model_dump(mode='json')[key] for key in ('course_id', 'activity_id', 'lesson_id')}
    body.update(lesson_job_id=str(row['job_id']), learner_level='beginner')
    config = assertion_settings().model_dump(include={'session_jwt_issuer', 'session_jwt_jwks_url', 'oauth_audience'})
    with b1_api(integration, lesson, local=False, **config) as client:
        assert client.post('/v1/sessions', json=body, headers=headers(signing, row, body)).status_code == 403
        token = headers(signing, row, body, learner_level='beginner')
        response = client.post('/v1/sessions', json=body, headers=token)
        assert response.status_code == 201, response.text
        session_id = response.json()['session_id']
        wrong = headers(signing, row, body, learner_level='advanced', session_id=session_id)
        assert client.post(f'/v1/sessions/{session_id}/end', json={'reason': 'COMPLETED'}, headers=wrong).status_code == 403
        assert client.post(f'/v1/sessions/{session_id}/end', json={'reason': 'COMPLETED'}, headers=token).status_code == 204


def test_failed_variant_does_not_publish_partial_bundle_or_start_speech(integration, lesson, monkeypatch):
    row, _, _ = accept_bundle(integration, lesson, monkeypatch, fail_level='advanced')
    assert row['status'] == 'QUEUED' and row['error_code'] == 'LESSON_ALIGNMENT_IDS_INVALID'
    assert not lesson[1].prepared_chunks(row['job_id'])
    assert lesson[1].get_plan(row['job_id']) is None
    assert not lesson[2].speech.client.calls
    assert row['result'] is None


@pytest.mark.parametrize('needs_edit', [False, True])
@pytest.mark.parametrize('wire_mode', ['valid', 'escape', 'truncation'])
def test_native_original_flow_publishes_aligned_drafts_with_review_findings(
        integration, lesson, monkeypatch, needs_edit, wire_mode):
    doc = submit(integration)
    integration[4].process(integration[1].claim(integration[0].lease_seconds))
    settings, jobs, worker, service, principal, models = lesson

    def canonical(section, *args, **kwargs):
        page = section['pages'][0]
        return (SectionDraft(paragraphs=[{'text':
            rf'The degree is \(k=3\). The null hypothesis is \(H_{{0}}\). [p. {page}]', 'pages': [page]}]),
            ReviewReport(approved=True, summary='Invented material checked.'), 0)

    calls = []

    def adapt(section, units, language, **kwargs):
        page = units[0].source.pages[0]
        def texts(text):
            return json.dumps({'units': [{'content_unit_id': str(unit.content_unit_id), 'text': text}
                                         for unit in units]})
        review = json.dumps({'units': [{'content_unit_id': str(unit.content_unit_id),
            'material_preserved': True, 'level_suitable': True, 'in_language': True,
            'issues': [], 'revision_instructions': []} for unit in units]})
        bad = texts(f'The degree is $k=4$.\nThe null hypothesis is $H_0$. [p. {page}]')
        good = texts(f'The degree k equals three.\nThe null hypothesis is $H_0$. [p. {page}]')
        draft = bad if needs_edit else good
        draft = draft.replace(r'\n', '\n') if wire_mode == 'escape' else draft
        rejected = json.loads(review)
        for unit in rejected['units']:
            unit.update(material_preserved=False, issues=['Changed degree.'], revision_instructions=['Restore the correct degree.'])
        replies = [draft, json.dumps(rejected) if needs_edit else review]
        if needs_edit:
            replies.append(good)
        if wire_mode == 'truncation':
            replies.insert(0, {'stopReason': 'max_tokens',
                              'output': {'message': {'content': [{'text': good}]}}})
        client = Converse(replies)
        calls.append(client)
        return LessonModels(settings, client).adapt_units(section, units, language, **kwargs)

    monkeypatch.setattr(models, 'generate_section', canonical)
    monkeypatch.setattr(models, 'adapt_units', adapt, raising=False)
    data = AlignedLessonInput.model_validate(request_data(document_ids=[doc['document_id']],
        options={'target_chunk_words': 30, 'max_chunk_words': 50}))
    response = service.submit(data, principal, None)
    row = jobs.claim(settings.lease_seconds, [settings.lesson_signature, settings.lesson_v2_signature])
    worker.process(row)
    row = jobs.get(row['job_id'])
    assert str(row['job_id']) == response['job_id']
    assert row['status'] == 'DONE', row['error_code']
    bundle = service.result(row)
    assert bundle.status == 'DRAFT'
    assert len(calls) == len(settings.lesson_level_profiles)
    expected_calls = 2 + int(needs_edit) + int(wire_mode == 'truncation')
    assert all(len(client.calls) == expected_calls for client in calls)
    assert all([chunk.content_unit_id for chunk in variant.chunks] == bundle.content_unit_ids
               for variant in bundle.variants.values())
    assert all('k equals three' in chunk.text and '$H_0$' in chunk.text and 'k=4' not in chunk.text for chunk in bundle.chunks)
    assert all(chunk['ssml'] for chunk in jobs.prepared_chunks(row['job_id']))
    assert bundle.versions['math_validation'] == 'source-review-math-markup-v1'
    assert bundle.versions['review_strategy'] == 'original-three-role-v1'
    assert bundle.versions['post_edit_material_review'] == 'deferred'
    assert 'math_repair_attempts' not in bundle.versions
    assert bundle.versions['response_format'] == 'aligned-json-strings-v1'
    assert worker.speech.client.calls
    if needs_edit:
        assert row['result']['requires_review'] and not row['result']['review_approved']
        assert all(not chunk.reviewer_report.approved for chunk in bundle.chunks)
        assert all(any(issue.message == 'Changed degree.' for issue in chunk.reviewer_report.issues)
                   for chunk in bundle.chunks)
        assert all('Restore the correct degree.' in chunk.reviewer_report.revision_instructions
                   for chunk in bundle.chunks)


def test_plan_is_immutable_and_repeatable_migrations_preserve_all_rows(integration, lesson, monkeypatch):
    row, _, _ = accept_bundle(integration, lesson, monkeypatch)
    saved = lesson[1].prepared_chunks(row['job_id'])
    lesson[1].migrate()
    lesson[1].migrate()
    assert lesson[1].prepared_chunks(row['job_id']) == saved
    plan = lesson[1].get_plan(row['job_id'])
    with lesson[1].connect() as conn:
        with pytest.raises(IngestionError, match='PLAN_CONFLICT'):
            from upraise_ai.schemas.aligned_lessons import ChunkPlan
            edited = ChunkPlan.model_validate(plan)
            edited.units[0].material_text = 'Different material.'
            lesson[1].save_plan(conn, row['job_id'], edited)


def test_a7_revises_selected_level_preserving_plan_and_parent(integration, lesson, monkeypatch):
    from upraise_ai.schemas.aligned_lessons import AlignedReview
    row, _, _ = accept_bundle(integration, lesson, monkeypatch)
    lesson[5].transport = SimpleNamespace(language='en', teaching_prompt=None)
    original = lesson[3].result(row)
    chunk = original.variants['beginner'].chunks[1]
    monkeypatch.setattr(lesson[5], 'revise_chunk', lambda section, text, *args, **kwargs:
                        (text + ' A clearer explanation.', ReviewReport(approved=True, summary='Checked.'), 1), raising=False)
    monkeypatch.setattr('upraise_ai.services.revision_pipeline.reviewed', lambda *args:
        AlignedReview(units=[{'content_unit_id': chunk.content_unit_id, 'material_preserved': True,
                             'level_suitable': True, 'in_language': True}]))
    monkeypatch.setattr(lesson[2], 'failure', lambda job, error: (_ for _ in ()).throw(error))
    with api(integration, lesson) as client:
        response = client.post('/v1/audio:regenerate', json={'lesson_job_id': str(row['job_id']),
            'chunk_id': chunk.chunk_id, 'teacher_review': 'Explain this more clearly.'})
        assert response.status_code == 202, response.text
        revised_row = process(lesson)
        assert revised_row['status'] == 'DONE', revised_row['error_code']
        revised = lesson[3].result(revised_row)
        assert revised.learner_level == 'beginner'
        assert revised.chunk_plan_id == original.chunk_plan_id
        assert [part.content_unit_id for part in revised.chunks] == original.content_unit_ids
        assert revised.chunks[1].text != chunk.text
        assert lesson[3].result(row) == original
        assert lesson[1].get_plan(revised.job_id) == lesson[1].get_plan(row['job_id'])
