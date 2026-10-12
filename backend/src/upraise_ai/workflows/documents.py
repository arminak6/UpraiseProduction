from fastapi import HTTPException
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.documents import DocumentSummary


class DocumentService:
    """Coordinate the operational registry, course vectors and deletion audit."""

    def __init__(self, jobs, vectors, audit):
        self.jobs, self.vectors, self.audit = jobs, vectors, audit

    @staticmethod
    def authorize(row, principal):
        if (not row or row["client_id"] != principal.client_id
                or not principal.can_access_course(row["metadata"]["course_id"])):
            raise HTTPException(404, "DOCUMENT_NOT_FOUND")

    def list(self, course_id, principal):
        if course_id is not None:
            principal.require_course(course_id)
        summaries = []
        for row in self.jobs.list_documents(principal.client_id, course_id):
            metadata = row["metadata"]
            if not principal.can_access_course(metadata["course_id"]):
                continue
            count = 0
            if row["status"] == "DONE" and row["active_attempt"]:
                try:
                    count = self.vectors.document_count(row["document_id"], metadata["course_id"], row["active_attempt"])
                except (ResponseHandlingException, UnexpectedResponse):
                    raise IngestionError("VECTOR_STORAGE_UNAVAILABLE", status=503) from None
            summaries.append(DocumentSummary(
                document_id=str(row["document_id"]),
                **{key: metadata[key] for key in ("course_id", "activity_id", "language", "source_ref", "content_hash")},
                version=metadata.get("version"), filename=metadata["source_filename"],
                status=row["status"], progress=row["progress"], chunk_count=count,
                created_at=row["created_at"], updated_at=row["updated_at"],
            ))
        return summaries

    def delete(self, document_id, principal):
        # Commit the tombstone first: neither a crash nor a stale worker can reactivate it.
        with self.jobs.document_transaction(document_id) as (conn, row):
            self.authorize(row, principal)
            if row["deleted_at"]:
                return
            self.jobs.request_deletion(conn, document_id)
        self.complete_deletion(document_id)

    def complete_deletion(self, document_id):
        with self.jobs.document_transaction(document_id) as (conn, row):
            if not row or row["deleted_at"] or not row["deletion_requested_at"]:
                return
            try:
                self.vectors.remove_document(document_id, row["metadata"]["course_id"])
            except (ResponseHandlingException, UnexpectedResponse):
                raise IngestionError("VECTOR_STORAGE_UNAVAILABLE", status=503) from None
            # Stable event identity makes retries after an uncertain commit safe.
            self.audit.record(row, "DOCUMENT_DELETED", {"course_id": row["metadata"]["course_id"]})
            self.jobs.finish_deletion(conn, document_id)

    def recover_deletion(self):
        row = self.jobs.pending_deletion()
        if row:
            self.complete_deletion(row["document_id"])
