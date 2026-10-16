from __future__ import annotations

import asyncio
import os

import scrapy
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .generic import GenericCatalogSpider


class LoginFailed(RuntimeError):
    """The login flow could not reach the catalog (bad creds, CSRF, drift)."""


class LoginCatalogSpider(GenericCatalogSpider):
    """Crawls catalogs behind a login form.

    Runs the login in a real browser (Playwright): fills the form, submits
    it, waits for the catalog, then reuses the browser session (cookies) for
    pagination — exactly what a logged-in human would do. Element selectors
    and extraction selectors are configurable, so one spider covers simple
    forms, CSRF-protected forms and multi-step (username -> password) flows:

      -a csrf_selector='meta[name=csrf-token]'   read the token from the page
      -a csrf_input='input[name=csrf_token]'     ...and fill this field with it
      -a next_selector='button[name=next]'       two-step: submit step 1 first
      -a submit_selector='button[name=signin]'   final submit button

    Credentials: -a username=... -a password=... (or SHOP_USER /
    SHOP_PASSWORD env vars). Demo:
        python -m pricetracker demo-shop --user demo --password demo --csrf
        python -m pricetracker crawl --spider logincatalog \\
            --start-url http://localhost:8000/login \\
            --username demo --password demo \\
            --csrf-selector 'meta[name=csrf-token]'
    """

    name = "logincatalog"
    default_start_url = "http://localhost:8000/login"

    def __init__(
        self,
        username=None,
        password=None,
        username_selector="input[name=username]",
        password_selector="input[name=password]",
        submit_selector="button[type=submit]",
        csrf_selector=None,
        csrf_input="input[name=csrf_token]",
        csrf_attribute="content",
        next_selector=None,
        error_selector=None,
        login_timeout=15,
        *args,
        **kwargs,
    ):
        kwargs["render"] = "1"  # login always needs a real browser
        super().__init__(*args, **kwargs)
        self.username = username or os.environ.get("SHOP_USER", "demo")
        self.password = password or os.environ.get("SHOP_PASSWORD", "demo")
        self.username_selector = username_selector
        self.password_selector = password_selector
        self.submit_selector = submit_selector
        self.csrf_selector = csrf_selector
        self.csrf_input = csrf_input
        self.csrf_attribute = csrf_attribute
        self.next_selector = next_selector
        self.error_selector = error_selector
        self.login_timeout_ms = int(float(login_timeout) * 1000)

    async def start(self):
        yield scrapy.Request(
            self.start_urls[0],
            callback=self.parse_login,
            errback=self.login_errback,
            meta={"playwright": True, "playwright_include_page": True},
        )

    async def login_errback(self, failure):
        page = failure.request.meta.get("playwright_page")
        if page is not None:
            await page.close()
        raise failure.value

    async def _read_csrf_token(self, page) -> str:
        try:
            token = await page.locator(self.csrf_selector).get_attribute(
                self.csrf_attribute, timeout=self.login_timeout_ms
            )
            if token and token.strip():
                return token.strip()
            token = await page.locator(self.csrf_selector).evaluate(
                "el => el.value || el.textContent"
            )
        except PlaywrightTimeoutError as exc:
            raise LoginFailed(f"CSRF selector {self.csrf_selector!r} not found") from exc
        if token and token.strip():
            return token.strip()
        raise LoginFailed(f"no CSRF token found via {self.csrf_selector!r}")

    async def _fill_csrf(self, page) -> None:
        if not self.csrf_selector:
            return
        token = await self._read_csrf_token(page)
        # Fill via JS: the CSRF field is usually hidden, so Playwright's
        # actionability checks would refuse page.fill.
        await page.locator(self.csrf_input).evaluate("(el, value) => el.value = value", token)

    async def _do_login(self, page) -> None:
        await page.fill(self.username_selector, self.username)
        if self.next_selector:
            # Multi-step flow: username first, then the password page. Each
            # step is a POST, so each step gets a fresh CSRF token.
            await self._fill_csrf(page)
            await page.click(self.next_selector)
            await page.wait_for_selector(self.password_selector, timeout=self.login_timeout_ms)
        await page.fill(self.password_selector, self.password)
        await self._fill_csrf(page)
        await page.click(self.submit_selector)

    async def _wait_for_result(self, page) -> None:
        tasks = {
            asyncio.create_task(
                page.wait_for_selector(self.wait_selector, timeout=self.login_timeout_ms)
            ): "catalog"
        }
        if self.error_selector:
            tasks[
                asyncio.create_task(
                    page.wait_for_selector(self.error_selector, timeout=self.login_timeout_ms)
                )
            ] = "error"
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            if task.exception() is None:
                if tasks[task] == "error":
                    raise LoginFailed("login rejected: error element is visible")
                return
        raise LoginFailed(f"login timed out waiting for {self.wait_selector!r}")

    async def parse_login(self, response):
        page = response.meta["playwright_page"]
        try:
            await self._do_login(page)
            await self._wait_for_result(page)
            html = await page.content()
            catalog = response.replace(
                body=html.encode(response.encoding or "utf-8"), url=page.url
            )
            for item in self.parse(catalog, page=1):
                yield item
        finally:
            await page.close()
