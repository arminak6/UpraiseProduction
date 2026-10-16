"""Immutable, verified lesson packages outside the temporary working volume."""

import hashlib
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from upraise_ai.core.errors import IngestionError


def persistent_output(row):
    return (row.get("result") or {}).get("persistent_output") is True


def output_expired(row):
    if persistent_output(row) and row.get("cleanup_reason") in {None, "TTL"}:
        return False
    return bool(row.get("cleanup_requested_at") or (
        row.get("expires_at") and row["expires_at"] <= datetime.now(UTC)))


def output_available(row):
    return bool(row and row["status"] == "DONE" and not row.get("content_deletion_requested_at")
                and not output_expired(row) and (row.get("expires_at") or persistent_output(row)))


def sync_directory(path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class LessonArchive:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def version(self, owner, artifact, digest):
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("invalid lesson archive digest")
        path = self.root / str(UUID(str(owner))) / str(UUID(str(artifact))) / digest
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("invalid lesson archive path")
        return path

    def save(self, owner, artifact, folder, manifest_name, digest, chunks):
        try:
            return self._save(owner, artifact, folder, manifest_name, digest, chunks)
        except OSError:
            raise IngestionError("LESSON_ARCHIVE_STORAGE_UNAVAILABLE", status=503, retryable=True) from None

    def _save(self, owner, artifact, folder, manifest_name, digest, chunks):
        """Caller holds the lesson's publication lock and DB ownership/fences."""
        if manifest_name not in {"result.json", "current-result.json"}:
            raise ValueError("invalid lesson manifest name")
        destination = self.version(owner, artifact, digest)
        raw = (folder / manifest_name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise IngestionError("LESSON_RESULT_INTEGRITY_FAILURE", status=503)
        files = [(manifest_name, digest)]
        files.extend((f"{UUID(chunk.chunk_id)}.wav", chunk.audio_sha256) for chunk in chunks)
        if destination.exists():
            # A retry can reuse only a complete, verified immutable package.
            for name, expected in files:
                path = destination / name
                if not path.is_file() or self.digest(path) != expected:
                    raise IngestionError("LESSON_ARCHIVE_INTEGRITY_FAILURE", status=503)
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        candidate = destination.parent / (".pending-" + uuid4().hex)
        candidate.mkdir(mode=0o700)
        try:
            for name, expected in files:
                source = folder / name
                if not source.is_file() or self.digest(source) != expected:
                    raise IngestionError("LESSON_AUDIO_INTEGRITY_FAILURE", status=503)
                target = candidate / name
                # A real copy survives working-volume cleanup and replacement.
                with source.open("rb") as reader, target.open("xb") as writer:
                    shutil.copyfileobj(reader, writer)
                    writer.flush()
                    os.fsync(writer.fileno())
                if self.digest(target) != expected:
                    raise IngestionError("LESSON_ARCHIVE_INTEGRITY_FAILURE", status=503)
            sync_directory(candidate)
            candidate.replace(destination)
            for directory in (destination.parent, destination.parent.parent, self.root):
                sync_directory(directory)
        finally:
            if candidate.exists():
                shutil.rmtree(candidate)

    @staticmethod
    def digest(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def remove(self, owner, artifact=None, *, keep_digest=None):
        path = self.root / str(UUID(str(owner)))
        if artifact is not None:
            path /= str(UUID(str(artifact)))
        if not path.resolve().is_relative_to(self.root) or path.resolve() == self.root:
            raise ValueError("invalid lesson archive removal path")
        if not path.exists():
            return
        if keep_digest is None:
            shutil.rmtree(path)
        else:
            if not re.fullmatch(r"[a-f0-9]{64}", keep_digest):
                raise ValueError("invalid lesson archive digest")
            for child in path.iterdir():
                if child.name != keep_digest:
                    if child.is_symlink() or not child.is_dir():
                        child.unlink()
                    else:
                        shutil.rmtree(child)
        sync_directory(path.parent)
