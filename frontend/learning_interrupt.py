"""B4 questions reuse the B1 session opened by Start lesson."""

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
    state = player["interrupt"]
    st.subheader("Ask your teacher")
    st.caption("You are interacting with an AI teacher.")
    st.caption("Lesson audio is paused. Ask a question or request another explanation of this part.")
    started = state["session"]
    token = state.get("session_token", "")
    if not state.get("questions_enabled", True):
        st.info("Questions were disabled when this session started. Start a new lesson session to enable them.")
        return
    message = st.text_area("Your question", max_chars=6000, key="learning_question_text")
    context = hashlib.sha256(json.dumps({"session": started["session_id"], "chunk": chunk["chunk_id"],
        "text": message.strip(), "offset_ms": player.get("resume_offset_ms", 0)}, sort_keys=True).encode()).hexdigest()
    if not state.get("turn") or state["turn"]["context"] != context:
        state["turn"] = {"context": context, "request_id": str(uuid4()), "attempted": False, "response": None}
        player["pending_action"] = None
    turn = state["turn"]
    send, retry = st.columns(2)
    submit = send.button("Send question", type="primary", width="stretch")
    repeat = retry.button("Retry question", disabled=not turn["attempted"], width="stretch")
    if submit or repeat:
        if expired(started):
            st.error("The lesson session has expired. Return to the lesson list and start a new session.")
        elif not message.strip():
            st.error("Enter your question or request.")
        else:
            headers = {"X-UPRAISE-Session-JWT": token} if token else None
            with st.spinner("Your teacher is preparing a reply…"):
                fields = {"request_id": turn["request_id"], "chunk_id": chunk["chunk_id"],
                          "text": message.strip(), "playback_offset_ms": player.get("resume_offset_ms", 0)}
                turn["response"] = api_client.request(connection, "POST",
                    f"/v1/sessions/{started['session_id']}/interrupts", json_body=fields, headers=headers, timeout=600)
                turn["attempted"] = True
                response = turn["response"]
                if response.successful and not response.error and isinstance(response.body, dict):
                    player["pending_action"] = response.body.get("playback_action")
            st.rerun()
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
                    st.caption("Select Back to course when ready to apply the playback instruction.")
