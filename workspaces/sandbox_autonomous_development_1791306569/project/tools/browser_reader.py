import atexit
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError, sync_playwright


class AuthenticatedBrowser:
    def __init__(self):
        self._playwright = None
        self._context = None

    def read_page(self, url: str, user_data_dir: Path) -> str:
        if self._context is None:
            self._playwright = sync_playwright().start()
            try:
                self._context = (
                    self._playwright.chromium.launch_persistent_context(
                        user_data_dir=str(user_data_dir),
                        channel="msedge",
                        headless=False,
                        ignore_default_args=["--no-sandbox"],
                    )
                )
            except Exception:
                self._playwright.stop()
                self._playwright = None
                raise

        page = (
            self._context.pages[0]
            if self._context.pages
            else self._context.new_page()
        )
        if page.url != url:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except PlaywrightError as exc:
                if "interrupted by another navigation" not in str(exc):
                    raise
                page.wait_for_load_state("domcontentloaded", timeout=30000)
        return page.locator("body").inner_text(timeout=15000)

    def close(self):
        cleanup_error = None
        if self._context is not None:
            try:
                self._context.close()
            except Exception as exc:
                if "Connection closed while reading from the driver" not in str(exc):
                    cleanup_error = exc
            finally:
                self._context = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception as exc:
                if (
                    cleanup_error is None
                    and "Connection closed while reading from the driver"
                    not in str(exc)
                ):
                    cleanup_error = exc
            finally:
                self._playwright = None
        if cleanup_error is not None:
            raise cleanup_error


_authenticated_browser = AuthenticatedBrowser()
atexit.register(_authenticated_browser.close)


def read_authenticated_page(url: str, user_data_dir: Path) -> str:
    """Read visible page text using IRIS's persistent, user-controlled browser."""
    user_data_dir.mkdir(parents=True, exist_ok=True)
    return _authenticated_browser.read_page(url, user_data_dir)
