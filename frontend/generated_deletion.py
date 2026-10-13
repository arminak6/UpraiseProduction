"""B7 generated teaching content management through HTTP only."""

import re
from uuid import UUID

import api_client
import streamlit as st
from learning import binding, course_label, expired

PAGE = "Delete teaching content (B7)"


def open_generated_deletion(job_id, level, source_binding):
    st.session_state["b7_prefill"] = {"job_id": job_id, "level": level, "binding": source_binding}
    st.session_state["page"] = PAGE


def invalidate_generated_content(job_id, *, whole=False):
    workspace = st.session_state.get("ingestion_workspace", {})
    job = workspace.get("jobs", {}).get(job_id)
    if job:
        for key in ("result", "result_response", "audio", "audio_response", "export", "status_body", "status_response"):
            job.pop(key, None)
        if whole:
            job["action_completed"] = "B7"
    workspace.update(catalog=[], catalog_context=None)
    workspace.pop("catalog_response", None)
    for key in ("iw_field_result_level", "iw_field_chunk"):
        st.session_state.pop(key, None)
    revision = st.session_state.get("a7_state", {})
    revision["items"] = [item for item in revision.get("items", []) if item.get("job_id") != job_id]
    if (revision.get("lesson") or {}).get("job_id") == job_id:
        for key in ("lesson", "load_response", "submit_response", "accepted", "revision_progress", "selected"):
            revision.pop(key, None)
    learning = st.session_state.get("learning_state", {})
    if (learning.get("active") or {}).get("job_id") == job_id:
        st.session_state.pop("learning_state", None)
        st.session_state.pop("b1_state", None)
    cached = st.session_state.get("lesson_result")
    cached = cached.body if isinstance(cached, api_client.Result) else cached
    if isinstance(cached, dict) and cached.get("job_id") == job_id:
        st.session_state.pop("lesson_result", None)
        st.session_state.pop("lesson_audio", None)


def refresh_catalog(connection, state, *, more=False):
    offset = state.get("offset", 0) if more else 0
    response = api_client.request(connection, "GET", "/v1/lesson-jobs", params={"limit": 100, "offset": offset})
    state["catalog_response"] = response
    items = []
    if response.successful and isinstance(response.body, list):
        try:
            items = [{**item, "job_id": str(UUID(item["job_id"]))} for item in response.body if not expired(item)]
            if any(not item.get("learner_level") for item in items):
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError):
            state["catalog_invalid"] = True
            items = []
        else:
            state["catalog_invalid"] = False
        state.update(offset=offset + len(response.body), more=len(response.body) == 100)
    state["items"] = [*state.get("items", []), *items] if more else items


def render_generated_deletion(connection, show_response):
    state = st.session_state.setdefault("b7_state", {})
    current = binding(connection)
    if state.get("binding") != current:
        state.clear()
        state.update(binding=current, items=[])
        for key in list(st.session_state):
            if key.startswith("b7_field_"):
                st.session_state.pop(key, None)
    prefill = st.session_state.pop("b7_prefill", None)
    if prefill and prefill.get("binding") == current and not state.get("pending"):
        st.session_state.update(b7_field_job=prefill["job_id"], b7_field_level=prefill["level"],
                                b7_field_scope="Selected level", b7_field_confirm=False)
        state.pop("response", None)
        state.pop("deleted", None)
        st.session_state.pop("b7_field_choice", None)
    if state.pop("reset_confirmation", False):
        st.session_state["b7_field_confirm"] = False
        st.session_state.pop("b7_field_choice", None)
    st.header("Delete generated teaching content (B7)")
    st.caption("Delete saved teaching text and audio. Ingested documents remain available for new generation.")
    if st.button("Refresh prepared lessons", key="b7_refresh") or "catalog_response" not in state:
        refresh_catalog(connection, state)
    if state.get("more") and st.button("Load more prepared lessons", key="b7_more"):
        refresh_catalog(connection, state, more=True)
    pending = state.get("pending")
    items = state.get("items", [])
    if items and not pending:
        choice = st.selectbox("Prepared lesson", items, index=None, format_func=course_label,
                              key="b7_field_choice", placeholder="Choose a generated lesson and difficulty")
        context = (choice["job_id"], choice["learner_level"]) if choice else None
        if choice and context != state.get("choice_context"):
            st.session_state.update(b7_field_job=choice["job_id"], b7_field_level=choice["learner_level"],
                                    b7_field_confirm=False)
        state["choice_context"] = context
    elif not items:
        st.info("No available prepared lessons are listed. You can enter a known job ID to delete retained text after expiry.")
    if state.get("catalog_invalid"):
        st.error("The lesson list contains invalid identities. Refresh it or enter the lesson job ID.")
    job = st.text_input("Lesson job ID", key="b7_field_job", disabled=bool(pending))
    scope = st.radio("Delete scope", ["Selected level", "All generated levels"], key="b7_field_scope",
                     horizontal=True, disabled=bool(pending))
    level = st.text_input("Difficulty level", value="intermediate", key="b7_field_level",
                          disabled=bool(pending) or scope != "Selected level")
    context = (job.strip(), scope, level.strip() if scope == "Selected level" else None)
    if context != state.get("confirm_context"):
        state["confirm_context"] = context
        st.session_state["b7_field_confirm"] = False
        if not pending:
            state.pop("response", None)
            state.pop("deleted", None)
    if pending:
        st.info("Cleanup may still be pending. Retry the same deletion to check; requests are never retried automatically.")
        submit = st.button("Retry deletion · B7", key="b7_retry")
        payload = pending
    else:
        target = level.strip() + " level" if scope == "Selected level" else "all generated levels"
        confirm = st.checkbox("Confirm deletion of " + target + " for this lesson", key="b7_field_confirm")
        submit = st.button("Delete generated content · B7", key="b7_delete", disabled=not confirm)
        payload = {"job_id": job.strip(), "level": level.strip() if scope == "Selected level" else None}
    if submit:
        try:
            payload["job_id"] = str(UUID(payload["job_id"]))
            if payload["level"] is not None and not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", payload["level"]):
                raise ValueError("Enter a valid difficulty level.")
        except ValueError:
            st.error("Enter a valid lesson job UUID and difficulty level.")
        else:
            options = {"params": {"learner_level": payload["level"]}} if payload["level"] else {}
            with st.spinner("Deleting generated content…"):
                response = api_client.request(connection, "DELETE", "/v1/lesson-jobs/" + payload["job_id"], **options)
            state["response"] = response
            if response.status == 204 and not response.error:
                state.update(deleted=payload, reset_confirmation=True)
                state.pop("pending", None)
                state.pop("catalog_response", None)
                known = {item["learner_level"] for item in items if item["job_id"] == payload["job_id"]}
                invalidate_generated_content(payload["job_id"], whole=payload["level"] is None or known == {payload["level"]})
                st.rerun()
            elif response.status == 503:
                state["pending"] = dict(payload)
                invalidate_generated_content(payload["job_id"])
                st.rerun()
    if state.get("deleted") and not state.get("pending"):
        deleted = state["deleted"]
        target = deleted["level"] + " level" if deleted["level"] else "All generated levels"
        st.success(target + " deleted for lesson " + deleted["job_id"] + ".")
    if state.get("response"):
        with st.expander("B7 HTTP response", expanded=not state["response"].successful):
            show_response(state["response"])
    if state.get("catalog_response") and not state["catalog_response"].successful:
        show_response(state["catalog_response"])
