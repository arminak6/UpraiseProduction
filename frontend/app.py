import hashlib
import json
import os
from uuid import UUID, uuid4

import streamlit as st
from api_client import Connection, normalize_base_url, request, status_path
from chunk_end import render_chunk_end
from chunk_revision import render_chunk_revision
from learner_interrupt import render_learner_interrupt
from learning import render_learning
from operations import render_operations
from session_end import render_session_end
from session_start import render_session_start

st.set_page_config(page_title="UPRAISE", page_icon="🧪", layout="wide")
st.title("UPRAISE")
st.caption("Prepared lesson learning and API testing")


def clear_session():
    nonce = st.session_state.get("credential_nonce", 0) + 1
    st.session_state.clear()
    st.session_state["credential_nonce"] = nonce


def new_submission():
    st.session_state["submission_key"] = str(uuid4())
    st.session_state.pop("ingestion_response", None)


def status_changed():
    st.session_state["tracking_path"] = st.session_state.get("status_url", "")
    st.session_state.pop("status_response", None)
    st.session_state.pop("job_action_response", None)
    st.session_state.pop("lesson_result", None)
    st.session_state.pop("lesson_audio", None)


def new_lesson():
    st.session_state["lesson_key"] = str(uuid4())
    for name in ("lesson_response", "lesson_result", "lesson_audio"):
        st.session_state.pop(name, None)


def list_course_changed():
    st.session_state.pop("documents_response", None)


def certificate_bytes(upload):
    if upload is None:
        return None
    if upload.size > 1024 * 1024:
        st.error("TLS files must be smaller than 1 MB.")
        st.stop()
    return upload.getvalue()


with st.sidebar:
    st.subheader("Connection")
    base_url = st.text_input(
        "Backend / gateway URL", os.environ.get("BACKEND_BASE_URL", "http://localhost:8000"),
        key="backend_url", help="Requests run on the Streamlit server. In Docker, backend resolves to the API container.",
    )
    token = st.text_input("Bearer token", type="password", key="bearer_token",
                          help="Optional for health; required by authenticated APIs. Accepts a token or Bearer <token>.")
    with st.expander("mTLS and private CA"):
        nonce = st.session_state.get("credential_nonce", 0)
        cert = st.file_uploader("Client certificate (PEM)", key=f"client_cert_{nonce}", max_upload_size=1)
        key = st.file_uploader("Client private key (PEM)", key=f"client_key_{nonce}", max_upload_size=1)
        password = st.text_input("Private-key password", type="password", key="key_password")
        ca = st.file_uploader("CA certificate (optional PEM)", key=f"ca_cert_{nonce}", max_upload_size=1)
        st.caption("Use your HTTPS mTLS gateway. Certificate files and tokens are session-only.")
    timeout = st.number_input("Request timeout (seconds)", min_value=5, max_value=600, value=180, step=5)
    st.divider()
    page = st.radio("Pages", ["Learning", "Start session (B1)", "Chunk end (B2)", "Learner interrupt (B4)", "End session (B5)", "Ingest material", "Manage material", "Generate lesson", "Revise chunk (A7)", "Processing status", "Health", "API explorer"], key="page")
    st.button("Clear session data", on_click=clear_session, width="stretch")

try:
    base_url = normalize_base_url(base_url)
except ValueError as error:
    st.error(str(error))
    st.stop()

local_testing = (os.environ.get("UPRAISE_LOCAL_TEST_MODE", "false").lower() == "true"
                 and base_url == os.environ.get("BACKEND_BASE_URL", "http://localhost:8000").rstrip("/"))
if local_testing:
    st.info("Local testing mode is enabled. Leave the bearer token and mTLS fields empty to use learning and API tools.")

if st.session_state.get("target") != base_url:
    for name in ("ingestion_response", "lesson_response", "lesson_result", "lesson_audio", "status_response", "job_action_response", "health_response", "explorer_response", "api_schema", "schema_response", "documents_response", "delete_response"):
        st.session_state.pop(name, None)
    st.session_state["status_url"] = ""
    st.session_state["tracking_path"] = ""
    st.session_state["auto_refresh"] = False
    st.session_state["list_course_id"] = ""
    st.session_state["delete_document_id"] = ""
    st.session_state["submission_key"] = str(uuid4())
    st.session_state["lesson_key"] = str(uuid4())
    st.session_state["target"] = base_url

