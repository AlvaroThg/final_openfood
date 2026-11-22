"""Cliente JWKS: caché, rotación de claves y comportamiento con auth_service caído."""

from __future__ import annotations

import pytest

from svc_common.jwks import AuthUnavailableError, JwksClient
from svc_common.tokens import TokenError
from tests.helpers.auth_fakes import make_issuer

ISSUER = make_issuer()


class Source:
    def __init__(self) -> None:
        self.document = ISSUER.jwks()
        self.calls = 0
        self.down = False

    def __call__(self, _url: str, _timeout: float) -> dict:
        self.calls += 1
        if self.down:
            raise OSError("connection refused")
        return self.document


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def make(source: Source, clock: Clock) -> JwksClient:
    return JwksClient(
        "http://auth/jwks",
        ttl_s=300,
        max_stale_s=3600,
        min_refresh_interval_s=10,
        fetch=source,
        clock=clock,
    )


def test_keys_are_cached_within_ttl() -> None:
    source, clock = Source(), Clock()
    client = make(source, clock)
    client.resolve(ISSUER.kid)
    clock.t = 299
    client.resolve(ISSUER.kid)
    assert source.calls == 1


def test_unknown_kid_triggers_refresh_but_rate_limited() -> None:
    source, clock = Source(), Clock()
    client = make(source, clock)
    client.resolve(ISSUER.kid)
    for _ in range(5):
        with pytest.raises(TokenError):
            client.resolve("kid-falso")
    assert source.calls == 1  # dentro de los 10 s no vuelve a pedir
    clock.t = 11
    with pytest.raises(TokenError):
        client.resolve("kid-falso")
    assert source.calls == 2


def test_rotated_key_is_picked_up() -> None:
    source, clock = Source(), Clock()
    client = make(source, clock)
    client.resolve(ISSUER.kid)
    new_key = dict(source.document["keys"][0], kid="kid-nuevo")
    source.document = {"keys": [*source.document["keys"], new_key]}
    clock.t = 20
    assert client.resolve("kid-nuevo") is not None


def test_stale_cache_is_used_while_auth_is_down_then_fails() -> None:
    source, clock = Source(), Clock()
    client = make(source, clock)
    client.resolve(ISSUER.kid)
    source.down = True
    clock.t = 1000  # caché vencida, auth caído: se usa la caché vieja
    assert client.resolve(ISSUER.kid) is not None
    clock.t = 3700  # superó max_stale_s
    with pytest.raises(AuthUnavailableError):
        client.resolve(ISSUER.kid)


def test_no_keys_at_all_is_unavailable() -> None:
    source, clock = Source(), Clock()
    source.down = True
    with pytest.raises(AuthUnavailableError):
        make(source, clock).resolve(ISSUER.kid)


def test_only_http_urls() -> None:
    with pytest.raises(ValueError):
        JwksClient("file:///etc/passwd")
