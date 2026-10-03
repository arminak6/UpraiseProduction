"""Real spawn-process supervision with synthetic jobs and no external services."""

import multiprocessing
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from upraise_ai.core.config import Settings
from upraise_ai.core.errors import IngestionError, LeaseLost
from upraise_ai.storage.files import StagingStore
from upraise_ai.workers.supervision import (
    ChildTerminationError,
    JobSupervisor,
    prepare_child,
    read_supervised_state,
)

pytest_plugins = ["test_a1_integration"]


def synthetic_pipeline_child(settings, job, events, parent_pid):
    """Exercise the production worker path with deterministic model providers."""
    if job.get("synthetic_mode") == "hang":
        return synthetic_child(settings, job, events, parent_pid)
    from test_a1 import processor

    from upraise_ai.workers.ingestion import IngestionWorker

    prepare_child(parent_pid)
    events.send(("ready", os.getpid() if os.name == "posix" else None))
    worker = IngestionWorker(settings, processor=processor(settings), notify=lambda *event: events.send(event))
    try:
        worker.process(job)
    finally:
        worker.vectors.close()
        events.close()


def synthetic_child(settings, job, events, parent_pid):
    """Use a top-level target so these tests exercise the spawn start method."""
    prepare_child(parent_pid)
    staging = StagingStore(settings.data_dir)
    with staging.artifact_lock(job["job_id"]):
        folder = staging.job_dir(job["job_id"])
        (folder / "child.pid").write_text(str(os.getpid()))
        (folder / "partial-output.json").write_text("unfinished synthetic output")
        mode = job.get("synthetic_mode", "hang")
        if mode == "startup_hang":
            while True:
                time.sleep(0.01)
        if mode == "ignore_terminate" and os.name != "nt":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        events.send(("ready", os.getpid() if os.name == "posix" else None))
        events.send(("workload", "parse", 1))
        if mode == "crash":
            os._exit(23)
        if mode == "descendant_crash":
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            (folder / "descendant.pid").write_text(str(child.pid))
            os._exit(23)
        if mode == "lease_lost":
            events.send(("lease_lost",))
        if mode == "clean":
            (folder / "finished.txt").write_text("complete synthetic output")
            events.close()
            return
        while True:
            if mode == "progress_hang":
                events.send(("workload", "parse", 1))
            time.sleep(0.01)


@pytest.fixture
def supervisor_settings(tmp_path):
    # model_copy permits short deterministic test budgets below production minima.
    return Settings(data_dir=tmp_path).model_copy(update={
        "job_timeout_seconds": 6,
        "worker_startup_timeout_seconds": 4,
        "worker_phase_timeout_seconds": 0.2,
        "worker_supervisor_poll_seconds": 0.03,
        "worker_status_timeout_seconds": 0.1,
        "worker_terminate_grace_seconds": 0.1,
        "parse_timeout_base_seconds": 0.15,
        "parse_timeout_per_page_seconds": 0.05,
        "parse_timeout_per_ocr_page_seconds": 0.05,
    })


def make_job(settings, mode="hang", attempts=1):
    job = {
        "job_id": str(uuid4()),
        "document_id": str(uuid4()),
        "lease_token": str(uuid4()),
        "attempts": attempts,
        "pipeline_version": settings.pipeline_signature,
        "synthetic_mode": mode,
    }
    folder = StagingStore(settings.data_dir).job_dir(job["job_id"])
    folder.mkdir()
    (folder / "source.pdf").write_bytes(b"synthetic source bytes")
    return job


def owned_state(job):
    return {
        "status": "RUNNING",
        "lease_token": job["lease_token"],
        "deletion_requested_at": None,
        "owned": True,
    }


def assert_child_stopped(settings, job):
    folder = StagingStore(settings.data_dir).job_dir(job["job_id"])
    pid = int((folder / "child.pid").read_text())
    assert pid not in {child.pid for child in multiprocessing.active_children()}
    # The OS lock must be released only after the child has stopped writing.
    with StagingStore(settings.data_dir).artifact_lock(job["job_id"]):
        assert (folder / "source.pdf").read_bytes() == b"synthetic source bytes"


