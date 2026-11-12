"""Transport that calls Moodle from inside a real browser (Playwright).

It is needed when the site sits behind an anti-bot check (e.g. Cloudflare) that
blocks HTTP clients: requests are sent from the Moodle page open in Chromium,
exactly like the ones the user interface makes, and the checks (login, SSO,
security verification) are passed by the user, by hand, in the window.

The browser lives in a dedicated thread running its own asyncio loop with
nodriver's async API, so it never interferes with the asyncio loop of the
interactive shell. The rest of moodlesh sees a synchronous `call`: it submits
a coroutine to that loop and waits for its result.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import dataclasses
import json
import logging
import nodriver as uc
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from nodriver import cdp
from types import TracebackType
from typing import Any, Final, Generator, Literal, Protocol, Self, TypeAlias, TypedDict, final, Union

from nodriver.cdp.runtime import await_promise

from moodlesh.core.errors import AuthenticationError, BackendError
from moodlesh.moodle.auth import RawCookie, SessionStore, is_moodle_page
from moodlesh.moodle.transport import (
    NotMoodleResponseError,
    Transport,
    ajax_request_body,
    decode_ajax_response,
)

logger = logging.getLogger(__name__)

DEFAULT_READY_TIMEOUT_S: Final = 300
_POLL_INTERVAL_S: Final = 1.0
_RETRY_ERRORCODES: Final = frozenset({"invalidsesskey", "servicerequireslogin"})

Notify: TypeAlias = Callable[[str], None]
"""Shows a message to the user (e.g. "complete the login in the browser window")."""

WaitUntil: TypeAlias = Literal["commit", "domcontentloaded", "load", "networkidle"]


class _Tab(Protocol):
    """The part of a nodriver async `Tab` this module uses (the tests pass a fake one)."""
    async def get(self, url='chrome://welcome', new_tab: bool = False, new_window: bool = False) -> Self: ...

    async def wait(self, t: Union[int, float] = 0.5): ...

    async def send(self, cdp_obj: Generator[dict[str, Any], dict[str, Any], Any], _attach: bool = False, **kwargs) -> Any: ...


class _Context(Protocol):
    """The part of a Playwright async `BrowserContext` this module uses."""

    async def add_cookies(self, cookies: Sequence[Any]) -> None: ...

    async def cookies(self, urls: str | Sequence[str] | None = ...) -> Sequence[Mapping[str, object]]: ...


@final
class _MoodleState(TypedDict):
    """What `_JS_MOODLE_STATE` returns for a Moodle page."""

    sesskey: str
    userId: int | None


@final
class _Loading(TypedDict):
    """What `_JS_MOODLE_STATE` returns while the page is still loading."""

    loading: Literal[True]


# State of the current page: sesskey and user if it is a Moodle page, `{loading: true}`
# while it is still loading, null otherwise (e.g. a security check page).
_JS_MOODLE_STATE: Final = """(window.M && M.cfg && M.cfg.sesskey)
    ? {sesskey: M.cfg.sesskey, userId: (M.cfg.userId === undefined ? null : M.cfg.userId)}
    : (document.readyState === "complete" ? null : {loading: true})"""

# Not worth telling the user: it resolves by itself.
_HINT_LOADING: Final = "waiting for the page to load"


def _log_notify(message: str) -> None:
    logger.warning("%s", message)


class BrowserTransport(Transport):
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
            notify=notify or _log_notify,
            ready_timeout_s=ready_timeout_s,
            poll_interval_s=poll_interval_s,
            launch_options=dict(launch_options or {}),
        )
        # Set by the browser thread before `_ready` completes successfully.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session: _TabSession | None = None
        self._stop: asyncio.Event | None = None
        self._ready: concurrent.futures.Future[None] = concurrent.futures.Future()
        self._calls = threading.Lock()  # one call at a time: Moodle calls are sequential
        self._thread = threading.Thread(target=self._run, name="moodlesh-browser", daemon=True)
        self._thread.start()
        try:
            self._ready.result()
        except BaseException:
            self._thread.join(timeout=10)
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def call(self, function: str, args: Mapping[str, Any]) -> Any:
        loop, session = self._loop, self._session
        if loop is None or session is None or not self._thread.is_alive():
            raise BackendError("the browser is no longer available", function=function)
        with self._calls:
            future = asyncio.run_coroutine_threadsafe(session.call(function, dict(args)), loop)
            try:
                return future.result()
            except concurrent.futures.CancelledError:  # the loop stopped: the browser closed
                raise BackendError("the browser closed", function=function) from None

    def close(self) -> None:
        """Save the session and close the browser."""
        loop, stop = self._loop, self._stop
        if loop is not None and stop is not None and self._thread.is_alive():
            loop.call_soon_threadsafe(stop.set)
            self._thread.join(timeout=30)

    # ------------------------------------------------------------ browser thread
    def _run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        cfg = self._config
        try:
            browser = await uc.start(headless=False)
            try:
                session = _TabSession(await browser.get(), cfg)
                await session.ensure_ready()
                self._stop = asyncio.Event()
                self._session = session
                self._loop = asyncio.get_running_loop()
                self._ready.set_result(None)
                await self._stop.wait()
            finally:
                browser.stop()

            #async with async_playwright() as p:
            #    browser = await p.chromium.launch(headless=False, **cfg.launch_options)
            #    try:
            #        context = await browser.new_context()
            #        await _restore_cookies(context, cfg)
            #        session = _TabSession(await context.new_page(), cfg)
            #        await session.ensure_ready()
            #        await _save_cookies(context, cfg)
            #        self._stop = asyncio.Event()
            #        self._session = session
            #        self._loop = asyncio.get_running_loop()
            #        self._ready.set_result(None)
            #        await self._stop.wait()  # calls run on this loop meanwhile
            #        await _save_cookies(context, cfg)
            #    finally:
            #        await browser.close()
        except BaseException as exc:  # must be forwarded to the main thread
            if not self._ready.done():
                self._ready.set_exception(exc)
            else:
                logger.error("the browser closed: %s", exc)


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


class _TabSession:
    """A Moodle tab and its sesskey. Used only by the browser thread's loop."""

    def __init__(self, tab: _Tab, cfg: _Config) -> None:
        self._tab = tab
        self._cfg = cfg
        self._sesskey: str | None = None

    async def ensure_ready(self) -> str:
        """Wait until the tab shows an authenticated Moodle page and return its sesskey.

        Raises:
            AuthenticationError: the window was closed, or Moodle did not become
                ready in time.
        """
        cfg = self._cfg
        if not is_moodle_page(self._tab.url, cfg.base_url):
            await self._goto(f"{cfg.base_url}/my/")
        deadline: float = time.monotonic() + cfg.ready_timeout_s
        last_hint: str = ""
        while True:
            state, hint = await self._inspect()
            if state is not None:
                self._sesskey = state
                if last_hint and last_hint != _HINT_LOADING:
                    cfg.notify("ok, the Moodle page is ready")
                return state
            if hint != last_hint:
                logger.debug("waiting: %s (page %s)", hint, self._tab.url)
                if hint != _HINT_LOADING:
                    cfg.notify(hint)
                last_hint = hint
            if time.monotonic() > deadline:
                raise AuthenticationError(
                    f"Moodle not reachable within {cfg.ready_timeout_s:.0f} seconds: {hint}"
                )
            await self._wait(cfg.poll_interval_s)

    async def call(self, function: str, args: Mapping[str, Any]) -> Any:
        sesskey: str = self._sesskey or await self.ensure_ready()
        url: str = f"{self._cfg.base_url}/lib/ajax/service.php?sesskey={sesskey}&info={function}"
        body = ajax_request_body(function, args)
        logger.debug("AJAX (browser) %s %s", function, args)
        expression = """(async () => {{
            const response = await fetch({url}, {{
            method: "POST",
            credentials: "same-origin",
            headers: {{"Content-Type": "application/json"}},
            body: {body}
            }});
            return [response.status, await response.text()];
        }})()""".format(url=json.dumps(url), body=json.dumps(json.dumps(body)))

        # TODO(borsattoz): handle exceptions
        result, error = await self._tab.send(cdp.runtime.evaluate(expression=expression, await_promise=True, return_by_value=True))
        if error is not None:
            raise BackendError("browser error: {details}".format(details=error.exception.description if error.exception else error.text))

        status, text = result.value
        return decode_ajax_response(function, int(status), str(text))

    # ------------------------------------------------------------------- helpers
    async def _inspect(self) -> tuple[str | None, str]:
        """(sesskey if the page is authenticated Moodle, otherwise a hint for the user)."""
        url: str = self._tab.url
        if not is_moodle_page(url, self._cfg.base_url):
            return None, "complete the login in the browser window"

        result, error = await self._tab.send(cdp.runtime.evaluate(expression=_JS_MOODLE_STATE, await_promise=True, return_by_value=True))
        if error is not None:
            raise BackendError("browser error: {details}".format(details=error.exception.description if error.exception else error.text))

        state: _MoodleState | _Loading | None = result.value
        if state is None:
            return None, "pass the security check in the browser window, if asked"
        if "loading" in state:
            return None, _HINT_LOADING
        if state["userId"] == 0:
            await self._goto(f"{self._cfg.base_url}/login/index.php")
            return None, "complete the login in the browser window"
        return state["sesskey"], ""

    async def _goto(self, url: str) -> None:
        try:
            await self._tab.get(url)
        except self._cfg.page_error as exc:  # chained redirects (SSO) or interrupted navigation
            logger.debug("navigation to %s interrupted: %s", url, exc)

    async def _wait(self, seconds: float) -> None:
        await self._tab.wait(seconds)


async def _restore_cookies(context: _Context, cfg: _Config) -> None:
    saved: list[RawCookie] = cfg.store.load_raw(cfg.base_url) or []
    usable: list[RawCookie] = [c for c in saved if c.get("domain") and c.get("path")]
    if usable:
        try:
            await context.add_cookies(usable)
            logger.debug("restored %d saved cookies", len(usable))
        except cfg.page_error as exc:  # file from an older version: the user logs in again
            logger.debug("saved cookies not usable: %s", exc)


async def _save_cookies(context: _Context, cfg: _Config) -> None:
    try:
        cookies: list[RawCookie] = [dict(c) for c in await context.cookies(cfg.base_url)]
    except cfg.page_error as exc:
        logger.debug("cannot read the cookies from the browser: %s", exc)
        return
    path = cfg.store.save_raw(cfg.base_url, cookies)
    logger.debug("session saved to %s", path)

