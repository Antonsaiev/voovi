"""Read-only bank export, durable per-account checkpoints and atomic publication."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from statement_parser import parse_statement, _decode

PROFILES = ('mi', 'ai')
JOB_TIMEOUT = 1800


def write_json(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x') as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def validate(raw, account, day):
    """Check both structural completeness and the requested account/date, including empty statements."""
    statement = parse_statement(raw)
    lines, _ = _decode(raw)
    header = []
    for line in lines:
        if line.startswith('СекцияДокумент='):
            break
        header.append(line)
    fields = {}
    for line in header:
        key, _, value = line.partition('=')
        fields.setdefault(key, []).append(value)
    if set(fields.get('РасчСчет', [])) != {account}:
        raise ValueError('Export account does not match requested account')
    value = day.strftime('%d.%m.%Y')
    if set(fields.get('ДатаНачала', [])) != {value} or set(fields.get('ДатаКонца', [])) != {value}:
        raise ValueError('Export dates do not match requested date')
    if statement.errors:
        raise ValueError('Export contains malformed payment records')
    return statement.record_count


def export(profile, day, force=False):
    from browser import BankStatementDownloader, _parse_accounts
    if profile not in PROFILES or day >= date.today():
        raise ValueError('Only known profiles and past dates are supported')
    root = Path(os.environ.get('BANK_DOWNLOAD_ROOT', '/bank'))
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o755)
    for name in ('ready', 'staging', 'archive', 'state', 'debug'):
        (root / name).mkdir(exist_ok=True, mode=0o700)
    os.chmod(root / 'staging', 0o711)
    # Browser writes to staging as seluser (uid 1200); importer can only see ready.
    with (root / '.download.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another bank export is running')
        for path in (root / 'debug').glob('bank-error-*.png'):
            if path.stat().st_mtime < time.time() - 7 * 86400:
                path.unlink()
        for path in (root / 'staging').iterdir():
            if path.stat().st_mtime < time.time() - 7 * 86400:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
        prefix = 'BANK_' + profile.upper() + '_'
        accounts = _parse_accounts([os.environ.get(prefix + 'ACCOUNTS', '')])
        if not accounts or any(not re.fullmatch(r'\d{20}', account) for account in accounts):
            raise ValueError('Configure explicit 20-digit accounts for the profile')
        login, password = os.environ.get(prefix + 'LOGIN'), os.environ.get(prefix + 'PASSWORD')
        if not login or not password:
            raise ValueError('Missing profile credentials')
        state_path = root / 'state' / f'{profile}-{day.isoformat()}.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else {'profile': profile, 'date': day.isoformat(), 'accounts': {}}
        if force:
            # A failed forced refresh must not make the next normal retry accept
            # the old checkpoint as a freshly successful bank export.
            for account in accounts:
                state['accounts'].pop(account, None)
            write_json(state_path, state)
        pending = [account for account in accounts if account not in state['accounts']]
        if not pending:
            return dict(state, status='success', cached=True)
        staging = root / 'staging' / uuid.uuid4().hex
        staging.mkdir(mode=0o777)
        os.chmod(staging, 0o777)
        downloader = BankStatementDownloader(
            login, password, pending, profile,
            os.environ.get('BANK_SELENIUM_URL', 'http://bank-selenium:4444'), staging,
            headless=True, login_timeout=int(os.environ.get('BANK_LOGIN_TIMEOUT', '180')),
            account_attempts=int(os.environ.get('BANK_ACCOUNT_ATTEMPTS', '2')),
            download_stable_seconds=float(os.environ.get('BANK_DOWNLOAD_STABLE_SECONDS', '3')))

        def publish(account, statement_date, paths):
            if len(paths) != 1:
                raise ValueError('Expected exactly one statement per account')
            raw = paths[0].read_bytes()
            count = validate(raw, account, statement_date)
            digest = hashlib.sha256(raw).hexdigest()
            name = f'{profile}-{day.isoformat()}-{account[-4:]}-{digest}.txt'
            # Keep immutable original, then publish a closed file on the same filesystem.
            for destination in (root / 'archive' / name, root / 'ready' / name):
                if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == digest:
                    continue
                temp = root / 'staging' / (uuid.uuid4().hex + '.tmp')
                with temp.open('xb') as stream:
                    os.chmod(temp, 0o600)
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, destination)
            state['accounts'][account] = {'file': name, 'sha256': digest, 'bytes': len(raw), 'documents': count}
            write_json(state_path, state)

        downloader.on_download = publish
        try:
            downloader.run(day)
        finally:
            # Staging is only removed after browser.quit; failed/interrupted jobs remain private for diagnosis.
            if all(account in state['accounts'] for account in pending):
                shutil.rmtree(staging)
        return dict(state, status='success', cached=False)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, result):
        raw = json.dumps(result).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.reply(200 if self.path == '/health' else 404, {'service': 'bank-downloader'})

    def do_POST(self):
        token = os.environ.get('BANK_DOWNLOAD_TOKEN', '')
        if self.path != '/export' or not token or not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
            return self.reply(403, {'error': 'Forbidden'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length < 1024:
                raise ValueError()
            payload = json.loads(self.rfile.read(length))
            profile, day = payload['profile'], date.fromisoformat(payload['date'])
            if profile not in PROFILES or day >= date.today():
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            return self.reply(400, {'error': 'Invalid profile/date'})
        result_path = Path(os.environ.get('BANK_DOWNLOAD_ROOT', '/bank')) / ('result-' + uuid.uuid4().hex + '.json')
        try:
            command = [sys.executable, __file__, '--profile', profile, '--date', day.isoformat(), '--result', str(result_path)]
            if payload.get('force') is True:
                command.append('--force')
            # A hard bound covers a hung WebDriver as well as Python-level waits.
            completed = subprocess.run(command, timeout=JOB_TIMEOUT)
            if completed.returncode or not result_path.exists():
                return self.reply(502, {'error': 'Bank export failed; inspect downloader logs and protected debug directory'})
            self.reply(200, json.loads(result_path.read_text()))
        except subprocess.TimeoutExpired:
            self.reply(504, {'error': 'Bank export exceeded 1800 seconds'})
        finally:
            result_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--profile', choices=PROFILES)
    parser.add_argument('--date', type=date.fromisoformat)
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--result', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.serve:
        if not os.environ.get('BANK_DOWNLOAD_TOKEN'):
            parser.error('BANK_DOWNLOAD_TOKEN is required')
        ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
    elif args.profile and args.date:
        try:
            result = export(args.profile, args.date, args.force)
        except Exception as error:
            print('Bank export failed:', type(error).__name__, flush=True)
            raise SystemExit(1)
        if args.result:
            write_json(args.result, result)
        print(json.dumps({'profile': args.profile, 'date': str(args.date), 'status': 'success',
                          'accounts': len(result['accounts']), 'cached': result['cached']}), flush=True)
    else:
        parser.error('--serve or --profile and --date are required')


if __name__ == '__main__':
    main()
