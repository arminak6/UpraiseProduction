"""Lesson jobs and durable finalized text; downloadable audio stays on the TTL volume."""

from contextlib import contextmanager
from uuid import uuid4

from psycopg.types.json import Jsonb

from upraise_ai.core.errors import IngestionError, LeaseLost
from upraise_ai.storage.jobs import JobRepository

SCHEMA = """
CREATE TABLE IF NOT EXISTS lesson_jobs (
    job_id UUID PRIMARY KEY, client_id TEXT NOT NULL, idempotency_key TEXT,
    request_fingerprint TEXT NOT NULL, metadata JSONB NOT NULL, pipeline_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'QUEUED' CHECK(status IN ('QUEUED','RUNNING','DONE','FAILED','CANCELLED')),
    step TEXT NOT NULL DEFAULT 'ACCEPTED', progress DOUBLE PRECISION NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0, lease_token UUID, lease_until TIMESTAMPTZ,
    available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), error_code TEXT, result JSONB,
    completed_at TIMESTAMPTZ, expires_at TIMESTAMPTZ,
    cleanup_reason TEXT CHECK(cleanup_reason IN ('CANCEL','RECEIVED','TTL')),
    cleanup_requested_at TIMESTAMPTZ, cleanup_completed_at TIMESTAMPTZ,
    callback_status TEXT NOT NULL DEFAULT 'NONE', callback_attempts INTEGER NOT NULL DEFAULT 0,
    callback_token UUID, callback_until TIMESTAMPTZ,
    callback_available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), callback_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), UNIQUE(client_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS lesson_jobs_pending ON lesson_jobs(available_at,created_at)
    WHERE status IN ('QUEUED','RUNNING');
CREATE INDEX IF NOT EXISTS lesson_jobs_expiry ON lesson_jobs(expires_at)
    WHERE cleanup_completed_at IS NULL;
CREATE TABLE IF NOT EXISTS prepared_lesson_chunks (
    job_id UUID NOT NULL REFERENCES lesson_jobs(job_id) ON DELETE CASCADE,
    chunk_id UUID NOT NULL,
    chunk_index INTEGER NOT NULL CHECK(chunk_index >= 0),
    section_index INTEGER NOT NULL CHECK(section_index >= 0),
    text TEXT NOT NULL CHECK(btrim(text) <> ''),
    sources JSONB NOT NULL,
    reviewer_report JSONB NOT NULL,
    revisions INTEGER NOT NULL CHECK(revisions BETWEEN 0 AND 1),
    versions JSONB NOT NULL,
    ssml TEXT,
    ssml_parts JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(job_id, chunk_id),
    UNIQUE(job_id, chunk_index)
);
ALTER TABLE prepared_lesson_chunks ADD COLUMN IF NOT EXISTS level_key TEXT NOT NULL DEFAULT '';
ALTER TABLE prepared_lesson_chunks ADD COLUMN IF NOT EXISTS content_unit_id UUID;
ALTER TABLE prepared_lesson_chunks DROP CONSTRAINT IF EXISTS prepared_lesson_chunks_job_id_chunk_index_key;
CREATE UNIQUE INDEX IF NOT EXISTS prepared_lesson_chunks_level_index
    ON prepared_lesson_chunks(job_id,level_key,chunk_index);
CREATE TABLE IF NOT EXISTS lesson_chunk_plans (
    job_id UUID PRIMARY KEY REFERENCES lesson_jobs(job_id) ON DELETE CASCADE,
    plan JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE lesson_jobs ADD COLUMN IF NOT EXISTS content_deletion_requested_at TIMESTAMPTZ;
ALTER TABLE lesson_jobs ADD COLUMN IF NOT EXISTS content_deleted_at TIMESTAMPTZ;
ALTER TABLE lesson_jobs ADD COLUMN IF NOT EXISTS content_deletion_reason TEXT;
ALTER TABLE lesson_jobs ADD COLUMN IF NOT EXISTS deleted_levels TEXT[] NOT NULL DEFAULT '{}';
CREATE TABLE IF NOT EXISTS lesson_content_deletions (
    deletion_id UUID PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES lesson_jobs(job_id) ON DELETE CASCADE,
    level_key TEXT NOT NULL,
    levels TEXT[] NOT NULL,
    artifact_ids UUID[] NOT NULL DEFAULT '{}',
    requested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    published_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    UNIQUE(job_id,level_key)
);
CREATE INDEX IF NOT EXISTS lesson_jobs_course ON lesson_jobs(client_id,(metadata->>'course_id'));
CREATE INDEX IF NOT EXISTS lesson_jobs_documents ON lesson_jobs USING gin ((metadata->'document_ids'));
"""


