"""Transport that calls Moodle from inside a real browser (Playwright).

It is needed when the site sits behind an anti-bot check (e.g. Cloudflare) that
blocks HTTP clients: requests are sent from the Moodle page open in Chromium,
exactly like the ones the user interface makes, and the checks (login, SSO,
security verification) are passed by the user, by hand, in the window. No
check is bypassed.

Sync Playwright cannot coexist with the asyncio loop of the interactive shell,
so the browser lives in a dedicated thread: `call` queues the work and waits
for its result.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import queue
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any

from moodlesh.core.errors import AuthenticationError, BackendError
from moodlesh.moodle.auth import RawCookie, SessionStore, is_moodle_page
from moodlesh.moodle.transport import (
    NotMoodleResponseError,
    ajax_request_body,
    decode_ajax_response,
)

logger = logging.getLogger(__name__)

DEFAULT_READY_TIMEOUT_S = 300
_POLL_INTERVAL_S = 1.0
_RETRY_ERRORCODES = frozenset({"invalidsesskey", "servicerequireslogin"})

Notify = Callable[[str], None]

# State of the current page: sesskey and user if it is a Moodle page, `{loading: true}`
# while it is still loading, null otherwise (e.g. a security check page).
_JS_MOODLE_STATE = """() => (window.M && M.cfg && M.cfg.sesskey)
    ? {sesskey: M.cfg.sesskey, userId: (M.cfg.userId === undefined ? null : M.cfg.userId)}
    : (document.readyState === "complete" ? null : {loading: true})"""

# Not worth telling the user: it resolves by itself.
_HINT_LOADING = "waiting for the page to load"

_JS_FETCH = """async ({url, body}) => {
    const response = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {"Content-Type": "application/json"},
        body,
    });
    return [response.status, await response.text()];
}"""


class BrowserTransport:
    """`Transport` implementation that uses a Chromium window.

    On opening it makes sure the page is an authenticated Moodle page, waiting
    if needed for the user to log in or pass a security check. Cookies are saved
    to `store`, so next time there is no need to log in again while the session
    is still valid.

    Must be closed with `close()` (or used as a context manager).
    """

    def __init__(
        self,
        base_url: str,
        *,
        store: SessionStore | None = None,
        notify: Notify | None = None,
        ready_timeout_s: float = DEFAULT_READY_TIMEOUT_S,
        poll_interval_s: float = _POLL_INTERVAL_S,
        launch_options: Mapping[str, Any] | None = None,
    ) -> None:
        self._config = _Config(
            base_url=base_url.rstrip("/"),
            store=store or SessionStore(),
            notify=notify or (lambda message: logger.warning("%s", message)),
            ready_timeout_s=ready_timeout_s,
            poll_interval_s=poll_interval_s,
            launch_options=dict(launch_options or {}),
        )
        self._jobs: queue.Queue[_Job | None] = queue.Queue()
        self._ready: Future[None] = Future()
        self._thread = threading.Thread(target=self._run, name="moodlesh-browser", daemon=True)
        self._thread.start()
        try:
            self._ready.result()
        except BaseException:
            self._thread.join(timeout=10)
            raise

    def __enter__(self) -> BrowserTransport:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def call(self, function: str, args: Mapping[str, Any]) -> Any:
        if not self._thread.is_alive():
            raise BackendError("the browser is no longer available", function=function)
        job = _Job(function, dict(args))
        self._jobs.put(job)
        return job.result.result()

    def close(self) -> None:
        """Save the session and close the browser."""
        if self._thread.is_alive():
            self._jobs.put(None)
            self._thread.join(timeout=30)

    # ------------------------------------------------------------ browser thread
    def _run(self) -> None:
        try:
            from playwright.sync_api import Error as PlaywrightError  # noqa: PLC0415 - optional dependency
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
        except ImportError as exc:
            self._ready.set_exception(
                AuthenticationError(
                    "Playwright is not installed: `pip install playwright && playwright install chromium`"
                )
            )
            logger.debug("importing playwright failed: %s", exc)
            return

        cfg = dataclasses.replace(self._config, page_error=PlaywrightError)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False, **cfg.launch_options)
                try:
                    context = browser.new_context()
                    _restore_cookies(context, cfg)
                    session = _PageSession(context.new_page(), cfg)
                    session.ensure_ready()
                    _save_cookies(context, cfg)
                    self._ready.set_result(None)
                    self._serve(session)
                    _save_cookies(context, cfg)
                finally:
                    browser.close()
        except BaseException as exc:  # must be forwarded to the main thread
            if not self._ready.done():
                self._ready.set_exception(exc)
            else:
                logger.error("the browser closed: %s", exc)
        finally:
            self._fail_pending_jobs()

    def _serve(self, session: _PageSession) -> None:
        while (job := self._jobs.get()) is not None:
            try:
                job.result.set_result(session.call(job.function, job.args))
            except BaseException as exc:  # forwarded to the caller
                job.result.set_exception(exc)

    def _fail_pending_jobs(self) -> None:
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                return
            if job is not None and not job.result.done():
                job.result.set_exception(BackendError("the browser closed", function=job.function))


@dataclass(frozen=True, slots=True)
class _Config:
    base_url: str
    store: SessionStore
    notify: Notify
    ready_timeout_s: float
    poll_interval_s: float
    launch_options: dict[str, Any]
    # Errors raised by the page (Playwright's `Error`): only these are treated as
    # "the page is navigating or closed". Anything else is a bug and propagates.
    page_error: type[Exception] = Exception


@dataclass
class _Job:
    function: str
    args: dict[str, Any]

    def __post_init__(self) -> None:
        self.result: Future[Any] = Future()


class _PageSession:
    """A Moodle tab and its sesskey. Used only by the browser thread."""

    def __init__(self, page: Any, cfg: _Config) -> None:
        self._page = page
        self._cfg = cfg
        self._sesskey: str | None = None

    def ensure_ready(self) -> str:
        """Wait until the tab shows an authenticated Moodle page and return its sesskey."""
        cfg = self._cfg
        if not is_moodle_page(self._page.url, cfg.base_url):
            self._goto(f"{cfg.base_url}/my/")
        deadline = time.monotonic() + cfg.ready_timeout_s
        last_hint = ""
        while True:
            if self._page.is_closed():
                raise AuthenticationError("the browser window was closed")
            state, hint = self._inspect()
            if state is not None:
                self._sesskey = state
                if last_hint and last_hint != _HINT_LOADING:
                    cfg.notify("ok, the Moodle page is ready")
                return state
            if hint != last_hint:
                logger.debug("waiting: %s (page %s)", hint, self._page.url)
                if hint != _HINT_LOADING:
                    cfg.notify(hint)
                last_hint = hint
            if time.monotonic() > deadline:
                raise AuthenticationError(
                    f"Moodle not reachable within {cfg.ready_timeout_s:.0f} seconds: {hint}"
                )
            self._wait(cfg.poll_interval_s)

    def call(self, function: str, args: Mapping[str, Any]) -> Any:
        for attempt in (1, 2):
            sesskey = self._sesskey or self.ensure_ready()
            url = f"{self._cfg.base_url}/lib/ajax/service.php?sesskey={sesskey}&info={function}"
            logger.debug("AJAX (browser) %s %s", function, args)
            try:
                status, text = self._page.evaluate(
                    _JS_FETCH, {"url": url, "body": _json_dumps(ajax_request_body(function, args))}
                )
                return decode_ajax_response(function, int(status), str(text))
            except AuthenticationError as exc:
                retryable = isinstance(exc, NotMoodleResponseError) or exc.errorcode in _RETRY_ERRORCODES
                if attempt == 2 or not retryable:
                    raise
                logger.debug("invalid response (%s): reloading Moodle and retrying", exc)
            except BackendError:
                raise  # error returned by Moodle: never retry (it might be a write)
            except self._cfg.page_error as exc:  # page navigating or closed
                if attempt == 2 or self._page.is_closed():
                    raise BackendError(f"browser error: {exc}", function=function) from exc
                logger.debug("browser error (%s): retrying", exc)
            # Go back to /my/ before retrying: if a login or a security check is
            # needed, the user sees it in the window and ensure_ready waits for it.
            self._sesskey = None
            self._goto(f"{self._cfg.base_url}/my/")
        raise AssertionError("unreachable")  # pragma: no cover

    # ------------------------------------------------------------------- helpers
    def _inspect(self) -> tuple[str | None, str]:
        """(sesskey if the page is authenticated Moodle, otherwise a hint for the user)."""
        url = self._page.url
        if not is_moodle_page(url, self._cfg.base_url):
            return None, "complete the login in the browser window"
        try:
            state = self._page.evaluate(_JS_MOODLE_STATE)
        except self._cfg.page_error:  # page still navigating: try again later
            return None, _HINT_LOADING
        if state and state.get("loading"):
            return None, _HINT_LOADING
        if not state:
            return None, "pass the security check in the browser window, if asked"
        if state.get("userId") == 0:
            self._goto(f"{self._cfg.base_url}/login/index.php")
            return None, "complete the login in the browser window"
        return str(state["sesskey"]), ""

    def _goto(self, url: str) -> None:
        try:
            self._page.goto(url, wait_until="commit")
        except self._cfg.page_error as exc:  # chained redirects (SSO) or interrupted navigation
            logger.debug("navigation to %s interrupted: %s", url, exc)

    def _wait(self, seconds: float) -> None:
        try:
            self._page.wait_for_timeout(seconds * 1000)
        except self._cfg.page_error:  # page closed while waiting: the next round detects it
            time.sleep(seconds)


def _restore_cookies(context: Any, cfg: _Config) -> None:
    saved = cfg.store.load_raw(cfg.base_url) or []
    usable = [c for c in saved if c.get("domain") and c.get("path")]
    if usable:
        try:
            context.add_cookies(usable)
            logger.debug("restored %d saved cookies", len(usable))
        except cfg.page_error as exc:  # file from an older version: the user logs in again
            logger.debug("saved cookies not usable: %s", exc)


def _save_cookies(context: Any, cfg: _Config) -> None:
    try:
        cookies: list[RawCookie] = [dict(c) for c in context.cookies(cfg.base_url)]
    except cfg.page_error as exc:
        logger.debug("cannot read the cookies from the browser: %s", exc)
        return
    path = cfg.store.save_raw(cfg.base_url, cookies)
    logger.debug("session saved to %s", path)


def _json_dumps(value: Any) -> str:
    return json.dumps(value)
