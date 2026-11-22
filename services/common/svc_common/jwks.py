"""Cliente JWKS: obtiene y cachea las claves públicas de auth_service.

Política de caché (AGENTS.md §7: JWKS con caché):
- claves frescas (menos de `ttl_s`): se usan sin consultar a auth_service;
- `kid` desconocido o caché vencida: se vuelve a pedir el JWKS (así se soporta la rotación de
  claves), como máximo una vez cada `min_refresh_interval_s` para no saturar a auth_service;
- auth_service caído: se siguen usando las claves cacheadas mientras no superen `max_stale_s`.
  Pasado ese límite, o si nunca se obtuvieron, se lanza AuthUnavailableError (→ 503). No hay
  fallback silencioso: un token jamás se acepta sin verificar su firma.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from collections.abc import Callable
from typing import Any

import jwt

from svc_common.tokens import ALGORITHM, TokenError

logger = logging.getLogger(__name__)


class AuthUnavailableError(Exception):
    """No hay claves verificables: auth_service no responde y la caché venció."""


def _http_fetch(url: str, timeout_s: float) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout_s) as response:  # noqa: S310 (URL validada)
        document: dict[str, Any] = json.loads(response.read())
        return document


def parse_jwks(document: dict[str, Any]) -> dict[str, Any]:
    """{kid: clave pública}. Solo claves RSA de firma RS256; el resto se ignora."""
    keys: dict[str, Any] = {}
    for jwk in document.get("keys", []):
        if jwk.get("kty") != "RSA" or jwk.get("alg", ALGORITHM) != ALGORITHM:
            continue
        if jwk.get("use", "sig") != "sig" or not jwk.get("kid"):
            continue
        keys[jwk["kid"]] = jwt.PyJWK(jwk).key
    return keys


class JwksClient:
    def __init__(
        self,
        url: str,
        *,
        ttl_s: float = 300,
        max_stale_s: float = 3600,
        min_refresh_interval_s: float = 10,
        timeout_s: float = 3,
        fetch: Callable[[str, float], dict[str, Any]] = _http_fetch,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not url.startswith(("http://", "https://")):
            raise ValueError("La URL del JWKS debe ser http(s)")
        self._url = url
        self._ttl_s = ttl_s
        self._max_stale_s = max_stale_s
        self._min_interval_s = min_refresh_interval_s
        self._timeout_s = timeout_s
        self._fetch = fetch
        self._clock = clock
        self._keys: dict[str, Any] | None = None
        self._fetched_at = float("-inf")
        self._last_attempt = float("-inf")
        self._lock = threading.Lock()

    def resolve(self, kid: str | None) -> Any:
        """Clave pública para `kid`. Se usa como `resolve_key` de svc_common.tokens."""
        with self._lock:
            now = self._clock()
            stale = now - self._fetched_at >= self._ttl_s
            unknown = self._keys is None or kid not in self._keys
            if (stale or unknown) and now - self._last_attempt >= self._min_interval_s:
                self._refresh(now)
            if self._keys is None or now - self._fetched_at > self._max_stale_s:
                raise AuthUnavailableError("No se pudo obtener el JWKS de auth_service")
            key = self._keys.get(kid or "")
        if key is None:
            raise TokenError("kid desconocido")
        return key

    def _refresh(self, now: float) -> None:
        self._last_attempt = now
        try:
            keys = parse_jwks(self._fetch(self._url, self._timeout_s))
        except Exception as exc:  # red, JSON o claves mal formadas: se registra y se sigue
            logger.warning("jwks_fetch_failed", extra={"error_type": type(exc).__name__})
            return
        self._keys = keys
        self._fetched_at = now
