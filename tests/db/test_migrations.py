"""Las migraciones se aplican, se revierten y se vuelven a aplicar sin dejar restos."""

from __future__ import annotations

import uuid

import pytest
from alembic.script import ScriptDirectory

from tests.helpers import pg

pytestmark = pytest.mark.integration


def _public_tables(dbname: str) -> set[str]:
    with pg.connect("app_migrator", dbname) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    return {row["tablename"] for row in rows}


def test_single_linear_history() -> None:
    script = ScriptDirectory.from_config(pg.alembic_config("unused"))
    assert script.get_heads() == ["0002"]


def test_upgrade_downgrade_upgrade_roundtrip(pg_server: None) -> None:
    name = f"nutriscore_mig_{uuid.uuid4().hex[:8]}"
    pg.create_database(name)
    try:
        pg.migrate(name)
        assert set(pg.APP_TABLES) <= _public_tables(name)

        pg.downgrade(name, "base")
        assert _public_tables(name) == {"alembic_version"}

        pg.migrate(name)
        with pg.connect("app_migrator", name) as conn:
            version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert version == {"version_num": "0002"}
    finally:
        pg.drop_database(name)