def test_hung_child_is_stopped_and_next_job_can_complete(supervisor_settings):
    settings = supervisor_settings
    first = make_job(settings)
    current_job = first
    staging = StagingStore(settings.data_dir)
    saw_writer_lock = []

    def read_state(job_id, timeout_seconds):
        if job_id == first["job_id"] and (staging.job_dir(first["job_id"]) / "child.pid").exists():
            with pytest.raises(IngestionError, match="JOB_CLEANUP_PENDING"):
                with staging.artifact_lock(first["job_id"]):
                    pytest.fail("Cleanup acquired a live child's artifact lock")
            saw_writer_lock.append(True)
        return owned_state(current_job)

    supervisor = JobSupervisor(settings, child_target=synthetic_child, read_state=read_state)
    started = time.monotonic()
    outcome = supervisor.run(first, threading.Event())
    assert outcome.reason == "PROCESSING_TIMEOUT"
    assert time.monotonic() - started < 6
    assert saw_writer_lock
    assert_child_stopped(settings, first)

    second = make_job(settings, "clean")
    current_job = second
    outcome = supervisor.run(second, threading.Event())
    assert outcome.reason is None and outcome.exit_code == 0
    assert (staging.job_dir(second["job_id"]) / "finished.txt").exists()
    assert_child_stopped(settings, second)


@pytest.mark.parametrize("revocation", ["cancel", "lease", "deletion", "expired"])
def test_ownership_revocation_stops_live_writer(supervisor_settings, revocation):
    settings = supervisor_settings.model_copy(update={
        "parse_timeout_base_seconds": 10,
        "worker_phase_timeout_seconds": 10,
    })
    job = make_job(settings)
    marker = StagingStore(settings.data_dir).job_dir(job["job_id"]) / "child.pid"

    def read_state(job_id, timeout_seconds):
        state = owned_state(job)
        if marker.exists():
            if revocation == "cancel":
                state.update(status="CANCELLED", lease_token=None, owned=False)
            elif revocation == "lease":
                state.update(lease_token=str(uuid4()))
            elif revocation == "deletion":
                state.update(deletion_requested_at="requested")
            else:
                state.update(owned=False)
        return state

    outcome = JobSupervisor(settings, child_target=synthetic_child, read_state=read_state).run(
        job, threading.Event()
    )
    assert outcome.reason == "LEASE_LOST"
    assert_child_stopped(settings, job)


def test_worker_shutdown_stops_live_writer(supervisor_settings):
    settings = supervisor_settings
    job = make_job(settings)
    marker = StagingStore(settings.data_dir).job_dir(job["job_id"]) / "child.pid"
    stop = threading.Event()

    def read_state(job_id, timeout_seconds):
        if marker.exists():
            stop.set()
        return owned_state(job)

    outcome = JobSupervisor(settings, child_target=synthetic_child, read_state=read_state).run(job, stop)
    assert outcome.reason == "WORKER_STOPPED"
    assert_child_stopped(settings, job)


def test_crashed_child_releases_artifact_lock(supervisor_settings):
    settings = supervisor_settings
    job = make_job(settings, "crash")
    outcome = JobSupervisor(
        settings, child_target=synthetic_child, read_state=lambda *_: owned_state(job)
    ).run(job, threading.Event())
    assert outcome.reason == "WORKER_PROCESS_EXITED" and outcome.exit_code == 23
    assert_child_stopped(settings, job)


def test_child_lease_loss_signal_stops_process(supervisor_settings):
    settings = supervisor_settings
    job = make_job(settings, "lease_lost")
    outcome = JobSupervisor(
        settings, child_target=synthetic_child, read_state=lambda *_: owned_state(job)
    ).run(job, threading.Event())
    assert outcome.reason == "LEASE_LOST"
    assert_child_stopped(settings, job)


