"""Transports for calling Moodle's external functions.

Moodle exposes the same functions (`core_courseformat_get_state`, ...) through
two channels, which share the same `Transport` interface here:

* `AjaxSessionTransport`: `lib/ajax/service.php`, authenticated with the
  session cookie and the `sesskey`. Works even with web services disabled.
* `RestTransport`: `webservice/rest/server.php`, authenticated with a token.

A third transport, `BrowserTransport` (module `browser`), uses the same AJAX
endpoint but makes the calls from inside a real browser: it is needed when the
site sits behind anti-bot checks that block HTTP clients.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Protocol

import httpx

from moodlesh.core.errors import AuthenticationError, BackendError

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
_SESSKEY_RE = re.compile(r'"sesskey"\s*:\s*"([^"]+)"')
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
# Errors meaning "the session is not valid". Note that `requireloginerror` is *not*
# one of them: inside a function it means "this course is not accessible to you".
_AJAX_AUTH_ERRORS = frozenset({"invalidsesskey", "servicerequireslogin"})
_REST_AUTH_ERRORS = frozenset({"invalidtoken", "accessexception"})


class Transport(Protocol):
    """Call a Moodle external function and return its result."""

    def call(self, function: str, args: Mapping[str, Any]) -> Any: ...


class AjaxSessionTransport:
    """Transport based on the browser session (`MoodleSession` cookie)."""

    def __init__(
        self,
        base_url: str,
        cookies: httpx.Cookies,
        *,
        client: httpx.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)
        self._client.cookies = cookies
        self._sesskey: str | None = None

    @property
    def sesskey(self) -> str:
        """The session sesskey, read (once) from a Moodle page."""
        if self._sesskey is None:
            self._sesskey = self._fetch_sesskey()
        return self._sesskey

    def _fetch_sesskey(self) -> str:
        with _network_errors("sesskey"):
            response = self._client.get(f"{self._base_url}/my/")
        landed = f'{response.url} (HTTP {response.status_code}, title "{_page_title(response.text)}")'
        logger.debug("page for the sesskey: %s", landed)
        match = _SESSKEY_RE.search(response.text)
        if match is None:
            raise AuthenticationError(f"session not authenticated: /my/ leads to {landed}")
        if '"userId":0' in response.text.replace(" ", ""):
            raise AuthenticationError(f"guest session, not authenticated: /my/ leads to {landed}")
        return match.group(1)

    def call(self, function: str, args: Mapping[str, Any]) -> Any:
        with _network_errors(function):
            try:
                return self._call_once(function, args)
            except AuthenticationError as exc:
                if exc.errorcode != "invalidsesskey":
                    raise
                logger.debug("sesskey expired, reading it again and retrying")
                self._sesskey = None
                return self._call_once(function, args)

    def _call_once(self, function: str, args: Mapping[str, Any]) -> Any:
        logger.debug("AJAX %s %s", function, args)
        response = self._client.post(
            f"{self._base_url}/lib/ajax/service.php",
            params={"sesskey": self.sesskey, "info": function},
            json=ajax_request_body(function, args),
        )
        return decode_ajax_response(function, response.status_code, response.text)


def ajax_request_body(function: str, args: Mapping[str, Any]) -> list[dict[str, Any]]:
    """JSON body of a call to `lib/ajax/service.php`."""
    return [{"index": 0, "methodname": function, "args": dict(args)}]


def decode_ajax_response(function: str, status: int, text: str) -> Any:
    """Interpret a `lib/ajax/service.php` response (shared by all AJAX transports).

    Raises:
        NotMoodleResponseError: the response does not come from Moodle (HTML page, anti-bot check...).
        AuthenticationError: invalid session or sesskey.
        BackendError: error returned by the function.
    """
    try:
        payload = json.loads(text)
    except ValueError:
        raise NotMoodleResponseError(
            f'non-JSON response (HTTP {status}, title "{_page_title(text)}")', function=function
        ) from None
    # A global error (e.g. invalid sesskey) arrives as an object, not as a list.
    if isinstance(payload, dict):
        _raise_error(function, payload, _AJAX_AUTH_ERRORS)
    if status != httpx.codes.OK or not isinstance(payload, list) or not payload:
        raise BackendError(f"unexpected response (HTTP {status})", function=function)
    result = payload[0]
    if result.get("error"):
        _raise_error(function, result.get("exception") or {}, _AJAX_AUTH_ERRORS)
    return result.get("data")


class NotMoodleResponseError(AuthenticationError):
    """The response is not from Moodle: typically a login page or an anti-bot check."""


class RestTransport:
    """Transport based on the REST web services, with a token."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not token:
            raise ValueError("empty token")
        self._url = f"{base_url.rstrip('/')}/webservice/rest/server.php"
        self._token = token
        self._client = client or httpx.Client(timeout=timeout)

    def call(self, function: str, args: Mapping[str, Any]) -> Any:
        logger.debug("REST %s %s", function, args)
        # flatten_params always yields distinct keys (indices are part of the name).
        data = {
            "wstoken": self._token,
            "wsfunction": function,
            "moodlewsrestformat": "json",
            **dict(flatten_params(args)),
        }
        with _network_errors(function):
            response = self._client.post(self._url, data=data)
        if response.status_code == httpx.codes.FORBIDDEN:
            raise BackendError("web services or REST protocol disabled (HTTP 403)", function=function)
        if response.status_code != httpx.codes.OK:
            raise BackendError(f"HTTP {response.status_code}", function=function)
        payload = response.json()
        if isinstance(payload, dict) and "exception" in payload:
            _raise_error(function, payload, _REST_AUTH_ERRORS)
        return payload


def flatten_params(args: Mapping[str, Any], prefix: str = "") -> list[tuple[str, str]]:
    """Convert nested arguments into the form encoding used by Moodle REST.

    >>> flatten_params({"ids": [3, 4], "opt": {"a": True}})
    [('ids[0]', '3'), ('ids[1]', '4'), ('opt[a]', '1')]
    """
    flat: list[tuple[str, str]] = []
    for key, value in args.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        flat.extend(_flatten_value(name, value))
    return flat


def _flatten_value(name: str, value: Any) -> list[tuple[str, str]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return flatten_params(value, name)
    if isinstance(value, list | tuple):
        return [pair for i, item in enumerate(value) for pair in _flatten_value(f"{name}[{i}]", item)]
    if isinstance(value, bool):
        return [(name, "1" if value else "0")]
    return [(name, str(value))]


@contextmanager
def _network_errors(function: str) -> Iterator[None]:
    """Turn httpx network errors into `BackendError`."""
    try:
        yield
    except httpx.HTTPError as exc:
        raise BackendError(f"network error: {exc}", function=function) from exc


def _page_title(html: str) -> str:
    match = _TITLE_RE.search(html)
    return " ".join(match.group(1).split())[:80] if match else ""


def _raise_error(function: str, error: Mapping[str, Any], auth_codes: frozenset[str]) -> None:
    errorcode = error.get("errorcode")
    # Function errors carry "message"; global AJAX errors put the text in "error".
    candidates = (error.get("message"), error.get("error"), error.get("exception"))
    message = next((c for c in candidates if isinstance(c, str) and c), "unknown error")
    cls = AuthenticationError if errorcode in auth_codes else BackendError
    raise cls(str(message), function=function, errorcode=errorcode)
