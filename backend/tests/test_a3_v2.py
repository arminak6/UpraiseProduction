"""Aligned prompts, hard bounds and extensible profiles, without cloud calls."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_a3 import request_data
from test_a3_model_compat import Converse

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.aligned_lessons import AlignedLessonInput, AlignedTexts, ContentUnit
from upraise_ai.services.aligned_models import (
    AlignmentIdsError,
    UnitSizeError,
    adapt_units,
    provider_schema,
    structured,
    validate_material_format,
)
from upraise_ai.services.lesson_models import LessonModels


def unit():
    return ContentUnit(content_unit_id=uuid4(), index=0, section_index=0, source_language='en',
        material_text=r'There are 12 nodes. The degree is \(k=3\). [p. 1]',
        source={'document_id': str(uuid4()), 'document_version_id': 'version', 'pages': [1],
                'source_chunk_ids': ['source'], 'quality_status': 'AUTO_CHECKED'})


def candidate(item, text=None):
    return json.dumps({'units': [{'content_unit_id': str(item.content_unit_id), 'text': text or item.material_text}]})


def review(item, accepted=True):
    return json.dumps({'units': [{'content_unit_id': str(item.content_unit_id), 'material_preserved': accepted,
        'level_suitable': True, 'in_language': True, 'issues': [] if accepted else ['Missing facts.'],
        'revision_instructions': [] if accepted else ['Restore facts.']}]})


def invoke(replies, item):
    client = Converse(replies)
    models = LessonModels(Settings(), client)
    value = adapt_units(models, {'chunks': [{'text': item.material_text, 'page_numbers': [1]}]}, [item], 'en',
        learner_level='beginner', profiles=models.settings.lesson_level_profiles, target_words=30, max_words=50)
    return value, client


def test_v2_request_generates_configured_levels_not_one_level():
    data = AlignedLessonInput.model_validate(request_data())
    assert data.levels is None
    assert data.options.max_chunk_words == 500
    for options in ({'learner_level': 'beginner'}, {'target_chunk_words': 201, 'max_chunk_words': 200}):
        with pytest.raises(ValidationError):
            AlignedLessonInput.model_validate(request_data(options=options))
    settings = Settings(lesson_level_profiles={'foundation': 'Explain prerequisites.', 'expert': 'Explain formally.'},
                        lesson_plan_level='foundation')
    assert settings.lesson_v2_signature != Settings().lesson_v2_signature
    with pytest.raises(ValidationError):
        Settings(lesson_level_profiles={'expert': 'Explain formally.'})


@pytest.mark.parametrize('levels', [[], ['beginner', 'beginner'], [''], ['Advanced'], [12],
                                   ['level_' + str(index) for index in range(9)], 'beginner'])
def test_v2_rejects_invalid_level_selections(levels):
    with pytest.raises(ValidationError):
        AlignedLessonInput.model_validate(request_data(levels=levels))


def test_v2_accepts_single_subset_and_custom_level_selections():
    for levels in (['beginner'], ['beginner', 'intermediate'], ['foundation', 'expert']):
        assert AlignedLessonInput.model_validate(request_data(levels=levels)).levels == levels


def test_original_flow_retains_approved_draft_without_editor_or_fourth_review():
    item = unit()
    (texts, reports), client = invoke([candidate(item), review(item)], item)
    assert texts.units[0].text == item.material_text and reports[str(item.content_unit_id)].approved
    assert len(client.calls) == 2
    assert client.calls[1]['modelId'] == Settings().lesson_review_model_id
    assert 'without an independent post-edit material review' in reports[str(item.content_unit_id)].summary
    for call in client.calls:
        assert str(item.content_unit_id) in json.dumps(call['messages'])
        assert 'beginner' in json.dumps(call['system'])


@pytest.mark.parametrize('failure', ['wrong_id', 'citation'])
def test_alignment_failures_do_not_become_delivered_material(failure):
    item = unit()
    final = candidate(item)
    if failure == 'wrong_id':
        final = candidate(item).replace(str(item.content_unit_id), str(uuid4()))
    else:
        final = candidate(item, item.material_text.replace('[p. 1]', '[p. 2]'))
    with pytest.raises(IngestionError):
        invoke([candidate(item), review(item, False), final, final] if failure == 'wrong_id'
               else [candidate(item), review(item, False), final], item)


def test_oversize_editor_gets_one_shortening_attempt_then_shared_plan_refinement():
    item = unit()
    long = candidate(item, item.material_text + ' explanation' * 60)
    with pytest.raises(UnitSizeError):
        invoke([candidate(item), review(item, False), long, long], item)


def test_current_shortening_payload_is_json_serializable():
    from upraise_ai.schemas.aligned_lessons import AlignedTexts
    from upraise_ai.services.aligned_models import structured
    item = unit()
    values = AlignedTexts.model_validate_json(candidate(item))
    fake = SimpleNamespace(teacher=SimpleNamespace(_structured_output_config=lambda **kw: kw,
        _converse_text=lambda **kw: candidate(item), _parse_structured_model=lambda raw, schema: schema.model_validate_json(raw)),
        settings=Settings())
    assert structured(fake, 'test', {'current': values.model_dump(mode='json')}, AlignedTexts, 'test') == values


@pytest.mark.parametrize('formula', [r'$ k = 3 $', r'$$k=3$$', r'\(k = 3\)', r'\[k=3\]'])
def test_equivalent_delimiters_spacing_and_fewer_repetitions_are_accepted(formula):
    item = unit().model_copy(update={'material_text': r'The degree is \(k=3\). Compare \(k=3\). [p. 1]'})
    text = f'The degree is {formula}, which we compare. [p. 1]'
    (texts, _), client = invoke([candidate(item, text), review(item)], item)
    assert texts.units[0].text == text and len(client.calls) == 2


def test_math_layout_is_normalized_without_rewriting_the_delivered_text():
    item = unit().model_copy(update={'material_text': r'Use \(\left(x + y\right)\,=z\). [p. 1]'})
    text = r'Use $(x+y)=z$. [p. 1]'
    assert validate_material_format(item, text) == text


@pytest.mark.parametrize('original, equivalent', [
    (r'\(H_{0}\)', r'\(H_0\)'),
    (r'\(k=3\)', r'\(k={3}\)'),
    (r'\(n\) and \(p\)', r'\(n,p\)'),
    (r'\(\frac{a}{b}\)', r'\(\dfrac{a}{b}\)'),
    (r'\(cc(G)\ \text{conforms with the distribution of }cc(\tilde G)\)',
     r'\(cc(G)\) follows the same distribution as \(cc(\tilde{G})\)'),
    (r'\(k=3\)', 'k equals three'),
])
def test_original_math_review_allows_equivalent_notation_regrouping_and_prose(original, equivalent):
    item = unit().model_copy(update={'material_text': f'Use {original}. [p. 1]'})
    text = f'Use {equivalent}. [p. 1]'
    (texts, _), client = invoke([candidate(item, text), review(item)], item)
    assert texts.units[0].text == text
    assert len(client.calls) == 2
    judge = json.loads(client.calls[1]['messages'][0]['content'][0]['text'])
    assert judge['units'][0]['material_text'] == item.material_text
    assert judge['candidates']['units'][0]['text'] == text
    assert judge['source_context'][0]['text'] == item.material_text


@pytest.mark.parametrize('original, changed', [
    (r'\(k=3\)', r'$k=4$'),
    (r'\(k=3\)', r'$n=3$'),
    (r'\(k=3\)', r'$k>3$'),
    (r'\(x+y\)', r'$x-y$'),
    (r'\(\alpha n\)', r'$\alphan$'),
    (r'\(\text{not independent}\)', r'$\text{notindependent}$'),
    (r'\(\text{group {a b}}\)', r'$\text{group {ab}}$'),
])
def test_original_source_review_routes_changed_math_to_editor_and_retains_findings(original, changed):
    item = unit().model_copy(update={'material_text': f'Use {original}. [p. 1]'})
    bad = candidate(item, f'Use {changed}. [p. 1]')
    (texts, reports), client = invoke([bad, review(item, False), candidate(item)], item)
    assert texts.units[0].text == item.material_text
    assert len(client.calls) == 3
    assert not reports[str(item.content_unit_id)].approved
    assert reports[str(item.content_unit_id)].issues[0].message == 'Missing facts.'
    editor = json.loads(client.calls[2]['messages'][0]['content'][0]['text'])
    assert editor['draft']['units'][0]['text'] == json.loads(bad)['units'][0]['text']
    assert editor['review']['units'][0]['material_preserved'] is False


def test_editor_applies_source_review_without_an_additional_blocking_review():
    item = unit()
    bad = candidate(item, 'There are 12 nodes. The degree is $k=4$. [p. 1]')
    final = candidate(item, 'There are 12 nodes. The degree k equals three. [p. 1]')
    (texts, reports), client = invoke([bad, review(item, False), final], item)
    editor = json.loads(client.calls[2]['messages'][0]['content'][0]['text'])
    assert editor['review']['units'][0]['material_preserved'] is False
    assert 'formula_corrections' not in editor
    assert texts.units[0].text == json.loads(final)['units'][0]['text']
    assert len(client.calls) == 3
    assert not reports[str(item.content_unit_id)].approved
    assert reports[str(item.content_unit_id)].revision_instructions == ['Restore facts.']


def test_review_findings_are_not_promoted_to_final_approval_after_editing(caplog):
    item = unit()
    bad = candidate(item, item.material_text + ' PRIVATE_CONTRADICTION The degree is also $k=4$.')
    client = Converse([bad, review(item, False), candidate(item)])
    models = LessonModels(Settings(), client)
    texts, reports = models.adapt_units({'chunks': [{'text': item.material_text}]}, [item], 'en', learner_level='beginner',
        profiles=models.settings.lesson_level_profiles, target_words=30, max_words=50)
    assert texts.units[0].text == item.material_text
    assert len(client.calls) == 3
    assert not reports[str(item.content_unit_id)].approved
    assert reports[str(item.content_unit_id)].issues
    assert 'PRIVATE_CONTRADICTION' not in caplog.text and '$k=4$' not in caplog.text


@pytest.mark.parametrize('field', ['material_preserved', 'level_suitable', 'in_language', 'issues', 'revision_instructions'])
def test_draft_review_flags_are_preserved_with_valid_reports_and_editor_context(field):
    item = unit()
    verdict = json.loads(review(item))
    verdict['units'][0][field] = ['Unresolved problem.'] if field in {'issues', 'revision_instructions'} else False
    (texts, reports), client = invoke([candidate(item), json.dumps(verdict), candidate(item)], item)
    assert texts.units[0].text == item.material_text and len(client.calls) == 3
    report = reports[str(item.content_unit_id)]
    assert not report.approved and report.issues
    editor = json.loads(client.calls[2]['messages'][0]['content'][0]['text'])
    assert editor['review'] == verdict
    if field == 'revision_instructions':
        assert report.revision_instructions == ['Unresolved problem.']


def test_plain_math_uses_original_delimiter_recovery_for_delivered_text():
    from upraise_ai.services.v3.math_speech_v4 import auto_delimit_math_for_speech, has_unwrapped_math
    plain = 'The probability is p = 0.5. [p. 1]'
    item = unit().model_copy(update={'material_text': plain})
    expected = auto_delimit_math_for_speech(plain)
    assert expected != plain and not has_unwrapped_math(expected)
    (texts, _), client = invoke([candidate(item, plain), review(item)], item)
    assert texts.units[0].text == expected
    assert len(client.calls) == 2


def test_unusual_math_keeps_original_speech_recovery_path_and_content_free_log(monkeypatch, caplog):
    item = unit()
    text = 'Private material remains for speech recovery. [p. 1]'
    monkeypatch.setattr('upraise_ai.services.aligned_models.has_unwrapped_math', lambda text: True)
    assert validate_material_format(item, text) == text
    assert 'needs speech recovery' in caplog.text
    assert 'Private material' not in caplog.text


@pytest.mark.parametrize('enabled', [False, True])
def test_failed_alignment_capture_remains_opt_in_and_logs_content_free(tmp_path, caplog, enabled):
    item = unit()
    settings = Settings(environment='development', local_test_mode=True, lesson_capture_model_outputs=enabled)
    folder = tmp_path / 'diagnostics'
    bad = candidate(item, 'PRIVATE_ALIGNMENT_OUTPUT The degree is $k=4$. [p. 2]')
    models = LessonModels(settings, Converse([candidate(item), review(item, False), bad]), diagnostic_dir=folder)
    with pytest.raises(IngestionError, match='LESSON_CITATIONS_INVALID'):
        models.adapt_units({'chunks': [{'text': item.material_text}]}, [item], 'en', learner_level='beginner',
            profiles=settings.lesson_level_profiles, target_words=30, max_words=50)
    assert folder.exists() == enabled
    if enabled:
        diagnostic = json.loads(next(folder.glob('*.json')).read_text())
        assert diagnostic['phase'] == 'alignment' and diagnostic['raw_output'] == bad
    assert 'PRIVATE_ALIGNMENT_OUTPUT' not in caplog.text and '$k=4$' not in caplog.text


def test_native_review_schema_requires_all_properties_but_local_bounds_remain():
    from upraise_ai.schemas.aligned_lessons import AlignedReview
    native = provider_schema(AlignedReview.model_json_schema())
    entry = native['$defs']['UnitReview']
    assert set(entry['required']) == set(entry['properties'])
    assert {'issues', 'revision_instructions'} <= set(entry['required'])
    assert 'default' not in json.dumps(native) and 'minItems' not in json.dumps(native)


@pytest.mark.parametrize('both_truncated', [False, True])
def test_truncation_retries_with_bounded_budget_and_never_accepts_partial_text(both_truncated, caplog):
    from upraise_ai.schemas.aligned_lessons import AlignedTexts
    item = unit()
    truncated = {'stopReason': 'max_tokens', 'output': {'message': {'content': [{'text': candidate(item)}]}}}
    client = Converse([truncated, truncated if both_truncated else candidate(item)])
    models = LessonModels(Settings(), client)
    if both_truncated:
        with pytest.raises(IngestionError, match='LESSON_ALIGNMENT_INCOMPLETE') as failed:
            structured(models, 'test', {'units': [item.model_dump(mode='json')]}, AlignedTexts,
                       'aligned_unit_draft', tokens=8000)
        assert failed.value.stage == 'aligned_unit_draft' and failed.value.reason == 'MAX_TOKENS'
        assert failed.value.__cause__ is not None
    else:
        result = structured(models, 'test', {'units': [item.model_dump(mode='json')]}, AlignedTexts,
                            'aligned_unit_draft', tokens=8000)
        assert result.units[0].content_unit_id == item.content_unit_id
    assert [call['inferenceConfig']['maxTokens'] for call in client.calls] == [8000, 16000]
    assert 'reason=MAX_TOKENS' in caplog.text


@pytest.mark.parametrize('provider_code, expected', [
    ('AccessDeniedException', ('AWS_REQUEST_REJECTED', False)),
    ('ThrottlingException', ('AWS_TEMPORARY_FAILURE', True)),
])
def test_provider_errors_retain_real_classification_and_do_not_capture_stale_output(provider_code, expected, caplog):
    from botocore.exceptions import ClientError

    from upraise_ai.schemas.aligned_lessons import AlignedTexts
    from upraise_ai.workers.ingestion import classify_error
    error = ClientError({'Error': {'Code': provider_code, 'Message': 'PRIVATE_PROVIDER_ERROR'}}, 'Converse')
    models = LessonModels(Settings(), Converse([error, error]))
    models.transport.raw = 'PRIVATE_PREVIOUS_OUTPUT'
    with pytest.raises(ClientError) as failed:
        structured(models, 'test', {}, AlignedTexts, 'aligned_unit_draft')
    assert classify_error(failed.value) == expected
    assert models.transport.raw == '' and models.transport.stop_reason is None
    assert 'reason=PROVIDER_ERROR' in caplog.text
    assert 'PRIVATE_PROVIDER_ERROR' not in caplog.text and 'PRIVATE_PREVIOUS_OUTPUT' not in caplog.text


def test_schema_failure_reports_stage_and_reason_without_private_content(caplog):
    from upraise_ai.schemas.aligned_lessons import AlignedTexts
    bad = json.dumps({'units': [{'content_unit_id': 'PRIVATE_INVALID_ID', 'text': 'PRIVATE_RESPONSE_TEXT'}]})
    models = LessonModels(Settings(), Converse([bad, bad]))
    with pytest.raises(IngestionError, match='LESSON_ALIGNMENT_MODEL_INVALID') as failed:
        structured(models, 'test', {}, AlignedTexts, 'aligned_unit_final')
    assert failed.value.stage == 'aligned_unit_final' and failed.value.reason == 'JSON_SCHEMA'
    assert failed.value.__cause__ is not None
    assert 'stage=aligned_unit_final' in caplog.text and 'reason=JSON_SCHEMA' in caplog.text
    assert 'PRIVATE_INVALID_ID' not in caplog.text and 'PRIVATE_RESPONSE_TEXT' not in caplog.text


@pytest.mark.parametrize('stage', ['draft', 'review', 'final', 'shorten'])
def test_unit_identity_is_corrected_at_each_model_stage_before_continuing(stage, caplog):
    item = unit()
    wrong = item.model_copy(update={'content_unit_id': uuid4()})
    if stage == 'draft':
        replies = [candidate(wrong), candidate(item), review(item)]
    elif stage == 'review':
        replies = [candidate(item), review(wrong), review(item)]
    elif stage == 'final':
        replies = [candidate(item), review(item, False), candidate(wrong), candidate(item)]
    else:
        oversized = candidate(item, item.material_text + ' explanation' * 60)
        replies = [oversized, review(item), candidate(wrong), candidate(item)]
    (texts, reports), client = invoke(replies, item)
    assert texts.units[0].content_unit_id == item.content_unit_id
    assert texts.units[0].text == item.material_text
    assert reports[str(item.content_unit_id)].approved == (stage != 'final')
    assert len(client.calls) == len(replies)
    failed_call = 0 if stage == 'draft' else 1 if stage == 'review' else 2
    correction = client.calls[failed_call + 1]
    prompt = correction['messages'][0]['content'][0]['text']
    assert 'Previous response failure: UNIT_IDS_INVALID' in prompt
    assert 'Regenerate the complete batch' in prompt
    system = '\n'.join(block['text'] for block in correction['system'])
    assert 'Required content_unit_id sequence: ["' + str(item.content_unit_id) + '"]' in system
    assert 'stage=aligned_unit_' + stage in caplog.text


def identity_batch(failure):
    first = unit()
    second = unit().model_copy(update={'index': 1, 'material_text': 'There are 24 edges. [p. 1]'})
    expected = [first, second]
    good = {'units': [{'content_unit_id': str(item.content_unit_id), 'text': item.material_text} for item in expected]}
    bad = json.loads(json.dumps(good))
    if failure == 'wrong_id':
        bad['units'][0]['content_unit_id'] = str(uuid4())
    elif failure == 'missing':
        bad['units'].pop()
    elif failure == 'duplicate':
        bad['units'][1] = dict(bad['units'][0])
    else:
        bad['units'].reverse()
    for item in bad['units']:
        item['text'] = 'PRIVATE_REJECTED_UNIT_TEXT'
    return expected, json.dumps(good), json.dumps(bad)


@pytest.mark.parametrize('failure', ['wrong_id', 'missing', 'duplicate', 'reordered'])
def test_invalid_batch_identity_retries_and_keeps_only_the_complete_corrected_response(failure, caplog):
    expected, good, bad = identity_batch(failure)
    client = Converse([bad, good])
    models = LessonModels(Settings(), client)
    texts = structured(models, 'test', {'units': [item.model_dump(mode='json') for item in expected]},
                       AlignedTexts, 'aligned_unit_draft', expected_units=expected)
    assert [item.content_unit_id for item in texts.units] == [item.content_unit_id for item in expected]
    assert [item.text for item in texts.units] == [item.material_text for item in expected]
    assert len(client.calls) == 2
    assert 'PRIVATE_REJECTED_UNIT_TEXT' not in caplog.text
    assert all(str(item.content_unit_id) not in caplog.text for item in expected)


@pytest.mark.parametrize('failure, counts', [
    ('wrong_id', {'received_units': 2, 'missing_units': 1, 'unexpected_units': 1,
                  'duplicate_units': 0, 'order_mismatch': False}),
    ('missing', {'received_units': 1, 'missing_units': 1, 'unexpected_units': 0,
                 'duplicate_units': 0, 'order_mismatch': False}),
    ('duplicate', {'received_units': 2, 'missing_units': 1, 'unexpected_units': 0,
                   'duplicate_units': 1, 'order_mismatch': False}),
    ('reordered', {'received_units': 2, 'missing_units': 0, 'unexpected_units': 0,
                   'duplicate_units': 0, 'order_mismatch': True}),
])
def test_persistent_identity_errors_fail_with_bounded_calls_and_content_free_stage_counts(failure, counts, caplog):
    expected, good, bad = identity_batch(failure)
    client = Converse([bad, bad, good])
    models = LessonModels(Settings(), client)
    with pytest.raises(AlignmentIdsError, match='LESSON_ALIGNMENT_IDS_INVALID') as failed:
        structured(models, 'test', {'units': [item.model_dump(mode='json') for item in expected]},
                   AlignedTexts, 'aligned_unit_final', expected_units=expected)
    assert failed.value.retryable
    assert failed.value.diagnostics == {'stage': 'aligned_unit_final', 'reason': 'UNIT_IDS_INVALID',
                                       'expected_units': 2, **counts}
    assert len(client.calls) == 2
    assert 'stage=aligned_unit_final' in caplog.text and 'reason=UNIT_IDS_INVALID' in caplog.text
    assert 'PRIVATE_REJECTED_UNIT_TEXT' not in caplog.text
    assert all(str(item.content_unit_id) not in caplog.text for item in expected)


@pytest.mark.parametrize('format_failure', ['truncation', 'syntax'])
@pytest.mark.parametrize('corrected', [False, True])
def test_format_fallback_can_still_correct_identity_once_without_an_unbounded_retry_loop(format_failure, corrected):
    expected, good, bad = identity_batch('wrong_id')
    initial = ({'stopReason': 'max_tokens', 'output': {'message': {'content': [{'text': good}]}}}
               if format_failure == 'truncation' else 'PRIVATE_BROKEN_RESPONSE')
    client = Converse([initial, bad, good if corrected else bad, good])
    models = LessonModels(Settings(), client)

    def generate():
        return structured(models, 'test', {'units': [item.model_dump(mode='json') for item in expected]},
                          AlignedTexts, 'aligned_unit_draft', expected_units=expected)

    if corrected:
        texts = generate()
        assert [item.content_unit_id for item in texts.units] == [item.content_unit_id for item in expected]
        assert [item.text for item in texts.units] == [item.material_text for item in expected]
    else:
        with pytest.raises(AlignmentIdsError, match='LESSON_ALIGNMENT_IDS_INVALID'):
            generate()
    assert len(client.calls) == 3
    budget = 16000 if format_failure == 'truncation' else 8000
    assert [call['inferenceConfig']['maxTokens'] for call in client.calls] == [8000, budget, budget]
    assert 'Previous response failure: UNIT_IDS_INVALID' in client.calls[2]['messages'][0]['content'][0]['text']
