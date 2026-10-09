"""B5 session ending through HTTP; course artifacts remain backend-owned."""

from uuid import UUID, uuid4

import api_client
import streamlit as st

REASONS = ["COMPLETED", "STOPPED_BY_LEARNER", "TIMEOUT", "ERROR"]


def end_session(connection, session_id, reason, token=""):
    return api_client.request(connection, "POST", f"/v1/sessions/{UUID(session_id)}/end",
                              json_body={"reason": reason},
                              headers={"X-UPRAISE-Session-JWT": token} if token else None)


def clear_interruption_views(session_id):
    if st.session_state.get("b2_state", {}).get("session_id") == session_id:
        st.session_state.pop("b2_state", None)
        for key in list(st.session_state):
            if key.startswith("b2_field_"):
                del st.session_state[key]
    player = st.session_state.get("learning_state", {})
    interruption = player.get("interrupt", {})
    if (interruption.get("session") or {}).get("session_id") == session_id:
        from learning_interrupt import clear_question_widgets
        interruption.clear()
        player.update(active=None, audio=None, loaded=False, interrupt_open=False, pending_action=None)
        clear_question_widgets()
    # The standalone B4 page can retain the same session's inline audio.
    state = st.session_state.get("b4_state", {})
    if state.get("session_id") == session_id:
        st.session_state.pop("b4_state", None)
        for key in list(st.session_state):
            if key.startswith("b4_field_"):
                del st.session_state[key]
    b1 = st.session_state.get("b1_state", {})
    result = b1.get("response")
    if result and isinstance(result.body, dict) and result.body.get("session_id") == session_id:
        b1.update(response=None, attempted=False, local_id=str(uuid4()))
        st.session_state.pop("b1_field_jwt", None)


def finish_learning_end(connection, player):
    pending = player["end_pending"]
    result = end_session(connection, pending["session_id"], pending["reason"], pending["token"])
    if result.error or result.status not in (204, 404, 410):
        player["end_error"] = result
        return False
    clear_interruption_views(pending["session_id"])
    player.update(end_pending=None, end_error=None)
    if pending["back"]:
        player.update(active=None, loaded=False, error=None)
    else:
        player["complete"] = True
    return True


def end_learning(connection, player, reason, *, back=False):
    from learning_interrupt import clear_question_widgets
    interruption = player.get("interrupt", {})
    session = interruption.get("session")
    token = interruption.get("session_token", "")
    player.update(audio=None, pending_action=None, interrupt_open=False)
    # Drop answer/audio and message widgets even when audit/network retry is needed.
    player["interrupt"] = {}
    interruption.clear()
    clear_question_widgets()
    if session is None:
        player.update(complete=not back, active=None if back else player["active"], loaded=not back)
        return True
    player["end_pending"] = {"session_id": session["session_id"], "token": token,
                             "reason": reason, "back": back}
    return finish_learning_end(connection, player)


def render_session_end(connection, show_response):
    from learning import binding
    state = st.session_state.setdefault("b5_state", {})
    current = binding(connection)
    if state.get("binding") != current:
        state.clear()
        state.update(binding=current, response=None)
        for key in ("b5_field_session", "b5_field_jwt", "b5_field_reason"):
            st.session_state.pop(key, None)
    st.header("B5 · End a lesson session")
    st.write("End a learner session and clear its interruption history and reply audio.")
    st.caption("Generated course text and audio keep their existing retention policy.")
    session_id = st.text_input("Session ID", key="b5_field_session")
    reason = st.selectbox("End reason", REASONS, index=1, key="b5_field_reason")
    token = st.text_input("Middleware session JWT", type="password", key="b5_field_jwt",
                          help="Leave empty only for an explicit local draft preview.").strip()
    if st.button("End session", type="primary"):
        try:
            session_id = str(UUID(session_id.strip()))
        except ValueError:
            st.error("Enter the session UUID returned by B1.")
        else:
            state["response"] = end_session(connection, session_id, reason, token)
            if state["response"].status == 204 and not state["response"].error:
                clear_interruption_views(session_id)
                st.session_state.pop("b5_field_jwt", None)
            st.rerun()
    if state.get("response") and state["response"].status == 204:
        st.success("Session ended. Its interruption history and cached reply audio were cleared.")
    show_response(state.get("response"))