# Keep form values when their widgets are hidden by navigation. In particular,
# a network retry must keep the same idempotency key after leaving A1.
for name in ("submission_key", "course_id", "activity_id", "language", "source_ref",
             "version", "content_hash", "source_mode", "fill_hash", "mime", "list_course_id", "delete_document_id",
             "lesson_key", "a3_lesson_id", "a3_course_id", "a3_activity_id", "a3_document_ids", "a3_language",
             "a3_voice", "a3_callback", "a3_window", "a3_words", "a3_level"):
    if name in st.session_state:
        st.session_state[name] = st.session_state[name]
for name, default in {"language": "en", "mime": "application/pdf", "fill_hash": True}.items():
    st.session_state.setdefault(name, default)
for name in list(st.session_state):
    if name.startswith(("b1_field_", "b2_field_", "b4_field_", "b5_field_", "a7_field_", "learning_question_")):
        st.session_state[name] = st.session_state[name]

connection = Connection(
    base_url=base_url, token=token, certificate=certificate_bytes(cert),
    private_key=certificate_bytes(key), key_password=password,
    ca_certificate=certificate_bytes(ca), timeout_seconds=timeout,
)


def show_response(result):
    if result is None:
        return
    if result.error:
        st.error(result.error)
        return
    columns = st.columns(3)
    columns[0].metric("HTTP status", result.status)
    columns[1].metric("Response time", f"{result.elapsed_ms} ms")
    columns[2].write(f"`{result.method} {result.path}`")
    if result.headers.get("x-upraise-auth-mode") == "local-test":
        st.caption("Backend authentication mode: local testing · OAuth/mTLS credentials are not required.")
    if result.successful:
        st.success("Accepted for background processing." if result.status == 202 else "Request succeeded.")
    else:
        st.error(f"The backend returned HTTP {result.status}.")
    if result.is_json:
        st.json(result.body)
    else:
        st.code(result.body or "(empty response)", language=None)
    if isinstance(result.body, dict) and result.body.get("detail") == "INGRESS_NOT_CONFIGURED":
        st.info("Configure OAuth and the trusted mTLS gateway on the backend to test authenticated requests. Removing the document Ingestion Guard does not remove INGRESS authentication.")
    if result.headers:
        with st.expander("Response headers"):
            st.json(result.headers)


def remember_submission(result):
    st.session_state["ingestion_response"] = result
    if result.status == 202 and isinstance(result.body, dict) and result.body.get("document_id"):
        st.session_state["delete_document_id"] = result.body["document_id"]
        st.session_state["list_course_id"] = st.session_state.get("course_id", "")
        st.session_state.pop("documents_response", None)
    if result.status == 202 and result.headers.get("location"):
        try:
            location = status_path(result.headers["location"], base_url)
        except ValueError:
            st.warning("The returned status URL is not a valid URL on this backend.")
        else:
            st.session_state["status_url"] = location
            st.session_state["tracking_path"] = location
            st.session_state.pop("status_response", None)
            st.session_state.pop("job_action_response", None)
            st.session_state["auto_refresh"] = False


if page == "Learning":
    render_learning(connection, local_testing=local_testing)

elif page == "Start session (B1)":
    render_session_start(connection, show_response, local_testing=local_testing)
elif page == "Chunk end (B2)":
    render_chunk_end(connection, show_response)
elif page == "Learner interrupt (B4)":
    render_learner_interrupt(connection, show_response)
elif page == "End session (B5)":
    render_session_end(connection, show_response)

elif page == "Revise chunk (A7)":
    render_chunk_revision(connection, show_response)

