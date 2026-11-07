import fcntl
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import statement_parser
except ImportError:
    import importlib.util
    spec = importlib.util.spec_from_file_location('statement_parser', Path(__file__).resolve().parents[2] / 's.voovi.ru-api/basic/banking/parser.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['statement_parser'] = module
    spec.loader.exec_module(module)
import runtime

ACCOUNT = '40702810900000001234'
SECOND = '40702810900000005678'
DAY = date(2026, 10, 30)


def statement(account=ACCOUNT, day=DAY):
    return ('1CClientBankExchange\nВерсияФормата=1.03\nКодировка=Windows\n'
            f'ДатаНачала={day:%d.%m.%Y}\nДатаКонца={day:%d.%m.%Y}\nРасчСчет={account}\n'
            'СекцияРасчСчет\nКонецРасчСчет\nКонецФайла\n').encode('cp1251')


class FakeDownloader:
    calls = []
    fail_second = False
    invalid = False

    def __init__(self, login, password, accounts, label, selenium_url, download_dir, **kwargs):
        self.accounts, self.directory = accounts, download_dir

    def run(self, day):
        for account in self.accounts:
            self.calls.append(account)
            if self.fail_second and account == SECOND:
                raise RuntimeError('Bank unavailable')
            path = self.directory / 'download.txt'
            path.write_bytes(b'partial' if self.invalid else statement(account, day))
            self.on_download(account, day, [path])


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.env = patch.dict(os.environ, {'BANK_DOWNLOAD_ROOT': str(self.root), 'BANK_MI_LOGIN': 'fixture',
                              'BANK_MI_PASSWORD': 'fixture', 'BANK_MI_ACCOUNTS': ACCOUNT + ',' + SECOND})
        self.env.start()
        self.addCleanup(self.env.stop)
        FakeDownloader.calls, FakeDownloader.fail_second, FakeDownloader.invalid = [], False, False
        self.browser = patch('browser.BankStatementDownloader', FakeDownloader)
        self.browser.start()
        self.addCleanup(self.browser.stop)

    def test_empty_statement_is_valid_but_account_and_period_must_match(self):
        self.assertEqual(runtime.validate(statement(), ACCOUNT, DAY), 0)
        for account, day in [(SECOND, DAY), (ACCOUNT, date(2026, 10, 29))]:
            with self.assertRaises(ValueError):
                runtime.validate(statement(), account, day)

    def test_partial_file_never_published(self):
        FakeDownloader.invalid = True
        with self.assertRaises(ValueError):
            runtime.export('mi', DAY)
        self.assertEqual(list((self.root / 'ready').iterdir()), [])
        self.assertEqual(list((self.root / 'state').iterdir()), [])

    def test_success_is_cached_and_closed_files_are_archived(self):
        result = runtime.export('mi', DAY)
        self.assertEqual(len(result['accounts']), 2)
        self.assertEqual(len(list((self.root / 'ready').glob('*.txt'))), 2)
        self.assertEqual(len(list((self.root / 'archive').glob('*.txt'))), 2)
        self.assertTrue(runtime.export('mi', DAY)['cached'])
        self.assertEqual(FakeDownloader.calls, [ACCOUNT, SECOND])

    def test_missing_ready_is_restored_from_verified_archive_without_bank_login(self):
        result = runtime.export('mi', DAY)
        for item in result['accounts'].values():
            (self.root / 'ready' / item['file']).unlink()
        self.assertTrue(runtime.export('mi', DAY)['cached'])
        for item in result['accounts'].values():
            ready = self.root / 'ready' / item['file']
            self.assertEqual(ready.read_bytes(), (self.root / 'archive' / item['file']).read_bytes())
        self.assertEqual(FakeDownloader.calls, [ACCOUNT, SECOND])

    def test_corrupt_ready_is_repaired_and_missing_archive_can_be_rebuilt(self):
        result = runtime.export('mi', DAY)
        first = result['accounts'][ACCOUNT]['file']
        second = result['accounts'][SECOND]['file']
        (self.root / 'ready' / first).write_bytes(b'corrupt')
        (self.root / 'archive' / second).unlink()
        self.assertTrue(runtime.export('mi', DAY)['cached'])
        self.assertEqual((self.root / 'ready' / first).read_bytes(), statement())
        self.assertEqual((self.root / 'archive' / second).read_bytes(), statement(SECOND))

    def test_checkpoint_without_any_verified_original_fails_and_force_can_recover(self):
        result = runtime.export('mi', DAY)
        item = result['accounts'][ACCOUNT]
        (self.root / 'ready' / item['file']).unlink()
        (self.root / 'archive' / item['file']).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'no verified statement'):
            runtime.export('mi', DAY)
        self.assertEqual(FakeDownloader.calls, [ACCOUNT, SECOND])
        self.assertFalse(runtime.export('mi', DAY, force=True)['cached'])

    def test_oversize_download_is_never_read_or_published(self):
        from unittest.mock import Mock
        huge = self.root / 'huge.txt'
        with huge.open('wb') as stream:
            stream.truncate(runtime.MAX_STATEMENT_BYTES + 1)
        with self.assertRaisesRegex(ValueError, '16 MiB'):
            runtime.read_statement(huge)
        with self.assertRaisesRegex(ValueError, '16 MiB'):
            runtime.validate(b'x' * (runtime.MAX_STATEMENT_BYTES + 1), ACCOUNT, DAY)
        class Oversize(FakeDownloader):
            def run(self, day):
                self.on_download(ACCOUNT, day, [huge])
        with patch('browser.BankStatementDownloader', Oversize), self.assertRaisesRegex(ValueError, '16 MiB'):
            runtime.export('mi', DAY)
        self.assertEqual(list((self.root / 'ready').iterdir()), [])
        self.assertEqual(list((self.root / 'state').iterdir()), [])

    def test_partial_success_only_retries_failed_account(self):
        FakeDownloader.fail_second = True
        with self.assertRaises(RuntimeError):
            runtime.export('mi', DAY)
        FakeDownloader.fail_second = False
        runtime.export('mi', DAY)
        self.assertEqual(FakeDownloader.calls, [ACCOUNT, SECOND, SECOND])

    def test_exclusive_browser_lock(self):
        with (self.root / '.download.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, 'Another bank export'):
                runtime.export('mi', DAY)
        self.assertEqual(FakeDownloader.calls, [])

    def test_explicit_force_reexports(self):
        runtime.export('mi', DAY)
        runtime.export('mi', DAY, force=True)
        self.assertEqual(FakeDownloader.calls, [ACCOUNT, SECOND, ACCOUNT, SECOND])
        self.assertEqual(len(list((self.root / 'ready').glob('*.txt'))), 2)

    def test_failed_force_cannot_become_cached_success_on_normal_retry(self):
        runtime.export('mi', DAY)
        FakeDownloader.fail_second = True
        with self.assertRaises(RuntimeError):
            runtime.export('mi', DAY, force=True)
        state = json.loads((self.root / 'state' / f'mi-{DAY}.json').read_text())
        self.assertNotIn(SECOND, state['accounts'])
        FakeDownloader.fail_second = False
        result = runtime.export('mi', DAY)
        self.assertFalse(result['cached'])
        self.assertEqual(FakeDownloader.calls, [ACCOUNT, SECOND, ACCOUNT, SECOND, SECOND])


if __name__ == '__main__':
    unittest.main()
