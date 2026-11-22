"""Esquema inicial: tablas, restricciones, índices e integridad del ciclo de vida del modelo.

Revision ID: 0001
Revises:
Create Date: 2026-11-19

Tablas y quién las usa:

    users, refresh_tokens            auth_service
    datasets                         retrain_service (escribe), ia_backend (lee)
    model_versions                   retrain_service (crea/evalúa), ia_backend (activa/archiva)
    model_version_events             auditoría automática de cada cambio de estado (trigger)
    retrain_jobs                     retrain_service (cola de trabajos)
    predictions                      ia_backend (historial inmutable)

Integridad que garantiza la base de datos (y no solo el código):
- una versión nueva nace en 'candidate';
- solo transiciones válidas (misma tabla que nutriscore/lifecycle.py, verificada por una prueba);
- la identidad de una versión y su artefacto/evaluación no cambian una vez evaluada;
- un único modelo 'active' y un único trabajo 'running' (índices únicos parciales);
- cada cambio de estado deja una fila en model_version_events con quién, cuándo y request_id.

Permisos por rol y RLS: migración 0002.
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# SQL explícito. Evitar el signo de porcentaje y los dos puntos seguidos de una palabra:
# SQLAlchemy los interpreta como parámetros.

MODEL_STATUSES = "('candidate', 'evaluating', 'approved', 'active', 'rejected', 'archived')"

UPGRADE = rf"""
-- Contexto de la petición, fijado por svc_common.db.transaction() con set_config(..., true).
CREATE FUNCTION app_current_user_id() RETURNS uuid
LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.user_id', true), '')::uuid $$;

CREATE FUNCTION app_current_role() RETURNS text
LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.role', true), '') $$;

CREATE FUNCTION app_current_request_id() RETURNS text
LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.request_id', true), '') $$;

-- ===================================================================== usuarios y sesiones
CREATE TABLE users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email         text NOT NULL,
    password_hash text NOT NULL,
    role          text NOT NULL DEFAULT 'user',
    is_active     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT users_email_key UNIQUE (email),
    CONSTRAINT users_email_normalized CHECK (
        email = lower(btrim(email)) AND char_length(email) BETWEEN 3 AND 254
        AND position('@' IN email) > 1),
    CONSTRAINT users_role_valid CHECK (role IN ('user', 'admin'))
);
COMMENT ON TABLE users IS 'Cuentas de usuario. El email se guarda normalizado (minúsculas).';

CREATE TABLE refresh_tokens (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES users (id),
    family_id      uuid NOT NULL,
    token_hash     char(64) NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    expires_at     timestamptz NOT NULL,
    revoked_at     timestamptz,
    revoked_reason text,
    replaced_by    uuid REFERENCES refresh_tokens (id),
    CONSTRAINT refresh_tokens_token_hash_key UNIQUE (token_hash),
    CONSTRAINT refresh_tokens_hash_format CHECK (token_hash ~ '^[0-9a-f]{{64}}$'),
    CONSTRAINT refresh_tokens_expiry CHECK (expires_at > created_at),
    CONSTRAINT refresh_tokens_revocation CHECK ((revoked_at IS NULL) = (revoked_reason IS NULL)),
    CONSTRAINT refresh_tokens_reason_valid CHECK (
        revoked_reason IN ('rotated', 'logout', 'reuse_detected')),
    CONSTRAINT refresh_tokens_replacement CHECK (replaced_by IS NULL OR revoked_reason = 'rotated')
);
COMMENT ON TABLE refresh_tokens IS
    'Solo el SHA-256 del token opaco. family_id agrupa las rotaciones de un mismo inicio de sesión.';
CREATE INDEX refresh_tokens_active_family_idx ON refresh_tokens (family_id) WHERE revoked_at IS NULL;
CREATE INDEX refresh_tokens_user_idx ON refresh_tokens (user_id);

-- ============================================================================ datasets
CREATE TABLE datasets (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version            text NOT NULL,
    sha256             char(64) NOT NULL,
    row_count          integer NOT NULL,
    file_name          text NOT NULL,
    cleaning_report    jsonb NOT NULL,
    class_distribution jsonb NOT NULL,
    created_by         uuid REFERENCES users (id),
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT datasets_version_key UNIQUE (version),
    CONSTRAINT datasets_version_format CHECK (version ~ '^v[1-9][0-9]*$'),
    CONSTRAINT datasets_sha256_format CHECK (sha256 ~ '^[0-9a-f]{{64}}$'),
    CONSTRAINT datasets_row_count_positive CHECK (row_count > 0)
);
COMMENT ON COLUMN datasets.created_by IS 'NULL = registrado por el sistema (dataset inicial).';