def test_startup_hang_has_separate_deadline(supervisor_settings):
    settings = supervisor_settings.model_copy(update={
        "worker_startup_timeout_seconds": 2,
        "worker_phase_timeout_seconds": 10,
    })
    job = make_job(settings, "startup_hang")
    started = time.monotonic()
    outcome = JobSupervisor(
        settings, child_target=synthetic_child, read_state=lambda *_: owned_state(job)
    ).run(job, threading.Event())
    assert outcome.reason == "PROCESSING_TIMEOUT"
    assert time.monotonic() - started < 4
    assert_child_stopped(settings, job)


def test_total_deadline_caps_long_phase_budget(supervisor_settings):
    settings = supervisor_settings.model_copy(update={
        "job_timeout_seconds": 2,
        "parse_timeout_base_seconds": 30,
        "worker_phase_timeout_seconds": 30,
    })
    job = make_job(settings)
    started = time.monotonic()
    outcome = JobSupervisor(
        settings, child_target=synthetic_child, read_state=lambda *_: owned_state(job)
    ).run(job, threading.Event())
    assert outcome.reason == "PROCESSING_TIMEOUT"
    assert time.monotonic() - started < 4
    assert_child_stopped(settings, job)


def test_repeated_progress_does_not_extend_phase_and_parent_heartbeat_continues(supervisor_settings):
    settings = supervisor_settings
    job = make_job(settings, "progress_hang")
    beats = []
    started = time.monotonic()
    outcome = JobSupervisor(
        settings, child_target=synthetic_child, read_state=lambda *_: owned_state(job)
    ).run(job, threading.Event(), heartbeat=lambda: beats.append(time.monotonic()))
    assert outcome.reason == "PROCESSING_TIMEOUT"
    assert time.monotonic() - started < 5
    assert len(beats) > 2
    assert_child_stopped(settings, job)


def test_blocked_status_read_cannot_block_watchdog_or_accumulate_readers(supervisor_settings):
    settings = supervisor_settings
    job = make_job(settings)
    release = threading.Event()
    reads = []

    def stalled_query(job_id, timeout):
        reads.append(job_id)
        release.wait(10)
        return owned_state(job)

    supervisor = JobSupervisor(settings, child_target=synthetic_child, read_state=stalled_query)
    started = time.monotonic()
    try:
        outcome = supervisor.run(job, threading.Event())
        assert outcome.reason == "PROCESSING_TIMEOUT"
        assert time.monotonic() - started < 5
        assert reads == [job["job_id"]]
        assert_child_stopped(settings, job)
        next_job = make_job(settings, "clean")
        outcome = supervisor.run(next_job, threading.Event())
        assert outcome.reason is None
        assert reads == [job["job_id"]]
    finally:
        release.set()


def test_prolonged_unverifiable_ownership_stops_processing(supervisor_settings):
    settings = supervisor_settings.model_copy(update={
        "lease_seconds": 2,
        "parse_timeout_base_seconds": 30,
    })
    job = make_job(settings)
    release = threading.Event()

    def stalled_query(*args):
        release.wait(10)
        return owned_state(job)

    try:
        result = JobSupervisor(settings, child_target=synthetic_child, read_state=stalled_query).run(
            job, threading.Event()
        )
        assert result.reason == "OWNERSHIP_CHECK_TIMEOUT"
        assert_child_stopped(settings, job)
    finally:
        release.set()


def test_status_reader_preserves_configured_schema_options(monkeypatch):
    captured = {}
    row = {"status": "RUNNING"}

    @contextmanager
    def connect(dsn, **kwargs):
        captured.update(kwargs)
        yield SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchone=lambda: row))

    monkeypatch.setattr("upraise_ai.workers.supervision.psycopg.connect", connect)
    assert read_supervised_state("dbname=synthetic options='-c search_path=test_jobs'", "job", 2) == row
    assert "search_path=test_jobs" in captured["options"]
    assert "statement_timeout=2000" in captured["options"]


class SyntheticJobs:
    def __init__(self, row):
        self.row = row
        self.failures = []

    @contextmanager
    def owned_transaction(self, job_id, token):
        if (self.row["status"] != "RUNNING" or not self.row.get("owned")
                or self.row.get("lease_token") != token or self.row.get("deletion_requested_at")):
            raise LeaseLost()
        yield object()

    def fail(self, conn, job_id, token, code, retry, delay):
        self.failures.append((code, retry))
        self.row.update(status="QUEUED" if retry else "FAILED", error_code=code)


