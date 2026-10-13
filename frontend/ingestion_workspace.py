"""Session-only Middleware simulator; every operation uses the public A-series APIs."""

import hashlib
import json
import math
import secrets
from datetime import datetime, timezone
from functools import partial
from urllib.parse import quote
from uuid import UUID, uuid4

import api_client
import streamlit as st
from learning import binding, expired, readable_text
from revision_progress import render_revision_progress
from voice_picker import render_language_voice

STAGES = ["1 · Ingest material", "2 · Manage material", "3 · Draft lesson", "4 · Jobs & results"]
TERMINAL = {"DONE", "FAILED", "CANCELLED"}


def suggested_number():
    return str(100000 + secrets.randbelow(900000))


def initialize(connection):
    current = binding(connection)
    state = st.session_state.setdefault("ingestion_workspace", {})
    if state.get("binding") != current:
        state.clear()
        state.update(binding=current, documents={}, jobs={}, trace=[], catalog=[], upload_nonce=0)
        for key in list(st.session_state):
            if key.startswith("iw_"):
                del st.session_state[key]
        defaults = {
            "course": suggested_number(), "activity": suggested_number(), "source": suggested_number(),
            "language": "en", "version": "1", "source_mode": "PDF upload", "hash": "",
            "lesson": "test-lesson-" + suggested_number(), "output_language": "en", "voice": "Joanna",
            "document_ids": "", "a1_key": str(uuid4()), "a3_key": str(uuid4()),
            "stage": STAGES[0], "window": 5, "words": 200, "maximum": 500,
        }
        for key, value in defaults.items():
            st.session_state["iw_field_" + key] = value
    if "next_job" in state:
        st.session_state["iw_field_job"] = state.pop("next_job")
    if st.session_state.get("iw_field_stage") not in STAGES:
        st.session_state["iw_field_stage"] = STAGES[0]
    return state


def context():
    return {key + "_id": st.session_state["iw_field_" + key].strip() for key in ("course", "activity")}


def send(connection, state, method, path, **kwargs):
    result = api_client.request(connection, method, path, **kwargs)
    record_request(state["trace"], result, method, path, **kwargs)
    return result


def record_request(trace, result, method, path, **kwargs):
    """Record into the captured session list, including deferred download requests."""
    payload = dict(kwargs.get("json_body") or kwargs.get("fields") or {})
    if "moodle_url" in payload:
        payload["moodle_url"] = "[signed URL omitted]"
    if "callback_url" in payload:
        payload["callback_url"] = "[callback URL omitted]"
    upload = kwargs.get("upload")
    if upload:
        payload["file"] = {"name": upload[0], "bytes": len(upload[1]), "mime": upload[2]}
    trace.append({
        "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "api": {"/v1/documents": "A1" if method == "POST" else "A2",
                "/v1/lesson-jobs": "A3" if method == "POST" else "A8",
                "/v2/lesson-jobs": "A3 v2", "/v1/audio:regenerate": "A7"}.get(
                    path, "A2" if path.startswith(("/v1/documents/", "/v1/courses/")) else
                    "A6" if method == "DELETE" else "A5" if "/result" in path or "/audio/" in path else "A4"),
        "method": method, "path": path, "status": result.status, "elapsed_ms": result.elapsed_ms,
        "error": result.error, "params": kwargs.get("params", {}), "body": payload,
        "idempotency_key": kwargs.get("headers", {}).get("Idempotency-Key"),
    })
    del trace[:-100]


def fingerprint(path, payload, upload=None):
    value = {"path": path, "body": payload}
    if upload:
        value["file"] = {"name": upload[0], "mime": upload[2], "sha256": hashlib.sha256(upload[1]).hexdigest()}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def check_retry(state, api, key, signature):
    previous = state.get(api + "_attempt")
    if previous and previous["key"] == key and previous["fingerprint"] != signature:
        raise ValueError("The request changed. Prepare a new " + ("document" if api == "a1" else "generation")
                         + " to get a new retry key, or restore the original inputs to retry.")
    if not key.strip():
        raise ValueError("Keep an Idempotency-Key for safe network retries.")
    state[api + "_attempt"] = {"key": key, "fingerprint": signature}