elif page == "Ingest material":
    st.subheader("A1 · Ingest teaching material")
    st.caption("POST /v1/documents · PDF processing · 202 response contains document_id and job_id")
    source_mode = st.radio("Document source", ["PDF upload", "Signed Moodle URL"], horizontal=True, key="source_mode")
    auto_hash = source_mode == "PDF upload" and st.checkbox("Fill content_hash from the uploaded PDF", key="fill_hash")
    st.caption("content_hash is source metadata; the backend does not verify it.")
    st.button("Start a new submission", on_click=new_submission)
    with st.form("ingestion_form"):
        left, right = st.columns(2)
        course = left.text_input("course_id", placeholder="Moodle course ID", key="course_id")
        activity = right.text_input("activity_id", placeholder="Moodle activity ID", key="activity_id")
        language = left.text_input("language", key="language", help="The document's source language, e.g. it or en-GB.")
        source_ref = right.text_input("source_ref", placeholder="Moodle file ID", key="source_ref")
        version = right.text_input("version (optional)", key="version")
        content_hash = left.text_input("content_hash", disabled=auto_hash, key="content_hash",
                                       placeholder="Computed on submission" if auto_hash else "Upstream hash or revision")
        if source_mode == "PDF upload":
            upload = st.file_uploader("Document (PDF currently supported)", max_upload_size=100, key="document_upload")
            moodle_url = None
        else:
            upload = None
            moodle_url = st.text_input("moodle_url", type="password", placeholder="https://moodle.example/...", key="moodle_url")
        with st.expander("Request options"):
            idempotency_key = st.text_input("Idempotency-Key", key="submission_key",
                                            help="Reuse for a retry. Start a new submission for a new key.")
            mime = st.text_input("Declared file MIME", key="mime", disabled=source_mode != "PDF upload")
        submitted = st.form_submit_button("Send A1 request", type="primary", width="stretch")
    if submitted:
        fields = {"course_id": course.strip(), "activity_id": activity.strip(), "language": language.strip(),
                  "source_ref": source_ref.strip(), "content_hash": content_hash.strip()}
        if auto_hash and upload is not None:
            fields["content_hash"] = hashlib.sha256(upload.getbuffer()).hexdigest()
        if version.strip():
            fields["version"] = version.strip()
        missing = [name for name, value in fields.items() if not value]
        if missing:
            st.error("Enter " + ", ".join(missing) + ".")
        elif source_mode == "PDF upload" and upload is None:
            st.error("Select a document to upload.")
        elif source_mode != "PDF upload" and not moodle_url.strip():
            st.error("Enter a signed Moodle URL.")
        else:
            headers = {"Idempotency-Key": idempotency_key.strip()} if idempotency_key.strip() else {}
            with st.spinner("Sending document to the API…"):
                if upload is not None:
                    result = request(connection, "POST", "/v1/documents", fields=fields,
                                     upload=(upload.name, upload.getvalue(), mime or "application/pdf"), headers=headers)
                else:
                    result = request(connection, "POST", "/v1/documents",
                                     json_body={**fields, "moodle_url": moodle_url.strip()}, headers=headers)
            remember_submission(result)
    show_response(st.session_state.get("ingestion_response"))
    if st.session_state.get("tracking_path"):
        st.info("Open Processing status to follow the latest accepted document.")

elif page == "Generate lesson":
    st.subheader("A3 · Generate a draft lesson")
    st.caption("Known documents are processed in the order supplied. Pending ingestion is awaited.")
    st.button("Start a new lesson generation", on_click=new_lesson)
    st.session_state.setdefault("lesson_key", str(uuid4()))
    with st.form("lesson_form"):
        left, right = st.columns(2)
        lesson_id = left.text_input("lesson_id", key="a3_lesson_id")
        course_id = right.text_input("course_id", key="a3_course_id")
        activity_id = left.text_input("activity_id", key="a3_activity_id")
        language = right.text_input("Generated language", value="en", key="a3_language")
        voice = left.text_input("Polly voice", value="Joanna", key="a3_voice")
        document_ids = st.text_area("Document UUIDs in order (one per line)", key="a3_document_ids")
        window = left.number_input("Source window (pages)", min_value=1, max_value=50, value=5, key="a3_window")
        words = right.number_input("Target chunk length (words)", min_value=30, max_value=1000, value=200, key="a3_words")
        level = left.selectbox("Learner level", ["beginner", "intermediate", "advanced"], index=1, key="a3_level")
        st.caption("Learner level controls how the lesson explains the source material.")
        callback = st.text_input("Middleware callback URL (optional)", key="a3_callback",
                                 help="Requires configured outbound mTLS and OAuth. Leave empty for A5 pull.")
        key = st.text_input("Idempotency-Key", key="lesson_key")
        submitted = st.form_submit_button("Send A3 request", type="primary")
    if submitted:
        try:
            ids = [str(UUID(value.strip())) for value in document_ids.splitlines() if value.strip()]
            if not all((lesson_id.strip(), course_id.strip(), activity_id.strip(), ids, language.strip(), voice.strip())):
                raise ValueError("Enter the lesson, course, activity, documents, language and voice.")
            payload = {"lesson_id": lesson_id.strip(), "course_id": course_id.strip(),
                       "activity_id": activity_id.strip(), "document_ids": ids,
                       "language": language.strip(), "voice": voice.strip(),
                       "options": {"source_window_pages": window, "target_chunk_words": words, "learner_level": level}}
            if callback.strip():
                payload["callback_url"] = callback.strip()
            result = request(connection, "POST", "/v1/lesson-jobs", json_body=payload,
                             headers={"Idempotency-Key": key.strip()} if key.strip() else {})
            st.session_state["lesson_response"] = result
            if result.status == 202 and result.headers.get("location"):
                path = status_path(result.headers["location"], base_url)
                st.session_state["status_url"] = path
                st.session_state["tracking_path"] = path
                for name in ("status_response", "job_action_response", "lesson_result", "lesson_audio"):
                    st.session_state.pop(name, None)
        except ValueError as error:
            st.error(str(error))
    show_response(st.session_state.get("lesson_response"))