-- ====================================================================== versiones de modelo
CREATE TABLE model_versions (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version                text NOT NULL,
    dataset_id             uuid NOT NULL REFERENCES datasets (id),
    algorithm              text NOT NULL,
    config                 jsonb NOT NULL,
    status                 text NOT NULL DEFAULT 'candidate',
    artifact_sha256        char(64),
    artifact_size_bytes    bigint,
    metrics                jsonb,
    dependencies           jsonb,
    quality_policy_version text,
    gate                   jsonb,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT model_versions_version_key UNIQUE (version),
    CONSTRAINT model_versions_version_format CHECK (
        version ~ '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'),
    CONSTRAINT model_versions_status_valid CHECK (status IN {MODEL_STATUSES}),
    CONSTRAINT model_versions_sha256_format CHECK (artifact_sha256 ~ '^[0-9a-f]{{64}}$'),
    CONSTRAINT model_versions_size_positive CHECK (artifact_size_bytes > 0),
    CONSTRAINT model_versions_evaluated CHECK (
        status IN ('candidate', 'evaluating', 'rejected')
        OR (artifact_sha256 IS NOT NULL AND artifact_size_bytes IS NOT NULL
            AND metrics IS NOT NULL AND dependencies IS NOT NULL
            AND quality_policy_version IS NOT NULL AND gate IS NOT NULL))
);
COMMENT ON TABLE model_versions IS
    'Registro de versiones. El artefacto vive en el volumen models/<version>/.';
-- Un único modelo activo: el índice solo contiene filas 'active' y es único por estado.
CREATE UNIQUE INDEX model_versions_single_active ON model_versions (status) WHERE status = 'active';

