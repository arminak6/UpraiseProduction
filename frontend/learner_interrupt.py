"""Explicit B4 API tests; all learner content stays in the Streamlit session."""

import base64
import hashlib
import json
from dataclasses import replace
from uuid import UUID, uuid4

import api_client
import streamlit as st
from learning import binding, expired


def render_learner_interrupt(connection, show_response):
    current = binding(connection)
    state = st.session_state.get("b4_state")
    if state is None or state["binding"] != current:
        state = {"binding": current, "context": None, "request_id": str(uuid4()), "response": None,
                 "attempted": False, "lesson": None, "lesson_job_id": None, "lesson_error": None}
        st.session_state["b4_state"] = state
        for key in list(st.session_state):
            if key.startswith("b4_field_"):
                del st.session_state[key]
    st.header("B4 · Learner interruption")
    st.write("Send a question or learning request to a session started through B1.")
    st.caption("Enable Questions consent when starting a local B1 session. The backend keeps only temporary conversation memory.")
    b1 = st.session_state.get("b1_state", {})
    result = b1.get("response") if b1.get("binding") == current else None
    started = result.body if result and result.successful and isinstance(result.body, dict) else None
    use_started = bool(started and not expired(started))
    if use_started:
        st.caption(f"Active B1 session: {started['session_id']} · {started.get('language', '')} · {started.get('learner_level', '')}")
    manual = st.checkbox("Enter session and chunk IDs manually", value=not use_started, key="b4_field_manual")
    token = ""
    if manual:
        session_id = st.text_input("Session ID", key="b4_field_session", max_chars=36)
        chunk_id = st.text_input("Current chunk ID", key="b4_field_chunk", max_chars=36)
        token = st.text_input("Middleware session JWT (production only)", type="password", key="b4_field_jwt").strip()
    elif not use_started:
        st.info("Start a session on the B1 page, or enter the IDs manually.")
        return
    else:
        session_id = started["session_id"]
        token = st.session_state.get("b1_field_jwt", "").strip() if not started.get("preview") else ""
        job_id = started["lesson_job_id"]
        if state["lesson_job_id"] != job_id:
            state.update(lesson_job_id=job_id, lesson=None, lesson_error=None)
        if st.button("Load teaching chunks"):
            loaded = api_client.request(connection, "GET", f"/v1/jobs/{job_id}/result")
            if loaded.successful and isinstance(loaded.body, dict):
                state.update(lesson=loaded.body, lesson_error=None)
            else:
                state.update(lesson=None, lesson_error=loaded)
        if state["lesson_error"]:
            show_response(state["lesson_error"])
        chunks = {item["chunk_id"]: item for item in (state["lesson"] or {}).get("chunks", [])}
        if chunks:
            if st.session_state.get("b4_field_choice") not in chunks:
                st.session_state.pop("b4_field_choice", None)
            chunk_id = st.selectbox("Current teaching chunk", list(chunks), key="b4_field_choice",
                format_func=lambda value: f"Part {chunks[value]['index']+1} · {value}")
            with st.expander("Current teaching text"):
                st.markdown(chunks[chunk_id]["text"])
        else:
            chunk_id = st.text_input("Current chunk ID", key="b4_field_chunk", max_chars=36)
            st.caption("Load chunks through A5, or paste an exact chunk ID. Loading requires jobs:read in production.")
    mode = st.radio("Message input", ["Text", "Audio WAV"], key="b4_field_input")
    message = st.text_area("Learner message", key="b4_field_text", max_chars=6000) if mode == "Text" else None
    upload = st.file_uploader("Learner recording", type=["wav"], max_upload_size=6,
                              key="b4_field_audio") if mode == "Audio WAV" else None
    audio = upload.getvalue() if upload is not None else None
    if mode == "Audio WAV":
        st.caption("PCM WAV · mono · 16-bit · 8–48 kHz · up to 60 seconds. Recording language follows B1.")
    context = hashlib.sha256(json.dumps({"session": session_id, "chunk": chunk_id, "mode": mode,
        "text": message, "audio_hash": hashlib.sha256(audio).hexdigest() if audio is not None else None,
        "token_hash": hashlib.sha256(token.encode()).hexdigest()}, sort_keys=True).encode()).hexdigest()
    try:
        state["session_id"] = str(UUID(session_id.strip()))
    except ValueError:
        state["session_id"] = None
    if state["context"] != context:
        state.update(context=context, request_id=str(uuid4()), response=None, attempted=False)
    st.caption("Message request ID (kept for retries)")
    st.code(state["request_id"], language=None)
    send, retry, fresh = st.columns(3)
    submit = send.button("Send interruption", type="primary", width="stretch")
    repeat = retry.button("Retry interruption", disabled=not state["attempted"], width="stretch")
    if fresh.button("New message ID", width="stretch"):
        state.update(request_id=str(uuid4()), response=None, attempted=False)
        st.rerun()
    if submit or repeat:
        try:
            session_id, chunk_id = str(UUID(session_id.strip())), str(UUID(chunk_id.strip()))
            if mode == "Text" and not (message or "").strip():
                raise ValueError("Enter the learner's message.")
            if mode == "Audio WAV" and not audio:
                raise ValueError("Select a PCM WAV recording.")
            fields = {"request_id": state["request_id"], "chunk_id": chunk_id}
            options = {"headers": {"X-UPRAISE-Session-JWT": token} if token else None, "timeout": 600}
            if mode == "Text":
                options["json_body"] = {**fields, "text": message.strip()}
            else:
                options.update(fields={key: str(value) for key, value in fields.items()},
                               upload=("recording.wav", audio, "audio/wav"), upload_field="audio")
            with st.spinner("The teacher is interpreting your request…"):
                state["response"] = api_client.request(connection, "POST", f"/v1/sessions/{session_id}/interrupts", **options)
            state["attempted"] = True
            st.rerun()
        except ValueError as error:
            st.error(str(error) if "message" in str(error) or "recording" in str(error) else "Use valid session and chunk UUIDs.")
    value = state["response"]
    if value and value.successful and isinstance(value.body, dict):
        body = value.body
        if body.get("text"):
            st.markdown(body["text"])
        if body.get("audio"):
            try:
                st.audio(base64.b64decode(body["audio"]["data"], validate=True), format="audio/wav")
            except (ValueError, KeyError):
                st.warning("The response audio could not be decoded.")
        if body.get("playback_action"):
            st.info(f"Player action: {body['playback_action']['action']}" +
                    (f" · rate {body['playback_action']['rate']}" if body['playback_action'].get('rate') else ""))
            st.caption("The test page displays the control instruction. Middleware/your lesson player applies it.")
        if body.get("grounded") is not None:
            st.caption(f"Supported by course sources: {'yes' if body['grounded'] else 'no'}")
        if body.get("sources"):
            with st.expander("Course sources used"):
                st.json(body["sources"])
        for warning in body.get("warnings", []):
            if warning == "GROUNDING_NOT_VERIFIED":
                st.caption("This reply has not been independently verified against the course sources.")
            else:
                st.warning(warning)
    if value and value.successful and isinstance(value.body, dict) and value.body.get("audio"):
        # The HTTP response carries the actual WAV; avoid rendering megabytes of
        # base64 into the JSON inspector after the audio widget has decoded it.
        inline = value.body["audio"]
        summary = {**inline, "data": f"Inline WAV: {len(inline.get('data', ''))} base64 characters"}
        show_response(replace(value, body={**value.body, "audio": summary}))
    else:
        show_response(value)
