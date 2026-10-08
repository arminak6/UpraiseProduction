"""A session-only lesson preview player using the backend's HTTP APIs."""

import base64
import hashlib
import math
import re
from datetime import UTC, datetime
from uuid import UUID

import api_client
import streamlit as st

LANGUAGES = {"en": "English", "it": "Italian", "fr": "French", "de": "German", "es": "Spanish"}
_MATH = re.compile(r"(```.*?```|`[^`\n]+`)|\\\((.*?)\\\)|\\\[(.*?)\\\]", re.DOTALL)


def readable_text(text):
    """Convert math delimiters for display; stored text and speech stay untouched."""
    def replace(match):
        if match[1] is not None:
            return match[1]
        body = match[2] if match[2] is not None else match[3]
        body = re.sub(r"\\n(?![A-Za-z])", "\n", body).strip()
        return f"${body}$" if match[2] is not None else f"\n$$\n{body}\n$$\n"
    return _MATH.sub(replace, text)


def binding(connection):
    digest = hashlib.sha256()
    for field in ("base_url", "token", "certificate", "private_key", "key_password", "ca_certificate"):
        value = getattr(connection, field) or b""
        value = value.encode() if isinstance(value, str) else value
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


def expired(lesson):
    value = lesson.get("expires_at")
    if not value:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC) <= datetime.now(UTC)
    except (ValueError, TypeError, AttributeError):
        return True


def failure(result):
    if result.status == 410:
        return "This lesson's temporary text and audio are no longer available. Choose another lesson."
    if result.status in (401, 403):
        return "Your connection is not authorized to access this lesson. Check the connection settings."
    if result.status == 404:
        return "This lesson was not found or is not accessible with your connection."
    if result.status == 409:
        return "This lesson is not ready for playback. Check its processing status."
    return result.error or "The lesson could not be loaded. Refresh the list or try again."


def load_part(connection, state, job_id, index):
    """Recheck A5 on every explicit playback action, including after A6/expiry."""
    job_id = str(UUID(job_id))
    path = f"/v1/jobs/{job_id}"
    result = api_client.request(connection, "GET", path + "/result")
    state.update(audio=None, audio_error=None, error=None)
    if not result.successful or result.error:
        state.update(active=None, error=failure(result), interrupt={}, interrupt_open=False)
        return
    lesson = result.body
    try:
        chunks = lesson["chunks"]
        if (str(UUID(lesson["job_id"])) != job_id or not chunks or expired(lesson)
                or [c["index"] for c in chunks] != list(range(len(chunks)))
                or len({str(UUID(c["chunk_id"])) for c in chunks}) != len(chunks)
                or not all(isinstance(c["text"], str) and c["text"].strip() for c in chunks)):
            raise ValueError
        index = max(0, min(index, len(chunks) - 1))
    except (KeyError, TypeError, ValueError, AttributeError):
        state.update(active=None, interrupt={}, interrupt_open=False,
                     error="This lesson has expired or its content cannot be displayed.")
        return
    from learning_interrupt import clear_question_widgets
    old = state.get("active") or {}
    if old.get("job_id") != job_id:
        state.update(interrupt={}, playback_rate=1.0)
        clear_question_widgets()
    elif state.get("index") != index:
        clear_question_widgets(keep_session=True)
        state.get("interrupt", {}).update(turn=None)
    state.update(active=lesson, index=index, complete=False,
                 playback=state.get("playback", 0) + 1, interrupt_open=False,
                 pending_action=None)
    if state["voice"]:
        # Construct the authenticated same-backend path rather than following
        # a URL supplied by lesson text or an external gateway's response.
        audio = api_client.request(connection, "GET", path + "/audio/" + str(UUID(chunks[index]["chunk_id"])))
        if audio.successful and not audio.error and audio.binary:
            state["audio"] = audio.binary
        else:
            state["audio_error"] = failure(audio)
            if audio.status in (401, 403, 404, 409, 410):
                state.update(active=None, error=failure(audio), interrupt={}, interrupt_open=False)


def course_label(item):
    language = item.get("language", "")
    language = LANGUAGES.get(language.lower().split("-", 1)[0], language)
    title = item.get("lesson_id", "Lesson").replace("-", " ").replace("_", " ")
    created = item.get("created_at", "")[:16].replace("T", " ")
    return f"{title} · {language} · {item.get('learner_level', 'intermediate')} · {created}"