def accepted_job(connection, state, result, kind, details):
    if result.status != 202 or not isinstance(result.body, dict):
        return None
    try:
        job = str(UUID(result.body["job_id"]))
        location = api_client.status_path(result.headers.get("location", job), connection.base_url)
        if location != "/v1/jobs/" + job:
            raise ValueError
        document = str(UUID(result.body["document_id"])) if kind == "material" else None
    except (ValueError, KeyError, TypeError, AttributeError):
        st.error("The acceptance response has invalid IDs or a status URL. Inspect its HTTP details.")
        return None
    state["jobs"].setdefault(job, {"job_id": job, "kind": kind, "status": "QUEUED", "progress": 0, **details})
    if document:
        state["jobs"][job]["document_id"] = document
        state["documents"].setdefault(document, {"document_id": document, "status": "QUEUED", "progress": 0, **details})
        existing = st.session_state["iw_field_document_ids"].splitlines()
        if document not in existing:
            st.session_state["iw_field_document_ids"] = "\n".join([*filter(None, existing), document])
    state["next_job"] = job
    st.session_state["status_url"] = location
    st.session_state["tracking_path"] = location
    for key in ("status_response", "job_action_response", "lesson_result", "lesson_audio"):
        st.session_state.pop(key, None)
    st.session_state["auto_refresh"] = False
    return job


def new_document(state):
    state["upload_nonce"] += 1
    for key in ("a1_attempt", "a1_response", "a1_accepted"):
        state.pop(key, None)
    st.session_state["iw_field_source"] = suggested_number()
    st.session_state["iw_field_a1_key"] = str(uuid4())
    st.session_state["iw_field_hash"] = ""


def new_generation(state):
    for key in ("a3_attempt", "a3_response", "a3_accepted"):
        state.pop(key, None)
    st.session_state["iw_field_lesson"] = "test-lesson-" + suggested_number()
    st.session_state["iw_field_a3_key"] = str(uuid4())


def navigate(stage):
    st.session_state["iw_field_stage"] = STAGES[stage]


def context_changed(state):
    new_document(state)
    new_generation(state)
    for key in ("a2_response", "delete_response", "catalog_response"):
        state.pop(key, None)
    state["catalog"] = []
    st.session_state.pop("iw_field_sources", None)
    st.session_state["iw_field_document_ids"] = "\n".join(
        key for key, row in state["documents"].items()
        if row.get("course_id") == context()["course_id"] and row.get("status") in {"DONE", "QUEUED", "RUNNING"})


def render_material(connection, state, show_response):
    st.subheader("A1 · Add teaching material")
    with st.expander("Course and activity"):
        left, right = st.columns(2)
        left.text_input("Course ID", key="iw_field_course", max_chars=120, on_change=context_changed, args=(state,))
        right.text_input("Activity ID", key="iw_field_activity", max_chars=120, on_change=context_changed, args=(state,))
        st.text_input("Source language", key="iw_field_language", max_chars=35)
        st.caption("Suggested test IDs are editable. Use authorized course IDs with your production gateway.")
    mode = st.radio("Document source", ["PDF upload", "Signed Moodle URL"], horizontal=True, key="iw_field_source_mode")
    st.button("Prepare a new document", on_click=new_document, args=(state,))
    with st.form("iw_ingestion"):
        left, right = st.columns(2)
        source = left.text_input("Source / Moodle file ID", key="iw_field_source", max_chars=200)
        version = right.text_input("Source version (optional)", key="iw_field_version", max_chars=120)
        if mode == "PDF upload":
            upload = st.file_uploader("Teaching material (PDF)", type=["pdf"], max_upload_size=100,
                                      key=f"iw_upload_{state['upload_nonce']}")
            url = ""
            content_hash = ""
            st.caption("content_hash will be filled with the uploaded PDF's SHA-256.")
        else:
            upload = None
            url = st.text_input("Signed Moodle URL", type="password", key="iw_field_url")
            content_hash = st.text_input("Source content_hash / revision", key="iw_field_hash", max_chars=200,
                                         help="Use the upstream hash or revision; it is source metadata.")
        with st.expander("Retry key"):
            key = st.text_input("A1 Idempotency-Key", key="iw_field_a1_key")
        submit = st.form_submit_button("Submit material · A1", type="primary")
    if submit:
        try:
            fields = {**context(), "language": st.session_state["iw_field_language"].strip(),
                      "source_ref": source.strip(), "content_hash": content_hash.strip()}
            file = None
            if mode == "PDF upload":
                if upload is None:
                    raise ValueError("Select a PDF to upload.")
                data = upload.getvalue()
                if len(data) > 100 * 1024 * 1024:
                    raise ValueError("The PDF must be at most 100 MB.")
                fields["content_hash"] = hashlib.sha256(data).hexdigest()
                file = (upload.name, data, "application/pdf")
            elif not url.strip():
                raise ValueError("Enter a signed Moodle URL.")
            if not all(fields.values()):
                raise ValueError("Fill the course, activity, language, source ID and content hash.")
            if version.strip():
                fields["version"] = version.strip()
            payload = fields if file else {**fields, "moodle_url": url.strip()}
            # Signed URLs can be renewed for a retry; the API fingerprints actual PDF bytes.
            check_retry(state, "a1", key.strip(), fingerprint("/v1/documents", fields, file))
            with st.spinner("Submitting material…"):
                options = {"fields": fields, "upload": file} if file else {"json_body": payload}
                result = send(connection, state, "POST", "/v1/documents", **options,
                              headers={"Idempotency-Key": key.strip()})
            state["a1_response"] = result
            job = accepted_job(connection, state, result, "material", {**fields,
                               "filename": upload.name if upload else "Moodle PDF"})
            if job:
                state["a1_accepted"] = job
        except ValueError as error:
            st.error(str(error))
    if state.get("a1_accepted"):
        job = state["a1_accepted"]
        st.success("Material accepted. Its document ID is already added to the draft's source list.")
        st.code(f"document_id: {state['a1_response'].body['document_id']}\njob_id: {job}", language=None)
        st.button("Continue to draft lesson", type="primary", on_click=navigate, args=(2,))
    if state.get("a1_response"):
        with st.expander("A1 HTTP response", expanded=not state["a1_response"].successful):
            show_response(state["a1_response"])


