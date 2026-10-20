from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from moodlesh.core.errors import AuthenticationError, BackendError
from moodlesh.moodle.transport import AjaxSessionTransport, RestTransport, flatten_params

BASE = "https://moodle.example.org"


class FakeAjaxServer:
    """Simulates /my/ (with the sesskey in the page) and lib/ajax/service.php."""

    def __init__(self) -> None:
        self.sesskeys = ["s1"]
        self.requests: list[httpx.Request] = []
        self.next_result: object = {"error": False, "data": "ok"}
        self.logged_in = True

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/my/":
            if not self.logged_in:
                return httpx.Response(303, headers={"Location": f"{BASE}/login/index.php"})
            return httpx.Response(200, text=f'<script>M.cfg = {{"sesskey":"{self.sesskeys[-1]}"}}</script>')
        if request.url.path == "/login/index.php":
            return httpx.Response(200, text="login")
        if request.url.params["sesskey"] != self.sesskeys[-1]:
            # Real Moodle 4.5 reports an invalid sesskey per request, inside the list.
            error = {"message": "Your session has most likely timed out.", "errorcode": "invalidsesskey"}
            return httpx.Response(200, json=[{"error": True, "exception": error}])
        result = self.next_result
        return httpx.Response(
            200, json=result if isinstance(result, dict) and "errorcode" in result else [result]
        )


@pytest.fixture
def server() -> FakeAjaxServer:
    return FakeAjaxServer()


def ajax(server: FakeAjaxServer) -> AjaxSessionTransport:
    client = httpx.Client(transport=httpx.MockTransport(server.handler), follow_redirects=True)
    return AjaxSessionTransport(BASE, httpx.Cookies({"MoodleSession": "abc"}), client=client)


def test_ajax_call_sends_method_and_sesskey(server: FakeAjaxServer) -> None:
    assert ajax(server).call("core_courseformat_get_state", {"courseid": 5}) == "ok"
    request = server.requests[-1]
    assert request.url.params["info"] == "core_courseformat_get_state"
    assert json.loads(request.content) == [
        {"index": 0, "methodname": "core_courseformat_get_state", "args": {"courseid": 5}}
    ]


def test_ajax_function_error_is_mapped(server: FakeAjaxServer) -> None:
    server.next_result = {"error": True, "exception": {"message": "No access", "errorcode": "nopermissions"}}
    with pytest.raises(BackendError) as info:
        ajax(server).call("f", {})
    assert info.value.errorcode == "nopermissions"
    assert not isinstance(info.value, AuthenticationError)


def test_ajax_refreshes_expired_sesskey_once(server: FakeAjaxServer) -> None:
    transport = ajax(server)
    transport.call("f", {})
    server.sesskeys.append("s2")  # Moodle regenerated the sesskey
    assert transport.call("f", {}) == "ok"


def test_ajax_expired_session_raises_authentication_error(server: FakeAjaxServer) -> None:
    server.logged_in = False
    with pytest.raises(AuthenticationError, match="login"):
        ajax(server).call("f", {})


def test_network_errors_become_backend_errors() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(fail))
    with pytest.raises(BackendError, match="network error"):
        AjaxSessionTransport(BASE, httpx.Cookies(), client=client).call("f", {})


def rest(handler: httpx.MockTransport) -> RestTransport:
    return RestTransport(BASE, "tok", client=httpx.Client(transport=handler))


def test_rest_call_encodes_nested_arguments() -> None:
    seen: dict[str, list[str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(parse_qs(request.content.decode()))
        return httpx.Response(200, json=[{"id": 3}])

    assert rest(httpx.MockTransport(handler)).call("core_course_get_courses", {"options": {"ids": [3]}}) == [
        {"id": 3}
    ]
    assert seen["wsfunction"] == ["core_course_get_courses"]
    assert seen["wstoken"] == ["tok"]
    assert seen["options[ids][0]"] == ["3"]


def test_rest_errors() -> None:
    def invalid_token(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"exception": "moodle_exception", "errorcode": "invalidtoken", "message": "Invalid token"},
        )

    with pytest.raises(AuthenticationError):
        rest(httpx.MockTransport(invalid_token)).call("f", {})

    with pytest.raises(BackendError, match="disabled"):
        rest(httpx.MockTransport(lambda _: httpx.Response(403))).call("f", {})


def test_flatten_params() -> None:
    assert flatten_params({"a": None, "b": False, "c": [{"x": 1}], "d": "t"}) == [
        ("b", "0"),
        ("c[0][x]", "1"),
        ("d", "t"),
    ]


def test_global_ajax_error_message_is_read_from_the_error_key(server: FakeAjaxServer) -> None:
    # Shape observed on Moodle 4.5 when calling a function that does not exist.
    server.next_result = {
        "error": "Can't find data record in database table external_functions.",
        "errorcode": "invalidrecord",
    }
    with pytest.raises(BackendError, match="external_functions") as info:
        ajax(server).call("f", {})
    assert info.value.errorcode == "invalidrecord"


def test_course_not_accessible_is_not_an_authentication_error(server: FakeAjaxServer) -> None:
    server.next_result = {
        "error": True,
        "exception": {"message": "Course or activity not accessible.", "errorcode": "requireloginerror"},
    }
    with pytest.raises(BackendError) as info:
        ajax(server).call("core_courseformat_get_state", {"courseid": 4})
    assert not isinstance(info.value, AuthenticationError)
