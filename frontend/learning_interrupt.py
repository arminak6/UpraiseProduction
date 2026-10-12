"""B1/B4 interaction within the lesson player; no provider or database access."""

import base64
import hashlib
import json
from uuid import uuid4

import api_client
import streamlit as st
from learning import expired, readable_text


def clear_question_widgets(*, keep_session=False):
    for key in list(st.session_state):
        if key.startswith("learning_question_") and not (keep_session and key in {
                "learning_question_auth", "learning_question_consent", "learning_question_jwt"}):
            del st.session_state[key]


def show_error(result):
    detail = result.body.get("detail") if isinstance(result.body, dict) else None
    st.error(result.error or (f"Request failed ({result.status}): {detail}" if detail else
                             f"Request failed ({result.status}). Try again."))


def render_interrupt(connection, player, *, local_testing=False):
    lesson = player["active"]
    chunk = lesson["chunks"][player["index"]]
    state = player.setdefault("interrupt", {})
    st.subheader("Ask your teacher")
    st.caption("You are interacting with an AI teacher.")
    st.caption("Lesson audio is paused. Ask a question or request another explanation of this part.")
    mode = st.radio("Question session", ["Local draft preview", "Signed Middleware session"],
                    index=0 if local_testing else 1, key="learning_question_auth")
    token, consent = "", False
    if mode == "Local draft preview":
        consent = st.checkbox("Enable questions for this lesson", key="learning_question_consent")
    else:
        token = st.text_input("Middleware session JWT", type="password", key="learning_question_jwt",
                              help="The signed session must allow questions for this lesson.").strip()
    identity = hashlib.sha256(json.dumps({"job": lesson["job_id"], 'level': lesson.get('learner_level'), "mode": mode,
        "consent": consent, "token": hashlib.sha256(token.encode()).hexdigest()}, sort_keys=True).encode()).hexdigest()
    if state.get("identity") != identity:
        state.clear()
        state.update(identity=identity, local_id=str(uuid4()), session=None, start_error=None, turn=None)
        player["pending_action"] = None
    started = state["session"]
    if started:
        st.info(started["ai_notice"])
        if expired(started):
            st.warning("Your question session has expired. Start a new question session to continue asking.")
    if st.button("New question session"):
        state.update(local_id=str(uuid4()), session=None, start_error=None, turn=None)
        player["pending_action"] = None
        st.rerun()
    message_mode = st.radio("Ask with", ["Text", "Audio WAV"], key="learning_question_input")
    message = st.text_area("Your question or request", max_chars=6000,
                            key="learning_question_text") if message_mode == "Text" else None
    upload = st.file_uploader("Your recording", type=["wav"], max_upload_size=6,
                              key="learning_question_audio") if message_mode == "Audio WAV" else None
    audio = upload.getvalue() if upload is not None else None
    if message_mode == "Audio WAV":
        st.caption("Mono 16-bit PCM WAV, 8–48 kHz, up to 60 seconds. Use the lesson's language.")
    context = hashlib.sha256(json.dumps({"identity": identity, "chunk": chunk["chunk_id"],
        "text": message, "mode": message_mode,
        "audio": hashlib.sha256(audio).hexdigest() if audio is not None else None}, sort_keys=True).encode()).hexdigest()
    if not state["turn"] or state["turn"]["context"] != context:
        state["turn"] = {"context": context, "request_id": str(uuid4()), "attempted": False, "response": None}
        player["pending_action"] = None
    turn = state["turn"]
    send, retry = st.columns(2)
    submit = send.button("Ask teacher", type="primary", width="stretch")
    repeat = retry.button("Retry question", disabled=not turn["attempted"], width="stretch")
    if submit or repeat:
        if mode == "Local draft preview" and not consent:
            st.error("Enable questions before sending your message.")
        elif mode == "Signed Middleware session" and not token:
            st.error("Enter the signed Middleware session JWT.")
        elif started and expired(started):
            st.error("Start a new question session before sending this message.")
        elif message_mode == "Text" and not (message or "").strip():
            st.error("Enter your question or request.")
        elif message_mode == "Audio WAV" and not audio:
            st.error("Select a WAV recording.")
        else:
            headers = {"X-UPRAISE-Session-JWT": token} if token else None
            with st.spinner("Your teacher is preparing a reply…"):
                if not started:
                    body = {key: lesson[key] for key in ("course_id", "activity_id", "lesson_id", "language")}
                    body["lesson_job_id"] = lesson["job_id"]
                    if lesson.get('generation_version') == 'a3-v2':
                        body['learner_level'] = lesson['learner_level']
                    if mode == "Local draft preview":
                        body.update(session_id=state["local_id"], consent_scope=["questions"])
                    result = api_client.request(connection, "POST", "/v1/sessions", json_body=body, headers=headers)
                    if result.successful and not result.error and isinstance(result.body, dict):
                        state.update(session=result.body, session_token=token, start_error=None)
                        started = result.body
                    else:
                        state["start_error"] = result
                if started:
                    fields = {"request_id": turn["request_id"], "chunk_id": chunk["chunk_id"]}
                    options = {"headers": headers, "timeout": 600}
                    if message_mode == "Text":
                        options["json_body"] = {**fields, "text": message.strip()}
                    else:
                        options.update(fields={key: str(value) for key, value in fields.items()},
                                       upload=("recording.wav", audio, "audio/wav"), upload_field="audio")
                    turn["response"] = api_client.request(connection, "POST",
                        f"/v1/sessions/{started['session_id']}/interrupts", **options)
                    turn["attempted"] = True
                    response = turn["response"]
                    if response.successful and not response.error and isinstance(response.body, dict):
                        player["pending_action"] = response.body.get("playback_action")
            st.rerun()
    if state["start_error"]:
        show_error(state["start_error"])
    result = turn["response"]
    if result:
        if not result.successful or result.error or not isinstance(result.body, dict):
            show_error(result)
        else:
            body = result.body
            with st.chat_message("assistant"):
                if body.get("text"):
                    st.markdown(readable_text(body["text"]))
                if body.get("audio"):
                    try:
                        with st.container(key=f"teacher_answer_{turn['request_id']}"):
                            st.audio(base64.b64decode(body["audio"]["data"], validate=True),
                                     format="audio/wav", autoplay=True)
                    except (ValueError, KeyError, TypeError):
                        st.warning("The teacher's audio could not be played.")
                if body.get("grounded") is not None:
                    st.caption("Supported by course sources: " + ("yes" if body["grounded"] else "no"))
                if body.get("sources"):
                    with st.expander("Course sources used"):
                        st.json(body["sources"])
                for warning in body.get("warnings", []):
                    if warning == "GROUNDING_NOT_VERIFIED":
                        st.caption("This reply has not been independently verified against the course sources.")
                    else:
                        st.warning(warning)
                action = body.get("playback_action")
                if action:
                    labels = {"REPLAY": "Replay this part", "RESUME": "Resume this part",
                              "PAUSE": "Keep the lesson paused", "SET_SPEED": "Change playback speed"}
                    st.info(labels.get(action["action"], action["action"]) +
                            (f" to {action['rate']}×" if action.get("rate") else ""))
                    st.caption("Select Continue course when ready to apply the playback instruction.")