def refresh_documents(connection, state):
    result = send(connection, state, "GET", "/v1/documents")
    state["a2_response"] = result
    if result.successful and isinstance(result.body, list):
        # Unfiltered A2 returns all undeleted material accessible to this caller.
        state["documents"] = {}
        for row in result.body:
            if isinstance(row, dict) and row.get("document_id"):
                state["documents"][row["document_id"]] = row


def progress_value(value):
    try:
        value = float(value)
        return max(0.0, min(1.0, value)) if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def progress_percentage(value):
    progress = progress_value(value)
    return f"{progress * 100:.2f}%" if progress is not None else "—"


def material_table(rows):
    st.dataframe([{
        "Material": row.get("filename"), "Course": row.get("course_id"),
        "Status": row.get("status"), "Progress": progress_percentage(row.get("progress")),
        "Chunks": row.get("chunk_count", 0), "Document ID": row["document_id"],
    } for row in rows], hide_index=True, width="stretch")


def render_documents(connection, state, show_response):
    st.subheader("A2 · Ingested material")
    listing, removal = st.tabs(["Show ingested material", "Remove material"])
    with listing:
        st.button("Show all ingested material", type="primary", on_click=refresh_documents, args=(connection, state))
        result = state.get("a2_response")
        if result and result.successful:
            rows = list(state["documents"].values())
            if rows:
                material_table(rows)
            else:
                st.info("No ingested material is available.")
        if result:
            with st.expander("A2 list response", expanded=not result.successful):
                show_response(result)
    with removal:
        rows = list(state["documents"].values())
        st.caption("Removing a document also deletes every lesson generated from it, across all levels, languages and revisions.")
        with st.form("iw_remove_material"):
            if rows:
                labels = {row["document_id"]: f"{row.get('filename', row['document_id'])} · {row.get('course_id', '')}"
                          for row in rows}
                document = st.selectbox("Document to remove", list(labels), key="iw_field_delete",
                                        format_func=lambda value: labels[value])
            else:
                document = st.text_input("Document UUID to remove", key="iw_field_delete_id")
            remove = st.form_submit_button("Remove material · A2")
        if remove:
            try:
                document = str(UUID(document.strip()))
            except ValueError:
                st.error("Select material or enter a valid document UUID.")
            else:
                result = send(connection, state, "DELETE", "/v1/documents/" + document)
                state["delete_response"] = result
                # A timeout/503 can follow a committed deletion fence. Discard
                # cached text/voice now; a fresh API read decides availability.
                clear_deleted_views(state)
                if result.status == 204:
                    state["documents"].pop(document, None)
                    st.session_state["iw_field_document_ids"] = "\n".join(
                        value for value in st.session_state["iw_field_document_ids"].splitlines() if value.strip() != document)
                    st.rerun()
        if state.get("delete_response"):
            show_response(state["delete_response"])
        with st.expander("Delete an entire course"):
            with st.form("iw_remove_course"):
                course = st.text_input("Course ID to delete", key="iw_field_delete_course", max_chars=120)
                selected_courses = sorted({row["course_id"] for row in rows})
                if selected_courses:
                    st.caption("Available course IDs: " + ", ".join(selected_courses))
                confirmed = st.checkbox("Delete all this course's ingested material and generated lessons", key="iw_field_delete_course_confirm")
                remove_course = st.form_submit_button("Delete entire course · A2")
            if remove_course:
                course = course.strip()
                if not course or not confirmed:
                    st.error("Enter the course ID and confirm deletion of its material and lessons.")
                else:
                    result = send(connection, state, "DELETE", "/v1/courses/" + quote(course, safe=""))
                    state["delete_course_response"] = result
                    clear_deleted_views(state)
                    if result.status == 204:
                        removed = {key for key, row in state["documents"].items() if row.get("course_id") == course}
                        state["documents"] = {key: row for key, row in state["documents"].items() if key not in removed}
                        st.session_state["iw_field_document_ids"] = "\n".join(
                            value for value in st.session_state["iw_field_document_ids"].splitlines() if value.strip() not in removed)
                        st.rerun()
            if state.get("delete_course_response"):
                show_response(state["delete_course_response"])


