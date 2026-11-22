"""Fixtures compartidas por las pruebas de integración (tests/db, tests/auth, ...).

Si el PostgreSQL de prueba no está levantado, las pruebas que lo usan se OMITEN con el motivo
visible en el resumen (-ra). En CI se define REQUIRE_INTEGRATION=1 para que, en ese caso,
fallen en lugar de omitirse.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Iterator

import pytest

from tests.helpers import pg


@pytest.fixture(autouse=True)
def _emit_info_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Los servicios loguean en INFO en producción; las pruebas también.

    Con el nivel por defecto de pytest (WARNING) los logs INFO ni se construyen, y un error al
    armarlos pasa inadvertido. Regresión real: `extra={"created": ...}` (atributo reservado de
    LogRecord) rompía el arranque de auth_service y ninguna prueba lo detectaba.
    """
    caplog.set_level(logging.INFO)


@pytest.fixture(scope="session")
def pg_server() -> None:
    reason = pg.server_unavailable_reason()
    if reason is None:
        return
    message = (
        f"PostgreSQL de prueba no disponible ({reason}). "
        "Levántalo con: docker compose -f docker-compose.test.yml up -d --wait"
    )
    if os.environ.get("REQUIRE_INTEGRATION") == "1":
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture(scope="session")
def migrated_db(pg_server: None) -> Iterator[str]:
    """Base nueva con todas las migraciones aplicadas; se borra al terminar la sesión."""
    name = f"nutriscore_test_{uuid.uuid4().hex[:8]}"
    pg.create_database(name)
    try:
        pg.migrate(name)
        yield name
    finally:
        pg.drop_database(name)


@pytest.fixture
def db(migrated_db: str) -> str:
    """La base de la sesión, vacía al empezar cada prueba."""
    pg.truncate_all(migrated_db)
    return migrated_db
