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
            if path == f"/v1/jobs/{revised_id}":
                return Result(method, path, 200, 1, {"job_id": revised_id, "status": "RUNNING",
                                                    "step": "REVIEW", "progress": 0.4}, True)
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
            self.assertFalse(app.checkbox(key="a7_field_all_levels").value)
            self.assertTrue(app.checkbox(key="a7_field_all_levels").disabled)
            app.text_area(key="a7_field_feedback").set_value("Explain this more simply.")
            button(app, "Generate revised chunk").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(next(call for call in calls if call[0] == "POST"), ("POST", "/v1/audio:regenerate", {"json_body": {
                "lesson_job_id": parent_id, "chunk_id": chunk_id, "teacher_review": "Explain this more simply."}}))
            self.assertEqual(app.session_state["tracking_path"], f"/v1/jobs/{revised_id}")
            self.assertTrue(button(app, "Generate revised chunk").disabled)
            app.run()
            self.assertEqual(sum(method == "POST" for method, _, _ in calls), 1)
            next(item for item in app.text_input if item.label == "Bearer token").set_value("changed-private-token").run()
            self.assertIsNone(app.session_state["a7_state"]["lesson"])
            self.assertIsNone(app.session_state["a7_state"]["accepted"])

    def test_inline_tracking_pauses_on_error_stops_at_done_and_opens_revised_text(self):
        parent, chunk, revision = [str(uuid4()) for _ in range(3)]
        status = {"job_id": revision, "status": "RUNNING", "step": "SPEECH", "progress": 0.7}
        status_code = 200
        calls = []

        def send(connection, method, path, **options):
            calls.append((method, path))
            if method == "POST":
                return Result(method, path, 202, 1, {"job_id": revision}, True)
            if path == f"/v1/jobs/{revision}":
                return Result(method, path, status_code, 1, dict(status), True)
            job = revision if path == f"/v1/jobs/{revision}/result" else parent
            return Result(method, path, 200, 1, {"job_id": job, "language": "en", "voice": "Joanna",
                          "learner_level": "advanced", "expires_at": "2099-01-01T00:00:00Z",
                          "chunks": [{"chunk_id": chunk, "index": 0,
                                      "text": "Revised teaching text." if job == revision else "Original teaching text."}]}, True)

        with patch("api_client.request", side_effect=send):
            app = AppTest.from_file(str(FRONTEND / "app.py"), default_timeout=10)
            app.session_state["page"] = "Revise chunk (A7)"
            app.run()
            app.text_input(key="a7_field_job").set_value(parent)
            button(app, "Load lesson by job ID").click().run()
            self.assertFalse(app.get("progress"))  # Loading a parent does not start A7.
            app.text_area(key="a7_field_feedback").set_value("Make the example clearer.")
            button(app, "Generate revised chunk").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("70.00%" in item.proto.text for item in app.get("progress")))
            self.assertFalse(any(item.label == "Open updated lesson" for item in app.button))
            status_code = 503
            button(app, "Refresh revision status").click().run()
            self.assertTrue(any("tracking paused" in item.value for item in app.warning))
            count = len(calls)
            app.run()
            self.assertEqual(len(calls), count)
            status_code = 200
            status.update(status="DONE", step="DONE", progress=1)
            button(app, "Refresh revision status").click().run()
            self.assertTrue(any("Revision complete" in item.value for item in app.success))
            count = len(calls)
            app.run()
            self.assertEqual(len(calls), count)
            button(app, "Open updated lesson").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["a7_state"]["lesson"]["job_id"], revision)
            self.assertEqual(app.text_input(key="a7_field_job").value, revision)
            self.assertTrue(any("Revised teaching text" in item.value for item in app.markdown))
            self.assertEqual(sum(method == "POST" for method, _ in calls), 1)

    def test_completed_replacement_automatically_reloads_same_lesson_and_allows_next_edit(self):
        parent, chunk, revision = [str(uuid4()) for _ in range(3)]
        first_chunk = str(uuid4())
        status = {"job_id": revision, "status": "RUNNING", "step": "SPEECH", "progress": 0.7}
        calls = []

        def send(connection, method, path, **options):
            calls.append((method, path, options))
            if method == "POST":
                return Result(method, path, 202, 1, {"job_id": revision}, True)
            if path == f"/v1/jobs/{revision}":
                return Result(method, path, 200, 1, dict(status), True)
            text = "Updated current chunk." if status["status"] == "DONE" else "Original current chunk."
            return Result(method, path, 200, 1, {
                "job_id": parent, "language": "en", "voice": "Joanna", "learner_level": "advanced",
                "expires_at": "2099-01-01T00:00:00Z", "chunks": [
                    {"chunk_id": first_chunk, "index": 0, "text": "Unchanged first part."},
                    {"chunk_id": chunk, "index": 1, "text": text}],
            }, True)

        with patch("api_client.request", side_effect=send):
            app = AppTest.from_file(str(FRONTEND / "app.py"), default_timeout=10)
            app.session_state["page"] = "Revise chunk (A7)"
            app.run()
            app.text_input(key="a7_field_job").set_value(parent)
            button(app, "Load lesson by job ID").click().run()
            app.selectbox(key="a7_chunk_choice").set_value(app.session_state["a7_state"]["lesson"]["chunks"][1]).run()
            app.text_area(key="a7_field_feedback").set_value("Improve the explanation.")
            button(app, "Generate revised chunk").click().run()
            status.update(status="DONE", step="DONE", progress=1, result={"replacement_target_job_id": parent})
            button(app, "Refresh revision status").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.text_input(key="a7_field_job").value, parent)
            self.assertTrue(any("Updated current chunk." in item.value for item in app.markdown))
            self.assertEqual(app.selectbox(key="a7_chunk_choice").value["chunk_id"], chunk)
            self.assertEqual(app.text_area(key="a7_field_feedback").value, "")
            self.assertFalse(button(app, "Generate revised chunk").disabled)
            self.assertIn(("GET", f"/v1/jobs/{parent}/result", {"params": {"learner_level": "advanced"}}), calls)
            app.run()
            self.assertEqual(sum(method == "POST" for method, _, _ in calls), 1)
            revision = str(uuid4())
            status.clear()
            status.update(job_id=revision, status="RUNNING", step="REVIEW", progress=0.4)
            app.text_area(key="a7_field_feedback").set_value("Improve the updated explanation again.")
            button(app, "Generate revised chunk").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(sum(method == "POST" for method, _, _ in calls), 2)
            self.assertTrue(button(app, "Generate revised chunk").disabled)
            app.run()
            self.assertEqual(sum(method == "POST" for method, _, _ in calls), 2)

    def test_all_levels_checkbox_keeps_selected_chunk_and_shows_combined_progress(self):
        parent, plan, revision = [str(uuid4()) for _ in range(3)]
        chunks = {level: str(uuid4()) for level in ("beginner", "intermediate", "advanced")}
        status = {"job_id": revision, "status": "RUNNING", "step": "REVIEW", "progress": 0.4}
        calls = []

        def send(connection, method, path, **options):
            calls.append((method, path, options))
            if method == "POST":
                return Result(method, path, 202, 1, {"job_id": revision}, True)
            if path == f"/v1/jobs/{revision}":
                return Result(method, path, 200, 1, dict(status), True)
            variants = {level: {"job_id": parent, "language": "en", "voice": "Joanna", "learner_level": level,
                "chunk_plan_id": plan, "expires_at": "2099-01-01T00:00:00Z",
                "chunks": [{"index": 0, "chunk_id": chunk, "text":
                    ("Updated" if status["status"] == "DONE" else "Original") + f" {level} text."}]}
                for level, chunk in chunks.items()}
            requested = options.get("params", {}).get("learner_level")
            return Result(method, path, 200, 1, variants[requested] if requested else {"job_id": parent, "variants": variants}, True)

        with patch("api_client.request", side_effect=send):
            app = AppTest.from_file(str(FRONTEND / "app.py"), default_timeout=10)
            app.session_state["page"] = "Revise chunk (A7)"
            app.run()
            app.text_input(key="a7_field_job").set_value(parent)
            button(app, "Load lesson by job ID").click().run()
            app.selectbox(key="a7_variant_level").set_value("advanced").run()
            self.assertFalse(app.checkbox(key="a7_field_all_levels").value)
            self.assertFalse(app.checkbox(key="a7_field_all_levels").disabled)
            app.checkbox(key="a7_field_all_levels").set_value(True)
            app.text_area(key="a7_field_feedback").set_value("Explain the same topic for all levels.")
            button(app, "Generate revised chunk").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any(call[0] == "POST" for call in calls))
            posted = next(call for call in calls if call[0] == "POST")
            self.assertEqual(posted[2]["json_body"], {"lesson_job_id": parent, "chunk_id": chunks["advanced"],
                "teacher_review": "Explain the same topic for all levels.", "apply_to_all_levels": True})
            status.update(status="DONE", step="DONE", progress=1, result={
                "replacement_target_job_id": parent, "revised_levels": list(chunks)})
            button(app, "Refresh revision status").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("all 3 difficulty levels" in item.value for item in app.success))
            self.assertTrue(any("Updated advanced text" in item.value for item in app.markdown))
            self.assertEqual(app.session_state["a7_state"]["lesson"]["job_id"], parent)
            self.assertEqual(sum(method == "POST" for method, _, _ in calls), 1)

    def test_failed_alignment_displays_reason_level_and_correction_instructions(self):
        parent, chunk, revision = [str(uuid4()) for _ in range(3)]
        calls = []

        def send(connection, method, path, **options):
            calls.append((method, path))
            if method == "POST":
                return Result(method, path, 202, 1, {"job_id": revision}, True)
            if path == f"/v1/jobs/{revision}":
                return Result(method, path, 200, 1, {"job_id": revision, "status": "FAILED", "step": "FAILED",
                    "progress": 0.58, "error": {"code": "REVISION_ALIGNMENT_REJECTED", "learner_level": "beginner",
                        "chunk_index": 1, "correction_rounds": 2, "reason": "The required value seven is missing.",
                        "review": {"revision_instructions": ["Restore the required value while removing attribution."]}}}, True)
            return Result(method, path, 200, 1, {"job_id": parent, "language": "en", "voice": "Joanna",
                "learner_level": "beginner", "expires_at": "2099-01-01T00:00:00Z",
                "chunks": [{"chunk_id": chunk, "index": 0, "text": "Current teaching text stays intact."}]}, True)

        with patch("api_client.request", side_effect=send):
            app = AppTest.from_file(str(FRONTEND / "app.py"), default_timeout=10)
            app.session_state["page"] = "Revise chunk (A7)"
            app.run()
            app.text_input(key="a7_field_job").set_value(parent)
            button(app, "Load lesson by job ID").click().run()
            app.text_area(key="a7_field_feedback").set_value("Remove lecturer attribution.")
            button(app, "Generate revised chunk").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("REVISION_ALIGNMENT_REJECTED" in item.value for item in app.error))
            self.assertTrue(any(item.label == "Why this revision failed" for item in app.expander))
            self.assertTrue(any("Level: beginner" in item.value and "Part 2" in item.value for item in app.markdown))
            self.assertTrue(any("required value seven" in item.value for item in app.markdown))
            self.assertTrue(any("Restore the required value" in item.value for item in app.markdown))
            self.assertTrue(any("Correction attempts: 2" in item.value for item in app.caption))
            self.assertTrue(any("Current teaching text stays intact" in item.value for item in app.markdown))
            count = len(calls)
            app.run()
            self.assertEqual(len(calls), count)
            self.assertEqual(sum(method == "POST" for method, _ in calls), 1)

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
