"""Real shared/exclusive artifact locks across Windows/Linux processes."""

import multiprocessing
from contextlib import contextmanager
from uuid import uuid4

import pytest

from upraise_ai.core.errors import IngestionError
from upraise_ai.storage.files import StagingStore


def hold_lock(root, job_id, shared, entered, release):
    with StagingStore(root).artifact_lock(job_id, shared=shared):
        entered.set()
        release.wait(15)


@contextmanager
def locked_child(root, job_id, *, shared):
    context = multiprocessing.get_context("spawn")
    entered, release = context.Event(), context.Event()
    child = context.Process(target=hold_lock, args=(root, job_id, shared, entered, release))
    child.start()
    try:
        assert entered.wait(10), "Child failed to acquire the artifact lock"
        yield child
    finally:
        if child.is_alive():
            release.set()
        child.join(5)
        if child.is_alive():
            child.terminate()
            child.join(5)


def test_shared_readers_coexist_and_block_cleanup(tmp_path):
    staging, job_id = StagingStore(tmp_path), str(uuid4())
    with locked_child(tmp_path, job_id, shared=True):
        with staging.artifact_lock(job_id, shared=True):
            with pytest.raises(IngestionError, match="JOB_CLEANUP_PENDING"):
                with staging.artifact_lock(job_id):
                    pytest.fail("Cleanup acquired an exclusive lock while a reader was active")
    with staging.artifact_lock(job_id):
        pass


def test_exclusive_writer_blocks_other_readers_and_writers(tmp_path):
    staging, job_id = StagingStore(tmp_path), str(uuid4())
    with locked_child(tmp_path, job_id, shared=False):
        for shared in [True, False]:
            with pytest.raises(IngestionError, match="JOB_CLEANUP_PENDING"):
                with staging.artifact_lock(job_id, shared=shared):
                    pytest.fail("Acquired an artifact lock while a writer was active")
    with staging.artifact_lock(job_id, shared=True):
        pass


def test_crashed_reader_releases_its_lock(tmp_path):
    staging, job_id = StagingStore(tmp_path), str(uuid4())
    with locked_child(tmp_path, job_id, shared=True) as child:
        child.terminate()
        child.join(5)
        assert not child.is_alive()
        with staging.artifact_lock(job_id):
            pass