elif page == "Manage material":
    st.subheader("A2 · List or remove ingested material")
    st.caption("List documents by course, including processing status and version.")
    course = st.text_input("Course ID to list", key="list_course_id", on_change=list_course_changed)
    if st.button("List documents", type="primary"):
        if not course.strip():
            st.error("Enter a course ID.")
        else:
            st.session_state["documents_response"] = request(
                connection, "GET", "/v1/documents", params={"course_id": course.strip()}
            )
    result = st.session_state.get("documents_response")
    if result and result.successful and isinstance(result.body, list):
        if result.body:
            st.dataframe(result.body, hide_index=True, width="stretch")
        else:
            st.info("No documents found for this course.")
    show_response(result)
    st.divider()
    st.caption("Delete removes all vectors for this document and prevents pending ingestion from publishing. Audit history is retained.")
    with st.form("delete_document"):
        document_id = st.text_input("Document ID to delete", key="delete_document_id")
        delete = st.form_submit_button("Delete document")
    if delete:
        try:
            document_id = str(UUID(document_id.strip()))
        except ValueError:
            st.error("Enter a valid document UUID.")
        else:
            deleted = request(connection, "DELETE", "/v1/documents/" + document_id)
            st.session_state["delete_response"] = deleted
            if deleted.status == 204:
                st.session_state.pop("learning_state", None)
                st.session_state.pop("b1_state", None)
                st.session_state.pop("status_response", None)
                st.session_state["auto_refresh"] = False
                if course.strip():
                    st.session_state["documents_response"] = request(
                        connection, "GET", "/v1/documents", params={"course_id": course.strip()}
                    )
                st.rerun()
    show_response(st.session_state.get("delete_response"))

