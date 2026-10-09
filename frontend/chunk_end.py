"""Explicit B2 tests using a supplied measurement, without automatic playback."""

import hashlib
import json
from uuid import UUID

import api_client
import streamlit as st
from learning import binding, expired


def render_chunk_end(connection, show_response):
    state = st.session_state.setdefault("b2_state", {})
    current = binding(connection)
    if state.get("binding") != current:
        state.clear()
        state.update(binding=current, response=None, context=None, session_id=None)
        for key in list(st.session_state):
            if key.startswith("b2_field_"):
                del st.session_state[key]
    st.header("B2 · Chunk-end decision")
    st.write("Test whether the learner should proceed after a teaching chunk.")
    st.caption("This test uses a supplied score: above 0.5 means high load; 0.5 or below means proceed. "
               "Without Telemetry consent in B1, the backend always returns PROCEED.")
    b1 = st.session_state.get("b1_state", {})
    result = b1.get("response") if b1.get("binding") == current else None
    started = result.body if result and result.successful and isinstance(result.body, dict) else None
    if started and not expired(started):
        if state.get("b1_session") != started["session_id"]:
            for key in list(st.session_state):
                if key.startswith("b2_field_"):
                    del st.session_state[key]
            state.update(b1_session=started["session_id"], response=None, context=None)
        st.session_state.setdefault("b2_field_session", started["session_id"])
        st.session_state.setdefault("b2_field_lesson", started["lesson_id"])
        if not started.get("preview"):
            st.session_state.setdefault("b2_field_jwt", st.session_state.get("b1_field_jwt", ""))
    session_id = st.text_input("Session ID from B1", key="b2_field_session", max_chars=36).strip()
    lesson_id = st.text_input("Lesson ID", key="b2_field_lesson", max_chars=120).strip()
    chunk_id = st.text_input("Completed chunk ID", key="b2_field_chunk", max_chars=36).strip()
    score = st.number_input("Cognitive load score (test measurement)", min_value=0.0, max_value=1.0,
                            value=0.7, step=0.1, key="b2_field_score")
    token = st.text_input("Middleware session JWT (production only)", type="password", key="b2_field_jwt").strip()
    context = hashlib.sha256(json.dumps([session_id, lesson_id, chunk_id, score,
        hashlib.sha256(token.encode()).hexdigest()]).encode()).hexdigest()
    if context != state["context"]:
        try:
            tracked_session = str(UUID(session_id))
        except ValueError:
            tracked_session = None
        state.update(context=context, response=None, session_id=tracked_session)
    if st.button("Check chunk end", type="primary"):
        try:
            session_id, chunk_id = str(UUID(session_id)), str(UUID(chunk_id))
            if not lesson_id:
                raise ValueError
        except ValueError:
            st.error("Enter the lesson ID and valid session/chunk UUIDs returned by B1/A5.")
        else:
            state["response"] = api_client.request(connection, "POST", f"/v1/sessions/{session_id}/chunk-end",
                json_body={"lesson_id": lesson_id, "chunk_id": chunk_id, "cognitive_load_score": score},
                headers={"X-UPRAISE-Session-JWT": token} if token else None)
    result = state.get("response")
    if result and result.successful and isinstance(result.body, dict):
        if result.body.get("decision") == "HIGH_COGNITIVE_LOAD":
            st.warning("High cognitive load: Middleware can pause and offer learner support.")
        elif result.body.get("decision") == "PROCEED":
            st.success("Proceed to the next chunk.")
    show_response(result)
