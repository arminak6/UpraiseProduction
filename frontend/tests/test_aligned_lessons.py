"""V2 discovery, selected-level playback and session payloads through HTTP only."""

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api_client  # noqa: E402
import learning  # noqa: E402
from session_start import payload_for  # noqa: E402


class AlignedLessonTests(unittest.TestCase):
    def setUp(self):
        self.job = str(uuid4())
        self.items = [{'job_id': self.job, 'lesson_id': 'aligned', 'course_id': 'course', 'activity_id': 'activity',
            'language': 'en', 'voice': 'Joanna', 'learner_level': level, 'generation_version': 'a3-v2',
            'chunk_count': 2, 'expires_at': '2099-01-01T00:00:00Z'} for level in ('beginner', 'intermediate', 'advanced')]
        self.variants = {item['learner_level']: {**item, 'chunks': [{'chunk_id': str(uuid4()), 'index': index,
            'content_unit_id': str(uuid4()), 'text': f"{item['learner_level']} teaching part {index + 1}."}
            for index in range(2)]} for item in self.items}

    def test_catalog_keeps_all_levels_and_b1_payload_identifies_selection(self):
        connection = api_client.Connection('http://backend:8000')
        state = {'items': []}
        with patch('api_client.request', return_value=api_client.Result('GET', '/v1/lesson-jobs', 200, 1, self.items, True)):
            learning.refresh_courses(connection, state)
        self.assertEqual(len(state['items']), 3)
        self.assertEqual(len({learning.catalog_key(item) for item in state['items']}), 3)
        self.assertEqual(payload_for(self.items[1])['learner_level'], 'intermediate')

    def test_learning_selects_level_and_keeps_it_on_next_chunk(self):
        calls = []
        def send(connection, method, path, **kwargs):
            calls.append((method, path, kwargs))
            if path == '/v1/lesson-jobs':
                return api_client.Result(method, path, 200, 1, copy.deepcopy(self.items), True)
            if path.endswith('/result'):
                level = kwargs.get('params', {}).get('learner_level')
                body = self.variants[level] if level else {'job_id': self.job, 'variants': self.variants}
                return api_client.Result(method, path, 200, 1, copy.deepcopy(body), True)
            return api_client.Result(method, path, 200, 1, binary=b'RIFF-invented-audio')
        with patch('api_client.request', side_effect=send):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'))
            app.session_state['page'] = 'Learning'
            app.run()
            self.assertFalse(app.exception)
            app.selectbox(key='learning_selected').set_value(learning.catalog_key(self.items[2])).run()
            next(button for button in app.button if button.label == 'Start course').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['learning_state']['active']['learner_level'], 'advanced')
            next(button for button in app.button if button.label == 'Next').click().run()
            results = [options for _, path, options in calls if path.endswith('/result')]
            self.assertEqual([value['params']['learner_level'] for value in results], ['advanced', 'advanced'])
            self.assertTrue(any('advanced teaching part 2' in value.value for value in app.markdown))
