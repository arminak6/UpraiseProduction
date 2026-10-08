import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from streamlit.testing.v1 import AppTest

FRONTEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FRONTEND))
from api_client import Result  # noqa: E402


class SessionEndTests(unittest.TestCase):
    def app(self):
        app = AppTest.from_file(str(FRONTEND / "app.py"), default_timeout=10)
        app.session_state["page"] = "End session (B5)"
        return app.run()

    def test_explicit_end_validates_uuid_and_returns_204(self):
        calls = []

        def send(connection, method, path, **options):
            calls.append((method, path, options))
            return Result(method, path, 204, 1, "")

        with patch("api_client.request", side_effect=send), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}):
            app = self.app()
            self.assertFalse(app.exception)
            next(button for button in app.button if button.label == "End session").click().run()
            self.assertTrue(app.error)
            self.assertFalse(calls)
            session_id = str(uuid4())
            app.text_input(key="b5_field_session").set_value(session_id)
            app.text_input(key="b5_field_jwt").set_value("private-jwt")
            next(button for button in app.button if button.label == "End session").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(calls, [("POST", f"/v1/sessions/{session_id}/end", {
                "json_body": {"reason": "STOPPED_BY_LEARNER"},
                "headers": {"X-UPRAISE-Session-JWT": "private-jwt"}})])
            self.assertEqual(app.metric[0].value, "204")
            self.assertTrue(any("Session ended" in item.value for item in app.success))

    def test_b5_success_clears_standalone_b4_reply_and_requires_new_b1_id(self):
        session_id = str(uuid4())
        with patch("api_client.request", return_value=Result("POST", "/end", 204, 1, "")):
            app = self.app()
            app.session_state["b4_state"] = {"session_id": session_id, "response": {"audio": "private"}}
            app.session_state["b4_field_text"] = "private question"
            app.session_state["learning_state"] = {"active": {"job_id": str(uuid4())}, "audio": b"course",
                "interrupt": {"session": {"session_id": session_id}, "turn": {"audio": "private"}},
                "interrupt_open": True}
            app.session_state["b1_state"] = {"response": Result("POST", "/v1/sessions", 201, 1,
                {"session_id": session_id}, True), "local_id": session_id, "attempted": True}
            app.text_input(key="b5_field_session").set_value(session_id)
            next(button for button in app.button if button.label == "End session").click().run()
            self.assertFalse(app.exception)
            self.assertNotIn("b4_state", app.session_state)
            self.assertNotIn("b4_field_text", app.session_state)
            self.assertIsNone(app.session_state["b1_state"]["response"])
            self.assertNotEqual(app.session_state["b1_state"]["local_id"], session_id)
            self.assertIsNone(app.session_state["learning_state"]["active"])
            self.assertIsNone(app.session_state["learning_state"]["audio"])
            self.assertFalse(app.session_state["learning_state"]["interrupt"])
