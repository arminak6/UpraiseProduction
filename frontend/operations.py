"""Explicit C1/C2/C5 checks and confirmed C3 administration; no automatic requests."""

import api_client
import streamlit as st
from learning import binding


def render_operations(connection, show_response):
    identity = binding(connection)
    if st.session_state.get("operations_binding") != identity:
        for key in ("health_response", "ready_response", "feature_response", "version_response",
                    "kill_switch_response", "operations_admin_confirm"):
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
    st.divider()
    render_controls(connection, show_response)
    st.divider()
    st.subheader("C5 · Deployed versions")
    st.caption("Current model IDs, prompt hashes, pipeline versions and the cognitive-load test policy. No AI calls.")
    if st.button("Check versions"):
        st.session_state["version_response"] = api_client.request(connection, "GET", "/v1/version", timeout=5)
    show_response(st.session_state.get("version_response"))


def clear_control_result():
    st.session_state.pop("kill_switch_response", None)
    st.session_state.pop("operations_admin_confirm", None)


def render_controls(connection, show_response):
    st.subheader("C3 · Runtime kill switch")
    st.caption("Requires an administrator token. Disable new AI work or release a runtime disable.")
    if st.session_state.pop("operations_admin_reset_confirm", False):
        st.session_state.pop("operations_admin_confirm", None)
    scope = st.selectbox("Kill-switch scope", ["Course", "Global"], key="operations_admin_scope",
                         on_change=clear_control_result)
    course = st.text_input("Kill-switch course ID", key="operations_admin_course",
                           on_change=clear_control_result) if scope == "Course" else ""
    feature = st.selectbox("Feature to control", ["answers", "generation", "adaptive_levels", "stt", "tts", "all"],
                           key="operations_admin_feature", on_change=clear_control_result)
    action = st.radio("Kill-switch action", ["Disable feature", "Release runtime disable"],
                      key="operations_admin_action", on_change=clear_control_result)
    target = f"course {course.strip()}" if scope == "Course" else "all courses"
    st.write(f"{action}: {feature} for {target}.")
    if action == "Release runtime disable":
        st.caption("Deployment settings and other active switches can still keep the feature disabled.")
    confirmed = st.checkbox("Confirm this kill-switch change", key="operations_admin_confirm")
    if st.button("Apply kill-switch change", disabled=not confirmed or (scope == "Course" and not course.strip())):
        payload = {"scope": "course_id" if scope == "Course" else "global", "feature": feature,
                   "enabled": action == "Disable feature"}
        if scope == "Course":
            payload["course_id"] = course.strip()
        result = api_client.request(connection, "PUT", "/v1/admin/kill-switch", json=payload)
        st.session_state["kill_switch_response"] = result
        if result.successful:
            st.session_state.pop("feature_response", None)
            st.session_state["operations_admin_reset_confirm"] = True
            st.rerun()
    result = st.session_state.get("kill_switch_response")
    if result and result.successful and isinstance(result.body, dict) and result.body.get("audit_pending"):
        st.warning("Change applied; audit delivery is pending.")
    show_response(result)
