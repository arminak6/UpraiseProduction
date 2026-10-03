import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from upraise_ai.core.errors import IngestionError


class StagingStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        return self.root / str(UUID(job_id))

    def source_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "source.pdf"

    @contextmanager
    def artifact_lock(self, job_id: str):
        # Separate from job_dir: cleanup/retention must never unlink a live lock.
        locks = self.root / ".locks"
        locks.mkdir(mode=0o700, exist_ok=True)
        path = locks / (str(UUID(job_id)) + ".lock")
        with path.open("a+b") as stream:
            if os.name == "nt":
                import msvcrt

                if path.stat().st_size == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    raise IngestionError("JOB_CLEANUP_PENDING", status=503, retryable=True) from None
            else:
                import fcntl

                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise IngestionError("JOB_CLEANUP_PENDING", status=503, retryable=True) from None
            try:
                yield
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def purge_output(self, job_id: str):
        folder = self.job_dir(job_id)
        if folder.resolve().parent != self.root:
            raise ValueError("invalid staging path")
        if not folder.exists():
            return
        for child in folder.iterdir():
            if child.name == "source.pdf":
                continue
            if child.is_symlink() or not child.is_dir():
                child.unlink()
            else:
                shutil.rmtree(child)
        if os.name != "nt":
            fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def stage(self, job_id: str, stream, max_bytes: int) -> Path:
        folder = self.job_dir(job_id)
        folder.mkdir(mode=0o700)
        temporary = folder / "source.part"
        total = 0
        try:
            with temporary.open("xb") as destination:
                while block := stream.read(1024 * 1024):
                    total += len(block)
                    if total > max_bytes:
                        raise IngestionError("PDF_TOO_LARGE", status=413)
                    destination.write(block)
                destination.flush()
                os.fsync(destination.fileno())
            path = self.source_path(job_id)
            temporary.replace(path)
            # Persist the rename before acknowledging acceptance.
            if os.name != "nt":
                for directory in (folder, self.root):
                    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
            return path
        except BaseException:
            self.remove(job_id)
            raise

    def remove(self, job_id: str) -> None:
        path = self.job_dir(job_id)
        # job_dir accepts a UUID only; resolve before any recursive deletion.
        if path.resolve().parent != self.root:
            raise ValueError("invalid staging path")
        shutil.rmtree(path, ignore_errors=False) if path.exists() else None
