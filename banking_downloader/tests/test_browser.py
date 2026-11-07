import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import browser


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.downloader = browser.BankStatementDownloader('fixture', 'fixture', ['1234'], 'mi',
                                                        'http://internal', self.path, headless=True)
        self.downloader.browser = Mock()
        self.downloader.wait = Mock()
        self.downloader.wait.until.return_value = []

    def run_download(self, partial=False, growing=False, oversize=False):
        ticks = []
        target = self.path / 'export.txt'
        def click(xpath):
            if xpath == browser.EXPORT_CONFIRM_BUTTON:
                target.write_bytes(b'' if growing else b'1CClientBankExchange\r\n')
                if oversize:
                    with target.open('ab') as stream:
                        stream.truncate(browser.MAX_STATEMENT_BYTES + 1)
                if partial:
                    (self.path / 'export.txt.part').write_bytes(b'partial')
        def until(condition, **kwargs):
            for tick in range(12):
                ticks.append(tick)
                if growing and tick == 2:
                    target.write_bytes(b'1CClientBankExchange\r\n')
                result = condition(None)
                if result:
                    return result
            raise browser.TimeoutException()
        self.downloader._click = Mock(side_effect=click)
        waiter = Mock()
        waiter.until.side_effect = until
        with patch.object(browser, 'WebDriverWait', return_value=waiter), \
                patch.object(browser.time, 'monotonic', side_effect=lambda: float(len(ticks))):
            self.downloader._download_statement('1234', date(2026, 10, 30))
        return ticks

    def test_partial_download_never_calls_publisher(self):
        self.downloader.on_download = Mock()
        with self.assertRaises(browser.TimeoutException):
            self.run_download(partial=True)
        self.downloader.on_download.assert_not_called()

    def test_direct_write_must_get_header_and_stabilize_before_publish(self):
        self.downloader.on_download = Mock()
        ticks = self.run_download(growing=True)
        self.assertGreaterEqual(len(ticks), 6)
        self.downloader.on_download.assert_called_once()
        paths = self.downloader.on_download.call_args.args[2]
        self.assertTrue(paths[0].read_bytes().startswith(b'1CClientBankExchange'))

    def test_modern_firefox_headless_option(self):
        self.assertIn('-headless', self.downloader._build_options().arguments)

    def test_new_oversize_file_never_reaches_publisher(self):
        self.downloader.on_download = Mock()
        with self.assertRaisesRegex(ValueError, '16 MiB'):
            self.run_download(oversize=True)
        self.downloader.on_download.assert_not_called()

    def test_old_oversize_failure_does_not_block_another_account(self):
        with (self.path / 'previous-account-failure.txt').open('wb') as stream:
            stream.truncate(browser.MAX_STATEMENT_BYTES + 1)
        self.downloader.on_download = Mock()
        self.run_download()
        self.downloader.on_download.assert_called_once()

    def test_failed_login_closes_browser_and_has_bounded_attempts(self):
        self.downloader._login = Mock(side_effect=browser.TimeoutException())
        self.downloader._save_failure_screenshot = Mock()
        remote = Mock()
        with patch.object(browser.webdriver, 'Remote', return_value=remote) as create:
            with self.assertRaises(RuntimeError):
                self.downloader.run(date(2026, 10, 30))
        self.assertEqual(create.call_count, 2)
        self.assertEqual(remote.quit.call_count, 2)
