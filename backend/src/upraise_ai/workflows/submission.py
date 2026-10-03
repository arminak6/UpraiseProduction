import hashlib
import json
import re
from uuid import uuid4

from upraise_ai.core.errors import IngestionError
from upraise_ai.core.utils import sha256_file
from upraise_ai.services.pdf.input import validate_pdf_input
from upraise_ai.services.pdf.languages import ocr_languages


class SubmissionService:
    def __init__(self, settings, jobs, staging):
        self.settings, self.jobs, self.staging = settings, jobs, staging

    def submit(self, metadata, stream, principal, key):
        principal.require_course(metadata.course_id)
        ocr_languages(metadata.language)
        if key and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", key):
            raise IngestionError("INVALID_IDEMPOTENCY_KEY", status=400)
        key_digest = hashlib.sha256(key.encode()).hexdigest() if key else None
        job_id, document_id = str(uuid4()), str(uuid4())
        source = self.staging.stage(job_id, stream, self.settings.max_pdf_size_mb * 1024**2)
        try:
            validate_pdf_input(
                source,
                self.settings.max_pdf_size_mb * 1024**2,
                self.settings.max_pdf_pages,
            )
            # Identify retry bytes internally; do not verify the caller's hash.
            source_hash = sha256_file(source)
            fingerprint = "bytes-v1:" + hashlib.sha256(
                json.dumps(
                    {
                        "metadata": metadata.model_dump(exclude={"source_filename"}),
                        "source_hash": source_hash,
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            legacy_metadata = metadata.model_dump(exclude={"source_filename"})
            legacy_metadata["content_hash"] = metadata.content_hash.lower().removeprefix("sha256:")
            # Older accepted requests used a verified SHA-256 in their fingerprint.
            # This only matches an old retry; no new submission is rejected by hash.
            legacy_fingerprint = None
            if legacy_metadata["content_hash"] == source_hash:
                legacy_fingerprint = hashlib.sha256(
                    json.dumps({"metadata": legacy_metadata}, sort_keys=True).encode()
                ).hexdigest()
        except Exception:
            self.staging.remove(job_id)
            raise
        try:
            row, created = self.jobs.accept(
                job_id,
                document_id,
                principal.client_id,
                key_digest,
                fingerprint,
                metadata.model_dump(),
                self.settings.pipeline_signature,
                legacy_fingerprint=legacy_fingerprint,
            )
        except IngestionError:
            self.staging.remove(job_id)
            raise
        # On an uncertain DB commit, preserve staged bytes for recovery. A committed
        # job must never point to a source removed because the response was lost.
        if not created:
            self.staging.remove(job_id)
            if row["deletion_requested_at"] is not None:
                raise IngestionError("DOCUMENT_DELETED", status=409)
            if row["status"] == "CANCELLED":
                raise IngestionError("JOB_CANCELLED", status=409)
        return {"document_id": str(row["document_id"]), "job_id": str(row["job_id"])}
