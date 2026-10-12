"""Check integrated player/session interruptions without calling AWS."""

import base64
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
                        "sources": [], "duration_seconds": 60,
                        "audio_sha256": hashlib.sha256(b"RIFF-lesson").hexdigest()} for index, chunk in enumerate(self.chunks)]}
        self.started = {**{key: self.lesson[key] for key in ("course_id", "activity_id", "lesson_id", "language",
            "learner_level", "voice", "expires_at")}, "session_id": self.session,
            "lesson_job_id": self.job, "ai_notice": "You are interacting with an AI.", "preview": True}
        self.posts = []
        self.answer = {"text": "Un'altra spiegazione.", "audio": {"data": base64.b64encode(b"RIFF-answer").decode()},
                       "grounded": None, "sources": [], "warnings": ["GROUNDING_NOT_VERIFIED"], "playback_action": None}
        self.start_status, self.result_status = 201, 200
        self.end_status = 204
        def render(state, chunk):
            event = state.pop("test_audio_event", None)
            if event:
                return event
            __import__("streamlit").audio(state["audio"])
        renderer = patch("lesson_audio.render_audio", side_effect=render)
        renderer.start()
        self.addCleanup(renderer.stop)

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

    def app(self, *, signed=False, questions=True):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=10)
        app.session_state["page"] = "Learning"
        app.run()
        if signed:
            app.text_input(key="learning_session_jwt").set_value("private-jwt")
        else:
            app.checkbox(key="learning_session_consent").set_value(questions)
        button(app, "Start lesson").click().run()
        self.assertFalse(app.exception)
        return app

    def open(self, app, offset=12345):
        state = app.session_state["learning_state"]
        state["test_audio_event"] = {"chunk_id": self.chunks[state["index"]],
            "playback": state["playback"], "offset_ms": offset}
        app.run()
        self.assertFalse(app.exception)

    def ask(self, app, question="Spiega in altre parole"):
        app.text_area(key="learning_question_text").set_value(question)
        button(app, "Send question").click().run()
        self.assertFalse(app.exception)

    def test_interruption_pauses_without_api_call_and_back_resumes_exact_offset(self):
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}):
            app = self.app()
            self.assertEqual(len(self.posts), 1)  # B1 at Start lesson.
            self.open(app, 23456)
            self.assertFalse(app.get("audio"))
            self.assertTrue(button(app, "Next chunk").disabled)
            self.assertEqual(len(self.posts), 1)
            button(app, "Send question").click().run()
            self.assertTrue(app.error)
            self.assertEqual(len(self.posts), 1)
            self.ask(app)
            self.assertEqual(self.posts[-1][1]["json_body"]["playback_offset_ms"], 23456)
            self.assertEqual(len(app.get("audio")), 1)  # Answer only.
            self.assertTrue(any("Un'altra" in m.value for m in app.markdown))
            button(app, "Back to course").click().run()
            state = app.session_state["learning_state"]
            self.assertEqual(state["resume_offset_ms"], 23456)
            self.assertEqual(state["index"], 0)
            self.assertFalse(state["interrupt_open"])
            self.assertIsNone(state["interrupt"]["turn"])
            self.assertFalse(any("Un'altra" in m.value for m in app.markdown))

    def test_b4_retry_stable_and_next_chunk_reuses_b1_but_clears_answer(self):
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}):
            app = self.app()
            self.open(app)
            self.ask(app)
            first = self.posts[-1]
            app.run()
            self.assertEqual(len(self.posts), 2)
            button(app, "Retry question").click().run()
            self.assertEqual(self.posts[-1], first)
            self.ask(app, "Make it shorter")
            self.assertNotEqual(self.posts[-1][1]["json_body"]["request_id"], first[1]["json_body"]["request_id"])
            button(app, "Back to course").click().run()
            button(app, "Next chunk").click().run()
            self.assertEqual(app.session_state["learning_state"]["resume_offset_ms"], 0)
            self.open(app)
            self.assertEqual(app.text_area(key="learning_question_text").value, "")
            self.ask(app)
            self.assertEqual(self.posts[-1][1]["json_body"]["chunk_id"], self.chunks[1])
            self.assertEqual(sum(p == "/v1/sessions" for p, _ in self.posts), 1)

    def test_signed_session_keeps_original_jwt_for_b1_b4_b5(self):
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "false"}):
            app = self.app(signed=True)
            self.open(app)
            self.ask(app)
            button(app, "End lesson").click().run()
            self.assertFalse(app.exception)
            for _, kwargs in self.posts:
                self.assertEqual(kwargs["headers"], {"X-UPRAISE-Session-JWT": "private-jwt"})
            self.assertNotIn("consent_scope", self.posts[0][1]["json_body"])
            self.assertNotIn("session_id", self.posts[0][1]["json_body"])
            self.assertFalse(any("private-jwt" in str(v.value) for category in (app.code, app.json, app.caption) for v in category))

    def test_tts_failure_preserves_answer_and_return_position(self):
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}):
            self.answer.update(audio=None, warnings=["AUDIO_UNAVAILABLE"])
            app = self.app()
            self.open(app, 34567)
            self.ask(app)
            self.assertTrue(any("Un'altra" in m.value for m in app.markdown))
            self.assertFalse(app.get("audio"))
            self.assertTrue(any("AUDIO_UNAVAILABLE" in m.value for m in app.warning))
            button(app, "Back to course").click().run()
            self.assertEqual(app.session_state["learning_state"]["resume_offset_ms"], 34567)

    def test_player_instructions_override_resume_only_when_requested(self):
        for action, offset, rate, autoplay in (({"action": "SET_SPEED", "rate": .8}, 12345, .8, True),
                ({"action": "REPLAY"}, 0, 1., True), ({"action": "PAUSE"}, 12345, 1., False)):
            with self.subTest(action=action), patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}):
                self.answer.update(text=None, audio=None, playback_action=action)
                app = self.app()
                self.open(app)
                self.ask(app)
                button(app, "Back to course").click().run()
                state = app.session_state["learning_state"]
                self.assertEqual(state["resume_offset_ms"], offset)
                self.assertEqual(state.get("playback_rate", 1.), rate)
                self.assertEqual(state["autoplay"], autoplay)
                self.assertFalse(app.exception)

    def test_expired_session_stops_player_without_silent_restart(self):
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}):
            app = self.app()
            self.open(app)
            self.ask(app)
            state = app.session_state["learning_state"]
            state["interrupt"]["session"]["expires_at"] = (datetime.now(UTC)-timedelta(seconds=1)).isoformat()
            app.run()
            self.assertEqual(len(self.posts), 2)
            self.assertFalse(app.get("audio"))
            self.assertFalse(app.text_area)
            self.assertTrue(app.warning)
            button(app, "Back to lessons").click().run()
            self.assertEqual(self.posts[-1][1]["json_body"], {"reason": "TIMEOUT"})

    def test_questions_consent_is_set_at_start(self):
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}):
            app = self.app(questions=False)
            self.assertEqual(self.posts[0][1]["json_body"]["consent_scope"], [])
            self.open(app)
            self.assertFalse(app.text_area)
            self.assertEqual(len(self.posts), 1)
            button(app, "Back to course").click().run()
            self.assertFalse(app.exception)

    def test_revoked_lesson_on_return_closes_session_and_discards_answer(self):
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}):
            app = self.app()
            self.open(app)
            self.ask(app)
            self.result_status = 410
            button(app, "Back to course").click().run()
            self.assertIsNone(app.session_state["learning_state"]["active"])
            self.assertFalse(app.session_state["learning_state"]["interrupt"])
            self.assertEqual(self.posts[-1][1]["json_body"], {"reason": "ERROR"})
            self.assertFalse(any("Un'altra" in m.value for m in app.markdown))

    def test_finish_and_end_retry_clear_answer_and_send_b5_without_questions(self):
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}):
            app = self.app()
            button(app, "Next chunk").click().run()
            button(app, "Finish lesson").click().run()
            self.assertEqual(self.posts[-1][1]["json_body"], {"reason": "COMPLETED"})
            self.assertFalse(app.get("audio"))
            button(app, "Back to lessons").click().run()
            button(app, "Start lesson").click().run()
            self.open(app)
            self.ask(app)
            self.end_status = 503
            button(app, "End lesson").click().run()
            self.assertFalse(app.get("audio"))
            self.assertFalse(app.session_state["learning_state"]["interrupt"])
            self.assertTrue(app.error)
            first = self.posts[-1]
            self.end_status = 204
            button(app, "Retry session end").click().run()
            self.assertEqual(self.posts[-1], first)
            self.assertIsNone(app.session_state["learning_state"]["active"])

    def test_invalid_stale_positions_are_rejected_and_valid_offset_is_clamped(self):
        import learning
        with patch("api_client.request", side_effect=self.response), patch.dict(os.environ, {"UPRAISE_LOCAL_TEST_MODE": "true"}):
            app = self.app()
            state = app.session_state["learning_state"]
            event = {"chunk_id": self.chunks[0], "playback": state["playback"], "offset_ms": 12345}
            for bad in ({**event, "chunk_id": self.chunks[1]}, {**event, "playback": -1},
                        {**event, "offset_ms": float("nan")}, {**event, "offset_ms": -1}, {**event, "offset_ms": True}):
                self.assertFalse(learning.open_interruption(state, bad))
            self.assertTrue(learning.open_interruption(state, {**event, "offset_ms": 999999}))
            self.assertEqual(state["resume_offset_ms"], 60000)


if __name__ == "__main__":
    unittest.main()