def policy_worker(settings, job, row):
    # Construct only the parent outcome logic, bypassing all client constructors.
    from upraise_ai.workers.ingestion import IngestionWorker

    worker = object.__new__(IngestionWorker)
    worker.settings = settings
    worker.stop = threading.Event()
    worker.staging = StagingStore(settings.data_dir)
    worker.jobs = SyntheticJobs(row)
    removed, audit, cleanup = [], [], []
    worker.vectors = SimpleNamespace(remove_attempt=lambda *args: removed.append(args), close=lambda: None)
    worker.audit = SimpleNamespace(record=lambda *args, **kwargs: audit.append((args, kwargs)))
    worker.job_control = SimpleNamespace(complete_cleanup=lambda job_id: cleanup.append(job_id))
    return worker, removed, audit, cleanup


def fake_outcome(monkeypatch, worker, reason, *, after_run=lambda: None):
    launches = []

    class Supervisor:
        def __init__(self, settings, child_target=None, **kwargs):
            self.settings = settings
            self.child_target = child_target

        def run(self, job, stop, heartbeat):
            if self.child_target is not None:
                # Execute the real recovery policy using only parent-process fakes.
                events = SimpleNamespace(send=lambda *args: None, close=lambda: None)
                self.child_target(self.settings, job, events, os.getpid())
                return SimpleNamespace(reason=None, exit_code=0)
            launches.append(job["job_id"])
            after_run()
            return SimpleNamespace(reason=reason, exit_code=0 if reason is None else -1)

    monkeypatch.setattr("upraise_ai.workers.ingestion.JobSupervisor", Supervisor)
    worker.supervisor = Supervisor(worker.settings)
    monkeypatch.setattr("upraise_ai.workers.ingestion.IngestionWorker", lambda *args, **kwargs: worker)
    monkeypatch.setattr("upraise_ai.workers.supervision.prepare_child", lambda parent_pid: None)
    return launches


@pytest.mark.parametrize("reason", ["PROCESSING_TIMEOUT", "WORKER_PROCESS_EXITED"])
def test_aborted_attempt_retries_are_bounded(supervisor_settings, monkeypatch, reason):
    settings = supervisor_settings
    job = make_job(settings)
    row = owned_state(job)
    worker, removed, audit, cleanup = policy_worker(settings, job, row)
    launches = fake_outcome(monkeypatch, worker, reason)
    statuses = []
    for attempt in range(1, settings.max_attempts + 1):
        job.update(attempts=attempt, lease_token=str(uuid4()))
        row.update(owned_state(job))
        worker.supervise(job)
        statuses.append(row["status"])
    assert statuses == ["QUEUED"] * (settings.max_attempts - 1) + ["FAILED"]
    assert worker.jobs.failures == [(reason, True)] * (settings.max_attempts - 1) + [(reason, False)]
    assert len(launches) == len(removed) == len(audit) == len(cleanup) == settings.max_attempts


def test_attempt_over_retry_limit_never_launches_another_child(supervisor_settings, monkeypatch):
    settings = supervisor_settings
    job = make_job(settings, attempts=settings.max_attempts + 1)
    row = owned_state(job)
    worker, removed, audit, cleanup = policy_worker(settings, job, row)
    launches = fake_outcome(monkeypatch, worker, "WORKER_PROCESS_EXITED")
    worker.supervise(job)
    assert launches == []
    assert row["status"] == "FAILED"
    assert worker.jobs.failures == [("RETRY_LIMIT_REACHED", False)]
    assert len(removed) == len(audit) == len(cleanup) == 1


