"""Transport that calls Moodle from inside a real browser (nodriver).

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
import json
import logging
import threading
import time
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass
from http.cookiejar import Cookie
from types import TracebackType
from typing import Any, Final, Literal, Protocol, Self, TypeAlias, TypedDict, final

import nodriver as uc
from nodriver import cdp

from moodlesh.core.errors import ConnectionLostError
from moodlesh.moodle.auth import SessionStore, is_moodle_page
from moodlesh.moodle.transport import (
    Transport,
    ajax_request_body,
    decode_ajax_response,
)

logger = logging.getLogger(__name__)

DEFAULT_READY_TIMEOUT_S: Final = 300
_POLL_INTERVAL_S: Final = 1.0
_RETRY_ERRORCODES: Final = frozenset({"invalidsesskey", "servicerequireslogin"})
_SAME_SITE = {
    "strict": cdp.network.CookieSameSite.STRICT,
    "lax": cdp.network.CookieSameSite.LAX,
    "none": cdp.network.CookieSameSite.NONE,
}

Notify: TypeAlias = Callable[[str], None]
"""Shows a message to the user (e.g. "complete the login in the browser window")."""

WaitUntil: TypeAlias = Literal["commit", "domcontentloaded", "load", "networkidle"]


class _Tab(Protocol):
    """The part of a nodriver async `Tab` this module uses (the tests pass a fake one)."""

    async def get(
        self, url: str = "chrome://welcome", new_tab: bool = False, new_window: bool = False
    ) -> Self: ...

    async def wait(self, t: int | float = 0.5) -> None: ...

    async def send(
        self, cdp_obj: Generator[dict[str, Any], dict[str, Any], Any], _attach: bool = False, **kwargs: Any
    ) -> Any: ...

    @property
    def url(self) -> str: ...


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
        if loop is None or session is None or session.is_closed() or not self._thread.is_alive():
            raise ConnectionLostError("the browser is no longer available", function=function)
        with self._calls:
            future = asyncio.run_coroutine_threadsafe(session.call(function, dict(args)), loop)
            try:
                return future.result()
            except concurrent.futures.CancelledError:  # the loop stopped: the browser closed
                raise ConnectionLostError("the browser closed", function=function) from None

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
                await self.restore_cookies(browser)
                tab = await browser.get()
                session = _TabSession(tab, cfg)

                async def on_destroyed(ev: cdp.target.TargetDestroyed) -> None:
                    await browser.update_targets()
                    if tab not in browser.tabs:
                        session.closed = True

                browser.add_handler(cdp.target.TargetDestroyed, on_destroyed)
                await browser.send(cdp.target.set_discover_targets(discover=True))
                await session.ensure_ready()
                await self.save_cookies(browser)
                self._stop = asyncio.Event()
                self._session = session
                self._loop = asyncio.get_running_loop()
                self._ready.set_result(None)
                await self._stop.wait()
                await self.save_cookies(browser)
            finally:
                browser.stop()
        except BaseException as exc:  # must be forwarded to the main thread
            if not self._ready.done():
                self._ready.set_exception(exc)
            else:
                logger.error("the browser closed: %s", exc)

    @staticmethod
    def _from_cdp_cookie(c: cdp.network.Cookie) -> Cookie:
        """Convert a Chrome cookie; session cookies (`expires == -1` in CDP) keep no expiry."""
        rest: dict[str, str] = {}
        if c.http_only:
            rest["HttpOnly"] = ""
        if c.same_site is not None:
            rest["SameSite"] = c.same_site.value
        expires = None if c.session or c.expires is None or c.expires < 0 else int(c.expires)
        session = expires is None
        return Cookie(
            version=0,
            name=c.name,
            value=c.value,
            port=None,
            port_specified=False,
            domain=c.domain,
            domain_specified=c.domain.startswith("."),
            domain_initial_dot=c.domain.startswith("."),
            path=c.path,
            path_specified=True,
            secure=c.secure,
            expires=expires,
            discard=session,
            comment=None,
            comment_url=None,
            rest=rest,
        )

    async def save_cookies(self, browser: uc.Browser) -> None:
        cookies = [c for c in await browser.cookies.get_all() if isinstance(c, cdp.network.Cookie)]
        self._config.store.save_cookies(self._config.base_url, [self._from_cdp_cookie(c) for c in cookies])

    def _to_cookie_param(self, c: Cookie) -> cdp.network.CookieParam:
        same_site = c.get_nonstandard_attr("SameSite")
        host = c.domain.lstrip(".")
        return cdp.network.CookieParam(
            name=c.name,
            value=c.value or "",
            domain=c.domain if c.domain.startswith(".") else None,
            url=None
            if c.domain.startswith(".")
            else f"{'https' if c.secure else 'http'}://{host}{c.path or '/'}",
            path=c.path or "/",
            secure=c.secure,
            http_only=c.has_nonstandard_attr("HttpOnly"),
            same_site=_SAME_SITE.get(same_site.lower()) if same_site else None,
            expires=cdp.network.TimeSinceEpoch(c.expires) if c.expires else None,
        )

    async def restore_cookies(self, browser: uc.Browser) -> None:
        await browser.cookies.set_all(
            [self._to_cookie_param(c) for c in self._config.store.load_cookies(self._config.base_url) or []]
        )


@dataclass(frozen=True, slots=True)
class _Config:
    base_url: str
    store: SessionStore
    notify: Notify
    ready_timeout_s: float
    poll_interval_s: float
    launch_options: dict[str, Any]
    # Errors raised by the page: only these are treated as
    # "the page is navigating or closed". Anything else is a bug and propagates.
    page_error: type[Exception] = Exception


class _TabSession:
    """A Moodle tab and its sesskey. Used only by the browser thread's loop."""

    def __init__(self, tab: _Tab, cfg: _Config) -> None:
        self._tab = tab
        self._cfg = cfg
        self._sesskey: str | None = None
        self.closed: bool = False

    def is_closed(self) -> bool:
        return self.closed

    async def ensure_ready(self) -> str:
        """Wait until the tab shows an authenticated Moodle page and return its sesskey.

        Raises:
            ConnectionLostError: the window was closed, or Moodle did not become
                ready in time.
        """
        cfg = self._cfg
        if not is_moodle_page(self._tab.url, cfg.base_url):
            await self._goto(f"{cfg.base_url}/my/")
        deadline: float = time.monotonic() + cfg.ready_timeout_s
        last_hint: str = ""
        while True:
            if self.is_closed():
                raise ConnectionLostError("the browser window was closed")
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
                raise ConnectionLostError(
                    f"Moodle not reachable within {cfg.ready_timeout_s:.0f} seconds: {hint}"
                )
            await self._wait(cfg.poll_interval_s)

    async def call(self, function: str, args: Mapping[str, Any]) -> Any:
        sesskey: str = self._sesskey or await self.ensure_ready()
        url: str = f"{self._cfg.base_url}/lib/ajax/service.php?sesskey={sesskey}&info={function}"
        body = ajax_request_body(function, args)
        logger.debug("AJAX (browser) %s %s", function, args)
        expression = f"""(async () => {{
            const response = await fetch({json.dumps(url)}, {{
            method: "POST",
            credentials: "same-origin",
            headers: {{"Content-Type": "application/json"}},
            body: {json.dumps(json.dumps(body))}
            }});
            return [response.status, await response.text()];
        }})()"""

        # TODO(borsattoz): handle exceptions
        result, error = await self._tab.send(
            cdp.runtime.evaluate(expression=expression, await_promise=True, return_by_value=True)
        )
        if error is not None:
            raise ConnectionLostError(
                f"browser error: {error.exception.description if error.exception else error.text}"
            )

        status, text = result.value
        return decode_ajax_response(function, int(status), str(text))

    # ------------------------------------------------------------------- helpers
    async def _inspect(self) -> tuple[str | None, str]:
        """(sesskey if the page is authenticated Moodle, otherwise a hint for the user)."""
        url: str = self._tab.url
        if not is_moodle_page(url, self._cfg.base_url):
            return None, "complete the login in the browser window"

        result, error = await self._tab.send(
            cdp.runtime.evaluate(expression=_JS_MOODLE_STATE, await_promise=True, return_by_value=True)
        )
        if error is not None:
            raise ConnectionLostError(
                f"browser error: {error.exception.description if error.exception else error.text}"
            )

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