def clear_deleted_views(state):
    for job in state["jobs"].values():
        for key in ("result", "result_response", "export", "audio", "audio_response"):
            job.pop(key, None)
    state["catalog"] = []
    for key in ("catalog_response", "catalog_context", "download_course"):
        state.pop(key, None)
    for key in ("learning_state", "b1_state", "b2_state", "b4_state", "lesson_result", "lesson_audio"):
        st.session_state.pop(key, None)


def render_generation(connection, state, show_response, *, refresh_on_open=False):
    st.subheader("A3 · Generate a draft from your material")
    if refresh_on_open:
        refresh_documents(connection, state)
    st.button("Refresh source status", on_click=refresh_documents, args=(connection, state))
    result = state.get("a2_response")
    rows = list(state["documents"].values()) if result and result.successful else []
    if rows:
        material_table(rows)
    if result and not result.successful:
        show_response(result)
    available = {row["document_id"]: row for row in rows if row.get("status") in {"DONE", "QUEUED", "RUNNING"}}
    choices = list(available)
    if st.session_state.get("iw_field_source_document") not in choices:
        preferred = (state.get("generation_source") or (None,))[0]
        if preferred not in choices:
            preferred = st.session_state.get("iw_field_document_ids", "")
        st.session_state["iw_field_source_document"] = preferred if preferred in choices else None
    with st.container(border=True):
        st.session_state["iw_field_generation"] = "A3 v2 · All aligned levels"
        st.selectbox("Generation version", ["A3 v2 · All aligned levels"], disabled=True, key="iw_field_generation")
        labels = {key: f"{row.get('filename', key)} · Course {row['course_id']} · {key[:8]}"
                  for key, row in available.items()}
        selected = st.selectbox("Course material", choices, key="iw_field_source_document", disabled=not choices,
                                format_func=lambda value: labels[value], index=None,
                                placeholder="Select course material" if choices else "No material available")
        material = available.get(selected)
        identity = (selected, material.get("course_id"), material.get("activity_id")) if material else None
        if material and state.get("generation_source") != identity:
            new_generation(state)
            state["generation_source"] = identity
        st.session_state["iw_field_draft_course"] = material["course_id"] if material else ""
        st.session_state["iw_field_draft_activity"] = material["activity_id"] if material else ""
        st.session_state["iw_field_draft_document"] = selected or ""
        if not choices:
            st.info("Ingest material through A1, then refresh this list to generate a lesson.")
        elif not material:
            st.caption("Select the material you want to turn into a lesson.")
        elif material["status"] in {"QUEUED", "RUNNING"}:
            st.caption("This source is still processing. A3 will wait for ingestion to finish.")
        st.button("Prepare a new generation", on_click=new_generation, args=(state,))
        language, voice = render_language_voice("Output language", language_key="iw_field_output_language",
                                               voice_key="iw_field_voice")
        with st.form("iw_generation"):
            lesson = st.text_input("Lesson ID", key="iw_field_lesson", max_chars=120)
            left, right = st.columns(2)
            left.text_input("Course ID", key="iw_field_draft_course", disabled=True)
            right.text_input("Activity ID", key="iw_field_draft_activity", disabled=True)
            window = left.number_input("Source window (pages)", 1, 50, key="iw_field_window")
            words = right.number_input("Target chunk length (words)", 30, 1000, key="iw_field_words")
            maximum = left.number_input("Maximum chunk length (words)", 50, 2000, key="iw_field_maximum")
            st.caption("The backend's configured levels share teaching units, source scope and chunk count.")
            with st.expander("Source and request details"):
                st.text_input("Document UUID", key="iw_field_draft_document", disabled=True)
                if material:
                    st.caption("Source language: " + material.get("language", "unknown"))
                callback = st.text_input("Middleware callback URL (optional)", key="iw_field_callback",
                                         help="Leave empty for A5 pull. Push requires backend outbound OAuth/mTLS configuration.")
                key = st.text_input("A3 Idempotency-Key", key="iw_field_a3_key")
            submit = st.form_submit_button("Generate lesson · A3", type="primary", disabled=not material)
    if submit:
        try:
            payload = {"course_id": material["course_id"], "activity_id": material["activity_id"],
                       "lesson_id": lesson.strip(), "document_ids": [str(UUID(selected))],
                       "language": language.strip(), "voice": voice.strip(),
                       "options": {"source_window_pages": window, "target_chunk_words": words, "max_chunk_words": maximum}}
            if not all(payload[name] for name in ("course_id", "activity_id", "lesson_id", "language", "voice")):
                raise ValueError("Fill the course, activity, lesson, output language and voice.")
            if words > maximum:
                raise ValueError("Target length must not exceed maximum length.")
            if callback.strip():
                payload["callback_url"] = callback.strip()
            path = "/v2/lesson-jobs"
            check_retry(state, "a3", key.strip(), fingerprint(path, payload))
            with st.spinner("Submitting draft generation…"):
                result = send(connection, state, "POST", path, json_body=payload, headers={"Idempotency-Key": key.strip()})
            state["a3_response"] = result
            job = accepted_job(connection, state, result, "lesson", payload)
            if job:
                state["a3_accepted"] = job
        except ValueError as error:
            st.error(str(error))
    if state.get("a3_accepted"):
        st.success("Draft generation accepted: " + state["a3_accepted"])
        st.button("Track jobs and review results", type="primary", on_click=navigate, args=(3,))
    if state.get("a3_response"):
        with st.expander("A3 HTTP response", expanded=not state["a3_response"].successful):
            show_response(state["a3_response"])