@pytest.mark.parametrize("reason", [None, "PROCESSING_TIMEOUT", "WORKER_PROCESS_EXITED"])
def test_done_commit_and_active_vectors_win_race_with_parent_outcome(
    supervisor_settings, monkeypatch, reason
):
    settings = supervisor_settings
    job = make_job(settings)
    row = owned_state(job)
    worker, removed, audit, cleanup = policy_worker(settings, job, row)

    def commit_done():
        row.update(status="DONE", active_attempt=job["lease_token"], result={"chunk_count": 7}, owned=False)

    fake_outcome(monkeypatch, worker, reason, after_run=commit_done)
    worker.supervise(job)
    assert row["status"] == "DONE" and row["active_attempt"] == job["lease_token"]
    assert row["result"] == {"chunk_count": 7}
    assert removed == audit == worker.jobs.failures == []
    assert cleanup == [job["job_id"]]


@pytest.mark.parametrize("status", ["CANCELLED", "FAILED", "QUEUED"])
def test_concurrent_terminal_or_retry_state_is_not_overwritten(supervisor_settings, monkeypatch, status):
    settings = supervisor_settings
    job = make_job(settings)
    row = owned_state(job)
    worker, removed, audit, cleanup = policy_worker(settings, job, row)
    fake_outcome(monkeypatch, worker, "PROCESSING_TIMEOUT", after_run=lambda: row.update(status=status))
    worker.supervise(job)
    assert row["status"] == status
    assert removed == audit == worker.jobs.failures == []


@pytest.mark.parametrize("reason", ["LEASE_LOST", "WORKER_STOPPED"])
def test_revoke_or_shutdown_preserves_recoverable_state(supervisor_settings, monkeypatch, reason):
    settings = supervisor_settings
    job = make_job(settings)
    row = owned_state(job)
    original = dict(row)
    worker, removed, audit, cleanup = policy_worker(settings, job, row)
    fake_outcome(monkeypatch, worker, reason)
    worker.supervise(job)
    assert row == original
    assert removed == audit == worker.jobs.failures == []
    assert cleanup == ([] if reason == "WORKER_STOPPED" else [job["job_id"]])


def test_clean_child_without_committed_outcome_gets_recoverable_retry(supervisor_settings, monkeypatch):
    settings = supervisor_settings
    job = make_job(settings)
    row = owned_state(job)
    worker, removed, audit, cleanup = policy_worker(settings, job, row)
    fake_outcome(monkeypatch, worker, None)
    worker.supervise(job)
    assert row["status"] == "QUEUED"
    assert worker.jobs.failures == [("WORKER_PROCESS_EXITED", True)]
    assert removed == [(job["document_id"], job["lease_token"])]


def test_recovery_does_not_construct_pdf_processor(supervisor_settings, monkeypatch):
    from upraise_ai.workers.supervision import run_recovery_child

    job = make_job(supervisor_settings)
    row = owned_state(job)
    worker, _, _, _ = policy_worker(supervisor_settings, job, row)
    constructors = []

    def recovery_worker(settings, **kwargs):
        constructors.append(kwargs)
        assert kwargs["processor"] is not None
        return worker

    monkeypatch.setattr("upraise_ai.workers.ingestion.IngestionWorker", recovery_worker)
    monkeypatch.setattr("upraise_ai.workers.supervision.prepare_child", lambda *_: None)
    events = SimpleNamespace(send=lambda *_: None, close=lambda: None)
    run_recovery_child(supervisor_settings, job, events, os.getpid())
    assert len(constructors) == 1


def test_failed_termination_is_fatal_and_does_not_claim_another_job(supervisor_settings, monkeypatch):
    from upraise_ai.workers.ingestion import IngestionWorker

    job = make_job(supervisor_settings)
    worker, _, _, _ = policy_worker(supervisor_settings, job, owned_state(job))
    claims = []
    worker.jobs.claim = lambda *args: claims.append(job) or job
    worker.documents = SimpleNamespace(recover_deletion=lambda: None)
    worker.job_control.recover_cleanup = lambda: None

    def stuck_writer(job):
        raise ChildTerminationError("Synthetic writer survived")

    worker.supervise = stuck_writer
    monkeypatch.setattr("upraise_ai.workers.ingestion.Path", lambda *_: SimpleNamespace(touch=lambda: None))
    with pytest.raises(ChildTerminationError):
        IngestionWorker.run(worker)
    assert claims == [job]


