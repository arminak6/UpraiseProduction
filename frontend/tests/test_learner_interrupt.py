import base64
import io
import os
import sys
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api_client import Connection, Result, request  # noqa: E402


def button(app, label):
    return next(item for item in app.button if item.label == label)


class LearnerInterruptTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.session_id, self.chunk_id = str(uuid4()), str(uuid4())
        self.response = {"request_id": str(uuid4()), "chunk_id": self.chunk_id, "answer_id": str(uuid4()),
                         "text": "Here is another explanation. [p. 1]", "grounded": None,
                         "sources": [], "audio": None, "warnings": ["GROUNDING_NOT_VERIFIED"]}

    def send(self, connection, method, path, **kwargs):
        self.calls.append((connection, method, path, kwargs))
        self.assertEqual((method, path), ("POST", f"/v1/sessions/{self.session_id}/interrupts"))
        return Result(method, path, 200, 2, self.response, True)

    def app(self):
        value = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
        value.session_state["page"] = "Learner interrupt (B4)"
        value.run()
        self.assertFalse(value.exception)
        return value

    def fill(self, app):
        app.text_input(key="b4_field_session").set_value(self.session_id)
        app.text_input(key="b4_field_chunk").set_value(self.chunk_id)
        app.text_area(key="b4_field_text").set_value("Explain in other words")
        app.run()

    def test_explicit_submission_stable_retry_and_navigation_without_reposting(self):
        with patch("api_client.request", side_effect=self.send), patch.dict(os.environ, {"BACKEND_BASE_URL": "http://backend:8000"}):
            app = self.app()
            self.assertFalse(self.calls)
            self.fill(app)
            button(app, "Send interruption").click().run()
            self.assertFalse(app.exception)
            first = self.calls[0][3]["json_body"]
            self.assertEqual(first["text"], "Explain in other words")
            self.assertTrue(any("another explanation" in item.value for item in app.markdown))
            self.assertTrue(any("not been independently verified" in item.value for item in app.caption))
            self.assertFalse(any("Supported by course sources: yes" in item.value for item in app.caption))
            app.run()
            app.radio(key="page").set_value("Health").run()
            app.radio(key="page").set_value("Learner interrupt (B4)").run()
            self.assertEqual(len(self.calls), 1)
            button(app, "Retry interruption").click().run()
            self.assertEqual(self.calls[-1][3]["json_body"], first)
            app.text_area(key="b4_field_text").set_value("Make it shorter").run()
            self.assertTrue(button(app, "Retry interruption").disabled)
            button(app, "Send interruption").click().run()
            self.assertNotEqual(self.calls[-1][3]["json_body"]["request_id"], first["request_id"])

    def test_invalid_ids_empty_message_and_new_id_do_not_post(self):
        with patch("api_client.request", side_effect=self.send):
            app = self.app()
            button(app, "Send interruption").click().run()
            self.assertTrue(app.error)
            self.assertFalse(self.calls)
            self.fill(app)
            previous = app.session_state["b4_state"]["request_id"]
            button(app, "New message ID").click().run()
            self.assertNotEqual(app.session_state["b4_state"]["request_id"], previous)
            self.assertFalse(self.calls)

    def test_signed_session_header_is_separate_and_not_displayed(self):
        with patch("api_client.request", side_effect=self.send):
            app = self.app()
            self.fill(app)
            app.text_input(key="b4_field_jwt").set_value("private-session-jwt").run()
            button(app, "Send interruption").click().run()
            self.assertEqual(self.calls[0][3]["headers"], {"X-UPRAISE-Session-JWT": "private-session-jwt"})
            self.assertFalse(any("private-session-jwt" in str(item.value) for category in (app.code, app.json, app.caption)
                                 for item in category))

    def test_inline_audio_and_player_action_are_displayed(self):
        data = io.BytesIO()
        with wave.open(data, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x00"*100)
        self.response.update(audio={"data": base64.b64encode(data.getvalue()).decode()},
                             playback_action={"action": "SET_SPEED", "rate": 0.8})
        with patch("api_client.request", side_effect=self.send):
            app = self.app()
            self.fill(app)
            button(app, "Send interruption").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get("audio")), 1)
            self.assertTrue(any("SET_SPEED" in item.value for item in app.info))

    def test_http_audio_uses_named_audio_part_and_timeout_retry_guidance(self):
        def handler(req):
            self.assertIn(b'name="audio"', req.content)
            self.assertNotIn(b'name="file"', req.content)
            return httpx.Response(200, json=self.response)
        result = request(Connection("http://backend:8000"), "POST", f"/v1/sessions/{self.session_id}/interrupts",
                         fields={"request_id": str(uuid4()), "chunk_id": self.chunk_id},
                         upload=("recording.wav", b"wave", "audio/wav"), upload_field="audio",
                         transport=httpx.MockTransport(handler))
        self.assertTrue(result.successful)
        def offline(req):
            raise httpx.ReadTimeout("private", request=req)
        result = request(Connection("http://backend:8000"), "POST", f"/v1/sessions/{self.session_id}/interrupts",
                         transport=httpx.MockTransport(offline))
        self.assertIn("same request ID", result.error)