def refresh_courses(connection, state, *, more=False):
    offset = state.get("offset", 0) if more else 0
    result = api_client.request(connection, "GET", "/v1/lesson-jobs", params={"limit": 100, "offset": offset})
    if result.successful and not result.error and isinstance(result.body, list):
        try:
            items = [item for item in result.body if not expired(item)]
            for item in items:
                item["job_id"] = str(UUID(item["job_id"]))
            combined = [*state["items"], *items] if more else items
            state.update(items=list({item["job_id"]: item for item in combined}.values()),
                         catalog_error=None, error=None, more=len(result.body) == 100,
                         offset=offset + len(result.body))
            return
        except (KeyError, TypeError, ValueError, AttributeError):
            pass
    state.update(items=[], active=None, audio=None, more=False,
                 catalog_error=("Automatic course listing is unavailable on this backend. "
                                "You can open a lesson by its job ID below."
                                if result.status in (404, 405) else failure(result)))


def render_lesson_audio(state):
    rate = state.get("playback_rate", 1.0)
    with st.container(key=f"lesson_audio_{state['playback']}"):
        if rate == 1.0:
            st.audio(state["audio"], format="audio/wav", autoplay=True)
        else:
            # The native Streamlit audio widget has no playback-rate argument.
            # A sandboxed player only embeds trusted WAV bytes and bounded numbers.
            data = base64.b64encode(state["audio"]).decode("ascii")
            st.iframe(f'<audio id="lesson" controls autoplay style="width:100%" '
                f'src="data:audio/wav;base64,{data}"></audio><script>'
                f'const a=document.getElementById("lesson");a.playbackRate={rate};'
                '</script>', height=65)
    if rate != 1.0:
        st.caption(f"Playback speed: {rate}×")


def render_player(connection, state, *, local_testing=False):
    from session_end import end_learning, finish_learning_end
    if state.get("end_pending"):
        st.info("Playback stopped. Session cleanup needs a retry.")
        from learning_interrupt import show_error
        show_error(state["end_error"])
        if st.button("Retry session end", type="primary"):
            finish_learning_end(connection, state)
            st.rerun()
        return
    lesson = state["active"]
    if expired(lesson):
        state.update(active=None, audio=None, items=[], loaded=False, interrupt={}, interrupt_open=False,
                     error="This lesson's temporary playback has expired. Refresh the available courses.")
        st.rerun()
    chunks, index = lesson["chunks"], state["index"]
    st.subheader(lesson["lesson_id"].replace("-", " ").replace("_", " "))
    language = lesson["language"]
    st.caption(f"{LANGUAGES.get(language.split('-')[0], language)} · "
               f"{lesson['learner_level'].title()} · Voice: {lesson['voice']}")
    st.caption("AI-generated draft · Human review is required.")
    if state["complete"]:
        st.success("You reached the end of this lesson.")
        if st.button("Restart course", type="primary"):
            load_part(connection, state, lesson["job_id"], 0)
            st.rerun()
    else:
        st.progress((index + 1) / len(chunks), text=f"Part {index + 1} of {len(chunks)}")
        previous, following, replay = st.columns(3)
        interrupted = state.get("interrupt_open", False)
        if previous.button("Previous", disabled=index == 0 or interrupted, width="stretch"):
            load_part(connection, state, lesson["job_id"], index - 1)
            st.rerun()
        if following.button("Next", disabled=index == len(chunks) - 1 or interrupted, width="stretch", type="primary"):
            load_part(connection, state, lesson["job_id"], index + 1)
            st.rerun()
        if replay.button("Replay audio", disabled=not state["voice"] or interrupted, width="stretch"):
            load_part(connection, state, lesson["job_id"], index)
            st.rerun()
        st.session_state.setdefault("learning_read_aloud", state["voice"])
        voice = st.toggle("Read aloud", key="learning_read_aloud", disabled=interrupted)
        if voice != state["voice"]:
            state["voice"] = voice
            if voice:
                load_part(connection, state, lesson["job_id"], index)
            else:
                state["audio"] = None
            st.rerun()
        if not interrupted and st.button("Interrupt / ask teacher", type="primary"):
            from learning_interrupt import clear_question_widgets
            clear_question_widgets(keep_session=True)
            state.get("interrupt", {}).update(turn=None)
            state.update(interrupt_open=True, pending_action=None)
            st.rerun()
        with st.chat_message("assistant"):
            st.markdown(readable_text(chunks[index]["text"]))
            pages = sorted({page for source in chunks[index].get("sources", []) for page in source.get("pages", [])})
            if pages:
                st.caption("Source pages: " + ", ".join(map(str, pages)))
            if state.get("audio_error"):
                st.warning("The text is available, but audio could not be loaded. Try Replay audio.")
            if state["voice"] and state.get("audio") and not interrupted:
                # A fresh keyed container remounts the controls for Replay even
                # when Streamlit reuses the same media URL for identical bytes.
                render_lesson_audio(state)
                st.caption("Use the audio controls to pause or resume. Select Next when you are ready.")
        if interrupted:
            from learning_interrupt import render_interrupt
            render_interrupt(connection, state, local_testing=local_testing)
            if st.button("Continue course", type="primary"):
                action = state.get("pending_action") or {}
                if action.get("action") == "SET_SPEED":
                    try:
                        rate = float(action["rate"])
                        if not math.isfinite(rate) or not 0.5 <= rate <= 2.0:
                            raise ValueError
                        state["playback_rate"] = rate
                    except (KeyError, TypeError, ValueError):
                        st.error("The teacher returned an invalid playback speed.")
                        return
                load_part(connection, state, lesson["job_id"], index)
                st.rerun()
        if not interrupted and index == len(chunks) - 1 and st.button("Finish course", type="primary"):
            end_learning(connection, state, "COMPLETED")
            st.rerun()
    if st.button("Back to courses"):
        end_learning(connection, state, "STOPPED_BY_LEARNER", back=True)
        st.rerun()


