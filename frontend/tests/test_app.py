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


def text_input(app, label):
    return next(item for item in app.text_input if item.label == label)


class AppTests(unittest.TestCase):
    def app(self):
        app = AppTest.from_file(str(FRONTEND / 'app.py'), default_timeout=10).run()
        self.assertFalse(app.exception)
        return app

    def test_navigation_health_and_clear_credentials(self):
        with patch('api_client.request', return_value=Result('GET', '/health', 200, 4,
                                                            {'status': 'alive'}, True)) as send:
            app = self.app()
            app.radio(key='page').set_value('Health').run()
            text_input(app, 'Bearer token').set_value('private-test-token').run()
            button(app, 'Check backend health').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[0].value, '200')
            send.assert_called_once()
            button(app, 'Clear session data').click().run()
            self.assertEqual(text_input(app, 'Bearer token').value, '')
            self.assertFalse(app.exception)

    def test_signed_submission_tracks_location_without_resubmission_on_rerun(self):
        job = str(uuid4())
        accepted = Result('POST', '/v1/documents', 202, 12, {'document_id': 'document'}, True,
                          {'location': '/v1/jobs/' + job})
        with patch('api_client.request', return_value=accepted) as send:
            app = self.app()
            next(r for r in app.radio if r.label == 'Document source').set_value('Signed Moodle URL').run()
            for label, value in [('course_id', '25'), ('activity_id', '310'), ('source_ref', '48127'),
                                 ('content_hash', 'Moodle-revision'), ('moodle_url', 'https://moodle.example/file')]:
                text_input(app, label).set_value(value)
            button(app, 'Send A1 request').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(send.call_args.kwargs['json_body']['content_hash'], 'Moodle-revision')
            original_key = send.call_args.kwargs['headers']['Idempotency-Key']
            app.run()
            self.assertEqual(send.call_count, 1)
            self.assertEqual(app.session_state['tracking_path'], '/v1/jobs/' + job)
            app.radio(key='page').set_value('Health').run()
            app.radio(key='page').set_value('Processing status').run()
            self.assertEqual(text_input(app, 'Status URL or job UUID').value, '/v1/jobs/' + job)
            app.radio(key='page').set_value('Ingest material').run()
            self.assertEqual(text_input(app, 'Idempotency-Key').value, original_key)

    def test_status_refresh_and_new_target_do_not_show_previous_job(self):
        with patch('api_client.request', return_value=Result('GET', '/v1/jobs/x', 200, 2,
                                                            {'status': 'DONE', 'step': 'DONE', 'progress': 1}, True)):
            app = self.app()
            app.radio(key='page').set_value('Processing status').run()
            text_input(app, 'Status URL or job UUID').set_value(str(uuid4())).run()
            button(app, 'Refresh status').click().run()
            self.assertFalse(app.exception)
            self.assertTrue(app.json)
            text_input(app, 'Status URL or job UUID').set_value(str(uuid4())).run()
            self.assertFalse(app.json)

    def test_a6_explicit_action_retry_and_no_resubmission_on_navigation(self):
        job = str(uuid4())
        responses = [Result('DELETE', '/v1/jobs/' + job, 503, 2,
                            {'detail': 'JOB_CLEANUP_PENDING'}, True),
                     Result('DELETE', '/v1/jobs/' + job, 204, 2, '')]
        with patch('api_client.request', side_effect=responses) as send:
            app = self.app()
            app.radio(key='page').set_value('Processing status').run()
            text_input(app, 'Status URL or job UUID').set_value(job).run()
            button(app, 'Send A6 request').click().run()
            self.assertEqual(send.call_args.args[1:3], ('DELETE', '/v1/jobs/' + job))
            self.assertEqual(send.call_args.kwargs['params'], {'reason': 'CANCEL'})
            self.assertTrue(any('Cancellation is recorded' in item.value for item in app.info))
            button(app, 'Send A6 request').click().run()
            self.assertEqual(app.metric[0].value, '204')
            app.run()
            app.radio(key='page').set_value('Health').run()
            app.radio(key='page').set_value('Processing status').run()
            self.assertEqual(send.call_count, 2)
            self.assertFalse(app.exception)

    def test_auth_failure_is_visible_and_explorer_rejects_invalid_json(self):
        with patch('api_client.request', return_value=Result('POST', '/v1/documents', 503, 1,
                                                            {'detail': 'INGRESS_NOT_CONFIGURED'}, True)) as send:
            app = self.app()
            app.radio(key='page').set_value('API explorer').run()
            app.selectbox[0].set_value('POST')
            text_input(app, 'Endpoint path').set_value('/v1/documents')
            button(app, 'Send request').click().run()
            self.assertEqual(app.metric[0].value, '503')
            self.assertTrue(any('OAuth' in item.value for item in app.info))
            send.reset_mock()
            app.text_area[0].set_value('not-json')
            button(app, 'Send request').click().run()
            send.assert_not_called()
            self.assertFalse(app.exception)

    def test_a2_list_delete_refresh_and_no_resubmission(self):
        document = str(uuid4())
        responses = [Result('GET', '/v1/documents', 200, 2,
                            [{'document_id': document, 'status': 'DONE', 'version': '2'}], True),
                     Result('DELETE', '/v1/documents/' + document, 204, 2, ''),
                     Result('GET', '/v1/documents', 200, 2, [], True)]
        with patch('api_client.request', side_effect=responses) as send:
            app = self.app()
            app.radio(key='page').set_value('Manage material').run()
            text_input(app, 'Course ID to list').set_value('25').run()
            button(app, 'List documents').click().run()
            self.assertEqual(send.call_args.kwargs['params'], {'course_id': '25'})
            self.assertTrue(app.dataframe)
            text_input(app, 'Document ID to delete').set_value(document)
            button(app, 'Delete document').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(send.call_args_list[1].args[1:3], ('DELETE', '/v1/documents/' + document))
            self.assertEqual(app.session_state['delete_response'].status, 204)
            self.assertFalse(app.dataframe)
            app.run()
            self.assertEqual(send.call_count, 3)
            app.radio(key='page').set_value('Health').run()
            app.radio(key='page').set_value('Manage material').run()
            self.assertEqual(text_input(app, 'Course ID to list').value, '25')
            self.assertEqual(text_input(app, 'Document ID to delete').value, document)

    def test_a2_invalid_uuid_and_failed_delete_do_not_refresh_list(self):
        with patch('api_client.request', return_value=Result('DELETE', '/v1/documents/x', 503, 2,
                                                            {'detail': 'INGRESS_NOT_CONFIGURED'}, True)) as send:
            app = self.app()
            app.radio(key='page').set_value('Manage material').run()
            text_input(app, 'Document ID to delete').set_value('not-a-uuid')
            button(app, 'Delete document').click().run()
            send.assert_not_called()
            text_input(app, 'Document ID to delete').set_value(str(uuid4()))
            button(app, 'Delete document').click().run()
            self.assertEqual(send.call_count, 1)
            self.assertEqual(app.session_state['delete_response'].status, 503)
            self.assertFalse(app.exception)

    def test_backend_local_mode_is_visible_without_credentials(self):
        with patch('api_client.request', return_value=Result('GET', '/v1/documents', 200, 2, [], True,
                                                            {'x-upraise-auth-mode': 'local-test'})):
            app = self.app()
            app.radio(key='page').set_value('Manage material').run()
            text_input(app, 'Course ID to list').set_value('25').run()
            button(app, 'List documents').click().run()
            self.assertEqual(text_input(app, 'Bearer token').value, '')
            self.assertTrue(any('local testing' in item.value for item in app.caption))
            self.assertFalse(app.exception)


if __name__ == '__main__':
    unittest.main()
