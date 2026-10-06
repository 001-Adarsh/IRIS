import unittest
from unittest.mock import Mock, patch

from playwright.sync_api import Error as PlaywrightError

from tools.browser_reader import AuthenticatedBrowser


class TestAuthenticatedBrowser(unittest.TestCase):
    def make_browser_with_page(self, page):
        browser = AuthenticatedBrowser()
        browser._context = Mock(pages=[page])
        return browser

    def test_launches_edge_without_no_sandbox_flag(self):
        page = Mock(url="https://www.linkedin.com/authwall")
        page.locator.return_value.inner_text.return_value = "Sign in"
        context = Mock()
        context.pages = [page]
        playwright = Mock()
        playwright.chromium.launch_persistent_context.return_value = context
        playwright_manager = Mock()
        playwright_manager.start.return_value = playwright
        user_data_dir = Mock()
        browser = AuthenticatedBrowser()

        with patch(
            "tools.browser_reader.sync_playwright",
            return_value=playwright_manager,
        ):
            browser.read_page(page.url, user_data_dir=user_data_dir)

        playwright.chromium.launch_persistent_context.assert_called_once_with(
            user_data_dir=str(user_data_dir),
            channel="msedge",
            headless=False,
            ignore_default_args=["--no-sandbox"],
        )
        browser.close()

    def test_reads_page_after_redirect_interrupts_navigation(self):
        page = Mock(url="about:blank")
        page.goto.side_effect = PlaywrightError(
            'Page.goto: Navigation to "https://www.linkedin.com/in/example/" '
            'is interrupted by another navigation to '
            '"https://www.linkedin.com/authwall"'
        )
        page.locator.return_value.inner_text.return_value = "Sign in to continue"
        browser = self.make_browser_with_page(page)

        result = browser.read_page(
            "https://www.linkedin.com/in/example/", user_data_dir=None
        )

        self.assertEqual(result, "Sign in to continue")
        page.wait_for_load_state.assert_called_once_with(
            "domcontentloaded", timeout=30000
        )

    def test_propagates_unrelated_navigation_errors(self):
        page = Mock(url="about:blank")
        page.goto.side_effect = PlaywrightError("Navigation timed out")
        browser = self.make_browser_with_page(page)

        with self.assertRaisesRegex(PlaywrightError, "Navigation timed out"):
            browser.read_page("https://example.com/", user_data_dir=None)

    def test_close_ignores_already_closed_driver_and_resets_state(self):
        context = Mock()
        context.close.side_effect = Exception(
            "Connection closed while reading from the driver"
        )
        playwright = Mock()
        browser = AuthenticatedBrowser()
        browser._context = context
        browser._playwright = playwright

        browser.close()

        context.close.assert_called_once_with()
        playwright.stop.assert_called_once_with()
        self.assertIsNone(browser._context)
        self.assertIsNone(browser._playwright)

    def test_close_propagates_unexpected_errors_but_stops_playwright(self):
        context = Mock()
        context.close.side_effect = RuntimeError("unexpected close failure")
        playwright = Mock()
        browser = AuthenticatedBrowser()
        browser._context = context
        browser._playwright = playwright

        with self.assertRaisesRegex(RuntimeError, "unexpected close failure"):
            browser.close()

        playwright.stop.assert_called_once_with()
        self.assertIsNone(browser._context)
        self.assertIsNone(browser._playwright)


if __name__ == "__main__":
    unittest.main()
