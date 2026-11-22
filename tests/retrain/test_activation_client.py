"""Cliente de activación contra un servidor HTTP local: token de servicio, reintentos acotados
solo para errores transitorios y propagación del request_id."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from services.retrain_service.worker.activation import ActivationClient
from svc_common.logging import request_id_var


class FakeServices:
    def __init__(self) -> None:
        self.activation_statuses: list[int] = []
        self.requests: list[tuple[str, dict[str, str], dict]] = []


@pytest.fixture
def server() -> Iterator[tuple[str, FakeServices]]:
    state = FakeServices()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            headers = {k.lower(): v for k, v in self.headers.items()}  # sin distinguir mayúsculas
            state.requests.append((self.path, headers, body))
            if self.path == "/auth/token":
                ok = body.get("client_secret") == "secreto"
                self._reply(200 if ok else 401, {"access_token": "tok"} if ok else {})
            else:
                status = state.activation_statuses.pop(0) if state.activation_statuses else 200
                self._reply(status, {"version": "1.0.0", "changed": True, "previous": None})

        def _reply(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_: object) -> None:
            return None

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", state
    httpd.shutdown()


def client(url: str, secret: str = "secreto", sleeps: list | None = None) -> ActivationClient:
    return ActivationClient(
        auth_url=url,
        ia_url=url,
        client_id="retrain_service",
        client_secret=secret,
        timeout_s=5,
        sleep=(sleeps.append if sleeps is not None else lambda _s: None),
    )


def test_success_sends_service_token_and_request_id(server) -> None:
    url, state = server
    token = request_id_var.set("job-abc")
    try:
        assert client(url).request_activation("1.0.0") is True
    finally:
        request_id_var.reset(token)
    path, headers, _ = state.requests[-1]
    assert path == "/internal/models/1.0.0/activate"
    assert headers["authorization"] == "Bearer tok"
    assert headers["x-request-id"] == "job-abc"


def test_transient_errors_are_retried_with_backoff(server) -> None:
    url, state = server
    state.activation_statuses = [503, 503, 200]
    sleeps: list[float] = []
    assert client(url, sleeps=sleeps).request_activation("1.0.0") is True
    assert sleeps == [2, 4]


def test_retries_are_bounded(server) -> None:
    url, state = server
    state.activation_statuses = [503, 503, 503, 200]
    assert client(url).request_activation("1.0.0") is False
    activations = [r for r in state.requests if r[0].startswith("/internal")]
    assert len(activations) == 3


def test_client_errors_are_not_retried(server) -> None:
    url, state = server
    state.activation_statuses = [422, 200]
    assert client(url).request_activation("1.0.0") is False
    assert len([r for r in state.requests if r[0].startswith("/internal")]) == 1


def test_bad_service_credentials(server) -> None:
    url, state = server
    assert client(url, secret="otro").request_activation("1.0.0") is False
    assert not [r for r in state.requests if r[0].startswith("/internal")]
