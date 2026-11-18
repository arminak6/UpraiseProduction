from fastapi import HTTPException
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from upraise_ai.core.errors import IngestionError
from upraise_ai.schemas.documents import DocumentSummary
from upraise_ai.services.source_readiness import powerpoint_readiness
from upraise_ai.storage.courses import CourseRepository
from upraise_ai.storage.lessons import LessonRepository


class DocumentService:
    """Coordinate the operational registry, course vectors and deletion audit."""

    def __init__(self, jobs, vectors, audit, *, lessons=None, sessions=None):
        self.jobs, self.vectors, self.audit = jobs, vectors, audit
        self.lessons, self.sessions = lessons, sessions
        self.lesson_jobs = lessons.jobs if lessons else LessonRepository(jobs.dsn)
        self.courses = CourseRepository(jobs.dsn)

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
                ingestion_readiness=powerpoint_readiness(row),
                created_at=row["created_at"], updated_at=row["updated_at"],
            ))
        return summaries

    def delete(self, document_id, principal):
        # Commit the tombstone first: neither a crash nor a stale worker can reactivate it.
        with self.jobs.document_transaction(document_id) as (conn, row):
            self.authorize(row, principal)
            self.jobs.request_deletion(conn, document_id)
        self.complete_deletion(document_id)

    def complete_deletion(self, document_id):
        row = self.jobs.get_document(document_id)
        if not row or not row["deletion_requested_at"]:
            return
        # Never hold a source row lock while fencing lesson rows: publication
        # holds lesson -> source locks. The source tombstone is already durable.
        self._delete_lessons(row["client_id"], row["metadata"]["course_id"], document_id=document_id)
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

    def _delete_lessons(self, client_id, course_id, *, document_id=None):
        job_ids = self.lesson_jobs.request_content_deletion(client_id, course_id, document_id=document_id)
        if self.sessions:
            self.sessions.revoke_jobs(job_ids)
        if job_ids and self.lessons is None:
            raise IngestionError("LESSON_STORAGE_NOT_CONFIGURED", status=503)
        for job_id in job_ids:
            try:
                self.lessons.complete_cleanup(job_id)
            except OSError:
                raise IngestionError("TEMPORARY_OUTPUT_STORAGE_UNAVAILABLE", status=503) from None

    def delete_course(self, course_id, principal):
        principal.require_course(course_id)
        operation = self.courses.begin(principal.client_id, course_id)
        self.complete_course(operation)

    def complete_course(self, operation):
        client_id, course_id = operation["client_id"], operation["course_id"]
        # Serializes recovery and new acceptance. A stale recovery cannot delete
        # material ingested after an earlier deletion finished.
        with self.courses.transaction(client_id, course_id) as conn:
            current = self.courses.get(conn, client_id, course_id)
            if not current or current["completed_at"] or current["deletion_id"] != operation["deletion_id"]:
                return
            self._delete_lessons(client_id, course_id)
            for row in self.courses.documents(client_id, course_id):
                self.complete_deletion(str(row["document_id"]))
            conn.execute("UPDATE course_deletions SET completed_at=clock_timestamp() WHERE client_id=%s AND course_id=%s",
                         (client_id, course_id))

    def recover_deletion(self):
        course = self.courses.pending()
        if course:
            self.complete_course(course)
            return
        row = self.jobs.pending_deletion()
        if row:
            self.complete_deletion(row["document_id"])
