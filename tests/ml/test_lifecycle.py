from __future__ import annotations

import itertools

import pytest

from nutriscore.lifecycle import (
    InvalidTransitionError,
    ModelStatus,
    can_transition,
    ensure_transition,
    next_minor_version,
    parse_version,
)

S = ModelStatus
EXPECTED_ALLOWED = {
    (S.CANDIDATE, S.EVALUATING),
    (S.EVALUATING, S.APPROVED),
    (S.EVALUATING, S.REJECTED),
    (S.APPROVED, S.ACTIVE),
    (S.ACTIVE, S.ARCHIVED),
    (S.ARCHIVED, S.ACTIVE),
}


@pytest.mark.parametrize(("current", "target"), list(itertools.product(S, S)))
def test_transition_table_is_exactly_the_documented_one(current: S, target: S) -> None:
    assert can_transition(current, target) == ((current, target) in EXPECTED_ALLOWED)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.REJECTED, S.ACTIVE),
        (S.CANDIDATE, S.ACTIVE),
        (S.EVALUATING, S.ACTIVE),
        (S.ACTIVE, S.APPROVED),
    ],
)
def test_invalid_transitions_raise(current: S, target: S) -> None:
    with pytest.raises(InvalidTransitionError):
        ensure_transition(current, target)


def test_versions() -> None:
    assert next_minor_version([]) == "1.0.0"
    assert next_minor_version(["1.0.0", "1.2.0", "1.1.0"]) == "1.3.0"
    assert next_minor_version(["2.0.0", "1.9.0"]) == "2.1.0"
    assert parse_version("1.10.0") == (1, 10, 0)
    for invalid in ("v1.0.0", "1.0", "01.0.0", "1.0.0-beta"):
        with pytest.raises(ValueError, match="Versión inválida"):
            parse_version(invalid)
