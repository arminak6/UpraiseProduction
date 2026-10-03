import json
import sys
import unittest
from pathlib import Path
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api_client import Connection, normalize_base_url, request, status_path


class ClientTests(unittest.TestCase):
    def test_multipart_preserves_pdf_and_metadata_and_does_not_follow_redirects(self):
        seen = []

        def handle(incoming):
            seen.append(incoming)
            body = incoming.read()
            self.assertIn(b'%PDF-source', body)
            self.assertIn(b'name="course_id"', body)
            self.assertIn(b'name="file"; filename="teaching.pdf"', body)
            self.assertEqual(incoming.headers['authorization'], 'Bearer test-token')
            self.assertEqual(incoming.headers['idempotency-key'], 'repeat-1')
            return httpx.Response(302, headers={'Location': 'https://other.example/'})

        result = request(Connection('https://gateway.example', token='Bearer test-token'),
                         'POST', '/v1/documents', fields={'course_id': '25', 'content_hash': 'declared'},
                         upload=('teaching.pdf', b'%PDF-source', 'text/plain'),
                         headers={'Idempotency-Key': 'repeat-1'}, transport=httpx.MockTransport(handle))
        self.assertEqual(result.status, 302)
        self.assertEqual(len(seen), 1)
        self.assertNotIn('authorization', result.headers)

    def test_signed_url_json_and_document_only_response(self):
        payload = {'course_id': '25', 'moodle_url': 'https://moodle.example/file?token=private',
                   'content_hash': 'opaque'}

        def handle(incoming):
            self.assertEqual(json.loads(incoming.read()), payload)
            return httpx.Response(202, json={'document_id': 'document'},
                                  headers={'Location': '/v1/jobs/' + str(uuid4())})

        result = request(Connection('http://backend:8000'), 'POST', '/v1/documents', json_body=payload,
                         transport=httpx.MockTransport(handle))
        self.assertTrue(result.successful and result.is_json)
        self.assertEqual(result.body, {'document_id': 'document'})
        self.assertIn('location', result.headers)

    def test_status_url_stays_on_configured_origin(self):
        job = str(uuid4())
        self.assertEqual(status_path('https://gateway.example/api/v1/jobs/' + job,
                                     'https://gateway.example/api'), '/v1/jobs/' + job)
        for value in ['https://other.example/v1/jobs/' + job, '//other.example/' + job, 'not-a-uuid']:
            with self.assertRaises(ValueError):
                status_path(value, 'https://gateway.example')

    def test_tls_material_is_hidden_and_partial_credentials_are_rejected(self):
        config = Connection('https://gateway.example', token='secret-token', certificate=b'private-cert')
        self.assertNotIn('secret-token', repr(config))
        self.assertNotIn('private-cert', repr(config))
        result = request(config, 'GET', '/health')
        self.assertIsNone(result.status)
        self.assertIn('both', result.error)

    def test_http_errors_and_non_json_are_preserved(self):
        result = request(Connection('http://backend:8000'), 'GET', '/health',
                         transport=httpx.MockTransport(lambda _: httpx.Response(502, text='Gateway unavailable')))
        self.assertEqual((result.status, result.body, result.is_json), (502, 'Gateway unavailable', False))

    def test_timeout_and_invalid_target_are_reported(self):
        def timeout(incoming):
            raise httpx.ReadTimeout('private transport details', request=incoming)

        result = request(Connection('http://backend:8000'), 'POST', '/v1/documents',
                         transport=httpx.MockTransport(timeout))
        self.assertIn('same Idempotency-Key', result.error)
        self.assertNotIn('private transport details', result.error)
        for url in ['file:///tmp/data', 'http://user:password@example.com', 'http://example.com?q=secret']:
            with self.assertRaises(ValueError):
                normalize_base_url(url)

    def test_a2_query_delete_no_content_and_delete_timeout(self):
        document = str(uuid4())
        def handle(incoming):
            self.assertEqual(incoming.headers['authorization'], 'Bearer test-token')
            if incoming.method == 'GET':
                self.assertEqual(incoming.url.params['course_id'], '25')
                return httpx.Response(200, json=[{'document_id': document, 'status': 'DONE', 'version': '2'}])
            self.assertEqual(incoming.url.path, '/v1/documents/' + document)
            self.assertEqual(incoming.read(), b'')
            return httpx.Response(204)
        config = Connection('http://backend:8000', token='test-token')
        result = request(config, 'GET', '/v1/documents', params={'course_id': '25'},
                         transport=httpx.MockTransport(handle))
        self.assertEqual(result.body[0]['version'], '2')
        deleted = request(config, 'DELETE', '/v1/documents/' + document,
                          transport=httpx.MockTransport(handle))
        self.assertEqual((deleted.status, deleted.body), (204, ''))
        def timeout(incoming):
            raise httpx.ReadTimeout('private details', request=incoming)
        failed = request(config, 'DELETE', '/v1/documents/' + document,
                         transport=httpx.MockTransport(timeout))
        self.assertIn('same document ID', failed.error)


if __name__ == '__main__':
    unittest.main()
