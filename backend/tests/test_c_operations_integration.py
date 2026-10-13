"""Real isolated PostgreSQL/Qdrant health, cross-process observations and queue controls."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from test_a1 import metadata, pdf_bytes

from upraise_ai.services.operations import OperationsMonitor
from upraise_ai.storage.lessons import LessonRepository
from upraise_ai.storage.operations import OperationsRepository, configuration_id
from upraise_ai.workflows.submission import SubmissionService


def test_c_operations_real_dependencies_and_cross_process_observation_visibility(integration):
    settings, jobs, audit, vectors, worker, principal = integration
    vectors.ensure_collection()
    current = settings.model_copy(update={"qdrant_collection": vectors.collection,
                                          "aws_credentials_file": settings.data_dir / "no-credentials"})
    reporter = OperationsRepository(current)
    reader = OperationsRepository(current)
    reporter.report(["bedrock_generation", "bedrock_review", "bedrock_embeddings", "polly", "transcribe", "lesson_worker"],
                    "AVAILABLE", "PROVIDER_CALL_SUCCEEDED")
    assert len(reader.read()) == 6
    monitor = OperationsMonitor(current)
    try:
        monitor.refresh()
        assert monitor.snapshot().status == "READY", monitor.snapshot()
        assert monitor.status("course-1").answers_available
        reporter.report(["transcribe"], "UNAVAILABLE", "PROVIDER_ACCESS_DENIED")
        monitor.refresh()
        assert monitor.status().stt_available is False
        assert monitor.status().answers_available and monitor.status().generation_available
        with jobs.connect() as conn:
            conn.execute("UPDATE operational_observations SET observed_at=%s WHERE component='lesson_worker'",
                         (datetime.now(UTC)-timedelta(minutes=5),))
        monitor.refresh()
        assert monitor.status().generation_available is False
        assert monitor.status().answers_available
        rotated = current.model_copy(update={"lesson_generation_model_id": "different-invented-model"})
        assert configuration_id(rotated) != configuration_id(current)
        assert not OperationsRepository(rotated).read()
    finally:
        monitor.close()


def test_c_operations_disabled_courses_remain_queued_without_consuming_attempts(integration):
    settings, jobs, _, vectors, worker, principal = integration
    source = SubmissionService(settings, jobs, worker.staging).submit(metadata(pdf_bytes()),
        __import__("io").BytesIO(pdf_bytes()), principal, "ops-source")
    assert jobs.claim(settings.lease_seconds, excluded_courses=["course-1"]) is None
    assert jobs.get(source["job_id"])["attempts"] == 0
    assert jobs.claim(settings.lease_seconds)["job_id"]
    lesson_jobs = LessonRepository(settings.jobs_dsn.get_secret_value())
    pending = lesson_jobs.accept(principal.client_id, str(uuid4()), "fingerprint",
                                 {"course_id": "course-1", "document_ids": [source["document_id"]]}, "ops-test")
    assert lesson_jobs.claim(settings.lease_seconds, "ops-test", excluded_courses=["course-1"]) is None
    assert lesson_jobs.get(pending["job_id"])["attempts"] == 0
    assert lesson_jobs.claim(settings.lease_seconds, "ops-test")["job_id"] == pending["job_id"]
