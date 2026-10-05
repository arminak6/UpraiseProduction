"""Deliver and expire completed lessons independently of lengthy generation."""

import logging
import signal
from pathlib import Path

from upraise_ai.core.config import get_settings
from upraise_ai.workers.lessons import LessonWorker, run_callback_child, run_lesson_recovery
from upraise_ai.workers.supervision import ChildTerminationError, JobSupervisor

log = logging.getLogger("upraise.lesson_delivery")
HEARTBEAT = Path("/tmp/upraise-delivery-heartbeat")


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    worker = LessonWorker(get_settings())
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: worker.stop.set())
    settings = worker.settings.model_copy(update={"job_timeout_seconds": 90, "worker_startup_timeout_seconds": 90})
    cleanup = JobSupervisor(settings, child_target=run_lesson_recovery, check_ownership=False)
    callbacks = JobSupervisor(settings, child_target=run_callback_child, check_ownership=False)
    try:
        while not worker.stop.is_set():
            HEARTBEAT.touch()
            try:
                for row in worker.jobs.maintenance(settings.callback_max_attempts):
                    cleanup.run(row, worker.stop, heartbeat=HEARTBEAT.touch)
                    if worker.stop.is_set():
                        break
                if worker.stop.is_set():
                    break
                row = worker.jobs.claim_callback(settings.callback_max_attempts)
                if row:
                    callbacks.run(row, worker.stop, heartbeat=HEARTBEAT.touch)
            except ChildTerminationError:
                raise
            except Exception:
                log.warning("Lesson delivery dependency unavailable; durable retry retained")
            worker.stop.wait(settings.worker_poll_seconds)
    finally:
        worker.vectors.close()


if __name__ == "__main__":
    main()
