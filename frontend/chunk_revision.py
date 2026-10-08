"""Explicit A7 author feedback; submissions are never retried automatically."""

from uuid import UUID

import api_client
import streamlit as st
from learning import binding, course_label, expired, readable_text


def render_chunk_revision(connection, show_response):
    state = st.session_state.setdefault("a7_state", {})
    current = binding(connection)
    if state.get("binding") != current:
        state.clear()
        state.update(binding=current, items=[], lesson=None, accepted=None)
        st.session_state.pop("a7_field_feedback", None)
    st.header("Revise teaching chunk (A7)")
    st.caption("Select a prepared lesson and describe the changes. Its language, level and voice are preserved.")
    if st.button("Refresh available lessons", key="a7_refresh"):
        result = api_client.request(connection, "GET", "/v1/lesson-jobs", params={"limit": 100})
        state["catalog"] = result
        state["items"] = result.body if result.successful and isinstance(result.body, list) else []
    items = [item for item in state.get("items", []) if not expired(item)]
    if items:
        choice = st.selectbox("Prepared lesson", items, format_func=course_label, key="a7_lesson_choice")
        if st.button("Load selected lesson", key="a7_load_selected"):
            state["requested_job"] = choice["job_id"]
            state["lesson"] = None
    job_id = st.text_input("Lesson job ID", key="a7_field_job")
    if st.button("Load lesson by job ID", key="a7_load_id"):
        try:
            state["requested_job"] = str(UUID(job_id.strip()))
            state["lesson"] = None
        except ValueError:
            st.error("Enter a valid lesson job UUID.")
    if state.get("requested_job"):
        requested = state.pop("requested_job")
        result = api_client.request(connection, "GET", f"/v1/jobs/{requested}/result")
        state["load_response"] = result
        state["accepted"] = None
        state.pop("submit_response", None)
        state["lesson"] = result.body if result.successful and isinstance(result.body, dict) else None
    lesson = state.get("lesson")
    if lesson and isinstance(lesson.get("chunks"), list) and lesson["chunks"]:
        st.write(f"Language: {lesson['language']} · Level: {lesson['learner_level']} · Voice: {lesson['voice']}")
        chunk = st.selectbox("Teaching chunk", lesson["chunks"],
                             format_func=lambda value: f"Part {value['index'] + 1}", key="a7_chunk_choice")
        selected = (lesson["job_id"], chunk["chunk_id"])
        if state.get("selected") != selected:
            state["selected"] = selected
            st.session_state["a7_field_feedback"] = ""
        st.markdown(readable_text(chunk["text"]))
        with st.form("a7_revision_form"):
            feedback = st.text_area("Teacher review / requested changes", max_chars=8000, key="a7_field_feedback")
            submit = st.form_submit_button("Generate revised chunk", disabled=bool(state.get("accepted")))
        if submit:
            if not feedback.strip():
                st.error("Describe the changes requested by the teacher.")
            elif expired(lesson):
                st.error("This lesson has expired. Load an available lesson first.")
            else:
                result = api_client.request(connection, "POST", "/v1/audio:regenerate", json_body={
                    "lesson_job_id": lesson["job_id"], "chunk_id": chunk["chunk_id"], "teacher_review": feedback.strip(),
                })
                state["submit_response"] = result
                if result.status == 202 and isinstance(result.body, dict) and result.body.get("job_id"):
                    state["accepted"] = result.body["job_id"]
                    st.session_state["status_url"] = f"/v1/jobs/{state['accepted']}"
                    st.session_state["tracking_path"] = st.session_state["status_url"]
                    st.rerun()
        if state.get("accepted"):
            st.success(f"Revision job: {state['accepted']}")
            st.caption("Use Processing status to check progress and fetch text/audio. The completed version also appears in Learning.")
            if st.button("Prepare another edit", key="a7_another"):
                state["accepted"] = None
                state.pop("submit_response", None)
                st.rerun()
        st.caption("Repeated submissions create separate jobs. Requests are not retried automatically.")
    for key in ("load_response", "submit_response"):
        if state.get(key):
            show_response(state[key])
    if not state.get("items") and state.get("catalog"):
        show_response(state["catalog"])
