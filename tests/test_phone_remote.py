import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from maagent.core.controller import WorkflowController
from maagent.server.app import create_server
from maagent.server.security import token_digest


class PhoneRemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config = {
            'adapters': {key: {'enabled': True} for key in ('maa', 'maaend', 'bgi')},
            'workflow': {'chain': {'sequential_tasks': [
                {'software': 'maaend', 'enabled': True},
                {'software': 'maa', 'enabled': False},
                {'software': 'bgi', 'enabled': True},
            ]}},
            'server': {'db_path': str(root / 'accounts.db'), 'report_file': str(root / 'reports.jsonl')},
        }
        self.httpd, self.ctx = create_server(self.config, host='127.0.0.1', port=0)
        account = self.ctx.store.create_local_account('test-user', 'not-a-real-password')
        self.ctx.store.create_token(account['id'], token_digest('test-token'), 1)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(2)
        self.ctx.store.close()

    def request(self, path, body=None, auth=True):
        conn = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=3)
        headers = {'Authorization': 'Bearer test-token'} if auth else {}
        conn.request('GET' if body is None else 'POST', path,
                     None if body is None else json.dumps(body), headers)
        response = conn.getresponse()
        payload = response.read()
        result = response.status, dict(response.getheaders()), payload
        conn.close()
        return result

    def test_independent_start_and_chain_order(self):
        received = []
        with patch.object(WorkflowController, '_worker', lambda _, tasks: received.append(tasks)):
            code, _, body = self.request('/api/v1/workflow/start', {})
            self.ctx.controller._thread.join(2)
            self.assertEqual(code, 200)
            self.assertEqual(received, [['maaend', 'bgi']])
            self.assertEqual(json.loads(body)['status']['software_list'], ['maaend', 'bgi'])
            # A running chain excludes a simultaneous single-task start.
            self.assertEqual(self.request('/api/v1/workflow/start', {'software': ['maa']})[0], 409)
            self.ctx.controller._running = False
            self.assertEqual(self.request('/api/v1/workflow/start', {'software': ['maa']})[0], 200)
            self.ctx.controller._thread.join(2)
            self.assertEqual(received[-1], ['maa'])

    def test_available_tasks_include_enabled_software_outside_chain(self):
        self.config['workflow']['chain']['sequential_tasks'] = [{'software': 'bgi'}]
        self.config['adapters']['maaend']['enabled'] = False
        code, headers, body = self.request('/api/v1/status')
        self.assertEqual(code, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        tasks = {item['software']: item for item in json.loads(body)['status']['software']}
        self.assertTrue(tasks['maa']['can_start'])
        self.assertFalse(tasks['maa']['enabled'])
        self.assertFalse(tasks['maaend']['can_start'])

    def test_empty_selection_does_not_start_chain(self):
        code, _, _ = self.request('/api/v1/workflow/start', {'software': []})
        self.assertEqual(code, 409)
        self.assertFalse(self.ctx.controller.is_running())

    def test_unauthenticated_start_is_rejected(self):
        self.assertEqual(self.request('/api/v1/workflow/start', {}, auth=False)[0], 401)
        self.assertFalse(self.ctx.controller.is_running())

    def test_static_shell_revalidates(self):
        for path in ('/', '/app.js', '/styles.css', '/sw.js'):
            code, headers, body = self.request(path, auth=False)
            self.assertEqual(code, 200)
            self.assertEqual(headers['Cache-Control'], 'no-cache')
            self.assertTrue(body)

    def test_slow_event_consumer_cannot_block_controller(self):
        # Fill the subscriber queue before the stream sends its first snapshot.
        original_subscribe = self.ctx.controller.subscribe

        def flood(callback):
            original_subscribe(callback)
            for _ in range(1000):
                callback({'type': 'log', 'data': {'message': 'test'}})

        with patch.object(self.ctx.controller, 'subscribe', flood):
            code, _, body = self.request('/api/v1/events?token=test-token', auth=False)
        self.assertEqual(code, 200)
        self.assertIn(b'"type": "snapshot"', body)
        self.assertEqual(self.ctx.controller._subscribers, [])


if __name__ == '__main__':
    unittest.main()
