"""Read-only Centr-Invest statement export, migrated from the production downloader."""

import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional

from selenium import webdriver
from selenium.common.exceptions import ElementClickInterceptedException, StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from selenium.webdriver.remote.client_config import ClientConfig



BANK_URL = "https://dbo.centrinvest.ru/sbns-web/ru/html/login.html"
PERIOD_BUTTON = "//button[normalize-space(.)='за период']"
DATE_FROM_INPUT = "(//input[contains(@class, 'z-dateboxwrap-input')])[1]"
DATE_TO_INPUT = "(//input[contains(@class, 'z-dateboxwrap-input')])[2]"
GET_STATEMENT_BUTTON = "//button[normalize-space(.)='Получить выписку']"
EXPORT_BUTTON = "//button[starts-with(normalize-space(.), 'Экспорт (')]"
# В разных версиях ZK пункт меню бывает ссылкой или вложенным span. На сервере
# банка встречаются оба варианта, поэтому не привязываемся к одному HTML-тегу.
EXPORT_1C_BUTTON = (
    "//*[self::a or self::button or self::span]"
    "[contains(normalize-space(.), 'Экспорт') and "
    "contains(normalize-space(.), '1С')]"
)
REVALUATION_CHECKBOX = (
    "//*[self::label or self::span]"
    "[contains(normalize-space(.), 'Проводки по переоценке')]"
    "/..//input[@type='checkbox']"
)
EXPORT_CONFIRM_BUTTON = (
    "//div[contains(@class, 'z-window')]"
    "//button[normalize-space(.)='Экспорт']"
)