def refresh_status(connection, state, job):
    previous_status = job.get("status")
    result = send(connection, state, "GET", "/v1/jobs/" + job["job_id"], timeout=15)
    job["status_response"] = result
    if result.successful and isinstance(result.body, dict):
        body = result.body
        job.update(status=body.get("status", "UNKNOWN"), step=body.get("step"), status_body=body,
                   progress=body.get("progress"))
        if body.get("document_id"):
            job["kind"] = "material"
            document = state["documents"].get(body["document_id"])
            if document:
                document["status"] = job["status"]
                document["progress"] = body.get("progress")
        elif body.get("lesson_id"):
            job["kind"] = "lesson"
        if job["status"] != "DONE" or body.get("acknowledged") or body.get("cleanup_pending"):
            had_result = bool(job.get("result"))
            for key in ("result", "result_response", "audio", "audio_response", "export"):
                job.pop(key, None)
            return had_result
        return previous_status != "DONE" and job.get("kind") == "lesson"
    return False


def load_result(connection, state, job):
    result = send(connection, state, "GET", "/v1/jobs/" + job["job_id"] + "/result")
    job["result_response"] = result
    for key in ("result", "audio", "export"):
        job.pop(key, None)
    if result.successful and isinstance(result.body, dict):
        try:
            if str(UUID(result.body["job_id"])) != job["job_id"] or expired(result.body):
                raise ValueError
            lesson_variants(result.body)  # Check identities before using any chunk in a request.
        except (ValueError, KeyError, TypeError, AttributeError):
            st.error("The result has expired or contains inconsistent lesson/chunk identities.")
        else:
            job.update(result=result.body, kind="lesson", status="DONE")


def lesson_variants(body):
    variants = body.get("variants") or {body.get("learner_level", "lesson"): body}
    seen = set()
    for variant in variants.values():
        chunks = variant["chunks"]
        if not chunks or [chunk["index"] for chunk in chunks] != list(range(len(chunks))):
            raise ValueError("Invalid chunk order.")
        if variant.get("job_id") != body["job_id"]:
            raise ValueError("Invalid variant job.")
        for chunk in chunks:
            chunk_id = str(UUID(chunk["chunk_id"]))
            if chunk_id in seen:
                raise ValueError("Duplicate chunk ID.")
            seen.add(chunk_id)
    return variants


def download_voice(connection, trace, job_id, chunk_id, expected_hash):
    """Deferred, single-chunk A5 download; never use session widgets on its thread."""
    path = f"/v1/jobs/{UUID(job_id)}/audio/{UUID(chunk_id)}"
    result = api_client.request(connection, "GET", path)
    record_request(trace, result, "GET", path)
    if result.status == 410:
        raise ValueError("This draft has expired. Select an available draft.")
    if not result.successful or not result.binary:
        raise ValueError("The voice could not be downloaded. Refresh the selected job and try again.")
    if hashlib.sha256(result.binary).hexdigest() != expected_hash:
        raise ValueError("The voice checksum differs from the A5 result. Reload the draft and try again.")
    return result.binary


