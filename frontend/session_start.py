"""Explicit B1 session tests using A8 discovery and the normal HTTP client."""

import hashlib
import json
from uuid import UUID, uuid4

import api_client
import streamlit as st
from learning import binding, course_label, expired, refresh_courses


def payload_for(item, *, job_id="", course_id="", activity_id="", lesson_id="", language="", documents=""):
    if item is not None:
        return {name: item[name] for name in ("course_id", "activity_id", "lesson_id", "language")} | {
            "lesson_job_id": str(UUID(item["job_id"])),
        }
    if not all(value.strip() for value in (course_id, activity_id, lesson_id, job_id)):
        raise ValueError("Enter the lesson job ID, course ID, activity ID and lesson ID.")
    try:
        job_id = str(UUID(job_id.strip()))
        document_ids = [str(UUID(value.strip())) for value in documents.replace(",", "\n").splitlines() if value.strip()]
    except ValueError:
        raise ValueError("Use valid UUIDs for the lesson job and document IDs.") from None
    if len(document_ids) > 50 or len(document_ids) != len(set(document_ids)):
        raise ValueError("Use at most 50 unique document IDs.")
    body = {"lesson_job_id": job_id, "course_id": course_id.strip(), "activity_id": activity_id.strip(),
            "lesson_id": lesson_id.strip()}
    if language.strip():
        body["language"] = language.strip()
    if document_ids:
        body["document_ids"] = document_ids
    return body


def render_session_start(connection, show_response, *, local_testing=False):
    current = binding(connection)
    state = st.session_state.get("b1_state")
    if state is None or state["binding"] != current:
        state = {"binding": current, "items": [], "loaded": False, "more": False, "active": None,
                 "response": None, "context": None, "attempted": False, "local_id": str(uuid4())}
        st.session_state["b1_state"] = state
        for name in list(st.session_state):
            if name.startswith("b1_field_"):
                del st.session_state[name]
    st.header("B1 · Start a lesson session")
    st.write("Select a prepared lesson and open a temporary learner session.")
    if st.button("Refresh available lessons") or not state["loaded"]:
        with st.spinner("Loading lessons…"):
            refresh_courses(connection, state)
        state["loaded"] = True
    if state.get("catalog_error"):
        st.info("Available lessons could not be listed. You can enter the lesson details below.")
    items = {item["job_id"]: item for item in state["items"] if not expired(item)}
    choices = [*items, "manual"]
    if st.session_state.get("b1_field_choice") not in choices:
        st.session_state.pop("b1_field_choice", None)
    selected = st.selectbox("Lesson to start", choices, key="b1_field_choice",
                            format_func=lambda value: "Enter lesson details" if value == "manual" else course_label(items[value]))
    if not items and not state.get("catalog_error"):
        st.info("No available lessons were found. Generate a lesson or enter an available job's details.")
    if state["more"] and st.button("Load more lessons"):
        refresh_courses(connection, state, more=True)
        st.rerun()
    values = {}
    if selected == "manual":
        for field, label in (("job_id", "Lesson job ID"), ("course_id", "Course ID"),
                             ("activity_id", "Activity ID"), ("lesson_id", "Lesson ID")):
            values[field] = st.text_input(label, key="b1_field_" + field,
                                          max_chars=36 if field == "job_id" else 120)
        with st.expander("Optional lesson scope"):
            values["language"] = st.text_input("Language (optional)", key="b1_field_language", max_chars=35)
            values["documents"] = st.text_area("Document IDs (optional)", key="b1_field_documents",
                                                help="Leave empty to use the lesson's sources, or enter a subset, one UUID per line.")
    else:
        item = items[selected]
        st.caption(f"Course: {item['course_id']} · Activity: {item['activity_id']} · {item['chunk_count']} parts")

    mode = st.radio("Session authorization", ["Local draft preview", "Signed Middleware session"],
                    index=0 if local_testing else 1, key="b1_field_mode")
    token, consent = "", []
    if mode == "Local draft preview":
        st.caption("Requires the backend's explicit local testing mode. This opens a draft preview.")
        with st.expander("Simulate consent for local testing"):
            if st.checkbox("Questions consent", key="b1_field_questions"):
                consent.append("questions")
            if st.checkbox("Telemetry consent", key="b1_field_telemetry"):
                consent.append("telemetry")
        st.caption("Questions consent enables B4 learner interruptions. Cognitive-load APIs remain reserved.")
    else:
        token = st.text_input("Middleware session JWT", type="password", key="b1_field_jwt",
                              help="Separate from the sidebar service bearer token. Middleware signs session, consent and approval claims.").strip()

    error = None
    try:
        body = payload_for(items.get(selected), **values)
    except (ValueError, KeyError, TypeError) as invalid:
        body, error = None, str(invalid)
    context = hashlib.sha256(json.dumps({"body": body, "mode": mode, "consent": consent,
                                        "jwt_hash": hashlib.sha256(token.encode()).hexdigest()}, sort_keys=True).encode()).hexdigest()
    if state["context"] != context:
        state.update(context=context, local_id=str(uuid4()), response=None, attempted=False)
    if body is not None and mode == "Local draft preview":
        body.update(session_id=state["local_id"], consent_scope=consent)
        st.caption("Local session ID (kept for retries)")
        st.code(state["local_id"], language=None)
    if body is not None:
        with st.expander("Request body"):
            st.json(body)

    start, retry, fresh = st.columns(3)
    send = start.button("Start session", type="primary", width="stretch")
    repeat = retry.button("Retry session start", disabled=not state["attempted"], width="stretch")
    if fresh.button("New local session", disabled=mode != "Local draft preview", width="stretch"):
        state.update(local_id=str(uuid4()), response=None, attempted=False)
        st.rerun()
    if send or repeat:
        if error:
            st.error(error)
        elif mode == "Signed Middleware session" and not token:
            st.error("Enter the signed Middleware session JWT.")
        else:
            with st.spinner("Starting lesson session…"):
                state["response"] = api_client.request(connection, "POST", "/v1/sessions", json_body=body,
                                                        headers={"X-UPRAISE-Session-JWT": token} if token else None)
            state["attempted"] = True
            st.rerun()

    result = state["response"]
    if result and result.successful and isinstance(result.body, dict):
        value = result.body
        st.success("New lesson session created." if result.status == 201 else "Existing lesson session returned.")
        if value.get("ai_notice"):
            st.info(value["ai_notice"])
        if value.get("session_id"):
            st.caption("Session ID")
            st.code(value["session_id"], language=None)
        st.caption(f"Language: {value.get('language', '')} · Level: {value.get('learner_level', '')} · "
                   f"Expires (UTC): {value.get('expires_at', '')}")
        if value.get("preview"):
            st.caption("Draft preview · Human approval remains separate.")
        if expired(value):
            st.warning("This session's reported expiry has passed. Start a new session with an available lesson.")
        st.caption("This is the last B1 response. Restarting the backend removes sessions held in memory.")
    show_response(result)
