from __future__ import annotations

import pytest

from svc_common.rate_limit import SlidingWindowRateLimiter


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_allows_up_to_limit_then_blocks_with_retry_after() -> None:
    clock = Clock()
    limiter = SlidingWindowRateLimiter(3, 60, clock=clock)
    assert all(limiter.hit("ip").allowed for _ in range(3))
    clock.t += 10
    decision = limiter.hit("ip")
    assert not decision.allowed
    assert decision.retry_after_s == 50  # el primer evento vence a los 60 s


def test_window_slides() -> None:
    clock = Clock()
    limiter = SlidingWindowRateLimiter(2, 60, clock=clock)
    limiter.hit("k")
    clock.t += 30
    limiter.hit("k")
    assert not limiter.hit("k").allowed
    clock.t += 30.5  # el primero salió de la ventana
    assert limiter.hit("k").allowed
    assert not limiter.hit("k").allowed


def test_rejected_attempts_do_not_extend_the_block() -> None:
    clock = Clock()
    limiter = SlidingWindowRateLimiter(1, 60, clock=clock)
    limiter.hit("k")
    for _ in range(10):
        limiter.hit("k")
    clock.t += 60
    assert limiter.hit("k").allowed


def test_keys_are_independent() -> None:
    limiter = SlidingWindowRateLimiter(1, 60, clock=Clock())
    assert limiter.hit("a").allowed
    assert limiter.hit("b").allowed
    assert not limiter.hit("a").allowed


def test_memory_is_bounded() -> None:
    clock = Clock()
    limiter = SlidingWindowRateLimiter(1, 60, clock=clock, max_keys=10)
    for i in range(10):
        limiter.hit(f"k{i}")
    clock.t += 61
    limiter.hit("nueva")
    assert len(limiter._events) <= 2


@pytest.mark.parametrize(("limit", "window"), [(0, 60), (1, 0)])
def test_invalid_configuration(limit: int, window: float) -> None:
    with pytest.raises(ValueError):
        SlidingWindowRateLimiter(limit, window)