def _profile_environment_name(profile: str, suffix: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", profile).strip("_").upper()
    return f"BANK_{normalized}_{suffix}"


def _normalize_account(value: str) -> str:
    return "".join(character for character in str(value) if character.isdigit())


def _parse_accounts(values: Iterable[str]) -> List[str]:
    accounts = []
    for value in values:
        for part in value.split(","):
            account = _normalize_account(part)
            if account and account not in accounts:
                accounts.append(account)
    return accounts


class BankStatementDownloader:
    def __init__(
        self,
        login: str,
        password: str,
        accounts: List[str],
        label: str,
        selenium_url: str,
        download_dir: Path,
        headless: bool = False,
        wait_timeout: int = 60,
        login_timeout: int = 600,
        account_attempts: int = 2,
        download_stable_seconds: float = 3.0,
    ):
        self.login = login
        self.password = password
        self.accounts = accounts
        self.label = label
        self.selenium_url = selenium_url
        self.download_dir = download_dir
        self.headless = headless
        self.wait_timeout = wait_timeout
        self.login_timeout = login_timeout
        self.account_attempts = max(1, account_attempts)
        self.download_stable_seconds = max(1.0, download_stable_seconds)
        self.on_download = None
        self.browser = None
        self.wait = None
        self._plugin_confirmation_pending = False

    def _build_options(self) -> Options:
        options = Options()
        if self.headless:
            options.add_argument("-headless")
        # ZK-интерфейс банка иногда несколько минут загружает основной JS.
        # Не блокируем browser.get: готовность нужных элементов ждём сами.
        options.set_capability("pageLoadStrategy", "none")
        preferences = {
            "browser.download.useDownloadDir": True,
            "browser.download.folderList": 2,
            "browser.download.dir": str(self.download_dir),
            "browser.download.manager.alertOnEXEOpen": False,
            "browser.helperApps.neverAsk.saveToDisk": (
                "application/csv,application/download,application/octet-stream,"
                "application/zip,application/x-zip,application/x-zip-compressed,"
                "text/csv,text/plain"
            ),
            "browser.download.manager.showWhenStarting": False,
            "browser.download.manager.focusWhenStarting": False,
            "browser.download.manager.closeWhenDone": True,
            "browser.download.manager.showAlertOnComplete": False,
            "browser.helperApps.alwaysAsk.force": False,
            "pdfjs.disabled": True,
        }
        for name, value in preferences.items():
            options.set_preference(name, value)
        return options

    def _click(self, xpath: str):
        def click_when_ready(driver):
            if any(mask.is_displayed() for mask in driver.find_elements(By.ID, "loading-mask")):
                return False
            for element in driver.find_elements(By.XPATH, xpath):
                if not element.is_displayed() or not element.is_enabled():
                    continue
                try:
                    element.click()
                    return element
                except ElementClickInterceptedException:
                    return False
            return False

        return WebDriverWait(
            self.browser,
            self.wait_timeout,
            poll_frequency=0.5,
        ).until(click_when_ready)

    def _fill(self, xpath: str, value: str):
        self._wait_not_loading(self.wait_timeout)
        element = self.wait.until(EC.visibility_of_element_located((By.XPATH, xpath)))
        element.clear()
        element.send_keys(value)
        return element

    def _wait_not_loading(self, timeout: int):
        WebDriverWait(self.browser, timeout, poll_frequency=0.5).until(
            EC.invisibility_of_element_located((By.ID, "loading-mask"))
        )

    def _handle_signature_plugin_dialog(self, driver):
        """Dismiss only the missing-signature-plugin flow during login."""
        windows = [
            window for window in driver.find_elements(
                By.XPATH,
                "//div[contains(concat(' ', normalize-space(@class), ' '), ' z-window ')"
                " or @role='dialog']",
            )
            if window.is_displayed()
        ]
        warning = "Плагин для работы с электронными подписями не обнаружен"
        for window in windows:
            if warning not in window.text:
                continue
            for button in window.find_elements(
                By.XPATH, ".//button[normalize-space(.)='Отмена']"
            ):
                if button.is_displayed() and button.is_enabled():
                    button.click()
                    self._plugin_confirmation_pending = True
                    print("Окно плагина электронной подписи: нажата Отмена.", flush=True)
                    return True
            return True

        if self._plugin_confirmation_pending:
            # Some bank versions show an acknowledgement before the Yes
            # confirmation; other versions return directly to the dashboard.
            if len(windows) == 1:
                acknowledgement = (
                    "\u0420\u0430\u0431\u043e\u0442\u0430 \u0441 \u044d\u043b\u0435\u043a\u0442\u0440\u043e\u043d\u043d\u043e\u0439 \u043f\u043e\u0434\u043f\u0438\u0441\u044c\u044e \u0431\u0435\u0437 \u043f\u043b\u0430\u0433\u0438\u043d\u0430 \u043d\u0435\u0432\u043e\u0437\u043c\u043e\u0436\u043d\u0430"
                    in windows[0].text
                )
                label = "\u041e\u041a" if acknowledgement else "\u0414\u0430"
                xpath = ".//button[normalize-space(.)='%s']" % label
                for button in windows[0].find_elements(By.XPATH, xpath):
                    if button.is_displayed() and button.is_enabled():
                        button.click()
                        self._plugin_confirmation_pending = acknowledgement
                        print("Plugin dialog: clicked %s." % label, flush=True)
                        break
            # No modal: allow a ready dashboard to finish the login wait.
            # Keep the flag until then so a delayed Yes is still handled.
            return bool(windows)
        return False

    def _wait_first(self, xpaths: Iterable[str], timeout: int):
        def find_clickable(driver):
            if self._handle_signature_plugin_dialog(driver):
                return False
            for xpath in xpaths:
                for element in driver.find_elements(By.XPATH, xpath):
                    if element.is_displayed() and element.is_enabled():
                        self._plugin_confirmation_pending = False
                        return element
            return False

        element = WebDriverWait(
            self.browser,
            timeout,
            poll_frequency=1,
            ignored_exceptions=(StaleElementReferenceException, ElementClickInterceptedException),
        ).until(find_clickable)
        return element

    def _login(self):
        self._plugin_confirmation_pending = False
        self.browser.get(BANK_URL)
        self._fill("//*[@id='userName']", self.login)
        password_input = self._fill("//*[@id='password']", self.password)
        password_input.send_keys(Keys.ENTER)
        print(
            "Авторизация отправлена. Ожидаем загрузку интерфейса банка "
            f"(до {self.login_timeout} секунд)...",
            flush=True,
        )
        self._wait_first(
            (PERIOD_BUTTON,),
            timeout=self.login_timeout,
        )
        self._wait_not_loading(self.login_timeout)
        print("Интерфейс банка загружен.", flush=True)

    def _open_statement_page(self, account: Optional[str]):
        if account is None:
            self._click(PERIOD_BUTTON)
            return

        expected = _normalize_account(account)

        def click_period_button(driver):
            if any(mask.is_displayed() for mask in driver.find_elements(By.ID, "loading-mask")):
                return False
            for row in driver.find_elements(By.XPATH, "//tr"):
                if expected not in _normalize_account(row.text):
                    continue
                buttons = row.find_elements(By.XPATH, ".//button[normalize-space(.)='за период']")
                if buttons and buttons[0].is_displayed() and buttons[0].is_enabled():
                    try:
                        self.browser.execute_script(
                            "arguments[0].scrollIntoView({block: 'center'});",
                            buttons[0],
                        )
                        buttons[0].click()
                        return True
                    except ElementClickInterceptedException:
                        return False
            return False

        WebDriverWait(
            self.browser,
            self.wait_timeout,
            poll_frequency=0.5,
        ).until(click_period_button)

    def _prepare_statement(self, statement_date: date):
        value = statement_date.strftime("%d.%m.%Y")
        self._fill(DATE_FROM_INPUT, value)
        self._fill(DATE_TO_INPUT, value)
        self._click(GET_STATEMENT_BUTTON)
        self.wait.until(EC.element_to_be_clickable((By.XPATH, EXPORT_BUTTON)))

    def _download_statement(self, account: Optional[str], statement_date: date):
        existing_files = {
            path: (path.stat().st_mtime_ns, path.stat().st_size)
            for path in self.download_dir.glob("*")
            if path.is_file()
        } if self.download_dir.exists() else {}
        self._click(EXPORT_BUTTON)
        export_1c = self._click(EXPORT_1C_BUTTON)
        try:
            revaluation_checkboxes = self.wait.until(
                EC.presence_of_all_elements_located(
                    (By.XPATH, REVALUATION_CHECKBOX)
                )
            )
        except TimeoutException:
            # Иногда ZK принимает Selenium-click, но не вызывает обработчик
            # пункта меню. Повторный JS-click по ближайшему кликабельному
            # родителю корректно всплывает до обработчика ZK.
            clickable = self.browser.execute_script(
                "return arguments[0].closest('a, button, li') || arguments[0];",
                export_1c,
            )
            self.browser.execute_script("arguments[0].click();", clickable)
            revaluation_checkboxes = self.wait.until(
                EC.presence_of_all_elements_located(
                    (By.XPATH, REVALUATION_CHECKBOX)
                )
            )
        for checkbox in revaluation_checkboxes:
            if checkbox.is_selected():
                self.browser.execute_script("arguments[0].click();", checkbox)
        self._click(EXPORT_CONFIRM_BUTTON)

        stable_signature = None
        stable_since = None

        def has_1c_header(path: Path) -> bool:
            with path.open("rb") as statement_file:
                return statement_file.read(20).startswith(
                    b"1CClientBankExchange"
                )

        def download_finished():
            nonlocal stable_signature, stable_since
            if not self.download_dir.exists():
                return False
            changed_files = []
            for path in self.download_dir.glob("*"):
                if not path.is_file():
                    continue
                current_state = (path.stat().st_mtime_ns, path.stat().st_size)
                if existing_files.get(path) != current_state:
                    changed_files.append(path)
            partial = [path for path in changed_files if path.suffix in {".part", ".tmp"}]
            complete = [path for path in changed_files if path not in partial]
            if not complete or partial:
                stable_signature = None
                stable_since = None
                return False

            # На проде Firefox иногда сначала создаёт пустой итоговый .txt и
            # продолжает писать в него без файла .part. Не закрываем браузер,
            # пока размер не перестанет меняться и не появится заголовок 1С.
            try:
                signature = tuple(
                    sorted(
                        (
                            str(path),
                            path.stat().st_mtime_ns,
                            path.stat().st_size,
                        )
                        for path in complete
                    )
                )
                valid_files = all(
                    path.stat().st_size > 0
                    and has_1c_header(path)
                    for path in complete
                )
            except (FileNotFoundError, OSError):
                stable_signature = None
                stable_since = None
                return False

            if not valid_files:
                stable_signature = None
                stable_since = None
                return False

            now = time.monotonic()
            if signature != stable_signature:
                stable_signature = signature
                stable_since = now
                return False
            if now - stable_since < self.download_stable_seconds:
                return False
            return complete

        downloaded = WebDriverWait(
            self.browser,
            self.wait_timeout,
            poll_frequency=0.5,
        ).until(
            lambda _driver: download_finished(),
            message=(
                "Файл выписки не завершил загрузку или не содержит "
                "заголовок 1CClientBankExchange"
            ),
        )
        unique_downloads = []
        account_suffix = account[-4:] if account else "current"
        date_suffix = statement_date.strftime("%Y%m%d")
        for path in downloaded:
            destination = path.with_name(
                f"{path.stem}-{date_suffix}-{account_suffix}{path.suffix}"
            )
            counter = 1
            while destination.exists():
                destination = path.with_name(
                    f"{path.stem}-{date_suffix}-{account_suffix}-{counter}{path.suffix}"
                )
                counter += 1
            path.rename(destination)
            unique_downloads.append(destination)
        account_text = f" для счёта …{account[-4:]}" if account else ""
        download_details = []
        document_marker_cp1251 = "СекцияДокумент=".encode("cp1251")
        document_marker_utf8 = "СекцияДокумент=".encode("utf-8")
        for path in unique_downloads:
            contents = path.read_bytes()
            document_count = max(
                contents.count(document_marker_cp1251),
                contents.count(document_marker_utf8),
            )
            download_details.append(
                f"{len(contents)} байт, "
                f"документов: {document_count}"
            )
        if self.on_download:
            self.on_download(account, statement_date, unique_downloads)
        print(
            f"Выписка{account_text} скачана: "
            f"{', '.join(download_details)}",
            flush=True,
        )

    def _save_failure_screenshot(
        self,
        account: Optional[str] = None,
        attempt: Optional[int] = None,
    ):
        """Best-effort screenshot for diagnosing a changed bank UI."""
        if self.browser is None:
            return
        try:
            debug_dir = Path(os.getenv("BANK_DEBUG_DIR", "/files/debug"))
            debug_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            account_suffix = f"-{account[-4:]}" if account else ""
            attempt_suffix = f"-try{attempt}" if attempt else ""
            screenshot_path = (
                debug_dir
                / f"bank-error-{timestamp}{account_suffix}{attempt_suffix}.png"
            )
            self.browser.save_screenshot(str(screenshot_path))
            print(f"Диагностический снимок: {screenshot_path}")
            os.chmod(screenshot_path, 0o600)

        except Exception as screenshot_error:
            print(f"Не удалось сохранить диагностический снимок: {type(screenshot_error).__name__}")

    def run(self, statement_date: date):
        self.download_dir.mkdir(parents=True, exist_ok=True)
        errors = []
        target_accounts = self.accounts or [None]
        for account in target_accounts:
            account_text = f" для счёта …{account[-4:]}" if account else ""
            for attempt in range(1, self.account_attempts + 1):
                try:
                    # Экспорт 1С может завершить банковскую сессию. Для каждого
                    # счёта открываем отдельную сессию, поэтому профиль MI всегда
                    # обрабатывает оба счёта независимо.
                    self.browser = webdriver.Remote(
                        command_executor=self.selenium_url,
                        options=self._build_options(),
                        client_config=ClientConfig(remote_server_addr=self.selenium_url, timeout=90),
                    )
                    self.wait = WebDriverWait(self.browser, self.wait_timeout)
                    self._login()
                    self._open_statement_page(account)
                    self._prepare_statement(statement_date)
                    self._download_statement(account, statement_date)
                    break
                except Exception as error:
                    self._save_failure_screenshot(account, attempt)
                    print(
                        f"Ошибка{account_text}, попытка "
                        f"{attempt}/{self.account_attempts}: "
                        f"{type(error).__name__}",
                        flush=True,
                    )
                    if attempt == self.account_attempts:
                        errors.append((account, error))
                    else:
                        print(
                            f"Повторяем скачивание{account_text} "
                            "в новой банковской сессии...",
                            flush=True,
                        )
                finally:
                    if self.browser is not None:
                        try:
                            self.browser.quit()
                        except Exception as error:
                            print(f"Не удалось корректно закрыть браузер: {type(error).__name__}")
                    self.browser = None

        if errors:
            failed_accounts = ", ".join(
                f"…{account[-4:]}" if account else "текущий счёт"
                for account, _error in errors
            )
            raise RuntimeError(
                f"Не удалось скачать счета: {failed_accounts}"
            ) from errors[0][1]

