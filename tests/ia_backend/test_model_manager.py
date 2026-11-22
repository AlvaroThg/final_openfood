"""ModelManager: carga del modelo activo y activación idempotente sin perder el anterior."""

from __future__ import annotations

import uuid

import pytest

from nutriscore.lifecycle import ModelStatus
from services.ia_backend.app.application.model_manager import ModelManager
from services.ia_backend.app.domain.model import (
    ActivationConflictError,
    ModelLoadError,
    ModelNotActivatableError,
    ModelVersionNotFoundError,
)
from svc_common.tokens import Role
from tests.helpers.ia_fakes import FakeRegistry, FakeStore, model_record

ADMIN = uuid.uuid4()


def manager(*records) -> tuple[ModelManager, FakeRegistry, FakeStore]:
    registry, store = FakeRegistry(*records), FakeStore()
    return ModelManager(registry=registry, store=store), registry, store


def test_load_active_without_active_version() -> None:
    m, _, _ = manager(model_record("1.0.0", ModelStatus.APPROVED))
    assert m.load_active() is None
    assert m.current is None
    assert m.load_error is None


def test_load_active_loads_the_active_version() -> None:
    m, _, _ = manager(model_record("1.0.0", ModelStatus.ACTIVE))
    assert m.load_active().version == "1.0.0"


def test_load_active_with_broken_artifact_reports_error() -> None:
    m, _, store = manager(model_record("1.0.0", ModelStatus.ACTIVE))
    store.broken.add("1.0.0")
    assert m.load_active() is None
    assert "hash" in m.load_error


def test_activate_approved_swaps_model_and_records_actor() -> None:
    m, registry, _ = manager(
        model_record("1.0.0", ModelStatus.ACTIVE), model_record("1.1.0", ModelStatus.APPROVED)
    )
    m.load_active()
    result = m.activate("1.1.0", actor_user_id=ADMIN, role=Role.ADMIN)
    assert (result.changed, result.previous) == (True, "1.0.0")
    assert m.current.version == "1.1.0"
    assert registry.get("1.0.0").status is ModelStatus.ARCHIVED
    assert registry.activations == [("1.1.0", ADMIN, Role.ADMIN)]


def test_activation_is_idempotent() -> None:
    m, registry, store = manager(model_record("1.0.0", ModelStatus.ACTIVE))
    m.load_active()
    result = m.activate("1.0.0", actor_user_id=None, role=Role.SERVICE)
    assert result.changed is False
    assert registry.activations == []
    assert store.loads == ["1.0.0"]  # no se volvió a cargar


def test_rollback_to_archived_version() -> None:
    m, registry, _ = manager(
        model_record("1.0.0", ModelStatus.ARCHIVED), model_record("1.1.0", ModelStatus.ACTIVE)
    )
    m.load_active()
    m.activate("1.0.0", actor_user_id=ADMIN, role=Role.ADMIN)
    assert m.current.version == "1.0.0"
    assert registry.get("1.1.0").status is ModelStatus.ARCHIVED


@pytest.mark.parametrize(
    "status", [ModelStatus.CANDIDATE, ModelStatus.EVALUATING, ModelStatus.REJECTED]
)
def test_only_approved_or_archived_can_be_activated(status: ModelStatus) -> None:
    m, _, _ = manager(model_record("1.0.0", status))
    with pytest.raises(ModelNotActivatableError):
        m.activate("1.0.0", actor_user_id=ADMIN, role=Role.ADMIN)


def test_unknown_version() -> None:
    m, _, _ = manager()
    with pytest.raises(ModelVersionNotFoundError):
        m.activate("9.9.9", actor_user_id=ADMIN, role=Role.ADMIN)


def test_broken_artifact_keeps_previous_model_and_database() -> None:
    m, registry, store = manager(
        model_record("1.0.0", ModelStatus.ACTIVE), model_record("1.1.0", ModelStatus.APPROVED)
    )
    m.load_active()
    store.broken.add("1.1.0")
    with pytest.raises(ModelLoadError):
        m.activate("1.1.0", actor_user_id=ADMIN, role=Role.ADMIN)
    assert m.current.version == "1.0.0"
    assert registry.activations == []
    assert registry.get("1.0.0").status is ModelStatus.ACTIVE


def test_database_failure_keeps_previous_model() -> None:
    m, registry, _ = manager(
        model_record("1.0.0", ModelStatus.ACTIVE), model_record("1.1.0", ModelStatus.APPROVED)
    )
    m.load_active()
    registry.fail_with = ActivationConflictError("1.1.0")
    with pytest.raises(ActivationConflictError):
        m.activate("1.1.0", actor_user_id=ADMIN, role=Role.ADMIN)
    assert m.current.version == "1.0.0"
