"""A session-only lesson preview player using the backend's HTTP APIs."""

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


def clear_unavailable(connection, state, message):
    from session_end import end_learning
    if state.get("interrupt", {}).get("session"):
        end_learning(connection, state, "ERROR", back=True)
    state.update(active=None, audio=None, error=message, interrupt={}, interrupt_open=False)


def load_part(connection, state, job_id, index, learner_level=None, *, offset_ms=0, autoplay=True):
    """Recheck A5 on every explicit playback action, including after A6/expiry."""
    job_id = str(UUID(job_id))
    path = f"/v1/jobs/{job_id}"
    old = state.get('active') or {}
    if learner_level is None and old.get('job_id') == job_id:
        learner_level = old.get('learner_level') if old.get('generation_version') == 'a3-v2' else None
    options = {'params': {'learner_level': learner_level}} if learner_level else {}
    result = api_client.request(connection, "GET", path + "/result", **options)
    state.update(audio=None, audio_error=None, error=None)
    if not result.successful or result.error:
        clear_unavailable(connection, state, failure(result))
        return
    lesson = result.body
    if isinstance(lesson, dict) and lesson.get('variants'):
        state.update(pending_bundle=lesson, active=None, error=None)
        return
    try:
        chunks = lesson["chunks"]
        if (str(UUID(lesson["job_id"])) != job_id or not chunks or expired(lesson)
                or [c["index"] for c in chunks] != list(range(len(chunks)))
                or len({str(UUID(c["chunk_id"])) for c in chunks}) != len(chunks)
                or not all(isinstance(c["text"], str) and c["text"].strip() for c in chunks)):
            raise ValueError
        index = max(0, min(index, len(chunks) - 1))
    except (KeyError, TypeError, ValueError, AttributeError):
        clear_unavailable(connection, state, "This lesson has expired or its content cannot be displayed.")
        return
    from learning_interrupt import clear_question_widgets
    old = state.get("active") or {}
    if old.get("job_id") != job_id or old.get('learner_level') != lesson.get('learner_level'):
        state.update(interrupt={}, playback_rate=1.0)
        clear_question_widgets()
    elif state.get("index") != index:
        clear_question_widgets(keep_session=True)
        state.get("interrupt", {}).update(turn=None)
    state.update(active=lesson, index=index, complete=False,
                 playback=state.get("playback", 0) + 1, interrupt_open=False,
                 pending_action=None, resume_offset_ms=offset_ms, autoplay=autoplay)
    if state["voice"]:
        # Construct the authenticated same-backend path rather than following
        # a URL supplied by lesson text or an external gateway's response.
        audio = api_client.request(connection, "GET", path + "/audio/" + str(UUID(chunks[index]["chunk_id"])))
        if (audio.successful and not audio.error and audio.binary
                and hashlib.sha256(audio.binary).hexdigest() == chunks[index].get("audio_sha256")):
            state["audio"] = audio.binary
        else:
            state["audio_error"] = failure(audio)
            if audio.status in (401, 403, 404, 409, 410):
                clear_unavailable(connection, state, failure(audio))


def course_label(item, *, include_level=True):
    language = item.get("language", "")
    language = LANGUAGES.get(language.lower().split("-", 1)[0], language)
    title = item.get("lesson_id", "Lesson").replace("-", " ").replace("_", " ")
    created = item.get("created_at", "")[:16].replace("T", " ")
    revision = item.get("revision") or {}
    version = f" · revision {revision['number']}" if revision.get("number") else ""
    level = f" · {item.get('learner_level', 'intermediate')}" if include_level else ""
    return f"{title}{version} · {language}{level} · {created}"


def catalog_key(item):
    return (item['job_id'] + ':' + item['learner_level']
            if item.get('generation_version') == 'a3-v2' else item['job_id'])


