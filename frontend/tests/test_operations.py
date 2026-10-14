"""C1/C2 explicit HTTP checks, partial outages and credential/course isolation."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

FRONTEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FRONTEND))
from api_client import Result  # noqa: E402


def button(app, label):
    return next(item for item in app.button if item.label == label)


class OperationsTests(unittest.TestCase):
    def app(self):
        app = AppTest.from_file(str(FRONTEND / "app.py"), default_timeout=10)
        app.session_state["page"] = "Health"
        app.run()
        self.assertFalse(app.exception)
        return app

    def test_c3_explicit_confirmed_change_and_no_automatic_retry(self):
        with patch("api_client.request", return_value=Result("PUT", "/v1/admin/kill-switch", 200, 2,
                   {"features_enabled": {"answers": False}, "audit_pending": False}, True)) as send:
            app = self.app()
            send.assert_not_called()
            self.assertTrue(button(app, "Apply kill-switch change").disabled)
            app.text_input(key="operations_admin_course").set_value("course-1").run()
            app.checkbox(key="operations_admin_confirm").check().run()
            button(app, "Apply kill-switch change").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(send.call_args.args[1:3], ("PUT", "/v1/admin/kill-switch"))
            self.assertEqual(send.call_args.kwargs["json"], {
                "scope": "course_id", "course_id": "course-1", "feature": "answers", "enabled": True})
            self.assertFalse(app.checkbox(key="operations_admin_confirm").value)
            app.run()
            send.assert_called_once()

    def test_c3_changing_target_or_credentials_resets_confirmation_and_result(self):
        with patch("api_client.request") as send:
            app = self.app()
            app.text_input(key="operations_admin_course").set_value("course-1").run()
            app.checkbox(key="operations_admin_confirm").check().run()
            app.selectbox(key="operations_admin_feature").set_value("all").run()
            self.assertFalse(app.checkbox(key="operations_admin_confirm").value)
            app.checkbox(key="operations_admin_confirm").check().run()
            next(item for item in app.text_input if item.label == "Bearer token").set_value("changed-token").run()
            self.assertFalse(app.checkbox(key="operations_admin_confirm").value)
            send.assert_not_called()

    def test_c3_global_release_and_failed_change_require_manual_submit(self):
        with patch("api_client.request", return_value=Result("PUT", "/v1/admin/kill-switch", 403, 2,
                   {"detail": "INSUFFICIENT_SCOPE"}, True)) as send:
            app = self.app()
            app.selectbox(key="operations_admin_scope").set_value("Global").run()
            app.radio(key="operations_admin_action").set_value("Release runtime disable").run()
            app.checkbox(key="operations_admin_confirm").check().run()
            button(app, "Apply kill-switch change").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(send.call_args.kwargs["json"], {"scope": "global", "feature": "answers", "enabled": False})
            app.run()
            send.assert_called_once()

    def test_readiness_displays_503_without_sending_ai_work(self):
        with patch("api_client.request", return_value=Result("GET", "/ready", 503, 2,
                  {"status": "NOT_READY", "dependencies": {"qdrant": {"state": "UNAVAILABLE"}}}, True)) as send:
            app = self.app()
            send.assert_not_called()
            button(app, "Check readiness").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(send.call_args.args[1:3], ("GET", "/ready"))
            self.assertEqual(app.metric[0].value, "503")
            app.run()
            send.assert_called_once()

    def test_c5_explicit_read_only_check_and_no_automatic_retries(self):
        body = {"configuration_id": "sha256:invented-config", "application": {"version": "0.1.0"},
                "cognitive_load": {"fusion_rule": {"status": "NOT_IMPLEMENTED"}}}
        with patch("api_client.request", return_value=Result("GET", "/v1/version", 200, 2, body, True)) as send:
            app = self.app()
            send.assert_not_called()
            button(app, "Check versions").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(send.call_args.args[1:3], ("GET", "/v1/version"))
            self.assertEqual(app.session_state["version_response"].body, body)
            app.run()
            send.assert_called_once()
            next(item for item in app.text_input if item.label == "Bearer token").set_value("invented-new-token").run()
            self.assertNotIn("version_response", app.session_state)
            self.assertFalse(app.exception)

    def test_c2_optional_course_and_unknown_are_displayed_and_old_results_cleared(self):
        body = {"features": {"answers": {"enabled": True, "state": "UNKNOWN", "reason_codes": ["PROVIDER_NOT_VERIFIED"]}}}
        with patch("api_client.request", return_value=Result("GET", "/v1/status", 200, 2, body, True)) as send:
            app = self.app()
            button(app, "Check feature status").click().run()
            self.assertEqual(send.call_args.args[1:3], ("GET", "/v1/status"))
            self.assertEqual(send.call_args.kwargs["params"], {})
            self.assertEqual(app.dataframe[0].value.iloc[0]["State"], "UNKNOWN")
            app.text_input(key="operations_course_id").set_value("test-course").run()
            self.assertFalse(app.dataframe)
            button(app, "Check feature status").click().run()
            self.assertEqual(send.call_args.kwargs["params"], {"course_id": "test-course"})
            next(item for item in app.text_input if item.label == "Bearer token").set_value("different-test-token").run()
            self.assertFalse(app.dataframe)
            self.assertFalse(app.exception)


if __name__ == "__main__":
    unittest.main()
