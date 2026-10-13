"""Orchestrator interactions with invented material and fake HTTP; no provider calls."""

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import streamlit as st
from streamlit.testing.v1 import AppTest

FRONTEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FRONTEND))
import api_client  # noqa: E402
from ingestion_workspace import STAGES  # noqa: E402


def button(app, label):
    return next(item for item in app.button if item.label == label)


class IngestionWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.document, self.ingestion, self.lesson, self.revision = [str(uuid4()) for _ in range(4)]
        self.audio = b"RIFF-invented-test-audio"
        self.chunks = [str(uuid4()) for _ in range(3)]
        self.bundle = {"job_id": self.lesson, "lesson_id": "test", "course_id": "123456", "activity_id": "654321",
                       "language": "en", "voice": "Joanna", "status": "DRAFT", "generation_version": "a3-v2",
                       "expires_at": "2099-01-01T00:00:00Z", "variants": {}}
        for index, level in enumerate(("beginner", "intermediate", "advanced")):
            self.bundle["variants"][level] = {
                **{key: value for key, value in self.bundle.items() if key != "variants"}, "learner_level": level,
                "chunks": [{"index": 0, "chunk_id": self.chunks[index], "text": level + " invented lesson.",
                            "audio_sha256": hashlib.sha256(self.audio).hexdigest(),
                            "audio_url": "https://external.invalid/never-follow", "sources": [], "reviewer_report": {}}],
            }
        self.result_status = 200
        self.audio_status = 200
        self.audio_overrides = {}
        self.catalog_rows = None
        self.catalog_status = 200
        self.result_overrides = {}
        self.status_body = {"job_id": self.lesson, "lesson_id": "test", "status": "DONE", "step": "DONE", "progress": 1}

    def response(self, connection, method, path, **kwargs):
        if method == "POST":
            job = self.ingestion if path == "/v1/documents" else self.revision if path == "/v1/audio:regenerate" else self.lesson
            body = {"job_id": job}
            if path == "/v1/documents":
                body["document_id"] = self.document
            return api_client.Result(method, path, 202, 2, body, True, {"location": "/v1/jobs/" + job})
        if method == "DELETE":
            return api_client.Result(method, path, 204, 2, "")
        if path == "/v1/documents":
            return api_client.Result(method, path, 200, 1, [{"document_id": self.document, "course_id": "123456",
                                      "activity_id": "654321", "status": "RUNNING", "progress": 0.123456,
                                      "filename": "synthetic.pdf", "chunk_count": 2}], True)
        if path == "/v1/lesson-jobs":
            rows = self.catalog_rows if self.catalog_rows is not None else [
                {**{key: value for key, value in self.bundle.items() if key != "variants"}, "learner_level": level}
                for level in self.bundle["variants"]]
            return api_client.Result(method, path, self.catalog_status, 1, copy.deepcopy(rows), True)
        if path.endswith("/result"):
            return api_client.Result(method, path, self.result_status, 1,
                                    copy.deepcopy(self.result_overrides.get(path.split('/')[-2], self.bundle))
                                    if self.result_status == 200 else {"detail": "EXPIRED"}, True)
        if "/audio/" in path:
            audio = self.audio_overrides.get(path.rsplit("/", 1)[1], self.audio)
            return api_client.Result(method, path, self.audio_status, 1, binary=audio if self.audio_status == 200 else None)
        status_body = copy.deepcopy(self.status_body)
        status_body["job_id"] = path.rsplit("/", 1)[-1]
        return api_client.Result(method, path, 200, 1, status_body, True)

    def app(self):
        app = AppTest.from_file(str(FRONTEND / "app.py"), default_timeout=10)
        app.run()
        self.assertFalse(app.exception)
        app.text_input(key="iw_field_course").set_value("123456")
        app.text_input(key="iw_field_activity").set_value("654321")
        app.run()
        return app

    def stage(self, app, index):
        app.radio(key="iw_field_stage").set_value(STAGES[index]).run()
        self.assertFalse(app.exception)

    def submit_signed(self, app):
        self.stage(app, 0)
        app.radio(key="iw_field_source_mode").set_value("Signed Moodle URL").run()
        app.text_input(key="iw_field_url").set_value("https://moodle.example/synthetic.pdf?token=private-test-secret")
        app.text_input(key="iw_field_hash").set_value("revision-1")
        button(app, "Submit material · A1").click().run()
        self.assertFalse(app.exception)

    def load_draft(self, app):
        self.stage(app, 3)
        app.text_input(key="iw_field_import").set_value(self.lesson)
        button(app, "Track existing job").click().run()
        button(app, "Refresh selected job · A4").click().run()
        self.assertFalse(app.exception)

    def voice_callback(self, downloads):
        call = next(call for call in reversed(downloads.call_args_list) if call.args[0] == "Download voice")
        return call.kwargs["data"]

    def test_suggestions_are_stable_and_no_navigation_submits(self):
        with patch("api_client.request", side_effect=self.response) as send:
            app = self.app()
            self.assertEqual(app.radio(key="page").value, "Ingestion workspace")
            source = app.session_state["iw_field_source"]
            self.assertTrue(source.isdigit())
            for stage in (1, 2, 3, 0):
                self.stage(app, stage)
            app.radio(key="page").set_value("Health").run()
            app.radio(key="page").set_value("Ingestion workspace").run()
            self.assertEqual(app.session_state["iw_field_source"], source)
            self.assertEqual(app.text_input(key="iw_field_course").value, "123456")
            self.assertEqual(send.call_count, 2)
            self.assertTrue(all(call.args[1] == "GET" for call in send.call_args_list))

    def test_a1_a3_carry_context_ids_order_and_retry_keys(self):
        with patch("api_client.request", side_effect=self.response) as send:
            app = self.app()
            self.submit_signed(app)
            first = send.call_args
            a1_key = first.kwargs["headers"]["Idempotency-Key"]
            self.assertEqual(first.kwargs["json_body"]["course_id"], "123456")
            self.assertEqual(app.session_state["iw_field_document_ids"], self.document)
            self.stage(app, 2)
            button(app, "Generate lesson · A3").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(send.call_args.args[1:3], ("POST", "/v2/lesson-jobs"))
            payload = send.call_args.kwargs["json_body"]
            self.assertEqual(payload["document_ids"], [self.document])
            self.assertEqual(payload["activity_id"], "654321")
            self.assertEqual(payload["options"], {"source_window_pages": 5, "target_chunk_words": 200, "max_chunk_words": 500})
            self.assertEqual(len(app.session_state["ingestion_workspace"]["jobs"]), 2)
            self.stage(app, 0)
            button(app, "Submit material · A1").click().run()
            self.assertEqual(send.call_args.kwargs["headers"]["Idempotency-Key"], a1_key)
            self.assertNotIn("private-test-secret", json.dumps(app.session_state["ingestion_workspace"]["trace"]))
            self.assertEqual(send.call_count, 4)

    def test_voice_language_changes_preserve_job_context_and_only_submit_on_click(self):
        with patch("api_client.request", side_effect=self.response) as send:
            app = self.app()
            self.submit_signed(app)
            self.stage(app, 2)
            self.assertEqual(app.selectbox(key="iw_field_voice").value, "Joanna")
            retry_key = app.session_state["iw_field_a3_key"]
            calls = send.call_count
            app.text_input(key="iw_field_output_language").set_value("it").run()
            self.assertFalse(app.exception)
            picker = app.selectbox(key="iw_field_voice")
            self.assertEqual(picker.value, "Bianca")
            self.assertEqual(picker.options, ["Bianca · Italian · Female", "Adriano · Italian · Male"])
            picker.select("Adriano").run()
            self.assertEqual(send.call_count, calls)
            button(app, "Generate lesson · A3").click().run()
            self.assertEqual(send.call_args.kwargs["json_body"]["language"], "it")
            self.assertEqual(send.call_args.kwargs["json_body"]["voice"], "Adriano")
            self.assertEqual(send.call_args.kwargs["json_body"]["document_ids"], [self.document])
            self.stage(app, 0)
            self.stage(app, 2)
            self.assertEqual(app.selectbox(key="iw_field_voice").value, "Adriano")
            self.assertEqual(app.session_state["iw_field_a3_key"], retry_key)
            posts = sum(call.args[1] == "POST" for call in send.call_args_list)
            app.text_input(key="iw_field_output_language").set_value("en-GB").run()
            self.assertEqual(app.selectbox(key="iw_field_voice").value, "Amy")
            self.assertEqual(len(app.selectbox(key="iw_field_voice").options), 4)
            button(app, "Generate lesson · A3").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(sum(call.args[1] == "POST" for call in send.call_args_list), posts)
            self.assertTrue(any("request changed" in item.value for item in app.error))

    def test_pdf_hash_and_multipart_are_filled(self):
        import streamlit as st
        original = st.file_uploader
        data = b"%PDF-1.7 synthetic fixture"
        upload = SimpleNamespace(name="synthetic.pdf", getvalue=lambda: data)
        def uploader(label, *args, **kwargs):
            return upload if label == "Teaching material (PDF)" else original(label, *args, **kwargs)
        with patch("api_client.request", side_effect=self.response) as send, patch("streamlit.file_uploader", side_effect=uploader):
            app = self.app()
            self.stage(app, 0)
            button(app, "Submit material · A1").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(send.call_args.kwargs["fields"]["content_hash"], hashlib.sha256(data).hexdigest())
            self.assertEqual(send.call_args.kwargs["upload"], ("synthetic.pdf", data, "application/pdf"))

    def test_changed_input_requires_new_key_and_validation_does_not_send(self):
        with patch("api_client.request", side_effect=self.response) as send:
            app = self.app()
            self.submit_signed(app)
            original = app.session_state["iw_field_a1_key"]
            app.text_input(key="iw_field_version").set_value("2")
            button(app, "Submit material · A1").click().run()
            self.assertEqual(send.call_count, 1)
            self.assertTrue(any("request changed" in error.value for error in app.error))
            button(app, "Prepare a new document").click().run()
            self.assertNotEqual(app.session_state["iw_field_a1_key"], original)
            self.stage(app, 2)
            app.number_input(key="iw_field_words").set_value(600)
            button(app, "Generate lesson · A3").click().run()
            self.assertEqual(send.call_count, 2)
            self.assertTrue(any("Target length must not exceed" in error.value for error in app.error))

    def test_one_download_button_fetches_only_the_selected_level_and_chunk_on_click(self):
        second = str(uuid4())
        audio = self.audio + b"-advanced-part-two"
        self.audio_overrides[second] = audio
        for level, variant in self.bundle["variants"].items():
            variant["chunks"].append({**variant["chunks"][0], "index": 1,
                                       "chunk_id": second if level == "advanced" else str(uuid4()),
                                       "audio_sha256": hashlib.sha256(audio).hexdigest()})
        with patch("api_client.request", side_effect=self.response) as send, \
                patch("ingestion_workspace.st.download_button", wraps=st.download_button) as downloads:
            app = self.app()
            self.load_draft(app)
            app.selectbox(key="iw_field_result_level").set_value("advanced").run()
            app.selectbox(key="iw_field_chunk").set_value(1).run()
            labels = [item.label for item in app.get("download_button")]
            self.assertEqual(labels.count("Download voice"), 1)
            self.assertNotIn("Save draft JSON", labels)
            self.assertNotIn("Save complete delivery ZIP", labels)
            self.assertFalse(any(item.label in {"Fetch draft result · A5", "Fetch selected audio · A5",
                                               "Collect JSON and all levels' WAVs · A5"} for item in app.button))
            self.assertFalse(app.get("audio"))
            self.assertFalse(any("/audio/" in call.args[2] for call in send.call_args_list))
            count = send.call_count
            self.assertEqual(self.voice_callback(downloads)(), audio)
            self.assertEqual(send.call_count, count + 1)
            self.assertEqual(send.call_args.args[1:3], ("GET", f"/v1/jobs/{self.lesson}/audio/{second}"))
            self.assertTrue(all("external.invalid" not in str(call) for call in send.call_args_list))
            self.assertEqual(app.session_state["ingestion_workspace"]["trace"][-1]["api"], "A5")
            self.assertFalse(app.exception)

    def test_receipt_stays_explicit_and_does_not_automatically_reload_disposed_output(self):
        with patch("api_client.request", side_effect=self.response) as send:
            app = self.app()
            self.load_draft(app)
            state = app.session_state["ingestion_workspace"]
            count = send.call_count
            app.selectbox(key="iw_field_reason").set_value("RECEIVED")
            button(app, "Send job action · A6").click().run()
            self.assertEqual(send.call_count, count)
            app.checkbox(key="iw_field_saved").check()
            button(app, "Send job action · A6").click().run()
            self.assertEqual(send.call_args.kwargs["params"], {"reason": "RECEIVED"})
            self.assertNotIn("export", state["jobs"][self.lesson])
            self.assertNotIn("result", state["jobs"][self.lesson])
            app.run()
            self.assertEqual(send.call_count, count + 1)

    def test_failed_or_tampered_voice_cannot_be_downloaded(self):
        with patch("api_client.request", side_effect=self.response), \
                patch("ingestion_workspace.st.download_button", wraps=st.download_button) as downloads:
            app = self.app()
            self.load_draft(app)
            self.audio_status = 503
            with self.assertRaisesRegex(ValueError, "could not be downloaded"):
                self.voice_callback(downloads)()
            self.audio_status = 200
            self.audio = b"tampered-voice"
            with self.assertRaisesRegex(ValueError, "checksum differs"):
                self.voice_callback(downloads)()
            self.audio_status = 410
            with self.assertRaisesRegex(ValueError, "draft has expired"):
                self.voice_callback(downloads)()
            self.assertFalse(app.exception)

    def test_failed_draft_load_can_retry_without_creating_a_download_or_automatic_retry_loop(self):
        with patch("api_client.request", side_effect=self.response) as send:
            self.result_status = 503
            app = self.app()
            self.load_draft(app)
            self.assertFalse(any(item.label == "Download voice" for item in app.get("download_button")))
            count = send.call_count
            app.run()
            self.assertEqual(send.call_count, count)
            self.result_status = 200
            button(app, "Reload draft · A5").click().run()
            self.assertTrue(any(item.label == "Download voice" for item in app.get("download_button")))
            self.assertFalse(app.exception)

    def test_expired_result_cannot_expose_a_voice_download(self):
        with patch("api_client.request", side_effect=self.response):
            self.result_status = 410
            app = self.app()
            self.load_draft(app)
            job = app.session_state["ingestion_workspace"]["jobs"][self.lesson]
            self.assertNotIn("result", job)
            self.assertNotIn("audio", job)
            self.assertFalse(any(item.label == "Download voice" for item in app.get("download_button")))
            self.assertFalse(app.exception)

    def test_teacher_revision_creates_separate_tracked_job_once(self):
        with patch("api_client.request", side_effect=self.response) as send:
            app = self.app()
            self.load_draft(app)
            app.selectbox(key="iw_field_result_level").set_value("advanced").run()
            app.text_area(key="iw_field_feedback").set_value("Explain the assumption more clearly.")
            button(app, "Submit teacher revision · A7").click().run()
            self.assertFalse(app.exception)
            submitted = next(call for call in send.call_args_list if call.args[2] == "/v1/audio:regenerate")
            self.assertEqual(submitted.kwargs["json_body"]["chunk_id"], self.chunks[2])
            self.assertEqual(app.selectbox(key="iw_field_job").value, self.revision)
            self.assertIn(self.lesson, app.session_state["ingestion_workspace"]["jobs"])
            count = send.call_count
            app.run()
            self.assertEqual(send.call_count, count)

    def test_workspace_revision_progress_and_open_completed_version(self):
        with patch("api_client.request", side_effect=self.response) as send:
            app = self.app()
            self.load_draft(app)
            self.status_body.update(status="RUNNING", step="GENERATE", progress=0.25)
            app.text_area(key="iw_field_feedback").set_value("Clarify the proof.")
            button(app, "Submit teacher revision · A7").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("25.00%" in item.proto.text for item in app.get("progress")))
            self.status_body.update(status="DONE", step="DONE", progress=1)
            revised = copy.deepcopy(self.bundle["variants"]["beginner"])
            revised["job_id"] = self.revision
            revised["chunks"][0]["text"] = "The revised proof is now visible."
            self.result_overrides[self.revision] = revised
            self.catalog_rows = [{**{key: value for key, value in revised.items() if key != "chunks"},
                                  "chunk_count": 1},
                                 {**{key: value for key, value in self.bundle.items() if key != "variants"},
                                  "learner_level": "beginner"}]
            button(app, "Refresh revision status").click().run()
            self.assertTrue(any("Revision complete" in item.value for item in app.success))
            button(app, "Open revised lesson").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox(key="iw_field_download_course").value, self.revision)
            self.assertTrue(any("revised proof" in item.value for item in app.markdown))
            self.assertEqual(sum(call.args[1] == "POST" for call in send.call_args_list), 1)

    def test_a2_selection_a8_discovery_and_connection_reset(self):
        with patch("api_client.request", side_effect=self.response) as send:
            app = self.app()
            self.stage(app, 2)
            button(app, "Refresh source status").click().run()
            self.assertNotIn("params", send.call_args.kwargs)
            self.assertEqual(app.dataframe[0].value.iloc[0]["Progress"], "12.35%")
            app.selectbox(key="iw_field_source_document").set_value(self.document).run()
            self.assertEqual(app.text_input(key="iw_field_draft_document").value, self.document)
            self.assertEqual(app.text_input(key="iw_field_draft_course").value, "123456")
            self.assertTrue(app.text_input(key="iw_field_draft_course").disabled)
            self.stage(app, 3)
            button(app, "Refresh courses").click().run()
            self.assertNotIn("course_id", send.call_args.kwargs["params"])
            app.selectbox(key="iw_field_download_course").set_value(self.lesson).run()
            self.assertEqual(app.selectbox(key="iw_field_job").value, self.lesson)
            app.text_input(key="bearer_token").set_value("new-credentials").run()
            state = app.session_state["ingestion_workspace"]
            self.assertEqual(state["jobs"], {})
            self.assertEqual(state["documents"], {})
            self.assertEqual(state["trace"], [])
            self.assertFalse(app.exception)

    def test_course_level_chunk_download_flow_is_visible_without_importing_or_tracking_a_job(self):
        with patch("api_client.request", side_effect=self.response) as send, \
                patch("ingestion_workspace.st.download_button", wraps=st.download_button) as downloads:
            app = self.app()
            app.text_input(key="iw_field_course").set_value("unrelated-course").run()
            self.stage(app, 3)
            self.assertEqual(send.call_args.args[1:3], ("GET", "/v1/lesson-jobs"))
            self.assertEqual(send.call_args.kwargs["params"], {"limit": 100, "offset": 0})
            picker = app.selectbox(key="iw_field_download_course")
            self.assertEqual(picker.label, "Course")
            self.assertEqual(len(picker.options), 1)  # Three A8 levels collapse to one generation.
            self.assertIn("123456", picker.options[0])
            self.assertEqual(app.session_state["ingestion_workspace"]["jobs"], {})
            picker.set_value(self.lesson).run()
            self.assertEqual(app.selectbox(key="iw_field_result_level").label, "Level")
            app.selectbox(key="iw_field_result_level").set_value("intermediate").run()
            self.assertEqual(self.voice_callback(downloads)(), self.audio)
            self.assertEqual(send.call_args.args[1:3], ("GET", f"/v1/jobs/{self.lesson}/audio/{self.chunks[1]}"))
            self.assertEqual(len(app.get("download_button")), 2)  # Voice + collapsed request-sequence export.
            self.assertFalse(any(call.args[1] != "GET" for call in send.call_args_list))
            self.assertFalse(app.exception)

    def test_changing_course_resets_level_chunk_and_receipt_confirmation(self):
        other = str(uuid4())
        second = copy.deepcopy(self.bundle)
        second.update(job_id=other, course_id="987654", lesson_id="other-lesson")
        for variant in second["variants"].values():
            variant.update(job_id=other, course_id="987654", lesson_id="other-lesson")
            variant["chunks"][0]["chunk_id"] = str(uuid4())
        self.result_overrides[other] = second
        self.catalog_rows = [{key: value for key, value in body.items() if key != "variants"}
                             for body in (self.bundle, second)]
        with patch("api_client.request", side_effect=self.response) as send:
            app = self.app()
            self.stage(app, 3)
            app.selectbox(key="iw_field_download_course").set_value(self.lesson).run()
            app.selectbox(key="iw_field_result_level").set_value("advanced").run()
            app.checkbox(key="iw_field_saved").check().run()
            app.selectbox(key="iw_field_download_course").set_value(other).run()
            self.assertEqual(app.selectbox(key="iw_field_result_level").value, "beginner")
            self.assertEqual(app.selectbox(key="iw_field_chunk").value, 0)
            self.assertFalse(app.checkbox(key="iw_field_saved").value)
            self.assertEqual(app.selectbox(key="iw_field_job").value, other)
            self.assertEqual(send.call_args.args[1:3], ("GET", f"/v1/jobs/{other}/result"))
            self.assertFalse(app.exception)

    def test_course_catalog_error_does_not_retry_until_explicit_refresh(self):
        with patch("api_client.request", side_effect=self.response) as send:
            self.catalog_status = 503
            app = self.app()
            self.stage(app, 3)
            self.assertTrue(app.selectbox(key="iw_field_download_course").disabled)
            count = send.call_count
            app.run()
            self.assertEqual(send.call_count, count)
            self.catalog_status = 200
            button(app, "Refresh courses").click().run()
            self.assertFalse(app.selectbox(key="iw_field_download_course").disabled)
            self.assertFalse(app.exception)

    def test_course_catalog_pagination_deduplicates_generations_across_pages(self):
        row = {key: value for key, value in self.bundle.items() if key != "variants"}
        self.catalog_rows = [row, *[{**row, "job_id": str(uuid4())} for _ in range(99)]]
        with patch("api_client.request", side_effect=self.response) as send:
            app = self.app()
            self.stage(app, 3)
            self.assertEqual(len(app.selectbox(key="iw_field_download_course").options), 100)
            self.catalog_rows = [row, {**row, "job_id": str(uuid4()), "course_id": "777888"}]
            button(app, "Load more courses").click().run()
            self.assertEqual(send.call_args.kwargs["params"], {"limit": 100, "offset": 100})
            self.assertEqual(len(app.selectbox(key="iw_field_download_course").options), 101)
            self.assertFalse(any(item.label == "Load more courses" for item in app.button))
            self.assertFalse(app.exception)

    def test_context_changes_keep_history_but_prepare_fresh_submissions(self):
        with patch("api_client.request", side_effect=self.response):
            app = self.app()
            self.submit_signed(app)
            key = app.session_state["iw_field_a1_key"]
            self.stage(app, 0)
            app.text_input(key="iw_field_course").set_value("999999").run()
            self.assertNotEqual(app.session_state["iw_field_a1_key"], key)
            self.assertEqual(app.session_state["iw_field_document_ids"], "")
            self.assertIn(self.ingestion, app.session_state["ingestion_workspace"]["jobs"])
            self.assertFalse(app.exception)

    def test_a4_material_status_shows_extraction_quality(self):
        with patch("api_client.request", side_effect=self.response):
            app = self.app()
            self.submit_signed(app)
            self.status_body = {"job_id": self.ingestion, "document_id": self.document,
                                "status": "DONE", "step": "DONE", "progress": 1,
                                "result": {"page_count": 6, "chunk_count": 2,
                                           "quality_status": "NEEDS_REVIEW", "requires_review": True}}
            self.stage(app, 3)
            button(app, "Refresh selected job · A4").click().run()
            self.assertTrue(any(item.label == "Source pages" and item.value == "6" for item in app.metric))
            self.assertTrue(any("review flags" in item.value for item in app.warning))
            self.assertEqual(app.session_state["ingestion_workspace"]["documents"][self.document]["status"], "DONE")
            self.assertFalse(app.exception)

    def test_a2_delete_invalidates_sources_and_loaded_delivery(self):
        with patch("api_client.request", side_effect=self.response) as send:
            app = self.app()
            self.submit_signed(app)
            self.load_draft(app)
            self.stage(app, 1)
            button(app, "Remove material · A2").click().run()
            self.assertEqual(send.call_args.args[1:3], ("DELETE", "/v1/documents/" + self.document))
            state = app.session_state["ingestion_workspace"]
            self.assertNotIn(self.document, state["documents"])
            self.assertEqual(app.session_state["iw_field_document_ids"], "")
            self.assertNotIn("result", state["jobs"][self.lesson])
            self.assertTrue(any(metric.value == "204" for metric in app.metric))
            self.assertFalse(app.exception)

    def test_hostile_acceptance_location_is_not_tracked(self):
        accepted = api_client.Result("POST", "/v1/documents", 202, 1,
                                    {"job_id": self.ingestion, "document_id": self.document}, True,
                                    {"location": "https://external.invalid/v1/jobs/" + self.ingestion})
        with patch("api_client.request", return_value=accepted):
            app = self.app()
            self.submit_signed(app)
            self.assertEqual(app.session_state["ingestion_workspace"]["jobs"], {})
            self.assertTrue(any("invalid IDs or a status URL" in error.value for error in app.error))

    def test_a6_cleanup_pending_retries_same_action_explicitly(self):
        with patch("api_client.request", side_effect=self.response) as send:
            app = self.app()
            self.load_draft(app)
            send.side_effect = [api_client.Result("DELETE", "/v1/jobs/" + self.lesson, 503, 1,
                                                  {"detail": "JOB_CLEANUP_PENDING"}, True),
                                api_client.Result("DELETE", "/v1/jobs/" + self.lesson, 204, 1, "")]
            button(app, "Send job action · A6").click().run()
            self.assertTrue(any("cleanup is pending" in item.value for item in app.info))
            first = send.call_args
            app.run()
            self.assertEqual(send.call_args, first)
            button(app, "Send job action · A6").click().run()
            self.assertEqual(send.call_args.args, first.args)
            self.assertEqual(send.call_args.kwargs, first.kwargs)
            self.assertFalse(app.exception)

    def test_a2_has_only_list_and_remove_panels_and_loads_all_courses(self):
        with patch("api_client.request", side_effect=self.response) as send:
            app = self.app()
            self.assertNotIn("1 · Test context", app.radio(key="iw_field_stage").options)
            self.stage(app, 1)
            self.assertEqual([tab.label for tab in app.tabs], ["Show ingested material", "Remove material"])
            self.assertFalse(app.dataframe)
            button(app, "Show all ingested material").click().run()
            self.assertEqual(send.call_args.args[1:3], ("GET", "/v1/documents"))
            self.assertNotIn("params", send.call_args.kwargs)
            self.assertEqual(app.dataframe[0].value.iloc[0]["Progress"], "12.35%")
            self.assertFalse(app.exception)

    def test_draft_opens_with_live_table_v2_only_and_no_trace_or_order_controls(self):
        with patch("api_client.request", side_effect=self.response) as send:
            app = self.app()
            self.stage(app, 2)
            self.assertEqual(send.call_count, 1)
            self.assertTrue(app.dataframe)
            self.assertFalse(app.multiselect)
            self.assertFalse(app.text_area)
            self.assertEqual(app.selectbox(key="iw_field_generation").options, ["A3 v2 · All aligned levels"])
            self.assertFalse(any(item.label == "Orchestrator request sequence" for item in app.expander))
            self.assertTrue(button(app, "Generate lesson · A3").disabled)
            app.selectbox(key="iw_field_source_document").set_value(self.document).run()
            app.number_input(key="iw_field_maximum").set_value(650)
            app.run()
            self.assertEqual(send.call_count, 1)
            self.assertFalse(button(app, "Generate lesson · A3").disabled)
            self.assertFalse(app.exception)

    def test_material_selection_fills_ids_and_posts_one_v2_document(self):
        other = str(uuid4())
        documents = [{"document_id": self.document, "filename": "first.pdf", "course_id": "course-a",
                      "activity_id": "activity-a", "status": "DONE", "progress": 1},
                     {"document_id": other, "filename": "second.pdf", "course_id": "course-b",
                      "activity_id": "activity-b", "status": "DONE", "progress": 1}]
        original = self.response
        def send_response(connection, method, path, **kwargs):
            if method == "GET" and path == "/v1/documents":
                return api_client.Result(method, path, 200, 1, documents, True)
            return original(connection, method, path, **kwargs)
        with patch("api_client.request", side_effect=send_response) as send:
            app = self.app()
            self.stage(app, 2)
            app.selectbox(key="iw_field_source_document").set_value(self.document).run()
            first_key = app.session_state["iw_field_a3_key"]
            app.selectbox(key="iw_field_source_document").set_value(other).run()
            self.assertEqual(app.text_input(key="iw_field_draft_course").value, "course-b")
            self.assertEqual(app.text_input(key="iw_field_draft_activity").value, "activity-b")
            self.assertNotEqual(app.session_state["iw_field_a3_key"], first_key)
            app.number_input(key="iw_field_maximum").set_value(700)
            button(app, "Generate lesson · A3").click().run()
            self.assertEqual(send.call_args.args[1:3], ("POST", "/v2/lesson-jobs"))
            payload = send.call_args.kwargs["json_body"]
            self.assertEqual(payload["document_ids"], [other])
            self.assertEqual((payload["course_id"], payload["activity_id"]), ("course-b", "activity-b"))
            self.assertEqual(payload["options"]["max_chunk_words"], 700)
            self.assertNotIn("learner_level", payload["options"])
            retry_key = send.call_args.kwargs["headers"]["Idempotency-Key"]
            self.stage(app, 0)
            self.stage(app, 2)
            self.assertEqual(app.session_state["iw_field_a3_key"], retry_key)
            self.assertEqual(app.selectbox(key="iw_field_source_document").value, other)
            self.assertEqual(len([call for call in send.call_args_list if call.args[1] == "POST"]), 1)
            self.assertEqual(app.session_state["iw_field_course"], "123456")
            self.assertFalse(app.exception)

    def test_failed_material_refresh_preserves_same_submission_retry_key(self):
        with patch("api_client.request", side_effect=self.response) as send:
            app = self.app()
            self.stage(app, 2)
            app.selectbox(key="iw_field_source_document").set_value(self.document).run()
            button(app, "Generate lesson · A3").click().run()
            retry_key = app.session_state["iw_field_a3_key"]
            send.return_value = api_client.Result("GET", "/v1/documents", 503, 1, {"detail": "STORAGE_UNAVAILABLE"}, True)
            send.side_effect = None
            button(app, "Refresh source status").click().run()
            self.assertTrue(button(app, "Generate lesson · A3").disabled)
            self.assertEqual(app.session_state["iw_field_a3_key"], retry_key)
            send.side_effect = self.response
            button(app, "Refresh source status").click().run()
            self.assertEqual(app.selectbox(key="iw_field_source_document").value, self.document)
            self.assertEqual(app.session_state["iw_field_a3_key"], retry_key)
            self.assertFalse(app.exception)


    def test_delete_course_is_explicit_requires_confirmation_and_clears_cached_views(self):
        with patch("api_client.request", side_effect=self.response) as send:
            app = self.app()
            self.stage(app, 1)
            button(app, "Show all ingested material").click().run()
            app.text_input(key="iw_field_delete_course").set_value("123456")
            button(app, "Delete entire course · A2").click().run()
            self.assertTrue(app.error)
            self.assertFalse(any(call.args[1] == "DELETE" for call in send.call_args_list))
            app.session_state["learning_state"] = {"private": "old answer"}
            app.session_state["b4_state"] = {"response": "old voice"}
            app.checkbox(key="iw_field_delete_course_confirm").check()
            button(app, "Delete entire course · A2").click().run()
            self.assertFalse(app.exception)
            deletion = next(call for call in send.call_args_list if call.args[1] == "DELETE")
            self.assertEqual(deletion.args[2], "/v1/courses/123456")
            state = app.session_state["ingestion_workspace"]
            self.assertFalse(state["documents"])
            self.assertFalse(state["catalog"])
            self.assertEqual(state["trace"][-1]["api"], "A2")
            self.assertNotIn("learning_state", app.session_state)
            self.assertNotIn("b4_state", app.session_state)

    def test_document_delete_clears_related_results_and_never_calls_course_delete(self):
        with patch("api_client.request", side_effect=self.response) as send:
            app = self.app()
            self.stage(app, 1)
            button(app, "Show all ingested material").click().run()
            button(app, "Remove material · A2").click().run()
            self.assertFalse(app.exception)
            deletes = [call for call in send.call_args_list if call.args[1] == "DELETE"]
            self.assertEqual(len(deletes), 1)
            self.assertEqual(deletes[0].args[2], "/v1/documents/" + self.document)
            self.assertFalse(app.session_state["ingestion_workspace"]["documents"])

    def test_pending_course_deletion_clears_text_voice_cache_without_claiming_success(self):
        def response(connection, method, path, **options):
            if method == "DELETE":
                return api_client.Result(method, path, 503, 1, {"detail": "JOB_CLEANUP_PENDING"}, True)
            return self.response(connection, method, path, **options)
        with patch("api_client.request", side_effect=response):
            app = self.app()
            self.stage(app, 1)
            button(app, "Show all ingested material").click().run()
            app.session_state["learning_state"] = {"active": "old text", "audio": b"old voice"}
            app.text_input(key="iw_field_delete_course").set_value("123456")
            app.checkbox(key="iw_field_delete_course_confirm").check()
            button(app, "Delete entire course · A2").click().run()
            self.assertFalse(app.exception)
            self.assertNotIn("learning_state", app.session_state)
            state = app.session_state["ingestion_workspace"]
            self.assertTrue(state["documents"])
            self.assertEqual(state["delete_course_response"].status, 503)


if __name__ == "__main__":
    unittest.main()