class LessonRepository(JobRepository):
    def migrate(self):
        with self.connect() as conn:
            conn.execute(SCHEMA)

    def accept(self, client_id, key, fingerprint, metadata, pipeline_version):
        with self.connect() as conn:
            from upraise_ai.storage.courses import admit_course
            admit_course(conn, client_id, metadata["course_id"])
            # Lock the sources until acceptance commits: a concurrent document
            # deletion must see this job, or this acceptance must see its fence.
            for document_id in sorted(metadata["document_ids"]):
                source = conn.execute("SELECT * FROM ingestion_jobs WHERE document_id=%s FOR SHARE", (document_id,)).fetchone()
                if (not source or source["client_id"] != client_id or source["deletion_requested_at"]
                        or source["metadata"]["course_id"] != metadata["course_id"]):
                    raise IngestionError("DOCUMENT_NOT_FOUND", status=404)
            row = conn.execute(
                """INSERT INTO lesson_jobs(job_id,client_id,idempotency_key,request_fingerprint,
                metadata,pipeline_version) VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT(client_id,idempotency_key) DO NOTHING RETURNING *""",
                (uuid4(), client_id, key, fingerprint, Jsonb(metadata), pipeline_version),
            ).fetchone()
            if row:
                return row
            row = conn.execute(
                "SELECT * FROM lesson_jobs WHERE client_id=%s AND idempotency_key=%s", (client_id, key)
            ).fetchone()
            if not row or row["request_fingerprint"] != fingerprint:
                raise IngestionError("IDEMPOTENCY_KEY_CONFLICT", status=409)
            if row["cleanup_requested_at"]:
                raise IngestionError("LESSON_OUTPUT_DISPOSED", status=409)
            return row

    def get(self, job_id):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM lesson_jobs WHERE job_id=%s", (job_id,)).fetchone()

    def request_content_deletion(self, client_id, course_id, *, document_id=None):
        condition = " AND metadata->'document_ids' ? %s::text" if document_id else ""
        values = (client_id, course_id, document_id) if document_id else (client_id, course_id)
        with self.connect() as conn:
            conn.execute("SET LOCAL lock_timeout = '5s'")
            rows = conn.execute("SELECT * FROM lesson_jobs WHERE client_id=%s AND metadata->>'course_id'=%s"
                                + condition + " ORDER BY job_id FOR UPDATE", values).fetchall()
            for row in rows:
                if not row["content_deletion_requested_at"]:
                    conn.execute("""UPDATE lesson_jobs SET content_deletion_requested_at=clock_timestamp(),
                        content_deletion_reason=%s,cleanup_completed_at=NULL,
                        cleanup_requested_at=coalesce(cleanup_requested_at,clock_timestamp()),
                        cleanup_reason=coalesce(cleanup_reason,CASE WHEN status IN ('QUEUED','RUNNING') THEN 'CANCEL' ELSE 'RECEIVED' END),
                        status=CASE WHEN status IN ('QUEUED','RUNNING') THEN 'CANCELLED' ELSE status END,
                        step=CASE WHEN status IN ('QUEUED','RUNNING') THEN 'CANCELLED' ELSE step END,
                        lease_token=NULL,lease_until=NULL,callback_token=NULL,callback_until=NULL,
                        callback_status=CASE WHEN callback_status IN ('PENDING','SENDING') THEN 'DISPOSED' ELSE callback_status END,
                        updated_at=clock_timestamp() WHERE job_id=%s""",
                        ("DOCUMENT_DELETED" if document_id else "COURSE_DELETED", row["job_id"]))
                self.erase_content(conn, row["job_id"])
            return [str(row["job_id"]) for row in rows]

    @staticmethod
    def erase_content(conn, job_id):
        conn.execute("DELETE FROM prepared_lesson_chunks WHERE job_id=%s", (job_id,))
        conn.execute("DELETE FROM lesson_chunk_plans WHERE job_id=%s", (job_id,))
        # Revision metadata embeds teacher feedback and canonical material. Keep
        # identifiers for ownership/retries/audit, never that content after deletion.
        conn.execute("""UPDATE lesson_jobs SET result=NULL,metadata=jsonb_build_object(
            'course_id',metadata->'course_id','activity_id',metadata->'activity_id',
            'lesson_id',metadata->'lesson_id','document_ids',metadata->'document_ids',
            'language',metadata->'language','voice',metadata->'voice') WHERE job_id=%s""", (job_id,))

    def revoked_jobs(self, job_ids):
        if not job_ids:
            return []
        with self.connect() as conn:
            return [str(row["job_id"]) for row in conn.execute("""SELECT job_id FROM lesson_jobs
                WHERE job_id=ANY(%s::uuid[]) AND content_deletion_requested_at IS NOT NULL""", (job_ids,)).fetchall()]

    def result_hashes(self, job_ids):
        if not job_ids:
            return {}
        with self.connect() as conn:
            return {str(row["job_id"]): row["digest"] for row in conn.execute("""SELECT job_id,
                CASE WHEN EXISTS (SELECT 1 FROM lesson_content_deletions d WHERE d.job_id=lesson_jobs.job_id
                    AND d.completed_at IS NULL) THEN NULL ELSE result->>'result_sha256' END AS digest
                FROM lesson_jobs WHERE job_id=ANY(%s::uuid[])
                AND result ? 'result_sha256'""", (job_ids,)).fetchall()}

    def accept_revision(self, conn, job_id, client_id, fingerprint, metadata, pipeline_version):
        # Caller stages independent artifacts while holding the parent snapshot
        # lock. Visibility starts only when that transaction commits.
        return conn.execute(
            """INSERT INTO lesson_jobs(job_id,client_id,request_fingerprint,metadata,pipeline_version)
            VALUES (%s,%s,%s,%s,%s) RETURNING *""",
            (job_id, client_id, fingerprint, Jsonb(metadata), pipeline_version),
        ).fetchone()

    def list_ready(self, client_id, courses, course_id, limit, offset):
        # Apply ownership/course/availability filters BEFORE pagination. A8
        # exposes metadata only; A5 rechecks artifacts and audits playback.
        filters = ["j.client_id=%s", "j.status='DONE'",
                   "(j.cleanup_requested_at IS NULL OR (j.cleanup_reason='TTL' AND j.result->>'persistent_output'='true'))",
                   "j.content_deletion_requested_at IS NULL",
                   "NOT EXISTS (SELECT 1 FROM lesson_content_deletions d WHERE d.job_id=j.job_id AND d.completed_at IS NULL)",
                   "j.metadata->'revision_request' IS NULL",
                   "NOT (j.result ? 'replacement_target_job_id')",
                   "(j.expires_at>clock_timestamp() OR j.result->>'persistent_output'='true')",
                   "jsonb_array_length(j.metadata->'document_ids')>0"]
        values = [client_id]
        if courses is not None:
            filters.append("j.metadata->>'course_id'=ANY(%s)")
            values.append(list(courses))
        if course_id is not None:
            filters.append("j.metadata->>'course_id'=%s")
            values.append(course_id)
        filters.append("""NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements_text(j.metadata->'document_ids') AS requested(document_id)
            LEFT JOIN ingestion_jobs AS source ON source.document_id=requested.document_id::uuid
            WHERE source.document_id IS NULL OR source.client_id<>j.client_id
                OR source.deletion_requested_at IS NOT NULL OR source.status<>'DONE'
                OR source.active_attempt IS NULL
                OR source.metadata->>'course_id' IS DISTINCT FROM j.metadata->>'course_id'
        )""")
        with self.connect() as conn:
            return conn.execute(
                """SELECT j.*, levels.value AS catalog_level, provenance.teaching_policy
                FROM lesson_jobs AS j
                CROSS JOIN LATERAL jsonb_array_elements_text(
                    COALESCE(j.result->'levels',jsonb_build_array(
                        COALESCE(j.metadata->'options'->>'learner_level','intermediate')))
                ) WITH ORDINALITY AS levels(value,position)
                LEFT JOIN LATERAL (
                    SELECT CASE WHEN count(DISTINCT p.versions->>'teaching_policy')=1
                        AND count(p.versions->>'teaching_policy')=count(*)
                        THEN min(p.versions->>'teaching_policy') END AS teaching_policy
                    FROM prepared_lesson_chunks p WHERE p.job_id=j.job_id AND p.level_key=
                        CASE WHEN j.metadata->>'generation_version'='a3-v2' THEN levels.value ELSE '' END
                ) provenance ON true WHERE """ + " AND ".join(filters)
                + " ORDER BY j.created_at DESC,j.job_id DESC,levels.position LIMIT %s OFFSET %s",
                [*values, limit, offset],
            ).fetchall()

    def list_reviewable(self, client_id, courses, course_id, limit, offset):
        # One row per stable lesson, including retained text after output cleanup.
        # Filter ownership, source/deletion fences and empty text before pagination.
        filters = ["j.client_id=%s", "j.content_deletion_requested_at IS NULL",
                   "j.metadata->'revision_request' IS NULL",
                   "NOT coalesce(j.result ? 'replacement_target_job_id',false)",
                   "chunks.review_counts IS NOT NULL",
                   "jsonb_array_length(j.metadata->'document_ids')>0",
                   "NOT EXISTS (SELECT 1 FROM lesson_content_deletions d WHERE d.job_id=j.job_id AND d.completed_at IS NULL)"]
        values = [client_id]
        if courses is not None:
            filters.append("j.metadata->>'course_id'=ANY(%s)")
            values.append(list(courses))
        if course_id is not None:
            filters.append("j.metadata->>'course_id'=%s")
            values.append(course_id)
        filters.append("""NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements_text(j.metadata->'document_ids') AS requested(document_id)
            LEFT JOIN ingestion_jobs AS source ON source.document_id=requested.document_id::uuid
            WHERE source.document_id IS NULL OR source.client_id<>j.client_id
                OR source.deletion_requested_at IS NOT NULL OR source.status<>'DONE'
                OR source.active_attempt IS NULL
                OR source.metadata->>'course_id' IS DISTINCT FROM j.metadata->>'course_id'
        )""")
        with self.connect() as conn:
            return conn.execute("""SELECT j.*,chunks.review_counts FROM lesson_jobs AS j
                CROSS JOIN LATERAL (
                    SELECT jsonb_object_agg(parts.level,parts.n ORDER BY parts.level) AS review_counts FROM (
                        SELECT coalesce(nullif(p.level_key,''),j.metadata->'options'->>'learner_level','intermediate') AS level,
                            count(*) AS n FROM prepared_lesson_chunks p WHERE p.job_id=j.job_id
                            AND NOT (coalesce(nullif(p.level_key,''),j.metadata->'options'->>'learner_level','intermediate')=ANY(j.deleted_levels))
                        GROUP BY 1
                    ) parts
                ) chunks WHERE """ + " AND ".join(filters)
                + " ORDER BY j.created_at DESC,j.job_id DESC LIMIT %s OFFSET %s",
                [*values, limit, offset]).fetchall()

    def save_prepared_text(self, conn, job_id, chunks, versions, *, partial=False):
        """Caller holds the job lease and source locks; commit before any TTS call."""
        counters = {}
        for item in chunks:
            chunk_id, text, source, report, section_index, revisions = item[:6]
            level, unit_id = item[6:] if len(item) == 8 else ('', None)
            index = counters.get(level, 0)
            counters[level] = index + 1
            saved = conn.execute(
                """INSERT INTO prepared_lesson_chunks
                (job_id,chunk_id,chunk_index,section_index,text,sources,reviewer_report,revisions,versions,level_key,content_unit_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(job_id,chunk_id) DO NOTHING RETURNING chunk_id""",
                (job_id, chunk_id, index, section_index, text, Jsonb([source.model_dump()]),
                 Jsonb(report.model_dump()), revisions, Jsonb(versions), level, unit_id),
            ).fetchone()
            if saved is None:
                row = conn.execute(
                    "SELECT * FROM prepared_lesson_chunks WHERE job_id=%s AND chunk_id=%s", (job_id, chunk_id)
                ).fetchone()
                if (row["text"] != text or row["chunk_index"] != index or row["section_index"] != section_index
                        or row["sources"] != [source.model_dump()] or row["versions"] != versions
                        or row["reviewer_report"] != report.model_dump() or row["revisions"] != revisions
                        or row['level_key'] != level or str(row['content_unit_id'] or '') != str(unit_id or '')):
                    raise IngestionError("LESSON_PREPARED_TEXT_CONFLICT")
        count = conn.execute(
            "SELECT count(*) AS n FROM prepared_lesson_chunks WHERE job_id=%s", (job_id,)
        ).fetchone()["n"]
        if not partial and count != len(chunks):
            raise IngestionError("LESSON_PREPARED_TEXT_CONFLICT")

    def save_prepared_speech(self, conn, job_id, chunk_id, ssml, parts):
        """Save the precise narration requests before synthesis, including on retries."""
        if not conn.execute(
            """UPDATE prepared_lesson_chunks SET ssml=%s,ssml_parts=%s,updated_at=clock_timestamp()
            WHERE job_id=%s AND chunk_id=%s RETURNING chunk_id""", (ssml, Jsonb(parts), job_id, chunk_id)
        ).fetchone():
            raise IngestionError("LESSON_PREPARED_TEXT_MISSING")

    def save_plan(self, conn, job_id, plan):
        payload = plan.model_dump(mode='json')
        conn.execute('INSERT INTO lesson_chunk_plans(job_id,plan) VALUES (%s,%s) ON CONFLICT DO NOTHING',
                     (job_id, Jsonb(payload)))
        saved = conn.execute('SELECT plan FROM lesson_chunk_plans WHERE job_id=%s', (job_id,)).fetchone()
        if saved['plan'] != payload:
            raise IngestionError('LESSON_PLAN_CONFLICT')

    def get_plan(self, job_id):
        with self.connect() as conn:
            row = conn.execute('SELECT plan FROM lesson_chunk_plans WHERE job_id=%s', (job_id,)).fetchone()
            return row['plan'] if row else None

    def prepared_chunks(self, job_id, level=None):
        """Internal/operator access; API authorization and output expiry remain separate."""
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM prepared_lesson_chunks WHERE job_id=%s AND (%s::text IS NULL OR level_key=%s) ORDER BY level_key,chunk_index",
                (job_id, level, level)
            ).fetchall()

    def prepared_chunk(self, job_id, chunk_id):
        """B4 calls this only after authorizing the exact session/job/chunk."""
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM prepared_lesson_chunks WHERE job_id=%s AND chunk_id=%s", (job_id, chunk_id)
            ).fetchone()

    @contextmanager
    def job_transaction(self, job_id):
        with self.connect() as conn:
            conn.execute("SET LOCAL lock_timeout = '5s'")
            row = conn.execute("SELECT * FROM lesson_jobs WHERE job_id=%s FOR UPDATE", (job_id,)).fetchone()
            yield conn, row

    @contextmanager
    def owned_transaction(self, job_id, token):
        with self.job_transaction(job_id) as (conn, row):
            owned = row and conn.execute("SELECT %s::timestamptz>clock_timestamp()", (row["lease_until"],)).fetchone()
            if (not row or row["status"] != "RUNNING" or row["cleanup_requested_at"]
                    or str(row["lease_token"]) != str(token) or not owned or not next(iter(owned.values()))):
                raise LeaseLost()
            yield conn

    def claim(self, lease_seconds, pipeline_version, *, excluded_courses=()):
        with self.connect() as conn:
            return conn.execute(
                """WITH c AS (SELECT job_id FROM lesson_jobs WHERE
                ((status='QUEUED' AND available_at<=clock_timestamp()) OR
                 (status='RUNNING' AND lease_until<clock_timestamp()))
                AND cleanup_requested_at IS NULL AND pipeline_version=ANY(%s)
                AND NOT (metadata->>'course_id'=ANY(%s))
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
                UPDATE lesson_jobs j SET status='RUNNING', step='SOURCE', attempts=attempts+1,
                lease_token=%s, lease_until=clock_timestamp()+%s*interval '1 second',
                error_code=NULL, updated_at=clock_timestamp() FROM c WHERE j.job_id=c.job_id RETURNING j.*""",
                ([pipeline_version] if isinstance(pipeline_version, str) else list(pipeline_version),
                 list(excluded_courses), uuid4(), lease_seconds),
            ).fetchone()

    def renew(self, job_id, token, lease_seconds):
        with self.connect() as conn:
            return bool(conn.execute(
                """UPDATE lesson_jobs SET lease_until=clock_timestamp()+%s*interval '1 second',
                updated_at=clock_timestamp() WHERE job_id=%s AND lease_token=%s AND status='RUNNING'
                AND cleanup_requested_at IS NULL AND lease_until>clock_timestamp() RETURNING job_id""",
                (lease_seconds, job_id, token),
            ).fetchone())

    def progress(self, job_id, token, step, value):
        with self.connect() as conn:
            if not conn.execute(
                """UPDATE lesson_jobs SET step=%s,progress=%s,updated_at=clock_timestamp()
                WHERE job_id=%s AND lease_token=%s AND status='RUNNING' AND cleanup_requested_at IS NULL
                AND lease_until>clock_timestamp() RETURNING job_id""", (step, value, job_id, token)
            ).fetchone():
                raise LeaseLost()

    def wait_sources(self, conn, job_id, token, timeout_seconds, ttl):
        expired = conn.execute(
            "SELECT created_at+%s*interval '1 second'<clock_timestamp() AS expired FROM lesson_jobs WHERE job_id=%s",
            (timeout_seconds, job_id),
        ).fetchone()["expired"]
        if expired:
            self.fail(conn, job_id, token, "DOCUMENTS_NOT_READY", False, ttl)
            return
        conn.execute(
            """UPDATE lesson_jobs SET status='QUEUED', step='WAITING_FOR_INGESTION',
            error_code='DOCUMENTS_NOT_READY', attempts=greatest(0,attempts-1), lease_token=NULL,
            lease_until=NULL, available_at=clock_timestamp()+interval '10 seconds',
            updated_at=clock_timestamp() WHERE job_id=%s AND lease_token=%s""", (job_id, token)
        )

    def done(self, conn, job_id, token, result, ttl):
        conn.execute(
            """UPDATE lesson_jobs SET status='DONE',step='DONE',progress=1,result=%s,
            completed_at=clock_timestamp(), expires_at=clock_timestamp()+%s*interval '1 second',
            lease_until=NULL, callback_status=CASE WHEN metadata->>'callback_url' IS NULL
            THEN 'NONE' ELSE 'PENDING' END, updated_at=clock_timestamp()
            WHERE job_id=%s AND lease_token=%s""", (Jsonb(result), ttl, job_id, token)
        )

    def pause_for_control(self, conn, job_id, token, code):
        conn.execute("""UPDATE lesson_jobs SET status='QUEUED',step='CONTROL_PAUSED',error_code=%s,
            attempts=greatest(0,attempts-1),lease_token=NULL,lease_until=NULL,
            available_at=clock_timestamp()+interval '2 seconds',updated_at=clock_timestamp()
            WHERE job_id=%s AND lease_token=%s""", (code, job_id, token))

    def fail(self, conn, job_id, token, code, retry, ttl):
        conn.execute(
            """UPDATE lesson_jobs SET status=%s,step=%s,error_code=%s,lease_until=NULL,
            available_at=clock_timestamp()+interval '30 seconds',
            completed_at=CASE WHEN %s THEN NULL ELSE clock_timestamp() END,
            expires_at=CASE WHEN %s THEN NULL ELSE clock_timestamp()+%s*interval '1 second' END,
            callback_status=CASE WHEN %s OR metadata->>'callback_url' IS NULL THEN 'NONE' ELSE 'PENDING' END,
            updated_at=clock_timestamp() WHERE job_id=%s AND lease_token=%s""",
            ("QUEUED" if retry else "FAILED", "RETRY_WAIT" if retry else "FAILED", code,
             retry, retry, ttl, retry, job_id, token),
        )

    def maintenance(self, callback_maximum=8):
        with self.connect() as conn:
            conn.execute(
                """UPDATE lesson_jobs SET callback_status='FAILED',callback_error='CALLBACK_RETRY_LIMIT_REACHED'
                WHERE callback_status='SENDING' AND callback_until<clock_timestamp()
                AND callback_attempts>=%s""", (callback_maximum,)
            )
            conn.execute(
                """UPDATE lesson_jobs SET cleanup_reason='TTL',cleanup_requested_at=clock_timestamp(),
                callback_token=NULL,callback_until=NULL,
                callback_status=CASE WHEN callback_status IN ('PENDING','SENDING') THEN 'DISPOSED' ELSE callback_status END
                WHERE cleanup_requested_at IS NULL AND
                (expires_at<=clock_timestamp() OR (status='FAILED' AND completed_at IS NULL))"""
            )
            return conn.execute(
                """SELECT job_id FROM lesson_jobs WHERE
                (cleanup_requested_at IS NOT NULL AND cleanup_completed_at IS NULL)
                OR result ? 'replacement_cleanup_jobs'
                OR EXISTS (SELECT 1 FROM lesson_content_deletions d WHERE d.job_id=lesson_jobs.job_id AND d.completed_at IS NULL)
                ORDER BY cleanup_requested_at NULLS LAST LIMIT 20"""
            ).fetchall()

    def pending_content_deletion(self, job_id):
        with self.connect() as conn:
            return bool(conn.execute("""SELECT 1 FROM lesson_content_deletions
                WHERE job_id=%s AND completed_at IS NULL LIMIT 1""", (job_id,)).fetchone())

    def claim_callback(self, maximum):
        with self.connect() as conn:
            return conn.execute(
                """WITH c AS (SELECT job_id FROM lesson_jobs WHERE status IN ('DONE','FAILED')
                AND cleanup_requested_at IS NULL AND expires_at>clock_timestamp()
                AND callback_attempts<%s AND callback_available_at<=clock_timestamp()
                AND (callback_status='PENDING' OR (callback_status='SENDING' AND callback_until<clock_timestamp()))
                ORDER BY completed_at FOR UPDATE SKIP LOCKED LIMIT 1)
                UPDATE lesson_jobs j SET callback_status='SENDING',callback_attempts=callback_attempts+1,
                callback_token=%s,callback_until=clock_timestamp()+interval '120 seconds'
                FROM c WHERE j.job_id=c.job_id RETURNING j.*""", (maximum, uuid4())
            ).fetchone()

    def finish_callback(self, row, success, maximum):
        with self.connect() as conn:
            conn.execute(
                """UPDATE lesson_jobs SET callback_status=%s,callback_error=%s,callback_until=NULL,
                callback_available_at=clock_timestamp()+%s*interval '1 second'
                WHERE job_id=%s AND callback_token=%s AND cleanup_requested_at IS NULL""",
                ("DELIVERED" if success else "FAILED" if row["callback_attempts"] >= maximum else "PENDING",
                 None if success else "CALLBACK_DELIVERY_FAILED", min(3600, 10 * 2 ** row["callback_attempts"]),
                 row["job_id"], row["callback_token"]),
            )