def render_learning(connection, *, local_testing=False):
    current = binding(connection)
    state = st.session_state.get("learning_state")
    if not state or state["binding"] != current:
        state = {"binding": current, "items": [], "loaded": False, "active": None,
                 "audio": None, "error": None, "voice": True, "more": False}
        st.session_state["learning_state"] = state
        for key in ("learning_selected", "learning_read_aloud", "learning_manual_id"):
            st.session_state.pop(key, None)
        from learning_interrupt import clear_question_widgets
        clear_question_widgets()
    st.header("Learning")
    if state["active"]:
        render_player(connection, state, local_testing=local_testing)
        return
    st.write("Choose a ready lesson and start learning with its text and teacher voice.")
    if st.button("Refresh courses") or not state["loaded"]:
        with st.spinner("Loading available courses…"):
            refresh_courses(connection, state)
        state["loaded"] = True
    if state.get("error"):
        st.error(state["error"])
    if state.get("catalog_error"):
        st.info(state["catalog_error"])
    items = {item["job_id"]: item for item in state["items"] if not expired(item)}
    if items:
        if st.session_state.get("learning_selected") not in items:
            st.session_state.pop("learning_selected", None)
        selected = st.selectbox("Available course", list(items), format_func=lambda key: course_label(items[key]),
                                key="learning_selected")
        item = items[selected]
        st.caption(f"{item.get('chunk_count', '?')} parts · Course: {item.get('course_id', '')}")
        st.caption("Ready for draft preview. Human approval remains separate.")
        if st.button("Start course", type="primary"):
            with st.spinner("Opening your lesson…"):
                load_part(connection, state, selected, 0)
            st.rerun()
    elif not state.get("catalog_error"):
        st.info("No ready lessons are available. Generate a lesson, then refresh this list.")
    if state["more"] and st.button("Load more courses"):
        refresh_courses(connection, state, more=True)
        st.rerun()
    with st.expander("Open a lesson by job ID"):
        with st.form("learning_open_job"):
            job_id = st.text_input("Lesson job ID", key="learning_manual_id")
            submitted = st.form_submit_button("Open lesson")
        if submitted:
            try:
                job_id = str(UUID(job_id.strip()))
            except ValueError:
                st.error("Enter the job UUID returned when the lesson was generated.")
            else:
                load_part(connection, state, job_id, 0)
                st.rerun()
