"""B1 starts the player session; retries preserve the selected lesson and UUID."""

import hashlib
import json
from uuid import UUID, uuid4

import api_client
import streamlit as st


def session_options(*, local_testing=False):
    with st.expander("Session settings", expanded=not local_testing):
        mode = st.radio("Session authorization", ["Local draft preview", "Signed Middleware session"],
                        index=0 if local_testing else 1, key="learning_session_mode")
        token, questions = "", True
        if mode == "Local draft preview":
            questions = st.checkbox("Enable questions for this lesson", value=True,
                                    key="learning_session_consent")
            st.caption("Development preview of an AI-generated draft.")
        else:
            token = st.text_input("Middleware session JWT", type="password", key="learning_session_jwt",
                                  help="The signed assertion selects this lesson and permits questions.").strip()
    return {"mode": mode, "token": token, "questions": questions}


def start_lesson(connection, state, job_id, level, options):
    from learning import load_part

    if options["mode"] == "Signed Middleware session" and not options["token"]:
        state["error"] = "Enter the signed Middleware session JWT in Session settings."
        return
    context = hashlib.sha256(json.dumps({"job": job_id, "level": level,
        "mode": options["mode"], "questions": options["questions"],
        "token": hashlib.sha256(options["token"].encode()).hexdigest()}, sort_keys=True).encode()).hexdigest()
    previous = state.get("start_attempt") or {}
    if previous.get("context") != context:
        previous = {"context": context, "local_id": str(uuid4())}
        state["start_attempt"] = previous
    load_part(connection, state, job_id, 0, level)
    lesson = state.get("active")
    if not lesson:
        return
    body = {key: lesson[key] for key in ("course_id", "activity_id", "lesson_id", "language")}
    body["lesson_job_id"] = lesson["job_id"]
    if lesson.get("generation_version") == "a3-v2":
        body["learner_level"] = lesson["learner_level"]
    if options["mode"] == "Local draft preview":
        body.update(session_id=previous["local_id"], consent_scope=["questions"] if options["questions"] else [])
    headers = {"X-UPRAISE-Session-JWT": options["token"]} if options["token"] else None
    result = api_client.request(connection, "POST", "/v1/sessions", json_body=body, headers=headers)
    valid = result.status in (200, 201) and not result.error and isinstance(result.body, dict)
    if valid:
        try:
            valid = (str(UUID(result.body["session_id"])) and result.body["lesson_job_id"] == lesson["job_id"]
                     and result.body["learner_level"] == lesson["learner_level"])
        except (KeyError, TypeError, ValueError):
            valid = False
    if not valid:
        state.update(active=None, audio=None, error=result.error or
                     f"The lesson session could not be started ({result.status}). Press Start lesson to retry.")
        return
    state["interrupt"] = {"session": result.body, "session_token": options["token"],
                          "questions_enabled": options["questions"], "turn": None}
    state.pop("start_attempt", None)
