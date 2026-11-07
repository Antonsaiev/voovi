import fcntl
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import control


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.sessions = set()
        self.deleted = []
        self.lock_held_during_delete = []
        self.registered = threading.Event()
        self.cooldown = 0
        test = self
        class Grid(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                ready = not bool(test.sessions) and test.cooldown == 0
                test.cooldown = max(test.cooldown - 1, 0)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({'value': {'ready': ready, 'nodes': [{'slots': [
                    {'session': {'sessionId': session}} for session in test.sessions]}]}}).encode())

            def do_POST(self):
                test.sessions.add('fixture-session')
                test.registered.set()
                self.send_response(200)
                self.end_headers()

            def do_DELETE(self):
                session = self.path.rsplit('/', 1)[-1]
                with (test.root / '.download.lock').open('a') as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        test.lock_held_during_delete.append(False)
                    except BlockingIOError:
                        test.lock_held_during_delete.append(True)
                test.sessions.discard(session)
                test.cooldown = 2
                test.deleted.append(session)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"value":null}')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Grid)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        env = patch.dict(os.environ, {'BANK_SELENIUM_URL': 'http://127.0.0.1:' + str(self.server.server_port)})
        env.start()
        self.addCleanup(env.stop)
        control.STOPPING.clear()
        self.addCleanup(control.STOPPING.clear)

    def hanging_child(self):
        return [sys.executable, '-c', """
import os, pathlib, time, urllib.request
pathlib.Path(%r).write_text(str(os.getpid()))
urllib.request.urlopen(urllib.request.Request(%r, data=b'fixture')).close()
time.sleep(60)
""" % (str(self.root / 'pid'), os.environ['BANK_SELENIUM_URL'] + '/register')]

    def test_real_timeout_reaps_child_and_closes_remote_session_before_unlocking(self):
        with self.assertRaises(control.JobTimedOut):
            control.run_job(self.hanging_child(), self.root, timeout=1)
        self.assertTrue(self.registered.is_set())
        self.assertEqual(self.sessions, set())
        self.assertEqual(self.deleted, ['fixture-session'])
        self.assertTrue(all(self.lock_held_during_delete))
        with self.assertRaises(ProcessLookupError):
            os.kill(int((self.root / 'pid').read_text()), 0)
        self.assertEqual(control.run_job([sys.executable, '-c', 'pass'], self.root, timeout=3), 0)

    def test_shutdown_cleans_running_job(self):
        def stop_when_started():
            if self.registered.wait(4):
                control.STOPPING.set()
        stopper = threading.Thread(target=stop_when_started)
        stopper.start()
        try:
            with self.assertRaises(control.JobStopping):
                control.run_job(self.hanging_child(), self.root, timeout=5)
        finally:
            stopper.join()
        self.assertEqual(self.sessions, set())
        self.assertEqual(self.deleted, ['fixture-session'])

    def test_child_inherits_the_same_lock_and_stale_session_is_recovered(self):
        self.sessions.add('orphan-session')
        code = 'from pathlib import Path;from control import browser_lock;\nwith browser_lock(Path(%r)):pass' % str(self.root)
        self.assertEqual(control.run_job([sys.executable, '-c', code], self.root, 3), 0)
        self.assertEqual(self.deleted, ['orphan-session'])
        self.assertTrue(all(self.lock_held_during_delete))

    def test_cleanup_failure_does_not_start_another_browser_job(self):
        with patch.object(control, 'cleanup_sessions', side_effect=control.BrowserCleanupError('unavailable')), \
                patch.object(control.subprocess, 'Popen') as child:
            with self.assertRaises(control.BrowserCleanupError):
                control.run_job(['fixture'], self.root, 1)
            child.assert_not_called()

    def test_real_service_sigterm_drains_request_and_closes_grid_session(self):
        command = self.hanging_child()
        script = '''
import sys,control,runtime
def fixture_job(command, root, timeout):
    return control.run_job(%r, root, timeout=15)
runtime.run_job=fixture_job
sys.argv=['runtime.py','--serve']
runtime.main()
''' % command
        service = subprocess.Popen([sys.executable, '-c', script],
            env=dict(os.environ, BANK_DOWNLOAD_TOKEN='fixture', BANK_DOWNLOAD_ROOT=str(self.root)),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=0.1).close()
                    break
                except (OSError, urllib.error.URLError):
                    time.sleep(0.05)
            else:
                self.fail('Test service did not start')
            statuses = []
            def request():
                req = urllib.request.Request('http://127.0.0.1:8080/export',
                    data=b'{"profile":"mi","date":"2026-10-30"}',
                    headers={'Authorization': 'Bearer fixture'})
                try:
                    statuses.append(urllib.request.urlopen(req, timeout=10).status)
                except urllib.error.HTTPError as error:
                    statuses.append(error.code)
            requester = threading.Thread(target=request)
            requester.start()
            self.assertTrue(self.registered.wait(5))
            service.terminate()
            self.assertEqual(service.wait(timeout=10), 0)
            requester.join(timeout=10)
            self.assertEqual(statuses, [503])
            self.assertEqual(self.sessions, set())
            self.assertEqual(self.deleted, ['fixture-session'])
            self.assertTrue(all(self.lock_held_during_delete))
        finally:
            if service.poll() is None:
                service.kill()
                service.wait()
