"""Limitador de peticiones en memoria, por clave, con ventana deslizante.

Alcance consciente: vive en la memoria del proceso. Se pierde al reiniciar y no se comparte
entre réplicas. Alcanza porque cada servicio corre en una sola réplica; con varias, habría que
moverlo a Redis o a Postgres.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_s: int  # 0 si está permitido


class SlidingWindowRateLimiter:
    """Permite como máximo `limit` eventos por clave dentro de los últimos `window_s` segundos.

    hit(key):
        descartar los eventos de `key` más viejos que la ventana
        si quedan `limit` o más: rechazar e indicar cuándo vence el más viejo
        si no: registrar el evento y permitir

    Es seguro entre hilos: FastAPI ejecuta los endpoints síncronos en un pool de hilos.
    """

    def __init__(
        self,
        limit: int,
        window_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        if limit < 1 or window_s <= 0:
            raise ValueError("limit debe ser >= 1 y window_s > 0")
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._max_keys = max_keys
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> RateLimitDecision:
        now = self._clock()
        with self._lock:
            events = self._events.setdefault(key, deque())
            self._discard_old(events, now)
            if len(events) >= self.limit:
                retry_after = math.ceil(events[0] + self.window_s - now)
                return RateLimitDecision(allowed=False, retry_after_s=max(retry_after, 1))
            events.append(now)
            if len(self._events) > self._max_keys:
                self._prune(now)
            return RateLimitDecision(allowed=True, retry_after_s=0)

    def _discard_old(self, events: deque[float], now: float) -> None:
        while events and events[0] <= now - self.window_s:
            events.popleft()

    def _prune(self, now: float) -> None:
        """Acota la memoria: elimina las claves sin eventos dentro de la ventana."""
        for key in list(self._events):
            events = self._events[key]
            self._discard_old(events, now)
            if not events:
                del self._events[key]
