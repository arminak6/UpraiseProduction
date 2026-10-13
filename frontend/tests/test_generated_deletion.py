"""B7 explicit scope/confirmation, pinned retries and cache invalidation."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from streamlit.testing.v1 import AppTest

FRONTEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FRONTEND))

from api_client import Result


def button(app, label):
    return next(item for item in app.button if item.label == label)


class GeneratedDeletionTests(unittest.TestCase):
    def setUp(self):
        self.job = str(uuid4())
        self.calls = []
        self.status = 204
        self.levels = ["beginner", "intermediate", "advanced"]

    def send(self, connection, method, path, **options):
        self.calls.append((method, path, options))
        if method == "DELETE":
            if self.status == 204:
                level = options.get("params", {}).get("learner_level")
                self.levels = [value for value in self.levels if value != level] if level else []
            return Result(method, path, self.status, 1,
                          None if self.status == 204 else {"detail": "JOB_CLEANUP_PENDING"}, self.status != 204)
        return Result(method, path, 200, 1, [{"job_id": self.job, "lesson_id": "Example lesson",
            "course_id": "course-1", "language": "en", "voice": "Joanna", "learner_level": level,
            "generation_version": "a3-v2", "expires_at": "2099-01-01T00:00:00Z"} for level in self.levels], True)

    def app(self):
        app = AppTest.from_file(str(FRONTEND / "app.py"), default_timeout=10)
        app.session_state["page"] = "Delete teaching content (B7)"
        app.run()
        self.assertFalse(app.exception)
        app.text_input(key="b7_field_job").set_value(self.job)
        app.text_input(key="b7_field_level").set_value("intermediate").run()
        return app

    def test_selected_level_requires_confirmation_deletes_once_and_refreshes_catalog(self):
        with patch("api_client.request", side_effect=self.send):
            app = self.app()
            self.assertTrue(button(app, "Delete generated content · B7").disabled)
            self.assertEqual(sum(method == "DELETE" for method, _, _ in self.calls), 0)
            app.checkbox(key="b7_field_confirm").check().run()
            button(app, "Delete generated content · B7").click().run()
            self.assertFalse(app.exception)
            deletes = [call for call in self.calls if call[0] == "DELETE"]
            self.assertEqual(deletes, [("DELETE", "/v1/lesson-jobs/" + self.job, {"params": {"learner_level": "intermediate"}})])
            self.assertEqual(self.levels, ["beginner", "advanced"])
            self.assertTrue(any("intermediate level deleted" in item.value for item in app.success))
            self.assertFalse(app.checkbox(key="b7_field_confirm").value)
            app.run()
            self.assertEqual(sum(method == "DELETE" for method, _, _ in self.calls), 1)

    def test_whole_scope_omits_level_and_changing_scope_resets_confirmation(self):
        with patch("api_client.request", side_effect=self.send):
            app = self.app()
            app.checkbox(key="b7_field_confirm").check().run()
            app.radio(key="b7_field_scope").set_value("All generated levels").run()
            self.assertFalse(app.checkbox(key="b7_field_confirm").value)
            self.assertTrue(button(app, "Delete generated content · B7").disabled)
            self.assertTrue(app.text_input(key="b7_field_level").disabled)
            app.checkbox(key="b7_field_confirm").check().run()
            button(app, "Delete generated content · B7").click().run()
            self.assertFalse(app.exception)
            self.assertEqual([call for call in self.calls if call[0] == "DELETE"],
                             [("DELETE", "/v1/lesson-jobs/" + self.job, {})])
            self.assertTrue(any("All generated levels deleted" in item.value for item in app.success))

    def test_cleanup_retry_is_manual_and_pinned_across_navigation(self):
        self.status = 503
        with patch("api_client.request", side_effect=self.send):
            app = self.app()
            app.checkbox(key="b7_field_confirm").check().run()
            button(app, "Delete generated content · B7").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(app.text_input(key="b7_field_job").disabled)
            self.assertTrue(any("Cleanup may still be pending" in item.value for item in app.info))
            app.run()
            self.assertEqual(sum(method == "DELETE" for method, _, _ in self.calls), 1)
            app.radio(key="page").set_value("Health").run()
            app.radio(key="page").set_value("Delete teaching content (B7)").run()
            self.assertFalse(app.exception)
            self.assertEqual(sum(method == "DELETE" for method, _, _ in self.calls), 1)
            self.status = 204
            button(app, "Retry deletion · B7").click().run()
            self.assertFalse(app.exception)
            deletes = [call for call in self.calls if call[0] == "DELETE"]
            self.assertEqual(deletes[0], deletes[1])
            self.assertEqual(len(deletes), 2)

    def test_invalid_uuid_and_level_do_not_make_delete_requests(self):
        with patch("api_client.request", side_effect=self.send):
            app = self.app()
            app.text_input(key="b7_field_job").set_value("not-a-job").run()
            app.checkbox(key="b7_field_confirm").check().run()
            button(app, "Delete generated content · B7").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("valid lesson job UUID" in item.value for item in app.error))
            self.assertEqual(sum(method == "DELETE" for method, _, _ in self.calls), 0)

    def test_credential_change_clears_pinned_pending_request_without_delete(self):
        self.status = 503
        with patch("api_client.request", side_effect=self.send):
            app = self.app()
            app.checkbox(key="b7_field_confirm").check().run()
            button(app, "Delete generated content · B7").click().run()
            app.text_input(key="bearer_token").set_value("new-connection-token").run()
            self.assertFalse(app.exception)
            self.assertNotIn("pending", app.session_state["b7_state"])
            self.assertFalse(app.checkbox(key="b7_field_confirm").value)
            self.assertEqual(app.text_input(key="b7_field_job").value, "")
            self.assertEqual(sum(method == "DELETE" for method, _, _ in self.calls), 1)

    def test_deletion_clears_cached_generated_content_and_preserves_ingestion_sources(self):
        with patch("api_client.request", side_effect=self.send):
            app = self.app()
            app.session_state["ingestion_workspace"] = {"documents": {"source": {"document_id": "source"}},
                "jobs": {self.job: {"job_id": self.job, "result": {"text": "Old cached text"}, "audio": b"old"}},
                "catalog": [{"job_id": self.job}]}
            app.session_state["a7_state"] = {"lesson": {"job_id": self.job}, "items": [{"job_id": self.job}]}
            app.session_state["learning_state"] = {"active": {"job_id": self.job}}
            app.session_state["lesson_result"] = Result("GET", "result", 200, 1, {"job_id": self.job}, True)
            app.checkbox(key="b7_field_confirm").check().run()
            button(app, "Delete generated content · B7").click().run()
            self.assertFalse(app.exception)
            workspace = app.session_state["ingestion_workspace"]
            self.assertEqual(workspace["documents"], {"source": {"document_id": "source"}})
            self.assertNotIn("result", workspace["jobs"][self.job])
            self.assertNotIn("audio", workspace["jobs"][self.job])
            self.assertNotIn("lesson", app.session_state["a7_state"])
            self.assertNotIn("learning_state", app.session_state)
            self.assertNotIn("lesson_result", app.session_state)


if __name__ == "__main__":
    unittest.main()
