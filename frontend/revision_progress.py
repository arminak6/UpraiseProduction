"""Inline A4 tracking for accepted A7 revisions; only status GETs are automatic."""

import math

import api_client
import streamlit as st

TERMINAL = {"DONE", "FAILED", "CANCELLED"}


def paused(state):
    result = state.get("response")
    return bool(state.get("invalid_response") or (result and (result.error or not result.successful)))


def render_revision_progress(connection, state, job_id, show_response, *, key, on_open,
                             request_status=None, on_status=None):
    if state.get("job_id") != job_id:
        state.clear()
        state["job_id"] = job_id
    stopped = state.get("body", {}).get("status") in TERMINAL or paused(state)

    @st.fragment(run_every=None if stopped else 5)
    def monitor():
        st.subheader("Revision progress")
        st.caption("Revision job: " + job_id)
        manual = st.button("Refresh revision status", key=key + "_refresh")
        was_stopped = state.get("body", {}).get("status") in TERMINAL or paused(state)
        if manual or not was_stopped:
            path = "/v1/jobs/" + job_id
            response = (request_status(path) if request_status else
                        api_client.request(connection, "GET", path, timeout=15))
            state["response"] = response
            body = response.body
            valid = (response.successful and not response.error and isinstance(body, dict)
                     and body.get("job_id") == job_id
                     and body.get("status") in {"QUEUED", "RUNNING", *TERMINAL})
            state["invalid_response"] = bool(response.successful and not valid)
            if valid:
                state["body"] = body
                if on_status:
                    on_status(body)
            now_stopped = state.get("body", {}).get("status") in TERMINAL or paused(state)
            if now_stopped != was_stopped:
                st.rerun()  # Reschedule the fragment when processing stops or tracking resumes.
        body = state.get("body", {})
        if paused(state):
            st.warning("Revision tracking paused after a status error. Refresh revision status to retry.")
        elif body:
            try:
                progress = float(body.get("progress", 0))
                progress = min(1.0, max(0.0, progress)) if math.isfinite(progress) else 0.0
            except (ValueError, TypeError):
                progress = 0.0
            status = body["status"]
            st.progress(progress, text=f"{status} · {body.get('step', '')} · {progress * 100:.2f}%")
            if status == "DONE":
                levels = (body.get("result") or {}).get("revised_levels", [])
                message = (f"The corresponding chunk has been updated in all {len(levels)} difficulty levels."
                           if len(levels) > 1 else "The lesson's selected chunk has been updated.")
                st.success("Revision complete. " + message)
                if st.button("Open updated lesson", key=key + "_open"):
                    target = (body.get("result") or {}).get("replacement_target_job_id") or job_id
                    on_open(target)
                    st.rerun()
            elif status == "FAILED":
                error = body.get("error") or {}
                st.error("Revision failed: " + str(error.get("code", "Check the job details.")))
                if error.get("reason"):
                    with st.expander("Why this revision failed", expanded=True):
                        level = error.get("learner_level")
                        index = error.get("chunk_index")
                        if level and isinstance(index, int):
                            st.write(f"Level: {level} · Part {index + 1}")
                        st.write(error["reason"])
                        findings = error.get("review") or {}
                        for instruction in findings.get("revision_instructions", []):
                            st.write("Suggested correction: " + str(instruction))
                        if isinstance(error.get("correction_rounds"), int):
                            st.caption(f"Correction attempts: {error['correction_rounds']}")
                        st.caption("This revision was not applied.")
            elif status == "CANCELLED":
                st.info("Revision cancelled.")
            else:
                st.caption("Status refreshes every 5 seconds while this page is open.")
            if status in TERMINAL:
                st.caption("Automatic status checks have stopped.")
        if state.get("response"):
            with st.expander("Revision status · A4 HTTP details", expanded=paused(state)):
                show_response(state["response"])

    monitor()
