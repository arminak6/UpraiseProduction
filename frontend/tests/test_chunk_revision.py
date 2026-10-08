"""A7 authoring UI, explicit submission and connection isolation."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from streamlit.testing.v1 import AppTest

FRONTEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FRONTEND))
from api_client import Result  # noqa: E402


def button(app, label):
    return next(item for item in app.button if item.label == label)


class ChunkRevisionTests(unittest.TestCase):
    def test_load_feedback_submit_once_and_connection_change_clears_content(self):
        parent_id, chunk_id, revised_id = [str(uuid4()) for _ in range(3)]
        calls = []
        def send(connection, method, path, **options):
            calls.append((method, path, options))
            body = {"job_id": revised_id} if method == "POST" else {
                "job_id": parent_id, "language": "it", "voice": "Bianca", "learner_level": "beginner",
                "expires_at": "2099-01-01T00:00:00Z", "chunks": [{"chunk_id": chunk_id, "index": 0, "text": "Original teaching text."}]}
            return Result(method, path, 202 if method == "POST" else 200, 1, body, True)
        with patch("api_client.request", side_effect=send):
            app = AppTest.from_file(str(FRONTEND / "app.py"), default_timeout=10)
            app.session_state["page"] = "Revise chunk (A7)"
            app.run()
            self.assertFalse(app.exception)
            self.assertFalse(calls)
            app.text_input(key="a7_field_job").set_value(parent_id)
            button(app, "Load lesson by job ID").click().run()
            self.assertFalse(app.exception)
            app.text_area(key="a7_field_feedback").set_value("Explain this more simply.")
            button(app, "Generate revised chunk").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(calls[-1], ("POST", "/v1/audio:regenerate", {"json_body": {
                "lesson_job_id": parent_id, "chunk_id": chunk_id, "teacher_review": "Explain this more simply."}}))
            self.assertEqual(app.session_state["tracking_path"], f"/v1/jobs/{revised_id}")
            self.assertTrue(button(app, "Generate revised chunk").disabled)
            app.run()
            self.assertEqual(sum(method == "POST" for method, _, _ in calls), 1)
            next(item for item in app.text_input if item.label == "Bearer token").set_value("changed-private-token").run()
            self.assertIsNone(app.session_state["a7_state"]["lesson"])
            self.assertIsNone(app.session_state["a7_state"]["accepted"])

    def test_invalid_job_never_makes_a_request(self):
        with patch("api_client.request") as send:
            app = AppTest.from_file(str(FRONTEND / "app.py"), default_timeout=10)
            app.session_state["page"] = "Revise chunk (A7)"
            app.run()
            app.text_input(key="a7_field_job").set_value("invalid")
            button(app, "Load lesson by job ID").click().run()
            send.assert_not_called()
            self.assertTrue(app.error)


if __name__ == "__main__":
    unittest.main()