elif page == "Processing status":
    st.subheader("A4 · Processing status")
    st.caption("GET /v1/jobs/{job_id} · Uses the Location header from your last accepted A1 request")
    if "status_url" not in st.session_state:
        st.session_state["status_url"] = st.session_state.get("tracking_path", "")
    location = st.text_input("Status URL or job UUID", key="status_url", on_change=status_changed)
    auto_refresh = st.toggle("Auto-refresh every 5 seconds", key="auto_refresh")

    @st.fragment(run_every=5 if auto_refresh and location.strip() else None)
    def processing_status():
        manual = st.button("Refresh status", type="primary")
        result = st.session_state.get("status_response")
        terminal = result and result.successful and isinstance(result.body, dict) and result.body.get("status") in ("DONE", "FAILED", "CANCELLED")
        failed_request = result and (result.error or not result.successful)
        if manual or (auto_refresh and location.strip() and not terminal and not failed_request):
            try:
                path = status_path(location, base_url)
            except ValueError as error:
                st.error(str(error))
                return
            result = request(connection, "GET", path, timeout=15)
            st.session_state["status_response"] = result
        if result and result.successful and isinstance(result.body, dict):
            body = result.body
            try:
                progress = max(0.0, min(1.0, float(body.get("progress", 0))))
            except (TypeError, ValueError):
                progress = 0.0
            st.progress(progress, text=f"{body.get('status', '')} · {body.get('step', '')}")
            if body.get("status") in ("DONE", "FAILED", "CANCELLED"):
                st.caption("Processing finished. Automatic polling has stopped.")
        elif failed_request and auto_refresh:
            st.caption("Automatic polling paused after an error. Correct the connection and refresh manually.")
        show_response(result)

    processing_status()

    st.subheader("A5 · Lesson result and audio")
    if st.button("Fetch lesson result", disabled=not location.strip()):
        try:
            path = status_path(location, base_url)
            st.session_state["lesson_result"] = request(connection, "GET", path + "/result")
            st.session_state.pop("lesson_audio", None)
        except ValueError as error:
            st.error(str(error))
    lesson = st.session_state.get("lesson_result")
    show_response(lesson)
    if lesson and lesson.successful and isinstance(lesson.body, dict):
        chunks = lesson.body.get("chunks", [])
        st.caption("AI-generated DRAFT. Download all required audio before acknowledging receipt.")
        if chunks:
            selected = st.selectbox("Audio chunk", range(len(chunks)), format_func=lambda i: f"Chunk {chunks[i]['index'] + 1}")
            if st.button("Fetch chunk audio"):
                path = status_path(location, base_url)
                chunk_id = str(UUID(chunks[selected]["chunk_id"]))
                st.session_state["lesson_audio"] = request(connection, "GET", path + "/audio/" + chunk_id)
            audio = st.session_state.get("lesson_audio")
            if audio and audio.successful and audio.binary:
                st.audio(audio.binary, format="audio/wav")
                st.download_button("Download audio", audio.binary, file_name="lesson-chunk.wav", mime="audio/wav")
            elif audio:
                show_response(audio)

    st.subheader("A6 · Cancel or acknowledge")
    reason = st.selectbox("Job action", ["CANCEL", "RECEIVED"],
                          help="CANCEL stops queued/running work. RECEIVED acknowledges DONE/FAILED output and keeps ingested material.")
    if st.button("Send A6 request", disabled=not location.strip()):
        try:
            path = status_path(location, base_url)
        except ValueError as error:
            st.error(str(error))
        else:
            st.session_state["job_action_response"] = request(connection, "DELETE", path, params={"reason": reason})
    action = st.session_state.get("job_action_response")
    show_response(action)
    if action and action.status == 204:
        st.session_state.pop("learning_state", None)
        st.session_state.pop("b1_state", None)
        st.caption("Job action completed. Refresh status to see its current state.")
    elif action and isinstance(action.body, dict) and action.body.get("detail") == "JOB_CLEANUP_PENDING":
        st.info("Cancellation is recorded. The worker is finishing its current operation before cleanup. Retry the same action for the final 204 response.")

elif page == "Health":
    render_operations(connection, show_response)

else:
    st.subheader("API explorer")
    st.caption("Inspect the live API definition and send requests as endpoints are added.")
    if st.button("Load API definition"):
        result = request(connection, "GET", "/openapi.json", timeout=10)
        st.session_state["schema_response"] = result
        if result.successful and result.is_json and isinstance(result.body, dict):
            st.session_state["api_schema"] = result.body
    schema = st.session_state.get("api_schema")
    if schema:
        operations = []
        for path, methods in schema.get("paths", {}).items():
            for method, definition in methods.items():
                if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    operations.append({"Method": method.upper(), "Path": path,
                                       "Description": definition.get("summary", "")})
        st.dataframe(operations, hide_index=True, width="stretch")
        with st.expander("OpenAPI definition"):
            st.json(schema)
    elif st.session_state.get("schema_response"):
        show_response(st.session_state["schema_response"])
    with st.form("manual_request"):
        left, right = st.columns([1, 3])
        method = left.selectbox("Method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
        path = right.text_input("Endpoint path", value="/health", help="Replace path placeholders with actual IDs.")
        params_text = st.text_area("Query parameters (JSON object)", value="{}", height=80)
        body_text = st.text_area("JSON body (POST / PUT / PATCH / DELETE)", value="{}", height=160,
                                help="For multipart A1 requests, use Ingest material.")
        send = st.form_submit_button("Send request", type="primary")
    if send:
        try:
            params = json.loads(params_text or "{}")
            if not isinstance(params, dict):
                raise ValueError("Query parameters must be a JSON object.")
            body = json.loads(body_text or "{}") if method != "GET" else None
        except ValueError as error:
            st.error(f"Invalid request JSON: {error}")
        else:
            st.session_state["explorer_response"] = request(connection, method, path.strip(),
                                                             params=params, json_body=body)
    show_response(st.session_state.get("explorer_response"))