def render_result(connection, state, job, show_response):
    st.subheader("A5 · Draft voice")
    status = job.get("status_body", {})
    available = (job.get("status") == "DONE" and not job.get("action_completed")
                 and not status.get("acknowledged") and not status.get("cleanup_pending"))
    if available and not job.get("result") and not job.get("result_response"):
        load_result(connection, state, job)
    body = job.get("result")
    if body and expired(body):
        for key in ("result", "audio", "export"):
            job.pop(key, None)
        body = None
        st.info("This draft's temporary output has expired. Generate or load an available draft.")
    if not body and available and job.get("result_response"):
        response = job["result_response"]
        response_expired = response.status == 410 or (isinstance(response.body, dict) and expired(response.body))
        if not response_expired and st.button("Reload draft · A5"):
            load_result(connection, state, job)
            body = job.get("result")
    if body:
        st.warning("AI-generated DRAFT. Review the text and speech before human approval in your orchestrator.")
        st.caption("Temporary output expires at " + str(body.get("expires_at") or "the backend's retention deadline"))
        variants = lesson_variants(body)
        level = st.selectbox("Level", list(variants), key="iw_field_result_level")
        variant = variants[level]
        st.write(f"{len(variant['chunks'])} chunks · {variant.get('language', '')} · {variant.get('voice', '')}")
        index = st.selectbox("Teaching chunk", range(len(variant["chunks"])),
                             format_func=lambda value: f"Part {value + 1}", key="iw_field_chunk")
        chunk = variant["chunks"][index]
        st.download_button("Download voice", data=partial(
            download_voice, connection, state["trace"], job["job_id"], str(UUID(chunk["chunk_id"])), chunk["audio_sha256"]),
            file_name=f"{level}-part-{index + 1}.wav", mime="audio/wav", on_click="ignore", type="primary",
            key=f"iw_voice_{job['job_id']}_{chunk['chunk_id']}")
        st.markdown(readable_text(chunk["text"]))
        with st.expander("Source evidence, review findings and speech text"):
            st.json({name: chunk.get(name) for name in ("content_unit_id", "sources", "reviewer_report", "ssml", "ssml_parts")})
            st.caption("Automated review findings are evidence for human review, not a human approval decision.")
        render_revision(connection, state, job, variant, chunk, show_response)
    for key in ("result_response", "audio_response"):
        if job.get(key):
            with st.expander("A5 HTTP details · " + key, expanded=not job[key].successful):
                show_response(job[key])


def render_revision(connection, state, job, variant, chunk, show_response):
    with st.expander("A7 · Request a teacher revision", expanded=bool(job.get("revision_accepted"))):
        st.caption("Revise the selected chunk in this level. A7 creates a new complete version; each submission creates a job.")
        selected = (variant.get("learner_level"), chunk["chunk_id"])
        if state.get("revision_selection") != selected:
            state["revision_selection"] = selected
            st.session_state["iw_field_feedback"] = ""
        with st.form("iw_revision"):
            feedback = st.text_area("Teacher's requested changes", key="iw_field_feedback", max_chars=8000)
            submit = st.form_submit_button("Submit teacher revision · A7", disabled=bool(job.get("revision_accepted")))
        if submit:
            if not feedback.strip():
                st.error("Describe the teacher's requested changes.")
            else:
                result = send(connection, state, "POST", "/v1/audio:regenerate", json_body={
                    "lesson_job_id": job["job_id"], "chunk_id": chunk["chunk_id"], "teacher_review": feedback.strip(),
                })
                job["revision_response"] = result
                revision = accepted_job(connection, state, result, "lesson", {
                    **{key: variant.get(key) for key in ("course_id", "activity_id", "lesson_id")},
                    "parent_job_id": job["job_id"], "document_ids": job.get("document_ids", []),
                })
                if revision:
                    job["revision_accepted"] = revision
                    state["latest_revision"] = revision
                    st.rerun()
        if job.get("revision_accepted"):
            st.success("Revision job: " + job["revision_accepted"])
            if st.button("Prepare another teacher revision"):
                job.pop("revision_accepted", None)
                job.pop("revision_response", None)
                st.rerun()
        if job.get("revision_response"):
            show_response(job["revision_response"])


def render_action(connection, state, job, show_response):
    st.subheader("A6 · Cancel or acknowledge")
    with st.form("iw_job_action"):
        reason = st.selectbox("Job action", ["CANCEL", "RECEIVED"], key="iw_field_reason")
        saved = st.checkbox("I saved the required JSON and WAVs (or inspected this failed/material job)", key="iw_field_saved")
        st.caption("RECEIVED removes temporary output. Source material and retained text keep their separate lifecycle.")
        submit = st.form_submit_button("Send job action · A6")
    if submit:
        if reason == "RECEIVED" and not saved:
            st.error("Save the delivery or inspect the job before acknowledging receipt.")
        else:
            result = send(connection, state, "DELETE", "/v1/jobs/" + job["job_id"], params={"reason": reason})
            job["action_response"] = result
            if result.status == 204:
                job["status"] = "CANCELLED" if reason == "CANCEL" else job.get("status", "UNKNOWN")
                job["action_completed"] = reason
                for key in ("result", "result_response", "audio", "audio_response", "export"):
                    job.pop(key, None)
                st.session_state.pop("learning_state", None)
                st.session_state.pop("b1_state", None)
                st.rerun()
    if job.get("action_response"):
        show_response(job["action_response"])
        if isinstance(job["action_response"].body, dict) and job["action_response"].body.get("detail") == "JOB_CLEANUP_PENDING":
            st.info("The action is recorded; cleanup is pending. Retry the same job ID and reason.")


