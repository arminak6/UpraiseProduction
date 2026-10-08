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
