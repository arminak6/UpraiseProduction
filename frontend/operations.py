"""Explicit C1/C2 HTTP checks; no automatic AI calls or changes to operator controls."""

import api_client
import streamlit as st
from learning import binding


def render_operations(connection, show_response):
    identity = binding(connection)
    if st.session_state.get("operations_binding") != identity:
        for key in ("ready_response", "feature_response"):
            st.session_state.pop(key, None)
        st.session_state["operations_binding"] = identity
    st.subheader("C1 · Backend health and readiness")
    st.caption("Liveness checks the application. Readiness reports storage, workers and recent provider observations.")
    if st.button("Check backend health", type="primary"):
        st.session_state["health_response"] = api_client.request(connection, "GET", "/health", timeout=5)
    show_response(st.session_state.get("health_response"))
    if st.button("Check readiness"):
        st.session_state["ready_response"] = api_client.request(connection, "GET", "/ready", timeout=5)
    show_response(st.session_state.get("ready_response"))
    st.divider()
    st.subheader("C2 · AI feature availability")
    st.caption("UNKNOWN means no recent verification. Availability does not replace session consent or authorization.")
    course = st.text_input("Course ID (optional)", key="operations_course_id",
                           on_change=lambda: st.session_state.pop("feature_response", None))
    if st.button("Check feature status"):
        st.session_state["feature_response"] = api_client.request(connection, "GET", "/v1/status", timeout=5,
                                                                  params={"course_id": course.strip()} if course.strip() else {})
    result = st.session_state.get("feature_response")
    if result and result.successful and isinstance(result.body, dict):
        rows = [{"Feature": name, "Enabled": value.get("enabled"), "State": value.get("state"),
                 "Reason": ", ".join(value.get("reason_codes", []))}
                for name, value in result.body.get("features", {}).items()]
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch")
    show_response(result)