def refresh_courses(connection, state, *, more=False):
    offset = state.get("offset", 0) if more else 0
    result = api_client.request(connection, "GET", "/v1/lesson-jobs", params={"limit": 100, "offset": offset})
    if result.successful and not result.error and isinstance(result.body, list):
        try:
            items = [item for item in result.body if not expired(item)]
            for item in items:
                item["job_id"] = str(UUID(item["job_id"]))
            combined = [*state["items"], *items] if more else items
            state.update(items=list({(item["job_id"], item.get('learner_level')): item for item in combined}.values()),
                         catalog_error=None, more=len(result.body) == 100,
                         offset=offset + len(result.body))
            return
        except (KeyError, TypeError, ValueError, AttributeError):
            pass
    state.update(items=[], active=None, audio=None, more=False,
                 catalog_error=("Automatic course listing is unavailable on this backend. "
                                "You can open a lesson by its job ID below."
                                if result.status in (404, 405) else failure(result)))


def open_interruption(state, event):
    """Accept positions only from the currently displayed chunk/player instance."""
    chunk = state["active"]["chunks"][state["index"]]
    if not isinstance(event, dict) or event.get("chunk_id") != chunk["chunk_id"] or event.get("playback") != state["playback"]:
        return False
    position = event.get("offset_ms")
    if isinstance(position, bool) or not isinstance(position, (float, int)) or not math.isfinite(position) or position < 0:
        return False
    duration = chunk.get("duration_seconds")
    if isinstance(duration, (int, float)) and math.isfinite(duration):
        position = min(position, max(0, duration * 1000))
    from learning_interrupt import clear_question_widgets
    clear_question_widgets(keep_session=True)
    state.get("interrupt", {}).update(turn=None)
    state.update(interrupt_open=True, pending_action=None, resume_offset_ms=round(position))
    return True


def return_to_course(connection, state):
    action = state.get("pending_action") or {}
    offset = state.get("resume_offset_ms", 0)
    if action.get("action") == "REPLAY":
        offset = 0
    if action.get("action") == "SET_SPEED":
        rate = action.get("rate")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or not 0.5 <= rate <= 2:
            st.error("The teacher returned an invalid playback speed.")
            return False
        state["playback_rate"] = rate
    load_part(connection, state, state["active"]["job_id"], state["index"],
              offset_ms=offset, autoplay=action.get("action") != "PAUSE")
    if state.get("interrupt", {}).get("session"):
        state["interrupt"]["turn"] = None
    return True


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
    session = state.get("interrupt", {}).get("session")
    if not state.get("complete") and (expired(lesson) or not session or expired(session)):
        state.update(audio=None, interrupt_open=False)
        state.get("interrupt", {}).update(turn=None)
        st.warning("This lesson session has expired or is unavailable. Start a new session from the lesson list.")
        if st.button("Back to lessons", type="primary"):
            end_learning(connection, state, "TIMEOUT", back=True)
            st.rerun()
        return
    chunks, index = lesson["chunks"], state["index"]
    st.subheader(lesson["lesson_id"].replace("-", " ").replace("_", " "))
    language = lesson["language"]
    st.caption(f"{LANGUAGES.get(language.split('-')[0], language)} · "
               f"{lesson['learner_level'].title()} · Voice: {lesson['voice']}")
    st.caption("AI-generated draft · Human review is required.")
    if session:
        st.info(session.get("ai_notice", "You are interacting with an AI teacher."))
        with st.expander("Session details"):
            st.caption("Session · " + session["session_id"])
            st.caption("Expires · " + str(session.get("expires_at", "")))
    if state["complete"]:
        st.success("You reached the end of this lesson.")
        if st.button("Back to lessons", type="primary"):
            state.update(active=None, loaded=False)
            st.rerun()
    else:
        st.progress((index + 1) / len(chunks), text=f"Part {index + 1} of {len(chunks)}")
        following, replay = st.columns(2)
        interrupted = state.get("interrupt_open", False)
        if following.button("Next chunk", disabled=index == len(chunks) - 1 or interrupted, width="stretch", type="primary"):
            load_part(connection, state, lesson["job_id"], index + 1)
            st.rerun()
        if replay.button("Replay voice", disabled=interrupted, width="stretch"):
            load_part(connection, state, lesson["job_id"], index)
            st.rerun()
        with st.chat_message("assistant"):
            st.markdown(readable_text(chunks[index]["text"]))
            pages = sorted({page for source in chunks[index].get("sources", []) for page in source.get("pages", [])})
            if pages:
                st.caption("Source pages: " + ", ".join(map(str, pages)))
            for source in chunks[index].get("sources", []):
                if source.get("source_format") == "csv":
                    st.caption("CSV source rows: " + ", ".join(f"{span['start']}–{span['end']}"
                               for span in source.get("row_ranges", [])))
                elif source.get('source_format') == 'pptx':
                    st.caption('Slides ' + ', '.join(map(str, source.get('slide_numbers', []))))
                elif source.get('source_format') in {'doc', 'docx'}:
                    st.caption('Word source blocks: ' + ', '.join(map(str, source.get('source_blocks', []))))
            if state.get("audio_error"):
                st.warning("The text is available, but voice could not be loaded or verified. Try Replay voice.")
            if state["voice"] and state.get("audio") and not interrupted:
                from lesson_audio import render_audio
                event = render_audio(state, chunks[index])
                if event and open_interruption(state, event):
                    st.rerun()
            elif not interrupted and st.button("User interruption", type="primary"):
                open_interruption(state, {"chunk_id": chunks[index]["chunk_id"], "playback": state["playback"],
                                          "offset_ms": state.get("resume_offset_ms", 0)})
                st.rerun()
        if interrupted:
            from learning_interrupt import render_interrupt
            render_interrupt(connection, state, local_testing=local_testing)
            if st.button("Back to course", type="primary") and return_to_course(connection, state):
                st.rerun()
        if not interrupted and index == len(chunks) - 1 and st.button("Finish lesson", type="primary"):
            end_learning(connection, state, "COMPLETED")
            st.rerun()
    if not state["complete"] and st.button("End lesson"):
        end_learning(connection, state, "STOPPED_BY_LEARNER", back=True)
        st.rerun()