def ready_job_context(state):
    return tuple(sorted(key for key, job in state["jobs"].items()
                        if job.get("kind") == "lesson" and job.get("status") == "DONE"))


def refresh_catalog(connection, state, *, more=False):
    offset = state.get("catalog_offset", 0) if more else 0
    result = send(connection, state, "GET", "/v1/lesson-jobs", params={"limit": 100, "offset": offset})
    state["catalog_response"] = result
    state["catalog_context"] = ready_job_context(state)
    if result.successful and isinstance(result.body, list):
        try:
            items = [{**item, "job_id": str(UUID(item["job_id"]))} for item in result.body if not expired(item)]
            if any(not item.get("course_id") or not item.get("lesson_id") for item in items):
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError):
            state.update(catalog=[], catalog_more=False)
            st.error("The course list contains invalid course or job identities.")
        else:
            state["catalog"] = [*state["catalog"], *items] if more else items
            state.update(catalog_more=len(result.body) == 100, catalog_offset=offset + len(result.body))
    else:
        state.update(catalog=[], catalog_more=False)


def activate_job(state, selected):
    if state.get("active_job") != selected:
        state["active_job"] = selected
        for key in ("iw_field_saved", "iw_field_poll", "iw_field_chunk", "iw_field_result_level"):
            st.session_state.pop(key, None)


def render_courses(connection, state, show_response, *, refresh_on_open=False):
    st.subheader("Download course voice")
    open_revision = state.pop("open_revision", None)
    refresh = st.button("Refresh courses")
    newly_ready = set(ready_job_context(state)) - set(state.get("catalog_context") or ())
    if open_revision or refresh_on_open or refresh or not state.get("catalog_response") or newly_ready:
        refresh_catalog(connection, state)
    if state.get("catalog_more") and st.button("Load more courses"):
        refresh_catalog(connection, state, more=True)
        st.rerun()
    courses = {}
    for item in state["catalog"]:
        if not expired(item) and not state["jobs"].get(item["job_id"], {}).get("action_completed"):
            courses.setdefault(item["job_id"], item)  # A8 lists one row per level; show each generation once.
    if open_revision:
        st.session_state["iw_field_job"] = open_revision
        st.session_state["iw_field_download_course"] = open_revision if open_revision in courses else None
    if st.session_state.get("iw_field_download_course") not in courses:
        st.session_state["iw_field_download_course"] = None
    previous = state.get("download_course")
    if (previous and previous == st.session_state.get("iw_field_download_course")
            and st.session_state.get("iw_field_job") != previous):
        st.session_state["iw_field_download_course"] = None  # An explicit advanced job choice takes precedence.

    def label(key):
        item = courses[key]
        created = item.get("created_at", "")[:16].replace("T", " ")
        return f"Course {item['course_id']} · {item['lesson_id']}" + (f" · {created}" if created else "")

    selected = st.selectbox("Course", list(courses), key="iw_field_download_course", format_func=label,
                            index=None, placeholder="Choose a generated course", disabled=not courses)
    state["download_course"] = selected
    if selected:
        item = courses[selected]
        job = state["jobs"].setdefault(selected, {**item, "kind": "lesson", "status": "DONE"})
        job["kind"] = "lesson"
        if job.get("status") != "DONE":
            job.update(status="DONE", step="DONE", progress=1, kind="lesson")
            job.pop("status_response", None)
            job.pop("status_body", None)
        st.session_state["iw_field_job"] = selected
        activate_job(state, selected)
        state["catalog_context"] = ready_job_context(state)
        render_result(connection, state, job, show_response)
    elif not courses:
        st.info("No generated courses are available. Generate a lesson, then refresh this list.")
    else:
        st.caption("Choose a course above, then its level and teaching chunk to download the voice.")
    response = state.get("catalog_response")
    if response and not response.successful:
        show_response(response)
    return selected


def render_jobs(connection, state, show_response, *, refresh_on_open=False):
    revision = state.get("latest_revision")
    if revision in state["jobs"]:
        revision_job = state["jobs"][revision]

        def open_revision(job_id):
            state["open_revision"] = job_id
            state["next_job"] = job_id

        def status_changed(body):
            revision_job.update(status=body["status"], step=body.get("step"), progress=body.get("progress"),
                                status_body=body, status_response=revision_job["revision_progress"]["response"])

        render_revision_progress(connection, revision_job.setdefault("revision_progress", {}), revision,
                                 show_response, key="iw_revision_progress_" + revision, on_open=open_revision,
                                 request_status=lambda path: send(connection, state, "GET", path, timeout=15),
                                 on_status=status_changed)
    course_job = render_courses(connection, state, show_response, refresh_on_open=refresh_on_open)
    with st.expander("Advanced job tracking", expanded=not bool(course_job)):
        render_job_tracking(connection, state, show_response, course_job=course_job)


