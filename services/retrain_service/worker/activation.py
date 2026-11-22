"""Solicitud de activación a ia_backend, autenticada con un token de servicio.

    token  = POST {AUTH_URL}/auth/token  (client credentials de retrain_service)
    POST {IA_URL}/internal/models/{version}/activate  Authorization: Bearer <token>

- Timeout de 30 s por intento y como máximo 3 intentos, con espera creciente (2, 4 s).
- Solo se reintenta lo transitorio (red, 5xx, 429). Un 4xx es definitivo: reintentar no lo
  arregla (p. ej. el artefacto no pasó la verificación).
- La activación es idempotente en ia_backend, así que reintentar es seguro.
- Si todo falla, el modelo queda 'approved' y un admin puede activarlo a mano.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from svc_common.logging import request_id_var

logger = logging.getLogger(__name__)


class ActivationClient:
    def __init__(
        self,
        *,
        auth_url: str,
        ia_url: str,
        client_id: str,
        client_secret: str,
        timeout_s: float = 30,
        attempts: int = 3,
        backoff_s: float = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        for url in (auth_url, ia_url):
            if not url.startswith(("http://", "https://")):
                raise ValueError("Las URLs internas deben ser http(s)")
        self._auth_url = auth_url.rstrip("/")
        self._ia_url = ia_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout_s = timeout_s
        self._attempts = attempts
        self._backoff_s = backoff_s
        self._sleep = sleep

    def request_activation(self, version: str) -> bool:
        for attempt in range(1, self._attempts + 1):
            try:
                token = self._service_token()
                status, body = self._post(
                    f"{self._ia_url}/internal/models/{version}/activate", None, token
                )
            except OSError as exc:  # red caída, timeout, DNS
                status, body = 0, {"error": type(exc).__name__}
            if status == 200:
                logger.info("activation_succeeded", extra={"model_version": version})
                return True
            transient = status == 0 or status == 429 or status >= 500
            logger.warning(
                "activation_attempt_failed",
                extra={
                    "model_version": version,
                    "attempt": attempt,
                    "http_status": status,
                    "response": body,
                },
            )
            if not transient or attempt == self._attempts:
                return False
            self._sleep(self._backoff_s * attempt)
        return False

    def _service_token(self) -> str:
        status, body = self._post(
            f"{self._auth_url}/auth/token",
            {"client_id": self._client_id, "client_secret": self._client_secret},
            None,
        )
        if status != 200:
            raise OSError(f"No se obtuvo el token de servicio (HTTP {status})")
        token: str = body["access_token"]
        return token

    def _post(
        self, url: str, payload: dict[str, Any] | None, token: str | None
    ) -> tuple[int, dict[str, Any]]:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request_id = request_id_var.get()
        if request_id:
            headers["X-Request-ID"] = request_id  # correlación entre servicios
        data = json.dumps(payload).encode() if payload is not None else b""
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:  # noqa: S310
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read() or b"{}")
            except ValueError:
                body = {}
            return exc.code, body
