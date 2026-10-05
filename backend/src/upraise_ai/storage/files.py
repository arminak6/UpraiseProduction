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
    def artifact_lock(self, job_id: str, *, shared: bool = False):
        # Separate from job_dir: cleanup/retention must never unlink a live lock.
        locks = self.root / ".locks"
        locks.mkdir(mode=0o700, exist_ok=True)
        path = locks / (str(UUID(job_id)) + ".lock")
        with path.open("a+b") as stream:
            if os.name == "nt":
                with _windows_artifact_lock(stream, shared=shared):
                    yield
                return
            import fcntl

            try:
                mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
                fcntl.flock(stream, mode | fcntl.LOCK_NB)
            except BlockingIOError:
                raise IngestionError("JOB_CLEANUP_PENDING", status=503, retryable=True) from None
            try:
                yield
            finally:
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


@contextmanager
def _windows_artifact_lock(stream, *, shared):
    # CRT read-lock constants are aliases for exclusive locks. LockFileEx
    # provides actual shared locks, with the same one-byte range as before.
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", wintypes.HANDLE)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
    kernel.LockFileEx.restype = wintypes.BOOL
    kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.DWORD, ctypes.POINTER(Overlapped)]
    kernel.UnlockFileEx.restype = wintypes.BOOL
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno()))
    overlapped = Overlapped()
    flags = 1 | (0 if shared else 2)  # FAIL_IMMEDIATELY | optional EXCLUSIVE_LOCK
    if not kernel.LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(overlapped)):
        error = ctypes.get_last_error()
        if error == 33:  # ERROR_LOCK_VIOLATION
            raise IngestionError("JOB_CLEANUP_PENDING", status=503, retryable=True)
        raise ctypes.WinError(error)
    try:
        yield
    finally:
        if not kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlapped)):
            raise ctypes.WinError(ctypes.get_last_error())
