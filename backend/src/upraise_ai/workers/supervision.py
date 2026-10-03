"""A wall-clock watchdog outside the process doing PDF/model work."""

import ctypes
import logging
import multiprocessing
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from upraise_ai.workers.budgets import phase_timeout_seconds

log = logging.getLogger("upraise.supervisor")


@dataclass(frozen=True)
class SupervisionResult:
    reason: str | None
    exit_code: int | None


class ChildTerminationError(RuntimeError):
    """Fatal: the service must not claim more jobs while a writer survives."""


def prepare_child(parent_pid):
    """Keep local writers tied to the supervisor and in a separate process group."""
    if os.name == "posix":
        os.setsid()
    if sys.platform == "linux":
        # Closing the supervisor's lock on a crash must not leave an orphan writer.
        # The child owns the actual artifact lock; SIGKILL also releases it on death.
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
        libc.prctl.restype = ctypes.c_int
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
            raise OSError(ctypes.get_errno(), "Unable to bind job lifetime to supervisor")
    if os.getppid() != parent_pid:
        os._exit(1)


def run_job_child(settings, job, events, parent_pid):
    prepare_child(parent_pid)
    events.send(("ready", os.getpid() if os.name == "posix" else None))
    # Spawn fresh clients; never inherit connection pools or model/heartbeat threads.
    from upraise_ai.workers.ingestion import IngestionWorker

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    worker = None
    try:
        worker = IngestionWorker(settings, notify=lambda *event: events.send(event))
        worker.process(job)
    except Exception:
        log.error("Job child could not complete; supervisor will recover its outcome")
        raise SystemExit(1) from None
    finally:
        if worker is not None:
            worker.vectors.close()
        events.close()


def run_recovery_child(settings, job, events, parent_pid):
    """Outcome persistence/cleanup must not block the supervisor either."""
    prepare_child(parent_pid)
    events.send(("ready", os.getpid() if os.name == "posix" else None))
    from upraise_ai.core.errors import IngestionError
    from upraise_ai.workers.ingestion import IngestionWorker

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    worker = IngestionWorker(settings, processor=object(), notify=lambda *event: events.send(event))
    try:
        code = job.get("supervision_error")
        if code:
            with worker.staging.artifact_lock(str(job["job_id"])):
                worker._record_failure(job, IngestionError(code, retryable=code != "RETRY_LIMIT_REACHED"))
        worker.job_control.complete_cleanup(str(job["job_id"]))
    finally:
        worker.vectors.close()
        events.close()


def read_supervised_state(dsn, job_id, timeout_seconds):
    existing_options = conninfo_to_dict(dsn).get("options", "")
    with psycopg.connect(
        dsn, row_factory=dict_row, connect_timeout=5,
        options=existing_options + f" -c statement_timeout={max(1, int(timeout_seconds * 1000))}",
    ) as conn:
        return conn.execute(
            """SELECT status, lease_token, deletion_requested_at,
            lease_until > clock_timestamp() AS owned FROM ingestion_jobs WHERE job_id=%s""",
            (job_id,),
        ).fetchone()


class StateMonitor:
    """At most one read-only DB operation; the watchdog never waits for its I/O.

    Server/query timeouts cannot guarantee socket/cancellation cleanup deadlines.
    A daemon reader may outlive a poll, but cannot mutate a job. Reuse this monitor
    across jobs so an unavailable network cannot accumulate pending reader threads.
    """

    def __init__(self, read_state):
        self.read_state = read_state
        self.lock = threading.Lock()
        self.pending = False
        self.result = None

    def request(self, job_id, timeout, generation):
        with self.lock:
            if self.pending:
                return
            self.pending = True
            self.result = None

        def read():
            try:
                row, error = self.read_state(job_id, timeout), False
            except Exception:
                row, error = None, True
            with self.lock:
                self.result = (generation, row, error)
                self.pending = False

        threading.Thread(target=read, daemon=True, name="job-ownership-reader").start()

    def take(self, generation):
        with self.lock:
            result, self.result = self.result, None
        return result if result and result[0] is generation else None


