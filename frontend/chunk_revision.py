"""Explicit A7 author feedback; submissions are never retried automatically."""

from uuid import UUID

import api_client
import streamlit as st
from learning import binding, course_label, expired, readable_text
from revision_progress import render_revision_progress


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
            state['requested_level'] = choice.get('learner_level') if choice.get('generation_version') == 'a3-v2' else None
            state["lesson"] = None
    if state.get("requested_job"):
        st.session_state["a7_field_job"] = state["requested_job"]
    job_id = st.text_input("Lesson job ID", key="a7_field_job")
    if st.button("Load lesson by job ID", key="a7_load_id"):
        try:
            state["requested_job"] = str(UUID(job_id.strip()))
            state['requested_level'] = None
            state["lesson"] = None
        except ValueError:
            st.error("Enter a valid lesson job UUID.")
    if state.get("requested_job"):
        requested = state.pop("requested_job")
        level = state.pop('requested_level', None)
        options = {'params': {'learner_level': level}} if level else {}
        result = api_client.request(connection, "GET", f"/v1/jobs/{requested}/result", **options)
        state["load_response"] = result
        state["accepted"] = None
        state.pop("submit_response", None)
        state["lesson"] = result.body if result.successful and isinstance(result.body, dict) else None
        st.session_state.pop("a7_chunk_choice", None)
        state.pop("revision_progress", None)
    lesson = state.get("lesson")
    if lesson and lesson.get('variants'):
        level = st.selectbox('Revision level', list(lesson['variants']), key='a7_variant_level')
        lesson = lesson['variants'][level]
    if lesson and isinstance(lesson.get("chunks"), list) and lesson["chunks"]:
        st.caption("Loaded lesson job: " + lesson["job_id"])
        st.write(f"Language: {lesson['language']} · Level: {lesson['learner_level']} · Voice: {lesson['voice']}")
        open_chunk = state.pop("open_chunk_id", None)
        if open_chunk:
            selected_chunk = next((item for item in lesson["chunks"] if item["chunk_id"] == open_chunk), None)
            if selected_chunk:
                st.session_state["a7_chunk_choice"] = selected_chunk
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

            def open_revision(revision):
                state.update(requested_job=revision, requested_level=None, open_chunk_id=chunk["chunk_id"])

            render_revision_progress(connection, state.setdefault("revision_progress", {}), state["accepted"],
                                     show_response, key="a7_progress", on_open=open_revision)
            if st.button("Prepare another edit", key="a7_another"):
                state["accepted"] = None
                state.pop("submit_response", None)
                state.pop("revision_progress", None)
                st.rerun()
        st.caption("Repeated submissions create separate jobs. Requests are not retried automatically.")
    for key in ("load_response", "submit_response"):
        if state.get(key):
            title = "Loaded lesson · A5 HTTP details" if key == "load_response" else "Revision submission · A7 HTTP details"
            with st.expander(title, expanded=not state[key].successful):
                show_response(state[key])
    if not state.get("items") and state.get("catalog"):
        show_response(state["catalog"])