def render_learning(connection, *, local_testing=False):
    current = binding(connection)
    state = st.session_state.get("learning_state")
    if not state or state["binding"] != current:
        state = {"binding": current, "items": [], "loaded": False, "active": None,
                 "audio": None, "error": None, "voice": True, "more": False}
        st.session_state["learning_state"] = state
        for key in list(st.session_state):
            if key.startswith("learning_") and key != "learning_state":
                del st.session_state[key]
        from learning_interrupt import clear_question_widgets
        clear_question_widgets()
    st.header("Learning")
    if state["active"] or state.get("end_pending"):
        render_player(connection, state, local_testing=local_testing)
        return
    st.write("Choose a lesson and difficulty, then start your text and voice session.")
    if st.button("Refresh courses") or not state["loaded"]:
        with st.spinner("Loading available courses…"):
            refresh_courses(connection, state)
        state["loaded"] = True
    if state.get("error"):
        st.error(state["error"])
    if state.get("catalog_error"):
        st.info(state["catalog_error"])
    from learning_session import session_options, start_lesson
    options = session_options(local_testing=local_testing)
    if state.get('pending_bundle'):
        bundle = state['pending_bundle']
        level = st.selectbox('Difficulty level', list(bundle['variants']))
        if st.button('Start selected level', type='primary'):
            state.pop('pending_bundle')
            start_lesson(connection, state, bundle['job_id'], level, options)
            st.rerun()
    groups = {}
    for item in state["items"]:
        if not expired(item):
            groups.setdefault(item["job_id"], {})[item.get("learner_level", "intermediate")] = item
    items = {job: next(iter(levels.values())) for job, levels in groups.items()}
    if items:
        if st.session_state.get("learning_selected") not in items:
            st.session_state.pop("learning_selected", None)
        selected = st.selectbox("Available lesson", list(items), format_func=lambda key:
                                f"Course {items[key].get('course_id', '')} · {course_label(items[key], include_level=False)}",
                                key="learning_selected")
        level = st.selectbox("Difficulty level", list(groups[selected]), key=f"learning_level_{selected}")
        item = groups[selected][level]
        st.caption(f"{item.get('chunk_count', '?')} parts · Course: {item.get('course_id', '')}")
        st.caption("Ready for draft preview. Human approval remains separate.")
        if st.button("Start lesson", type="primary"):
            with st.spinner("Opening your lesson…"):
                start_lesson(connection, state, item['job_id'],
                             item.get('learner_level') if item.get('generation_version') == 'a3-v2' else None, options)
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
                start_lesson(connection, state, job_id, None, options)
                st.rerun()