class JobSupervisor:
    def __init__(self, settings, *, child_target=run_job_child, read_state=None, check_ownership=True):
        self.settings = settings
        self.child_target = child_target
        self.check_ownership = check_ownership
        self.read_state = read_state or (
            lambda job_id, timeout: read_supervised_state(
                settings.jobs_dsn.get_secret_value(), job_id, timeout
            )
        )
        self.monitor = StateMonitor(self.read_state)

    @staticmethod
    def _owns(row, job):
        return bool(
            row
            and row["status"] == "RUNNING"
            and row.get("deletion_requested_at") is None
            and str(row.get("lease_token")) == str(job["lease_token"])
            and row.get("owned")
        )

    def _stop_child(self, child, group):
        def send(sig):
            if group:
                try:
                    os.killpg(child.pid, sig)
                except ProcessLookupError:
                    pass
            elif child.is_alive():
                child.terminate() if sig == signal.SIGTERM else child.kill()

        send(signal.SIGTERM)
        child.join(self.settings.worker_terminate_grace_seconds)
        # Always kill the known group, even if its leader exited during SIGTERM:
        # parser descendants must also stop before artifacts can be cleaned.
        send(signal.SIGKILL if os.name == "posix" else signal.SIGTERM)
        if child.is_alive():
            child.kill()
        child.join(self.settings.worker_terminate_grace_seconds)
        if child.is_alive():
            # Do not return to claiming/cleanup while a local writer survives.
            raise ChildTerminationError("Job process could not be stopped")

    def run(self, job, stop_event, heartbeat=lambda: None):
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        child = context.Process(
            target=self.child_target, args=(self.settings, job, sender, os.getpid()),
            name="ingestion-" + str(job["job_id"]),
        )
        started = phase_started = time.monotonic()
        phase, units = "startup", 0
        reason = None
        group = False
        next_poll = started
        last_verified = started
        generation = object()
        child_started = False

        def receive_events():
            nonlocal group, phase, phase_started, units, reason
            for _ in range(128):
                try:
                    if not receiver.poll():
                        break
                    event = receiver.recv()
                except (EOFError, OSError):
                    break
                if event[0] == "ready" and os.name == "posix":
                    # The established group survives its leader. Keep the trusted
                    # startup proof even if the leader has already exited.
                    if len(event) > 1:
                        group = event[1] == child.pid and event[1] != os.getpgrp()
                    else:
                        try:
                            group = os.getpgid(child.pid) == child.pid
                        except ProcessLookupError:
                            pass
                elif event[0] == "workload":
                    new_phase, new_units = event[1:]
                    if new_phase != phase:
                        phase, phase_started, units = new_phase, time.monotonic(), 0
                    units = max(units, int(new_units))
                elif event[0] == "lease_lost":
                    if reason is None:
                        reason = "LEASE_LOST"

        try:
            child.start()
            child_started = True
            sender.close()
            while child.is_alive():
                now = time.monotonic()
                deadline = min(
                    started + self.settings.job_timeout_seconds,
                    phase_started + phase_timeout_seconds(self.settings, phase, units),
                )
                if now >= deadline:
                    reason = "PROCESSING_TIMEOUT"
                    break
                if stop_event.is_set():
                    reason = "WORKER_STOPPED"
                    break
                # Messages are tiny, contain no source text, and never extend the
                # absolute deadline. Repeated workload messages do not reset a phase.
                receive_events()
                if reason:
                    break
                now = time.monotonic()
                deadline = min(
                    started + self.settings.job_timeout_seconds,
                    phase_started + phase_timeout_seconds(self.settings, phase, units),
                )
                if now >= deadline:
                    reason = "PROCESSING_TIMEOUT"
                    break
                if self.check_ownership:
                    result = self.monitor.take(generation)
                    if result:
                        _, row, error = result
                        if error:
                            log.warning("Job ownership check unavailable; watchdog remains active")
                        else:
                            last_verified = now
                            if not self._owns(row, job):
                                reason = "LEASE_LOST"
                                break
                    if now - last_verified >= self.settings.lease_seconds:
                        reason = "OWNERSHIP_CHECK_TIMEOUT"
                        break
                    if now >= next_poll:
                        self.monitor.request(
                            str(job["job_id"]), self.settings.worker_status_timeout_seconds, generation
                        )
                        next_poll = now + self.settings.worker_supervisor_poll_seconds
                heartbeat()
                stop_event.wait(min(
                    self.settings.worker_supervisor_poll_seconds,
                    max(0, deadline - time.monotonic()),
                ))
            # Do not lose startup/group information when a child exits before the
            # first monitor iteration (including an initialization crash).
            receive_events()
            if reason is not None:
                self._stop_child(child, group)
            else:
                if group:
                    self._stop_child(child, group)
                else:
                    child.join()
                # A normal exit may have persisted DONE, FAILED or a retry. The
                # caller checks the fenced DB state before recovering any outcome.
                if child.exitcode != 0:
                    reason = "WORKER_PROCESS_EXITED"
            log.info(
                "Job %s supervised outcome=%s phase=%s elapsed_seconds=%.1f exit_code=%s",
                job["job_id"], reason or "EXITED", phase, time.monotonic() - started, child.exitcode,
            )
            return SupervisionResult(reason, child.exitcode)
        finally:
            if child_started:
                receive_events()
                if child.is_alive():
                    self._stop_child(child, group)
            sender.close()
            receiver.close()
            if not child.is_alive():
                child.close()