def render_job_tracking(connection, state, show_response, *, course_job=None):
    st.subheader("A4 · Track ingestion and lesson jobs")
    with st.form("iw_import_job"):
        imported = st.text_input("Existing job UUID", key="iw_field_import")
        add = st.form_submit_button("Track existing job")
    if add:
        try:
            job_id = str(UUID(imported.strip()))
        except ValueError:
            st.error("Enter a valid job UUID.")
        else:
            state["jobs"].setdefault(job_id, {"job_id": job_id, "kind": "unknown", "status": "UNKNOWN"})
            state["next_job"] = job_id
            st.rerun()
    if not state["jobs"]:
        st.info("Submit material, generate a draft, or enter an existing job UUID to begin tracking.")
        return
    choices = list(state["jobs"])
    if st.session_state.get("iw_field_job") not in choices:
        st.session_state["iw_field_job"] = choices[-1]
    labels = {value: f"{state['jobs'][value]['kind']} · {value}" for value in choices}
    selected = st.selectbox("Tracked job", choices, key="iw_field_job", format_func=lambda value: labels[value])
    job = state["jobs"][selected]
    activate_job(state, selected)
    poll = st.toggle("Poll selected job every 5 seconds", key="iw_field_poll")

    @st.fragment(run_every=5 if poll else None)
    def monitor():
        manual = st.button("Refresh selected job · A4", type="primary")
        response = job.get("status_response")
        failed = response and (response.error or not response.successful)
        if manual or (poll and job.get("status") not in TERMINAL and not failed):
            if refresh_status(connection, state, job):
                st.rerun()
        response = job.get("status_response")
        if response:
            body = job.get("status_body", {})
            progress = progress_value(body.get("progress"))
            if response.successful:
                st.progress(progress or 0.0, text=f"{job.get('status')} · {job.get('step', '')} · {progress_percentage(progress)}")
                if job.get("status") in TERMINAL:
                    st.caption("Terminal state reached. Automatic polling has stopped.")
                if body.get("cleanup_pending"):
                    st.info("Job cleanup is pending. Retry the recorded A6 action.")
                extraction = body.get("result")
                if job.get("kind") == "material" and isinstance(extraction, dict):
                    pages, chunks, quality = st.columns(3)
                    pages.metric("Source pages", extraction.get("page_count", "—"))
                    chunks.metric("Source chunks", extraction.get("chunk_count", "—"))
                    quality.metric("Source quality", extraction.get("quality_status", "UNVERIFIED"))
                    st.caption("DONE means extraction completed. Automated source checks do not establish human approval.")
                    if extraction.get("requires_review"):
                        st.warning("The extracted source has review flags. Inspect the A4 quality details before teaching from it.")
            else:
                st.caption("Polling paused after an HTTP/network error; refresh manually to retry.")
            with st.expander("A4 status and extraction details", expanded=not response.successful):
                show_response(response)
    monitor()
    if job.get("kind") == "material":
        st.button("Continue to draft lesson", on_click=navigate, args=(2,))
    elif not course_job:
        render_result(connection, state, job, show_response)
    render_action(connection, state, job, show_response)


def render_trace(state):
    with st.expander("Orchestrator request sequence"):
        if not state["trace"]:
            st.caption("API calls will appear here after an explicit action.")
            return
        st.dataframe([{key: entry[key] for key in ("time_utc", "api", "method", "path", "status", "elapsed_ms")}
                      for entry in state["trace"]], hide_index=True, width="stretch")
        st.caption("Last 100 calls, held only in this session. Credentials and signed Moodle URLs are omitted.")
        st.download_button("Save request sequence JSON", json.dumps(state["trace"], ensure_ascii=False, indent=2).encode("utf-8"),
                           file_name="ingestion-api-sequence.json", mime="application/json", on_click="ignore")


def render_ingestion_workspace(connection, show_response):
    state = initialize(connection)
    st.header("Ingestion workspace")
    st.caption("A1 · Upload   →   A2 · List / remove   →   A3 · Generate   →   A4–A8 · Track and review")
    stage = st.radio("Ingestion steps", STAGES, horizontal=True, key="iw_field_stage")
    entering = state.get("visible_stage") != stage
    state["visible_stage"] = stage
    if stage == STAGES[0]:
        render_material(connection, state, show_response)
    elif stage == STAGES[1]:
        render_documents(connection, state, show_response)
    elif stage == STAGES[2]:
        render_generation(connection, state, show_response, refresh_on_open=entering)
    else:
        render_jobs(connection, state, show_response, refresh_on_open=entering)
    if stage != STAGES[2]:
        render_trace(state)
