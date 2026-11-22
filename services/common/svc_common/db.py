"""Acceso a PostgreSQL: pool de conexiones y transacción con el contexto de RLS.

Las políticas de RLS leen tres variables de sesión: app.user_id, app.role y app.request_id.
`transaction()` las fija SIEMPRE al empezar, con `set_config(..., true)`:

- `true` = local a la transacción: al hacer COMMIT o ROLLBACK desaparecen, así que una
  conexión devuelta al pool nunca arrastra el usuario de la petición anterior;
- se fijan aunque estén vacías, para que el contexto sea siempre explícito.

Modelo de amenazas: estas variables las fija el código del servicio. La RLS protege contra
errores de ese código (una consulta sin filtro), no contra un servicio comprometido, que podría
fijar otro usuario. Contra eso protegen los permisos mínimos de cada rol (ver docs/seguridad.md).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from svc_common.logging import request_id_var

DbConnection = Connection[dict[str, Any]]

_SET_CONTEXT = (
    "SELECT set_config('app.user_id', %s, true),"
    " set_config('app.role', %s, true),"
    " set_config('app.request_id', %s, true)"
)


def create_pool(
    conninfo: str,
    *,
    name: str,
    min_size: int = 1,
    max_size: int = 5,
    timeout_s: float = 5.0,
) -> ConnectionPool[DbConnection]:
    """Crea el pool SIN abrirlo (se abre en el arranque del servicio).

    - autocommit=True: las transacciones son siempre explícitas, con `transaction()`;
    - check_connection: descarta conexiones rotas, por ejemplo si Postgres se reinició;
    - timeout: si no hay conexión disponible en `timeout_s`, se lanza PoolTimeout (→ 503).
    """
    return ConnectionPool(
        conninfo,
        name=name,
        min_size=min_size,
        max_size=max_size,
        timeout=timeout_s,
        open=False,
        check=ConnectionPool.check_connection,
        kwargs={"autocommit": True, "row_factory": dict_row, "application_name": name},
    )


@contextmanager
def transaction(
    pool: ConnectionPool[DbConnection],
    *,
    user_id: UUID | str | None = None,
    role: str | None = None,
) -> Iterator[DbConnection]:
    """Abre una transacción, fija el contexto de RLS y la confirma al salir sin errores.

    Si el bloque lanza una excepción, hace ROLLBACK y la propaga.
    """
    with pool.connection() as conn, conn.transaction():
        conn.execute(
            _SET_CONTEXT,
            (str(user_id) if user_id else "", role or "", request_id_var.get() or ""),
        )
        yield conn
