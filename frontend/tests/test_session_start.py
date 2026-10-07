import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from streamlit.testing.v1 import AppTest

FRONTEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FRONTEND))

from api_client import Connection, Result, request  # noqa: E402 - frontend modules need the path above
from session_start import payload_for  # noqa: E402


def button(app, label):
    return next(item for item in app.button if item.label == label)


class SessionStartTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"BACKEND_BASE_URL": "http://backend:8000",
                                                   "UPRAISE_LOCAL_TEST_MODE": "true"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.item = {"job_id": str(uuid4()), "lesson_id": "italian-lesson", "course_id": "course-1",
                     "activity_id": "activity-1", "language": "it", "learner_level": "intermediate",
                     "chunk_count": 14, "created_at": "2026-10-07T08:00:00Z", "expires_at": "2099-01-01T00:00:00Z"}
        self.items = [self.item]
        self.calls = []
        self.post_result = None
        self.seen_ids = set()

    def send(self, connection, method, path, **kwargs):
        self.calls.append((connection, method, path, kwargs))
        if method == "GET" and path == "/v1/lesson-jobs":
            return Result(method, path, 200, 3, self.items, True)
        self.assertEqual((method, path), ("POST", "/v1/sessions"))
        if self.post_result is not None:
            return self.post_result
        body = kwargs["json_body"]
        session_id = body.get("session_id", str(uuid4()))
        status = 200 if session_id in self.seen_ids else 201
        self.seen_ids.add(session_id)
        response = {"session_id": session_id, "lesson_job_id": body["lesson_job_id"],
                    "language": body.get("language", "it"), "learner_level": "intermediate",
                    "ai_notice": "Stai interagendo con un assistente di intelligenza artificiale.",
                    "expires_at": "2099-01-01T00:00:00Z", "preview": not bool(kwargs["headers"])}
        return Result(method, path, status, 2, response, True, {"x-upraise-auth-mode": "local-test"})

    def app(self):
        value = AppTest.from_file(str(FRONTEND / "app.py"), default_timeout=10)
        value.session_state["page"] = "Start session (B1)"
        value.run()
        self.assertFalse(value.exception)
        return value

    def posts(self):
        return [value for value in self.calls if value[1] == "POST"]

    def test_choose_lesson_start_display_and_retry_without_resubmission_on_navigation(self):
        with patch("api_client.request", side_effect=self.send):
            app = self.app()
            self.assertEqual(len(self.calls), 1)
            self.assertTrue(button(app, "Retry session start").disabled)
            button(app, "Start session").click().run()
            self.assertFalse(app.exception)
            first = self.posts()[0][3]["json_body"]
            self.assertEqual(first["lesson_job_id"], self.item["job_id"])
            self.assertEqual(first["consent_scope"], [])
            self.assertEqual(first["language"], "it")
            self.assertEqual(app.metric[0].value, "201")
            self.assertTrue(any("intelligenza artificiale" in value.value for value in app.info))
            self.assertFalse(app.dataframe)
            app.run()
            app.radio(key="page").set_value("Health").run()
            app.radio(key="page").set_value("Start session (B1)").run()
            self.assertEqual(len(self.posts()), 1)
            button(app, "Retry session start").click().run()
            self.assertEqual(self.posts()[-1][3]["json_body"], first)
            self.assertEqual(app.metric[0].value, "200")
            self.assertFalse(app.exception)

    def test_new_session_and_changed_consent_use_new_ids(self):
        with patch("api_client.request", side_effect=self.send):
            app = self.app()
            button(app, "Start session").click().run()
            original = self.posts()[-1][3]["json_body"]["session_id"]
            button(app, "New local session").click().run()
            self.assertEqual(len(self.posts()), 1)
            self.assertFalse(app.metric)
            button(app, "Start session").click().run()
            second = self.posts()[-1][3]["json_body"]["session_id"]
            self.assertNotEqual(second, original)
            app.checkbox(key="b1_field_questions").check().run()
            self.assertFalse(app.metric)
            button(app, "Start session").click().run()
            third = self.posts()[-1][3]["json_body"]
            self.assertNotEqual(third["session_id"], second)
            self.assertEqual(third["consent_scope"], ["questions"])

    def test_changed_lesson_or_credentials_clears_previous_response(self):
        self.items.append({**self.item, "job_id": str(uuid4()), "lesson_id": "other-lesson"})
        with patch("api_client.request", side_effect=self.send):
            app = self.app()
            button(app, "Start session").click().run()
            app.selectbox(key="b1_field_choice").set_value(self.items[1]["job_id"]).run()
            self.assertFalse(app.metric)
            button(app, "Start session").click().run()
            self.assertEqual(self.posts()[-1][3]["json_body"]["lesson_id"], "other-lesson")
            app.text_input(key="bearer_token").set_value("different-service-token").run()
            self.assertFalse(app.metric)
            self.assertTrue(button(app, "Retry session start").disabled)
            self.assertFalse(app.exception)

    def test_signed_mode_sends_separate_header_without_local_claims(self):
        with patch("api_client.request", side_effect=self.send):
            app = self.app()
            app.text_input(key="bearer_token").set_value("service-token").run()
            app.radio(key="b1_field_mode").set_value("Signed Middleware session").run()
            button(app, "Start session").click().run()
            self.assertEqual(len(self.posts()), 0)
            self.assertTrue(app.error)
            app.text_input(key="b1_field_jwt").set_value("private-session-assertion").run()
            button(app, "Start session").click().run()
            last = self.posts()[-1]
            self.assertEqual(last[0].token, "service-token")
            self.assertEqual(last[3]["headers"], {"X-UPRAISE-Session-JWT": "private-session-assertion"})
            self.assertNotIn("session_id", last[3]["json_body"])
            self.assertNotIn("consent_scope", last[3]["json_body"])
            # The token is only in the password widget and request header.
            displayed = [value.value for category in (app.info, app.caption, app.code, app.json) for value in category]
            self.assertFalse(any("private-session-assertion" in str(value) for value in displayed))
            app.text_input(key="b1_field_jwt").set_value("replacement-assertion").run()
            self.assertFalse(app.metric)
            self.assertTrue(button(app, "Retry session start").disabled)
            self.assertFalse(app.exception)

    def test_manual_details_and_scope_validation(self):
        self.items = []
        with patch("api_client.request", side_effect=self.send):
            app = self.app()
            button(app, "Start session").click().run()
            self.assertEqual(len(self.posts()), 0)
            for field, value in (("job_id", "invalid"), ("course_id", "course"),
                                 ("activity_id", "activity"), ("lesson_id", "lesson")):
                app.text_input(key="b1_field_" + field).set_value(value)
            app.run()
            button(app, "Start session").click().run()
            self.assertEqual(len(self.posts()), 0)
            app.text_input(key="b1_field_job_id").set_value(self.item["job_id"])
            document = str(uuid4())
            app.text_area(key="b1_field_documents").set_value(document + "\n" + document)
            button(app, "Start session").click().run()
            self.assertEqual(len(self.posts()), 0)
            app.text_area(key="b1_field_documents").set_value(document)
            button(app, "Start session").click().run()
            self.assertEqual(self.posts()[-1][3]["json_body"]["document_ids"], [document])
            self.assertNotIn("language", self.posts()[-1][3]["json_body"])
            self.assertFalse(app.exception)

    def test_timeout_and_backend_rejections_preserve_retry_context(self):
        with patch("api_client.request", side_effect=self.send):
            app = self.app()
            for response in [Result("POST", "/v1/sessions", None, 0, error="Session start timed out."),
                             Result("POST", "/v1/sessions", 410, 1, {"detail": "LESSON_OUTPUT_EXPIRED"}, True),
                             Result("POST", "/v1/sessions", 403, 1, {"detail": "LESSON_APPROVAL_REQUIRED"}, True)]:
                self.post_result = response
                button(app, "Start session").click().run()
                self.assertTrue(app.error)
                self.assertFalse(button(app, "Retry session start").disabled)
                self.assertFalse(app.exception)
            self.assertEqual(len({value[3]["json_body"]["session_id"] for value in self.posts()}), 1)

    def test_clear_session_removes_jwt_and_session_response(self):
        with patch("api_client.request", side_effect=self.send):
            app = self.app()
            app.radio(key="b1_field_mode").set_value("Signed Middleware session").run()
            app.text_input(key="b1_field_jwt").set_value("private-assertion").run()
            button(app, "Start session").click().run()
            button(app, "Clear session data").click().run()
            with self.assertRaises(KeyError):
                app.session_state["b1_state"]
            with self.assertRaises(KeyError):
                app.session_state["b1_field_jwt"]
            self.assertFalse(app.exception)

    def test_b1_client_transmits_service_and_session_tokens_and_safe_timeout_message(self):
        def handle(incoming):
            self.assertEqual(incoming.headers["Authorization"], "Bearer service-token")
            self.assertEqual(incoming.headers["X-UPRAISE-Session-JWT"], "session-token")
            raise httpx.ReadTimeout("PRIVATE_TRANSPORT_DETAILS", request=incoming)

        result = request(Connection("http://backend:8000", token="service-token"), "POST", "/v1/sessions",
                         json_body={"lesson_job_id": self.item["job_id"]},
                         headers={"X-UPRAISE-Session-JWT": "session-token"}, transport=httpx.MockTransport(handle))
        self.assertIn("Retry session start", result.error)
        self.assertNotIn("PRIVATE_TRANSPORT_DETAILS", result.error)

    def test_payload_normalizes_manual_ids_and_omits_empty_optional_fields(self):
        result = payload_for(None, job_id=self.item["job_id"].upper(), course_id=" course ",
                             activity_id="activity", lesson_id="lesson")
        self.assertEqual(result["lesson_job_id"], self.item["job_id"])
        self.assertEqual(result["course_id"], "course")
        self.assertNotIn("language", result)
        self.assertNotIn("document_ids", result)


if __name__ == "__main__":
    unittest.main()