@pytest.mark.skipif(os.name != "posix", reason="Process-group cleanup requires POSIX")
def test_descendant_is_stopped_after_leader_crashes(supervisor_settings):
    settings = supervisor_settings
    job = make_job(settings, "descendant_crash")
    outcome = JobSupervisor(
        settings, child_target=synthetic_child, read_state=lambda *_: owned_state(job)
    ).run(job, threading.Event())
    assert outcome.reason == "WORKER_PROCESS_EXITED"
    pid = int((StagingStore(settings.data_dir).job_dir(job["job_id"]) / "descendant.pid").read_text())
    # A killed orphan may remain a zombie until the container/host init reaps it.
    until = time.monotonic() + 2
    while time.monotonic() < until:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        if sys.platform == "linux":
            from pathlib import Path

            state = Path(f"/proc/{pid}/stat")
            if not state.exists() or state.read_text().split()[2] == "Z":
                break
        time.sleep(0.01)
    else:
        pytest.fail("A parser descendant survived its crashed job leader")
    assert_child_stopped(settings, job)


@pytest.mark.skipif(os.name == "nt", reason="Windows terminate() forcibly stops the child")
def test_child_ignoring_sigterm_is_killed(supervisor_settings):
    settings = supervisor_settings
    job = make_job(settings, "ignore_terminate")
    outcome = JobSupervisor(
        settings, child_target=synthetic_child, read_state=lambda *_: owned_state(job)
    ).run(job, threading.Event())
    assert outcome.reason == "PROCESSING_TIMEOUT"
    assert outcome.exit_code == -signal.SIGKILL
    assert_child_stopped(settings, job)


def test_supervised_worker_publishes_and_preserves_completed_material(integration):
    import io

    from test_a1 import metadata, pdf_bytes

    from upraise_ai.workflows.submission import SubmissionService

    settings, jobs, audit, vectors, worker, principal = integration
    raw = pdf_bytes()
    accepted = SubmissionService(settings, jobs, worker.staging).submit(
        metadata(raw), io.BytesIO(raw), principal, None
    )
    worker.supervisor = JobSupervisor(settings, child_target=synthetic_pipeline_child)
    worker.supervise(jobs.claim(settings.lease_seconds))
    row = jobs.get(accepted["job_id"])
    assert row["status"] == "DONE" and row["result"]["chunk_count"] == 3
    assert vectors.document_count(accepted["document_id"], "course-1", row["active_attempt"]) == 3
    assert worker.staging.source_path(accepted["job_id"]).read_bytes() == raw


def test_supervised_timeout_persists_failure_then_next_document_completes(integration):
    import io

    from test_a1 import metadata, pdf_bytes

    from upraise_ai.workflows.submission import SubmissionService

    original, jobs, audit, vectors, worker, principal = integration
    settings = original.model_copy(update={
        "max_attempts": 1, "parse_timeout_base_seconds": 0.1,
        "parse_timeout_per_ocr_page_seconds": 0.1, "worker_supervisor_poll_seconds": 0.05,
    })
    worker.settings = settings
    worker.supervisor = JobSupervisor(settings, child_target=synthetic_pipeline_child)
    service = SubmissionService(settings, jobs, worker.staging)
    raw = pdf_bytes()
    first = service.submit(metadata(raw), io.BytesIO(raw), principal, None)
    second = service.submit(metadata(raw), io.BytesIO(raw), principal, None)
    claim = jobs.claim(settings.lease_seconds)
    claim["synthetic_mode"] = "hang"
    worker.supervise(claim)
    row = jobs.get(first["job_id"])
    assert row["status"] == "FAILED" and row["error_code"] == "PROCESSING_TIMEOUT"
    assert vectors.document_count(first["document_id"], "course-1") == 0
    next_claim = jobs.claim(settings.lease_seconds)
    assert str(next_claim["job_id"]) == second["job_id"]
    # Restore generous processing budgets for the healthy synthetic document.
    worker.settings = settings.model_copy(update={"parse_timeout_base_seconds": 180})
    worker.supervisor = JobSupervisor(worker.settings, child_target=synthetic_pipeline_child)
    worker.supervise(next_claim)
    assert jobs.get(second["job_id"])["status"] == "DONE"
