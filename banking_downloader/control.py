"""Keep the browser lease in the supervisor until the remote session is closed."""
import fcntl
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

STOPPING = threading.Event()


class BrowserBusy(RuntimeError):
    pass


class BrowserCleanupError(RuntimeError):
    pass


class JobTimedOut(RuntimeError):
    pass


class JobStopping(RuntimeError):
    pass


@contextmanager
def browser_lock(root):
    path = root / '.download.lock'
    inherited = os.environ.get('BANK_BROWSER_LOCK_FD')
    if inherited is not None:
        fd = int(inherited)
        if (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != (path.stat().st_dev, path.stat().st_ino):
            raise RuntimeError('Invalid inherited browser lease')
        yield fd
        return
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BrowserBusy('Another bank export is running')
        yield lock.fileno()


def cleanup_sessions():
    """The dedicated Grid belongs exclusively to jobs holding browser_lock.

    Discovering sessions also covers a Remote constructor that timed out before
    returning its session ID to the client. No bank pages are opened here.
    """
    base = os.environ.get('BANK_SELENIUM_URL', 'http://bank-selenium:4444').rstrip('/')
    if base.endswith('/wd/hub'):
        base = base[:-7]
    deadline = time.monotonic() + 15
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise BrowserCleanupError('Selenium did not release its browser slot')
        return min(value, 5)
    try:
        while True:
            with urllib.request.urlopen(base + '/status', timeout=remaining()) as response:
                status = json.load(response)
            # Grid.ready is False while the single browser slot is occupied or
            # asynchronously being released; wait for actual availability.
            ids = {slot['session']['sessionId'] for node in status['value']['nodes']
                   for slot in node['slots'] if slot.get('session')}
            if not ids and status['value']['ready']:
                return
            for session_id in ids:
                if not re.fullmatch(r'[A-Za-z0-9-]+', session_id):
                    raise BrowserCleanupError('Invalid Selenium session ID')
                request = urllib.request.Request(base + '/session/' + session_id, method='DELETE')
                try:
                    with urllib.request.urlopen(request, timeout=remaining()) as response:
                        response.read(1024)
                except urllib.error.HTTPError as error:
                    if error.code != 404:
                        raise
            time.sleep(min(0.1, remaining()))
    except BrowserCleanupError:
        raise
    except Exception as error:
        raise BrowserCleanupError('Cannot confirm Selenium cleanup: ' + type(error).__name__) from None


def run_job(command, root, timeout):
    root.mkdir(parents=True, exist_ok=True)
    with browser_lock(root) as fd:
        if STOPPING.is_set():
            raise JobStopping('Downloader is stopping')
        cleanup_sessions()
        if STOPPING.is_set():
            raise JobStopping('Downloader is stopping')
        child = None
        try:
            child = subprocess.Popen(command, pass_fds=(fd,),
                                     env=dict(os.environ, BANK_BROWSER_LOCK_FD=str(fd)))
            deadline = time.monotonic() + timeout
            while True:
                if STOPPING.is_set():
                    raise JobStopping('Downloader is stopping')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise JobTimedOut('Bank export exceeded its time limit')
                try:
                    return child.wait(timeout=min(remaining, 0.25))
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            # The parent still owns the flock after child exit/SIGKILL. No next
            # job can enter the Grid until remote cleanup has finished.
            cleanup_sessions()
