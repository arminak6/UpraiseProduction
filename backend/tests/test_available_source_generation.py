"""Explicit partial-source choice, retained warnings, and unchanged source fences."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_a3 import request_data

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.aligned_lessons import AlignedLessonInput
from upraise_ai.schemas.lessons import LessonInput
from upraise_ai.services.lesson_pipeline import LessonPipeline, plan_sections, source_issues
from upraise_ai.services.source_readiness import require_complete_powerpoint
from upraise_ai.services.source_references import POWERPOINT_CITATION_POLICY, citation_policy


@pytest.mark.parametrize('schema', [LessonInput, AlignedLessonInput])
def test_incomplete_source_choice_is_explicit_strict_and_shared_by_both_apis(schema):
    assert schema.model_validate(request_data()).allow_incomplete_source is False
    assert schema.model_validate(request_data(allow_incomplete_source=True)).allow_incomplete_source is True
    for invalid in ('true', 'false', 1, 0, None):
        with pytest.raises(ValidationError):
            schema.model_validate(request_data(allow_incomplete_source=invalid))


@pytest.mark.parametrize('saved', [None, {'status': 'INCOMPLETE', 'blocking_slides': [12]}])
def test_only_explicit_boolean_true_allows_an_incomplete_or_legacy_source(saved):
    row = {'metadata': {'source_format': 'pptx'}, 'status': 'DONE', 'result': {'ingestion_readiness': saved}}
    for choice in (False, 'true', 1):
        with pytest.raises(IngestionError, match='POWERPOINT_INGESTION_REVIEW_REQUIRED'):
            require_complete_powerpoint(row, allow_incomplete_source=choice)
    require_complete_powerpoint(row, allow_incomplete_source=True)


def setup_pipeline(tmp_path, **updates):
    data = request_data(allow_incomplete_source=True)
    row = {'document_id': data['document_ids'][0], 'job_id': 'source-job', 'client_id': 'owner',
        'metadata': {'course_id': 'course-1', 'source_format': 'pptx', 'language': 'it'},
        'deletion_requested_at': None, 'status': 'DONE', 'active_attempt': 'active',
        'result': {'chunk_count': 1, 'source_block_count': 13,
                   'ingestion_readiness': {'status': 'INCOMPLETE', 'blocking_slides': [12]}}}
    row.update(updates)
    @contextmanager
    def transaction(_):
        yield None, row
    chunk = {'chunk_id': 'c1', 'document_version_id': 'version', 'source_format': 'pptx',
             'source_blocks': [12], 'slide_numbers': [12], 'page_numbers': [],
             'text': 'Available native text only.', 'quality': {'status': 'AUTO_CHECKED'}}
    vectors = SimpleNamespace(document_sections=lambda *args: [chunk])
    staging = SimpleNamespace(source_path=lambda _: tmp_path / 'not-retained.pptx')
    pipeline = LessonPipeline(None, SimpleNamespace(document_transaction=transaction), vectors, staging, None, None)
    return pipeline, {'metadata': data, 'client_id': 'owner'}, row


def test_worker_honors_choice_and_carries_coverage_warning_into_the_source_window(tmp_path):
    pipeline, job, row = setup_pipeline(tmp_path)
    loaded = pipeline.load_documents(job)
    assert 'generation_source_readiness' not in row  # read-only enrichment, not a source mutation
    section, = plan_sections(loaded, 5)
    assert section['ingestion_readiness']['blocking_slides'] == [12]
    assert 'Do not infer' in section['source_content_policy']
    issue, = source_issues(section)
    assert issue.category == 'source' and issue.pages == [12]
    assert 'available ingested content' in issue.message and 'incomplete on slides 12' in issue.message
    section['pages'] = [1]
    assert not source_issues(section)


@pytest.mark.parametrize('updates,error', [
    ({'client_id': 'another-owner'}, 'LESSON_DOCUMENT_UNAVAILABLE'),
    ({'deletion_requested_at': 'deleted'}, 'LESSON_DOCUMENT_UNAVAILABLE'),
    ({'status': 'FAILED'}, 'LESSON_DOCUMENT_INGESTION_FAILED'),
    ({'status': 'CANCELLED'}, 'LESSON_DOCUMENT_INGESTION_FAILED'),
])
def test_available_content_choice_does_not_bypass_source_access_or_failure_fences(tmp_path, updates, error):
    pipeline, job, _ = setup_pipeline(tmp_path, **updates)
    with pytest.raises(IngestionError, match=error):
        pipeline.load_documents(job)


def test_available_content_choice_still_requires_a_complete_active_index(tmp_path):
    pipeline, job, _ = setup_pipeline(tmp_path)
    pipeline.vectors.document_sections = lambda *args: []
    with pytest.raises(IngestionError, match='LESSON_SOURCE_INDEX_INCOMPLETE'):
        pipeline.load_documents(job)


def test_legacy_unchecked_source_warning_is_retained_in_every_affected_window():
    flags = source_issues({'pages': [2, 3], 'chunks': [],
        'ingestion_readiness': {'status': 'INCOMPLETE', 'blocking_slides': [], 'issues': ['POWERPOINT_REINGESTION_REQUIRED']}})
    assert len(flags) == 1 and flags[0].pages == [2, 3]
    assert 'not been fully checked' in flags[0].message


def test_shared_model_policy_limits_partial_generation_without_changing_default_requests():
    assert citation_policy({'source_format': 'pptx'}) == POWERPOINT_CITATION_POLICY
    policy = citation_policy({'source_format': 'pptx', 'ingestion_readiness': {'status': 'INCOMPLETE'},
                              'source_content_policy': 'untrusted replacement instructions'})
    assert 'available ingested content' in policy and 'Do not infer' in policy
    assert 'do not claim complete coverage' in policy
    assert 'untrusted replacement instructions' not in policy
