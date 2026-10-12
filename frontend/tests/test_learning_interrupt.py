"""Check integrated player/session interruptions without calling AWS."""

import base64
import copy
import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api_client  # noqa: E402


def button(app, label):
    return next(item for item in app.button if item.label == label)


class PlayerInterruptTests(unittest.TestCase):
    def setUp(self):
        self.job, self.session = str(uuid4()), str(uuid4())
        self.chunks = [str(uuid4()), str(uuid4())]
        self.lesson = {"job_id": self.job, "course_id": "course", "activity_id": "activity",
            "lesson_id": "Italian beginner", "language": "it", "learner_level": "beginner",
            "voice": "Bianca", "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "chunks": [{"chunk_id": chunk, "index": index, "text": f"Teaching part {index+1}",
                        "sources": [], "duration_seconds": 60} for index, chunk in enumerate(self.chunks)]}
        self.started = {**{key: self.lesson[key] for key in ("course_id", "activity_id", "lesson_id", "language",
            "learner_level", "voice", "expires_at")}, "session_id": self.session,
            "lesson_job_id": self.job, "ai_notice": "You are interacting with an AI.", "preview": True}
        self.posts = []
        self.answer = {"text": "Un'altra spiegazione.", "audio": {"data": base64.b64encode(b"RIFF-answer").decode()},
                       "grounded": None, "sources": [], "warnings": ["GROUNDING_NOT_VERIFIED"], "playback_action": None}
        self.start_status, self.result_status = 201, 200
        self.end_status = 204

    def response(self, connection, method, path, **kwargs):
        if method == "POST":
            self.posts.append((path, copy.deepcopy(kwargs)))
            if path == "/v1/sessions":
                return api_client.Result(method, path, self.start_status, 1, copy.deepcopy(self.started), True,
                                         error="Request timed out" if self.start_status is None else None)
            if path == f"/v1/sessions/{self.session}/end":
                return api_client.Result(method, path, self.end_status, 1,
                    {"detail": "AUDIT_STORAGE_UNAVAILABLE"} if self.end_status == 503 else "",
                    error="Request timed out" if self.end_status is None else None)
            self.assertEqual(path, f"/v1/sessions/{self.session}/interrupts")
            fields = kwargs.get("json_body", kwargs.get("fields"))
            body = {**self.answer, **{key: fields[key] for key in ("request_id", "chunk_id")}}
            return api_client.Result(method, path, 200, 1, copy.deepcopy(body), True)
        if path == "/v1/lesson-jobs":
            return api_client.Result(method, path, 200, 1, [copy.deepcopy(self.lesson)], True)
        if path.endswith("/result"):
            return api_client.Result(method, path, self.result_status, 1, copy.deepcopy(self.lesson), True)
        self.assertIn(path, [f"/v1/jobs/{self.job}/audio/{chunk}" for chunk in self.chunks])
        return api_client.Result(method, path, 200, 1, binary=b"RIFF-lesson")

    def app(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=10)
        app.session_state["page"] = "Learning"
        app.run()
        button(app, "Start course").click().run()
        self.assertFalse(app.exception)
        return app

    def open(self, app):
        button(app, "Interrupt / ask teacher").click().run()
        self.assertFalse(app.exception)

    def ask(self, app):
        app.checkbox(key="learning_question_consent").check()
        app.text_area(key="learning_question_text").set_value("Spiega in altre parole")
        button(app, "Ask teacher").click().run()
        self.assertFalse(app.exception)

    def test_button_pauses_course_and_never_posts_until_explicit_submission(self):
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}):
            app = self.app()
            self.assertEqual(len(app.get("audio")), 1)
            self.open(app)
            self.assertFalse(app.get("audio"))
            self.assertTrue(button(app, "Next").disabled)
            self.assertFalse(self.posts)
            app.text_area(key="learning_question_text").set_value("Explain this")
            button(app, "Ask teacher").click().run()
            self.assertTrue(app.error)
            self.assertFalse(self.posts)
            button(app, "Continue course").click().run()
            self.assertEqual(len(app.get("audio")), 1)
            self.assertEqual(app.session_state["learning_state"]["index"], 0)

    def test_current_chunk_b1_consent_voice_and_stable_b4_retry(self):
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}):
            app = self.app()
            self.open(app)
            self.ask(app)
            self.assertEqual(len(self.posts), 2)
            start, answer = self.posts
            self.assertEqual(start[1]["json_body"]["lesson_job_id"], self.job)
            self.assertEqual(start[1]["json_body"]["consent_scope"], ["questions"])
            self.assertEqual(start[1]["json_body"]["language"], "it")
            self.assertEqual(answer[1]["json_body"]["chunk_id"], self.chunks[0])
            self.assertTrue(any("Un'altra" in item.value for item in app.markdown))
            self.assertTrue(any("not been independently verified" in item.value for item in app.caption))
            self.assertFalse(any("Supported by course sources: yes" in item.value for item in app.caption))
            self.assertEqual(len(app.get("audio")), 1)  # Answer only, lesson stays paused.
            self.assertTrue(any("interacting with an AI" in item.value for item in app.info))
            app.run()
            app.radio(key="page").set_value("Health").run()
            app.radio(key="page").set_value("Learning").run()
            self.assertEqual(len(self.posts), 2)
            button(app, "Retry question").click().run()
            self.assertEqual(self.posts[-1][1], answer[1])
            app.text_area(key="learning_question_text").set_value("Make it shorter").run()
            self.assertTrue(button(app, "Retry question").disabled)
            button(app, "Ask teacher").click().run()
            self.assertNotEqual(self.posts[-1][1]["json_body"]["request_id"], answer[1]["json_body"]["request_id"])
            self.assertEqual(sum(path == "/v1/sessions" for path, _ in self.posts), 1)

    def test_next_part_reuses_session_but_not_old_message_or_answer(self):
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}):
            app = self.app()
            self.open(app)
            self.ask(app)
            button(app, "Continue course").click().run()
            button(app, "Next").click().run()
            self.open(app)
            self.assertEqual(app.text_area(key="learning_question_text").value, "")
            self.assertFalse(any("Un'altra" in item.value for item in app.markdown))
            self.ask(app)
            self.assertEqual(self.posts[-1][1]["json_body"]["chunk_id"], self.chunks[1])
            self.assertEqual(sum(path == "/v1/sessions" for path, _ in self.posts), 1)

    def test_b1_timeout_retry_preserves_session_uuid_and_does_not_post_b4(self):
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}):
            self.start_status = None
            app = self.app()
            self.open(app)
            self.ask(app)
            first = self.posts[0][1]["json_body"]
            self.assertEqual(len(self.posts), 1)
            self.assertTrue(app.error)
            self.start_status = 201
            button(app, "Ask teacher").click().run()
            self.assertEqual(self.posts[1][1]["json_body"], first)
            self.assertEqual(len(self.posts), 3)

    def test_signed_mode_sends_same_masked_jwt_without_body_consent(self):
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "false"}):
            app = self.app()
            self.open(app)
            app.text_input(key="learning_question_jwt").set_value("private-jwt")
            app.text_area(key="learning_question_text").set_value("Explain this")
            button(app, "Ask teacher").click().run()
            self.assertFalse(app.exception)
            for _, kwargs in self.posts:
                self.assertEqual(kwargs["headers"], {"X-UPRAISE-Session-JWT": "private-jwt"})
            self.assertNotIn("consent_scope", self.posts[0][1]["json_body"])
            self.assertNotIn("session_id", self.posts[0][1]["json_body"])
            self.assertFalse(any("private-jwt" in str(item.value) for category in (app.code, app.json, app.caption)
                                 for item in category))

    def test_speed_instruction_applies_on_continue_and_rechecks_a5(self):
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}):
            self.answer.update(text=None, audio=None, playback_action={"action": "SET_SPEED", "rate": 0.8})
            app = self.app()
            self.open(app)
            self.ask(app)
            self.assertFalse(app.get("audio"))
            button(app, "Continue course").click().run()
            self.assertFalse(app.exception)
            state = app.session_state["learning_state"]
            self.assertEqual(state["playback_rate"], 0.8)
            self.assertFalse(state["interrupt_open"])
            self.assertTrue(any("0.8" in item.value for item in app.caption))
            self.assertTrue(app.get("iframe"))

    def test_revoked_lesson_or_changed_connection_clears_session_and_answer(self):
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}):
            app = self.app()
            self.open(app)
            self.ask(app)
            self.result_status = 410
            button(app, "Continue course").click().run()
            self.assertIsNone(app.session_state["learning_state"]["active"])
            self.assertFalse(app.session_state["learning_state"]["interrupt"])
            self.assertFalse(app.get("audio"))
            self.assertFalse(any("Un'altra" in item.value for item in app.markdown))
            self.result_status = 200
            button(app, "Start course").click().run()
            self.open(app)
            app.text_input(key="bearer_token").set_value("another-user").run()
            self.assertFalse(app.exception)
            self.assertIsNone(app.session_state["learning_state"]["active"])
            with self.assertRaises(KeyError):
                app.session_state["learning_question_consent"]

    def test_expired_session_never_silently_restarts(self):
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}):
            app = self.app()
            self.open(app)
            self.ask(app)
            state = app.session_state["learning_state"]
            state["interrupt"]["session"]["expires_at"] = (datetime.now(UTC)-timedelta(seconds=1)).isoformat()
            app.text_area(key="learning_question_text").set_value("Another question").run()
            button(app, "Ask teacher").click().run()
            self.assertEqual(len(self.posts), 2)
            self.assertTrue(app.error)
            button(app, "New question session").click().run()
            self.assertEqual(len(self.posts), 2)
            self.assertIsNone(app.session_state["learning_state"]["interrupt"]["session"])

    def test_recording_uses_audio_multipart_and_replay_keeps_current_part(self):
        import streamlit as st
        original_uploader = st.file_uploader
        def uploader(label, **kwargs):
            return SimpleNamespace(getvalue=lambda: b"RIFF-recording") if label == "Your recording" else original_uploader(label, **kwargs)
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}), \
                patch("streamlit.file_uploader", side_effect=uploader):
            app = self.app()
            self.open(app)
            app.checkbox(key="learning_question_consent").check()
            app.radio(key="learning_question_input").set_value("Audio WAV").run()
            self.answer.update(text=None, audio=None, playback_action={"action": "REPLAY"})
            button(app, "Ask teacher").click().run()
            self.assertFalse(app.exception)
            options = self.posts[-1][1]
            self.assertEqual(options["upload_field"], "audio")
            self.assertEqual(options["upload"][1], b"RIFF-recording")
            self.assertNotIn("playback_offset_ms", options["fields"])
            self.assertNotIn("json_body", options)
            button(app, "Continue course").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["learning_state"]["index"], 0)

    def test_back_ends_session_and_drops_reply_audio_but_course_can_restart(self):
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}):
            app = self.app()
            self.open(app)
            self.ask(app)
            old_id = self.posts[0][1]["json_body"]["session_id"]
            button(app, "Back to courses").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(self.posts[-1][0], f"/v1/sessions/{self.session}/end")
            self.assertEqual(self.posts[-1][1]["json_body"], {"reason": "STOPPED_BY_LEARNER"})
            state = app.session_state["learning_state"]
            self.assertIsNone(state["active"])
            self.assertFalse(state["interrupt"])
            self.assertFalse(app.get("audio"))
            self.assertFalse(any("Un'altra" in item.value for item in app.markdown))
            button(app, "Start course").click().run()
            self.assertEqual(len(app.get("audio")), 1)
            self.open(app)
            self.ask(app)
            new_id = next(kwargs["json_body"]["session_id"] for path, kwargs in reversed(self.posts) if path == "/v1/sessions")
            self.assertNotEqual(old_id, new_id)

    def test_finish_course_ends_completed_session(self):
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}):
            app = self.app()
            self.open(app)
            self.ask(app)
            button(app, "Continue course").click().run()
            button(app, "Next").click().run()
            button(app, "Finish course").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(self.posts[-1][1]["json_body"], {"reason": "COMPLETED"})
            self.assertTrue(app.session_state["learning_state"]["complete"])
            self.assertFalse(app.session_state["learning_state"]["interrupt"])
            self.assertFalse(app.get("audio"))
            button(app, "Restart course").click().run()
            self.assertEqual(len(app.get("audio")), 1)

    def test_end_failure_clears_audio_immediately_and_retries_same_end(self):
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}):
            app = self.app()
            self.open(app)
            self.ask(app)
            self.end_status = 503
            button(app, "Back to courses").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.get("audio"))
            self.assertFalse(app.session_state["learning_state"]["interrupt"])
            self.assertTrue(app.error)
            first = self.posts[-1]
            self.end_status = 204
            button(app, "Retry session end").click().run()
            self.assertEqual(self.posts[-1], first)
            self.assertIsNone(app.session_state["learning_state"]["active"])
            self.assertFalse(app.exception)

    def test_signed_session_end_keeps_original_jwt_after_widgets_are_cleared(self):
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "false"}):
            app = self.app()
            self.open(app)
            app.text_input(key="learning_question_jwt").set_value("private-jwt")
            app.text_area(key="learning_question_text").set_value("Explain this")
            button(app, "Ask teacher").click().run()
            button(app, "Back to courses").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(self.posts[-1][1]["headers"], {"X-UPRAISE-Session-JWT": "private-jwt"})


if __name__ == "__main__":
    unittest.main()
