import json
import os
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest.mock import patch

from runtime import Handler, ThreadingHTTPServer


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, payload, token='fixture'):
        request = urllib.request.Request('http://127.0.0.1:{}/export'.format(self.server.server_port),
                                         data=json.dumps(payload).encode(),
                                         headers={'Authorization': 'Bearer ' + token})
        try:
            return urllib.request.urlopen(request, timeout=5).status
        except urllib.error.HTTPError as error:
            return error.code

    def test_unauthorized_request_never_runs_browser(self):
        with patch.dict(os.environ, {'BANK_DOWNLOAD_TOKEN': 'fixture'}), patch('runtime.run_job') as run:
            self.assertEqual(self.request({'profile': 'mi', 'date': '2026-10-30'}, token='wrong'), 403)
            run.assert_not_called()

    def test_invalid_payload_never_runs_browser(self):
        with patch.dict(os.environ, {'BANK_DOWNLOAD_TOKEN': 'fixture'}), patch('runtime.run_job') as run:
            for payload in ({'profile': 'unknown', 'date': '2026-10-30'}, [], {'profile': 'mi', 'date': None}):
                self.assertEqual(self.request(payload), 400)
            run.assert_not_called()

    def test_failed_child_returns_error(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {'BANK_DOWNLOAD_TOKEN': 'fixture', 'BANK_DOWNLOAD_ROOT': directory}), \
                patch('runtime.run_job', return_value=1):
            self.assertEqual(self.request({'profile': 'mi', 'date': '2026-10-30'}), 502)

    def test_hung_child_returns_timeout(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {'BANK_DOWNLOAD_TOKEN': 'fixture', 'BANK_DOWNLOAD_ROOT': directory}), \
                patch('runtime.run_job', side_effect=__import__('control').JobTimedOut('fixture')):
            self.assertEqual(self.request({'profile': 'mi', 'date': '2026-10-30'}), 504)
