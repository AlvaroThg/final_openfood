"""Seguridad: permisos mínimos por servicio, RLS y quién puede cambiar cada estado del modelo.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-19

Matriz de permisos (todo lo no listado está prohibido; nadie tiene DELETE):

    tabla                  app_auth      app_ia                     app_retrain
    users                  S I U         -                          -
    refresh_tokens         S I U         -                          -
    datasets               -             S                          S I
    model_versions         -             S, U(status)               S I, U(status y evaluación)
    model_version_events   -             S                          S          (escribe el trigger)
    retrain_jobs           -             -                          S I U      + RLS
    predictions            -             S I                        -          + RLS

RLS (segunda capa; la primera es la autorización en el código):
- predictions: cada usuario ve e inserta solo lo suyo; un admin ve todo.
- retrain_jobs: visible solo en contexto admin (API) o service (worker).
FORCE ROW LEVEL SECURITY: la RLS se aplica incluso al dueño de la tabla.

Guardia de estados (trigger): solo app_ia puede pasar un modelo a active/archived y solo
app_retrain puede pasarlo a evaluating/approved/rejected (AGENTS.md: solo ia_backend activa).
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

APP_ROLES = "app_auth, app_ia, app_retrain"

UPGRADE = f"""
-- ======================================================== conexión y esquema
DO $$
BEGIN
    EXECUTE 'REVOKE ALL ON DATABASE ' || quote_ident(current_database()) || ' FROM PUBLIC';
    EXECUTE 'GRANT CONNECT ON DATABASE ' || quote_ident(current_database())
        || ' TO {APP_ROLES}';
END
$$;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO {APP_ROLES};

REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_current_user_id(), app_current_role(), app_current_request_id(),
    model_status_transition_allowed(text, text) TO app_ia, app_retrain;

-- ======================================================== auth_service
GRANT SELECT, INSERT, UPDATE ON users, refresh_tokens TO app_auth;

-- ======================================================== ia_backend
GRANT SELECT ON datasets, model_versions, model_version_events TO app_ia;
GRANT UPDATE (status) ON model_versions TO app_ia;
GRANT SELECT, INSERT ON predictions TO app_ia;

-- ======================================================== retrain_service
GRANT SELECT, INSERT ON datasets TO app_retrain;
GRANT SELECT, INSERT ON model_versions TO app_retrain;
GRANT UPDATE (status, artifact_sha256, artifact_size_bytes, metrics, dependencies,
              quality_policy_version, gate) ON model_versions TO app_retrain;
GRANT SELECT ON model_version_events TO app_retrain;
GRANT SELECT, INSERT, UPDATE ON retrain_jobs TO app_retrain;

-- ======================================================== RLS
ALTER TABLE predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE predictions FORCE ROW LEVEL SECURITY;
CREATE POLICY predictions_select_own_or_admin ON predictions
    FOR SELECT TO app_ia
    USING (user_id = app_current_user_id() OR app_current_role() = 'admin');
CREATE POLICY predictions_insert_own ON predictions
    FOR INSERT TO app_ia
    WITH CHECK (user_id = app_current_user_id() AND app_current_role() IN ('user', 'admin'));

ALTER TABLE retrain_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE retrain_jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY retrain_jobs_admin_or_worker ON retrain_jobs
    FOR ALL TO app_retrain
    USING (app_current_role() IN ('admin', 'service'))
    WITH CHECK (app_current_role() IN ('admin', 'service'));

-- ======================================================== guardia de estados del modelo
CREATE FUNCTION model_versions_role_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IN ('active', 'archived') AND current_user <> 'app_ia' THEN
        RAISE EXCEPTION USING ERRCODE = 'insufficient_privilege',
            MESSAGE = 'Solo ia_backend (app_ia) puede activar o archivar versiones de modelo';
    END IF;
    IF NEW.status IN ('evaluating', 'approved', 'rejected') AND current_user <> 'app_retrain' THEN
        RAISE EXCEPTION USING ERRCODE = 'insufficient_privilege',
            MESSAGE = 'Solo retrain_service (app_retrain) puede evaluar, aprobar o rechazar modelos';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER model_versions_role_guard
    BEFORE UPDATE OF status ON model_versions
    FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION model_versions_role_guard();
"""

DOWNGRADE = f"""
DROP TRIGGER model_versions_role_guard ON model_versions;
DROP FUNCTION model_versions_role_guard();

DROP POLICY retrain_jobs_admin_or_worker ON retrain_jobs;
ALTER TABLE retrain_jobs NO FORCE ROW LEVEL SECURITY;
ALTER TABLE retrain_jobs DISABLE ROW LEVEL SECURITY;
DROP POLICY predictions_insert_own ON predictions;
DROP POLICY predictions_select_own_or_admin ON predictions;
ALTER TABLE predictions NO FORCE ROW LEVEL SECURITY;
ALTER TABLE predictions DISABLE ROW LEVEL SECURITY;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLES};
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM {APP_ROLES};
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO PUBLIC;
REVOKE USAGE ON SCHEMA public FROM {APP_ROLES};
GRANT USAGE ON SCHEMA public TO PUBLIC;

DO $$
BEGIN
    EXECUTE 'REVOKE CONNECT ON DATABASE ' || quote_ident(current_database())
        || ' FROM {APP_ROLES}';
    EXECUTE 'GRANT CONNECT, TEMPORARY ON DATABASE ' || quote_ident(current_database())
        || ' TO PUBLIC';
END
$$;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
