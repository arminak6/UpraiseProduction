"""Exercise the course selector/player without generating lessons or contacting AWS."""

import copy
import hashlib
import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from streamlit.testing.v1 import AppTest

FRONTEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FRONTEND))

import api_client  # noqa: E402
import learning  # noqa: E402


def button(app, label):
    return next(item for item in app.button if item.label == label)


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.job, self.session = str(uuid4()), str(uuid4())
        self.chunks = [str(uuid4()), str(uuid4())]
        expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        self.catalog = [{"job_id": self.job, "lesson_id": "example-course", "course_id": "course-1",
                         "activity_id": "activity", "language": "it", "voice": "Bianca", "learner_level": "beginner",
                         "chunk_count": 2, "status": "DRAFT", "requires_review": True,
                         "created_at": datetime.now(UTC).isoformat(), "expires_at": expires}]
        self.lesson = {**self.catalog[0], "chunks": [
            {"index": index, "chunk_id": chunk, "text": f"Teaching part {index + 1}: \\(x+1\\).",
             "sources": [{"pages": [index + 1]}], "duration_seconds": 60,
             "audio_sha256": hashlib.sha256(b"RIFF-test-audio").hexdigest(), "audio_url": "https://external.invalid/steal-token"}
            for index, chunk in enumerate(self.chunks)
        ]}
        self.audio_status = 200
        self.result_status = 200
        self.start_status = 201
        environment = patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"})
        environment.start()
        self.addCleanup(environment.stop)
        renderer = patch("lesson_audio.render_audio", side_effect=lambda state, chunk: __import__("streamlit").audio(state["audio"]))
        renderer.start()
        self.addCleanup(renderer.stop)

    def response(self, connection, method, path, **kwargs):
        if path == "/v1/sessions":
            body = dict(self.catalog[0] if self.catalog else self.lesson)
            body.update(session_id=self.session, lesson_job_id=self.job, ai_notice="AI teacher")
            return api_client.Result(method, path, self.start_status, 1, body, True)
        if path == f"/v1/sessions/{self.session}/end":
            return api_client.Result(method, path, 204, 1, "")
        self.assertEqual(method, "GET")
        if path == "/v1/lesson-jobs":
            return api_client.Result(method, path, 200, 1, copy.deepcopy(self.catalog), True)
        if path == f"/v1/jobs/{self.job}/result":
            return api_client.Result(method, path, self.result_status, 1, copy.deepcopy(self.lesson), True)
        self.assertIn(path, [f"/v1/jobs/{self.job}/audio/{chunk}" for chunk in self.chunks])
        return api_client.Result(method, path, self.audio_status, 1, binary=b"RIFF-test-audio")

    def app(self):
        app = AppTest.from_file(str(FRONTEND / "app.py"), default_timeout=10)
        app.session_state["page"] = "Learning"
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(app.radio(key="page").value, "Learning")
        return app

    def start(self, app):
        button(app, "Start lesson").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("Teaching part 1" in m.value for m in app.markdown))

    def test_start_next_finish_and_new_session(self):
        with patch("api_client.request", side_effect=self.response) as send:
            app = self.app()
            self.assertEqual(send.call_count, 1)
            self.start(app)
            self.assertEqual(send.call_args.args[2], "/v1/sessions")
            self.assertEqual(send.call_args.kwargs["json_body"]["consent_scope"], ["questions"])
            self.assertEqual(app.session_state["learning_state"]["audio"], b"RIFF-test-audio")
            sequence = app.session_state["learning_state"]["playback"]
            button(app, "Replay voice").click().run()
            self.assertGreater(app.session_state["learning_state"]["playback"], sequence)
            button(app, "Next chunk").click().run()
            self.assertTrue(any("Teaching part 2" in m.value for m in app.markdown))
            self.assertTrue(button(app, "Next chunk").disabled)
            self.assertEqual(sum(c.args[2] == "/v1/sessions" for c in send.call_args_list), 1)
            button(app, "Finish lesson").click().run()
            self.assertEqual(send.call_args.kwargs["json_body"], {"reason": "COMPLETED"})
            self.assertFalse(app.exception)
            self.assertTrue(app.success)
            self.assertFalse(app.get("audio"))
            button(app, "Back to lessons").click().run()
            self.start(app)
            self.assertEqual(sum(c.args[2] == "/v1/sessions" for c in send.call_args_list), 2)

    def test_b1_failure_blocks_player_and_explicit_retry_keeps_session_uuid(self):
        with patch("api_client.request", side_effect=self.response) as send:
            self.start_status = 503
            app = self.app()
            button(app, "Start lesson").click().run()
            first = send.call_args.kwargs["json_body"]
            self.assertIsNone(app.session_state["learning_state"]["active"])
            self.assertFalse(app.get("audio"))
            self.assertTrue(app.error)
            count = send.call_count
            app.run()
            self.assertEqual(send.call_count, count)
            self.start_status = 201
            self.start(app)
            self.assertEqual(send.call_args.kwargs["json_body"], first)

    def test_expired_or_acknowledged_result_clears_cached_text_and_audio(self):
        with patch("api_client.request", side_effect=self.response):
            app = self.app()
            self.start(app)
            self.result_status = 410
            button(app, "Next chunk").click().run()
            self.assertFalse(app.exception)
            self.assertIsNone(app.session_state["learning_state"]["active"])
            self.assertIsNone(app.session_state["learning_state"]["audio"])
            self.assertFalse(any("Teaching part" in m.value for m in app.markdown))
            self.assertTrue(any("no longer available" in m.value for m in app.error))

    def test_audio_outage_keeps_text_but_authorization_failure_clears_it(self):
        with patch("api_client.request", side_effect=self.response):
            app = self.app()
            self.audio_status = 503
            self.start(app)
            self.assertTrue(app.warning)
            self.assertFalse(app.get("audio"))
            self.audio_status = 403
            button(app, "Replay voice").click().run()
            self.assertIsNone(app.session_state["learning_state"]["active"])
            self.assertTrue(app.error)
            self.assertFalse(app.exception)

    def test_token_or_backend_change_discards_old_playback(self):
        with patch("api_client.request", side_effect=self.response):
            app = self.app()
            self.start(app)
            self.catalog = []
            app.text_input(key="bearer_token").set_value("new-identity").run()
            self.assertIsNone(app.session_state["learning_state"]["active"])
            self.assertFalse(app.get("audio"))
            app.text_input(key="backend_url").set_value("http://another-backend:8000").run()
            self.assertFalse(app.exception)
            self.assertFalse(app.session_state["learning_state"]["items"])

    def test_voice_checksum_mismatch_keeps_text_without_playing_unverified_audio(self):
        with patch("api_client.request", side_effect=self.response):
            app = self.app()
            self.start(app)
            self.lesson["chunks"][0]["audio_sha256"] = "0" * 64
            button(app, "Replay voice").click().run()
            self.assertFalse(app.exception)
            self.assertIsNone(app.session_state["learning_state"]["audio"])
            self.assertFalse(app.get("audio"))
            self.assertTrue(app.warning)
            self.assertTrue(any("Teaching part 1" in m.value for m in app.markdown))

    def test_empty_catalog_manual_open_and_invalid_uuid(self):
        with patch("api_client.request", side_effect=self.response) as send:
            self.catalog = []
            app = self.app()
            self.assertTrue(any("No ready lessons" in m.value for m in app.info))
            app.text_input(key="learning_manual_id").set_value("invalid")
            button(app, "Open lesson").click().run()
            self.assertEqual(send.call_count, 1)
            app.text_input(key="learning_manual_id").set_value(self.job)
            button(app, "Open lesson").click().run()
            self.assertTrue(any("Teaching part 1" in m.value for m in app.markdown))
            self.assertFalse(app.exception)

    def test_catalog_failure_can_be_retried(self):
        failed = api_client.Result("GET", "/v1/lesson-jobs", 503, 1, error="Unavailable")
        with patch("api_client.request", return_value=failed) as send:
            app = self.app()
            self.assertTrue(app.info)
            send.side_effect = self.response
            button(app, "Refresh courses").click().run()
            self.start(app)

    def test_math_display_preserves_code_and_greek_and_does_not_change_source(self):
        text = r"Equation \(\nu+1\), `\(code\)`, display \[\n x^2 \n\]."
        self.assertEqual(learning.readable_text(text),
                         "Equation $\\nu+1$, `\\(code\\)`, display \n$$\nx^2\n$$\n.")
        self.assertIn(r"\[\n", text)

    def test_pagination_offset_counts_returned_records_before_expiry_filter(self):
        state = {"items": [], "active": None, "audio": None}
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        items = [dict(self.catalog[0], job_id=str(uuid4())) for _ in range(100)]
        items[0]["expires_at"] = past
        with patch("api_client.request", return_value=api_client.Result("GET", "", 200, 1, items, True)) as send:
            learning.refresh_courses(api_client.Connection("http://localhost"), state)
            self.assertEqual(len(state["items"]), 99)
            learning.refresh_courses(api_client.Connection("http://localhost"), state, more=True)
            self.assertEqual(send.call_args.kwargs["params"]["offset"], 100)


if __name__ == "__main__":
    unittest.main()
