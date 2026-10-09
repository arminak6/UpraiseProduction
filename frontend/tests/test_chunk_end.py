"""B2 explicit HTTP submission, boundary input and credential/session isolation."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from streamlit.testing.v1 import AppTest

FRONTEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FRONTEND))
from api_client import Result  # noqa: E402
from learning import binding  # noqa: E402


def button(app, label):
    return next(item for item in app.button if item.label == label)


class ChunkEndTests(unittest.TestCase):
    def app(self):
        app = AppTest.from_file(str(FRONTEND / "app.py"), default_timeout=10)
        app.session_state["page"] = "Chunk end (B2)"
        return app.run()

    def test_validate_submit_once_and_clear_result_when_input_or_credentials_change(self):
        calls = []
        def send(connection, method, path, **options):
            calls.append((method, path, options))
            return Result(method, path, 200, 1, {"decision": "HIGH_COGNITIVE_LOAD"}, True)
        with patch("api_client.request", side_effect=send):
            app = self.app()
            self.assertFalse(app.exception)
            button(app, "Check chunk end").click().run()
            self.assertTrue(app.error)
            self.assertFalse(calls)
            session_id, chunk_id = str(uuid4()), str(uuid4())
            app.text_input(key="b2_field_session").set_value(session_id.upper())
            app.text_input(key="b2_field_lesson").set_value("lesson-test")
            app.text_input(key="b2_field_chunk").set_value(chunk_id)
            app.text_input(key="b2_field_jwt").set_value("private-jwt")
            button(app, "Check chunk end").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["b2_state"]["session_id"], session_id)
            self.assertEqual(calls, [("POST", f"/v1/sessions/{session_id}/chunk-end", {
                "json_body": {"lesson_id": "lesson-test", "chunk_id": chunk_id, "cognitive_load_score": 0.7},
                "headers": {"X-UPRAISE-Session-JWT": "private-jwt"}})])
            self.assertTrue(any("High cognitive load" in item.value for item in app.warning))
            app.run()
            self.assertEqual(len(calls), 1)
            app.number_input(key="b2_field_score").set_value(0.5).run()
            self.assertIsNone(app.session_state["b2_state"]["response"])
            app.text_input(key="bearer_token").set_value("changed-service-token").run()
            self.assertEqual(app.text_input(key="b2_field_session").value, "")
            self.assertEqual(app.text_input(key="b2_field_jwt").value, "")
            self.assertEqual(len(calls), 1)

    def test_boundary_score_and_prefill_follow_the_active_b1_session(self):
        from api_client import Connection
        calls = []
        def send(connection, method, path, **options):
            calls.append(options["json_body"])
            return Result(method, path, 200, 1, {"decision": "PROCEED"}, True)
        with patch("api_client.request", side_effect=send):
            app = self.app()
            current = binding(Connection(base_url=app.text_input(key="backend_url").value))
            session_id, next_id = str(uuid4()), str(uuid4())
            started = {"session_id": session_id, "lesson_id": "lesson-test", "preview": True,
                       "expires_at": "2099-01-01T00:00:00Z"}
            app.session_state["b1_state"] = {"binding": current,
                "response": Result("POST", "/v1/sessions", 201, 1, started, True)}
            app.run()
            self.assertEqual(app.text_input(key="b2_field_session").value, session_id)
            app.text_input(key="b2_field_chunk").set_value(str(uuid4()))
            app.number_input(key="b2_field_score").set_value(0.5)
            button(app, "Check chunk end").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(calls[0]["cognitive_load_score"], 0.5)
            self.assertTrue(any("Proceed to" in item.value for item in app.success))
            started["session_id"] = next_id
            app.session_state["b1_state"] = {"binding": current,
                "response": Result("POST", "/v1/sessions", 201, 1, started, True)}
            app.run()
            self.assertEqual(app.text_input(key="b2_field_session").value, next_id)
            self.assertEqual(app.text_input(key="b2_field_chunk").value, "")
            self.assertIsNone(app.session_state["b2_state"]["response"])

    def test_b5_clears_the_same_sessions_cached_score_and_decision(self):
        session_id = str(uuid4())
        with patch("api_client.request", return_value=Result("POST", "/end", 204, 1, "")):
            app = self.app()
            app.session_state["b2_state"] = {"session_id": session_id, "response": "old-decision"}
            app.session_state["b2_field_score"] = 0.8
            app.session_state["page"] = "End session (B5)"
            app.run()
            app.text_input(key="b5_field_session").set_value(session_id)
            button(app, "End session").click().run()
            self.assertFalse(app.exception)
            self.assertNotIn("b2_state", app.session_state)
            self.assertNotIn("b2_field_score", app.session_state)


if __name__ == "__main__":
    unittest.main()