-- Misma tabla que ALLOWED_TRANSITIONS en ml/nutriscore/lifecycle.py
-- (tests/db/test_model_versions.py verifica que coincidan).
CREATE FUNCTION model_status_transition_allowed(from_status text, to_status text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT (from_status, to_status) IN (VALUES
        ('candidate', 'evaluating'),
        ('evaluating', 'approved'),
        ('evaluating', 'rejected'),
        ('approved', 'active'),
        ('active', 'archived'),
        ('archived', 'active'))
$$;

CREATE FUNCTION model_versions_integrity() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'candidate' THEN
            RAISE EXCEPTION USING ERRCODE = 'check_violation',
                MESSAGE = 'Una versión nueva debe empezar en candidate, no en ' || NEW.status;
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.version IS DISTINCT FROM OLD.version
       OR NEW.dataset_id IS DISTINCT FROM OLD.dataset_id
       OR NEW.algorithm IS DISTINCT FROM OLD.algorithm
       OR NEW.config IS DISTINCT FROM OLD.config
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION USING ERRCODE = 'check_violation',
            MESSAGE = 'La identidad de una versión de modelo es inmutable';
    END IF;

    IF OLD.status NOT IN ('candidate', 'evaluating') AND (
           NEW.artifact_sha256 IS DISTINCT FROM OLD.artifact_sha256
        OR NEW.artifact_size_bytes IS DISTINCT FROM OLD.artifact_size_bytes
        OR NEW.metrics IS DISTINCT FROM OLD.metrics
        OR NEW.dependencies IS DISTINCT FROM OLD.dependencies
        OR NEW.quality_policy_version IS DISTINCT FROM OLD.quality_policy_version
        OR NEW.gate IS DISTINCT FROM OLD.gate) THEN
        RAISE EXCEPTION USING ERRCODE = 'check_violation',
            MESSAGE = 'El artefacto y la evaluación de una versión ya evaluada son inmutables';
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status
       AND NOT model_status_transition_allowed(OLD.status, NEW.status) THEN
        RAISE EXCEPTION USING ERRCODE = 'check_violation',
            MESSAGE = 'Transición de estado no permitida: ' || OLD.status || ' -> ' || NEW.status;
    END IF;

    NEW.updated_at := now();
    RETURN NEW;
END
$$;

CREATE TRIGGER model_versions_integrity
    BEFORE INSERT OR UPDATE ON model_versions
    FOR EACH ROW EXECUTE FUNCTION model_versions_integrity();

-- ================================================================ auditoría de estados
CREATE TABLE model_version_events (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model_version_id uuid NOT NULL REFERENCES model_versions (id),
    from_status      text,
    to_status        text NOT NULL,
    db_role          text NOT NULL,
    actor_user_id    uuid REFERENCES users (id),
    request_id       text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT model_version_events_to_valid CHECK (to_status IN {MODEL_STATUSES}),
    CONSTRAINT model_version_events_from_valid CHECK (
        from_status IS NULL OR from_status IN {MODEL_STATUSES})
);
COMMENT ON COLUMN model_version_events.db_role IS
    'Usuario de base de datos que hizo el cambio (app_ia = ia_backend, app_retrain = retrain).';
COMMENT ON COLUMN model_version_events.actor_user_id IS
    'Usuario de la plataforma que lo pidió (app.user_id), si hubo uno.';
CREATE INDEX model_version_events_version_idx
    ON model_version_events (model_version_id, created_at);

-- SECURITY DEFINER: los servicios no tienen permiso de INSERT sobre la auditoría, así que no
-- pueden falsificar ni omitir eventos; solo este trigger escribe en ella.
CREATE FUNCTION log_model_version_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    INSERT INTO model_version_events
        (model_version_id, from_status, to_status, db_role, actor_user_id, request_id)
    VALUES (
        NEW.id,
        CASE WHEN TG_OP = 'UPDATE' THEN OLD.status END,
        NEW.status,
        session_user,
        app_current_user_id(),
        app_current_request_id());
    RETURN NULL;
END
$$;

CREATE TRIGGER model_versions_log_insert
    AFTER INSERT ON model_versions
    FOR EACH ROW EXECUTE FUNCTION log_model_version_event();
CREATE TRIGGER model_versions_log_status
    AFTER UPDATE OF status ON model_versions
    FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION log_model_version_event();

-- =========================================================================== reentrenamiento
CREATE TABLE retrain_jobs (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id       uuid NOT NULL REFERENCES datasets (id),
    config_name      text NOT NULL,
    requested_by     uuid NOT NULL REFERENCES users (id),
    status           text NOT NULL DEFAULT 'queued',
    model_version_id uuid REFERENCES model_versions (id),
    activation       text NOT NULL DEFAULT 'not_requested',
    error            text,
    attempts         integer NOT NULL DEFAULT 0,
    created_at       timestamptz NOT NULL DEFAULT now(),
    started_at       timestamptz,
    heartbeat_at     timestamptz,
    finished_at      timestamptz,
    CONSTRAINT retrain_jobs_status_valid CHECK (
        status IN ('queued', 'running', 'succeeded', 'failed')),
    CONSTRAINT retrain_jobs_activation_valid CHECK (
        activation IN ('not_requested', 'succeeded', 'failed', 'skipped')),
    CONSTRAINT retrain_jobs_config_name_format CHECK (config_name ~ '^[a-z0-9_]{{1,64}}$'),
    CONSTRAINT retrain_jobs_attempts_positive CHECK (attempts >= 0),
    CONSTRAINT retrain_jobs_started CHECK (status = 'queued' OR started_at IS NOT NULL),
    CONSTRAINT retrain_jobs_finished CHECK (
        (status IN ('succeeded', 'failed')) = (finished_at IS NOT NULL)),
    CONSTRAINT retrain_jobs_failed_has_error CHECK (status <> 'failed' OR error IS NOT NULL)
);
COMMENT ON TABLE retrain_jobs IS
    'Cola de entrenamiento: el worker toma trabajos con FOR UPDATE SKIP LOCKED.';
CREATE INDEX retrain_jobs_queue_idx ON retrain_jobs (created_at) WHERE status = 'queued';
CREATE UNIQUE INDEX retrain_jobs_single_running ON retrain_jobs (status) WHERE status = 'running';

-- ============================================================================= predicciones
CREATE TABLE predictions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES users (id),
    model_version text NOT NULL REFERENCES model_versions (version),
    input         jsonb NOT NULL,
    grade         char(1) NOT NULL,
    probabilities jsonb NOT NULL,
    inference_ms  real NOT NULL,
    request_id    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT predictions_grade_valid CHECK (grade IN ('A', 'B', 'C', 'D', 'E')),
    CONSTRAINT predictions_input_object CHECK (jsonb_typeof(input) = 'object'),
    CONSTRAINT predictions_probabilities_object CHECK (jsonb_typeof(probabilities) = 'object'),
    CONSTRAINT predictions_inference_ms_positive CHECK (inference_ms >= 0)
);
COMMENT ON TABLE predictions IS
    'Historial inmutable: cada fila guarda la versión exacta del modelo que la produjo.';
CREATE INDEX predictions_user_created_idx ON predictions (user_id, created_at DESC);
"""

DOWNGRADE = """
DROP TABLE predictions;
DROP TABLE retrain_jobs;
DROP TABLE model_version_events;
DROP TABLE model_versions;
DROP FUNCTION log_model_version_event();
DROP FUNCTION model_versions_integrity();
DROP FUNCTION model_status_transition_allowed(text, text);
DROP TABLE datasets;
DROP TABLE refresh_tokens;
DROP TABLE users;
DROP FUNCTION app_current_request_id();
DROP FUNCTION app_current_role();
DROP FUNCTION app_current_user_id();
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
