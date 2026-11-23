--
-- PostgreSQL database dump
--

\restrict fYUVH10i7dWF6IQ5kjl2o1O7xXco0oqOBnQtmnyuYzeSyqVTV1ctF5ukoyvCb7d

-- Dumped from database version 16.14
-- Dumped by pg_dump version 16.14

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: app_current_request_id(); Type: FUNCTION; Schema: public; Owner: app_migrator
--

CREATE FUNCTION public.app_current_request_id() RETURNS text
    LANGUAGE sql STABLE
    AS $$ SELECT nullif(current_setting('app.request_id', true), '') $$;


ALTER FUNCTION public.app_current_request_id() OWNER TO app_migrator;

--
-- Name: app_current_role(); Type: FUNCTION; Schema: public; Owner: app_migrator
--

CREATE FUNCTION public.app_current_role() RETURNS text
    LANGUAGE sql STABLE
    AS $$ SELECT nullif(current_setting('app.role', true), '') $$;


ALTER FUNCTION public.app_current_role() OWNER TO app_migrator;

--
-- Name: app_current_user_id(); Type: FUNCTION; Schema: public; Owner: app_migrator
--

CREATE FUNCTION public.app_current_user_id() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$ SELECT nullif(current_setting('app.user_id', true), '')::uuid $$;


ALTER FUNCTION public.app_current_user_id() OWNER TO app_migrator;

--
-- Name: log_model_version_event(); Type: FUNCTION; Schema: public; Owner: app_migrator
--

CREATE FUNCTION public.log_model_version_event() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'pg_temp'
    AS $$
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


ALTER FUNCTION public.log_model_version_event() OWNER TO app_migrator;

--
-- Name: model_status_transition_allowed(text, text); Type: FUNCTION; Schema: public; Owner: app_migrator
--

CREATE FUNCTION public.model_status_transition_allowed(from_status text, to_status text) RETURNS boolean
    LANGUAGE sql IMMUTABLE
    AS $$
    SELECT (from_status, to_status) IN (VALUES
        ('candidate', 'evaluating'),
        ('evaluating', 'approved'),
        ('evaluating', 'rejected'),
        ('approved', 'active'),
        ('active', 'archived'),
        ('archived', 'active'))
$$;


ALTER FUNCTION public.model_status_transition_allowed(from_status text, to_status text) OWNER TO app_migrator;

--
-- Name: model_versions_integrity(); Type: FUNCTION; Schema: public; Owner: app_migrator
--

CREATE FUNCTION public.model_versions_integrity() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


ALTER FUNCTION public.model_versions_integrity() OWNER TO app_migrator;

--
-- Name: model_versions_role_guard(); Type: FUNCTION; Schema: public; Owner: app_migrator
--

CREATE FUNCTION public.model_versions_role_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


ALTER FUNCTION public.model_versions_role_guard() OWNER TO app_migrator;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: app_migrator
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE public.alembic_version OWNER TO app_migrator;

--
-- Name: datasets; Type: TABLE; Schema: public; Owner: app_migrator
--

CREATE TABLE public.datasets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    version text NOT NULL,
    sha256 character(64) NOT NULL,
    row_count integer NOT NULL,
    file_name text NOT NULL,
    cleaning_report jsonb NOT NULL,
    class_distribution jsonb NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT datasets_row_count_positive CHECK ((row_count > 0)),
    CONSTRAINT datasets_sha256_format CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT datasets_version_format CHECK ((version ~ '^v[1-9][0-9]*$'::text))
);


ALTER TABLE public.datasets OWNER TO app_migrator;

--
-- Name: COLUMN datasets.created_by; Type: COMMENT; Schema: public; Owner: app_migrator
--

COMMENT ON COLUMN public.datasets.created_by IS 'NULL = registrado por el sistema (dataset inicial).';


--
-- Name: model_version_events; Type: TABLE; Schema: public; Owner: app_migrator
--

CREATE TABLE public.model_version_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    model_version_id uuid NOT NULL,
    from_status text,
    to_status text NOT NULL,
    db_role text NOT NULL,
    actor_user_id uuid,
    request_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT model_version_events_from_valid CHECK (((from_status IS NULL) OR (from_status = ANY (ARRAY['candidate'::text, 'evaluating'::text, 'approved'::text, 'active'::text, 'rejected'::text, 'archived'::text])))),
    CONSTRAINT model_version_events_to_valid CHECK ((to_status = ANY (ARRAY['candidate'::text, 'evaluating'::text, 'approved'::text, 'active'::text, 'rejected'::text, 'archived'::text])))
);


ALTER TABLE public.model_version_events OWNER TO app_migrator;

--
-- Name: COLUMN model_version_events.db_role; Type: COMMENT; Schema: public; Owner: app_migrator
--

COMMENT ON COLUMN public.model_version_events.db_role IS 'Usuario de base de datos que hizo el cambio (app_ia = ia_backend, app_retrain = retrain).';


--
-- Name: COLUMN model_version_events.actor_user_id; Type: COMMENT; Schema: public; Owner: app_migrator
--

COMMENT ON COLUMN public.model_version_events.actor_user_id IS 'Usuario de la plataforma que lo pidió (app.user_id), si hubo uno.';


--
-- Name: model_versions; Type: TABLE; Schema: public; Owner: app_migrator
--

CREATE TABLE public.model_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    version text NOT NULL,
    dataset_id uuid NOT NULL,
    algorithm text NOT NULL,
    config jsonb NOT NULL,
    status text DEFAULT 'candidate'::text NOT NULL,
    artifact_sha256 character(64),
    artifact_size_bytes bigint,
    metrics jsonb,
    dependencies jsonb,
    quality_policy_version text,
    gate jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT model_versions_evaluated CHECK (((status = ANY (ARRAY['candidate'::text, 'evaluating'::text, 'rejected'::text])) OR ((artifact_sha256 IS NOT NULL) AND (artifact_size_bytes IS NOT NULL) AND (metrics IS NOT NULL) AND (dependencies IS NOT NULL) AND (quality_policy_version IS NOT NULL) AND (gate IS NOT NULL)))),
    CONSTRAINT model_versions_sha256_format CHECK ((artifact_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT model_versions_size_positive CHECK ((artifact_size_bytes > 0)),
    CONSTRAINT model_versions_status_valid CHECK ((status = ANY (ARRAY['candidate'::text, 'evaluating'::text, 'approved'::text, 'active'::text, 'rejected'::text, 'archived'::text]))),
    CONSTRAINT model_versions_version_format CHECK ((version ~ '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'::text))
);


ALTER TABLE public.model_versions OWNER TO app_migrator;

--
-- Name: TABLE model_versions; Type: COMMENT; Schema: public; Owner: app_migrator
--

COMMENT ON TABLE public.model_versions IS 'Registro de versiones. El artefacto vive en el volumen models/<version>/.';


--
-- Name: predictions; Type: TABLE; Schema: public; Owner: app_migrator
--

CREATE TABLE public.predictions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    model_version text NOT NULL,
    input jsonb NOT NULL,
    grade character(1) NOT NULL,
    probabilities jsonb NOT NULL,
    inference_ms real NOT NULL,
    request_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT predictions_grade_valid CHECK ((grade = ANY (ARRAY['A'::bpchar, 'B'::bpchar, 'C'::bpchar, 'D'::bpchar, 'E'::bpchar]))),
    CONSTRAINT predictions_inference_ms_positive CHECK ((inference_ms >= (0)::double precision)),
    CONSTRAINT predictions_input_object CHECK ((jsonb_typeof(input) = 'object'::text)),
    CONSTRAINT predictions_probabilities_object CHECK ((jsonb_typeof(probabilities) = 'object'::text))
);

ALTER TABLE ONLY public.predictions FORCE ROW LEVEL SECURITY;


ALTER TABLE public.predictions OWNER TO app_migrator;

--
-- Name: TABLE predictions; Type: COMMENT; Schema: public; Owner: app_migrator
--

COMMENT ON TABLE public.predictions IS 'Historial inmutable: cada fila guarda la versión exacta del modelo que la produjo.';


--
-- Name: refresh_tokens; Type: TABLE; Schema: public; Owner: app_migrator
--

CREATE TABLE public.refresh_tokens (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    family_id uuid NOT NULL,
    token_hash character(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    revoked_reason text,
    replaced_by uuid,
    CONSTRAINT refresh_tokens_expiry CHECK ((expires_at > created_at)),
    CONSTRAINT refresh_tokens_hash_format CHECK ((token_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT refresh_tokens_reason_valid CHECK ((revoked_reason = ANY (ARRAY['rotated'::text, 'logout'::text, 'reuse_detected'::text]))),
    CONSTRAINT refresh_tokens_replacement CHECK (((replaced_by IS NULL) OR (revoked_reason = 'rotated'::text))),
    CONSTRAINT refresh_tokens_revocation CHECK (((revoked_at IS NULL) = (revoked_reason IS NULL)))
);


ALTER TABLE public.refresh_tokens OWNER TO app_migrator;

--
-- Name: TABLE refresh_tokens; Type: COMMENT; Schema: public; Owner: app_migrator
--

COMMENT ON TABLE public.refresh_tokens IS 'Solo el SHA-256 del token opaco. family_id agrupa las rotaciones de un mismo inicio de sesión.';


--
-- Name: retrain_jobs; Type: TABLE; Schema: public; Owner: app_migrator
--

CREATE TABLE public.retrain_jobs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    dataset_id uuid NOT NULL,
    config_name text NOT NULL,
    requested_by uuid NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    model_version_id uuid,
    activation text DEFAULT 'not_requested'::text NOT NULL,
    error text,
    attempts integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    heartbeat_at timestamp with time zone,
    finished_at timestamp with time zone,
    CONSTRAINT retrain_jobs_activation_valid CHECK ((activation = ANY (ARRAY['not_requested'::text, 'succeeded'::text, 'failed'::text, 'skipped'::text]))),
    CONSTRAINT retrain_jobs_attempts_positive CHECK ((attempts >= 0)),
    CONSTRAINT retrain_jobs_config_name_format CHECK ((config_name ~ '^[a-z0-9_]{1,64}$'::text)),
    CONSTRAINT retrain_jobs_failed_has_error CHECK (((status <> 'failed'::text) OR (error IS NOT NULL))),
    CONSTRAINT retrain_jobs_finished CHECK (((status = ANY (ARRAY['succeeded'::text, 'failed'::text])) = (finished_at IS NOT NULL))),
    CONSTRAINT retrain_jobs_started CHECK (((status = 'queued'::text) OR (started_at IS NOT NULL))),
    CONSTRAINT retrain_jobs_status_valid CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'succeeded'::text, 'failed'::text])))
);

ALTER TABLE ONLY public.retrain_jobs FORCE ROW LEVEL SECURITY;


ALTER TABLE public.retrain_jobs OWNER TO app_migrator;

--
-- Name: TABLE retrain_jobs; Type: COMMENT; Schema: public; Owner: app_migrator
--

COMMENT ON TABLE public.retrain_jobs IS 'Cola de entrenamiento: el worker toma trabajos con FOR UPDATE SKIP LOCKED.';


--
-- Name: users; Type: TABLE; Schema: public; Owner: app_migrator
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    email text NOT NULL,
    password_hash text NOT NULL,
    role text DEFAULT 'user'::text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT users_email_normalized CHECK (((email = lower(btrim(email))) AND ((char_length(email) >= 3) AND (char_length(email) <= 254)) AND (POSITION(('@'::text) IN (email)) > 1))),
    CONSTRAINT users_role_valid CHECK ((role = ANY (ARRAY['user'::text, 'admin'::text])))
);


ALTER TABLE public.users OWNER TO app_migrator;

--
-- Name: TABLE users; Type: COMMENT; Schema: public; Owner: app_migrator
--

COMMENT ON TABLE public.users IS 'Cuentas de usuario. El email se guarda normalizado (minúsculas).';


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: datasets datasets_pkey; Type: CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.datasets
    ADD CONSTRAINT datasets_pkey PRIMARY KEY (id);


--
-- Name: datasets datasets_version_key; Type: CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.datasets
    ADD CONSTRAINT datasets_version_key UNIQUE (version);


--
-- Name: model_version_events model_version_events_pkey; Type: CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.model_version_events
    ADD CONSTRAINT model_version_events_pkey PRIMARY KEY (id);


--
-- Name: model_versions model_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.model_versions
    ADD CONSTRAINT model_versions_pkey PRIMARY KEY (id);


--
-- Name: model_versions model_versions_version_key; Type: CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.model_versions
    ADD CONSTRAINT model_versions_version_key UNIQUE (version);


--
-- Name: predictions predictions_pkey; Type: CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.predictions
    ADD CONSTRAINT predictions_pkey PRIMARY KEY (id);


--
-- Name: refresh_tokens refresh_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT refresh_tokens_pkey PRIMARY KEY (id);


--
-- Name: refresh_tokens refresh_tokens_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT refresh_tokens_token_hash_key UNIQUE (token_hash);


--
-- Name: retrain_jobs retrain_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.retrain_jobs
    ADD CONSTRAINT retrain_jobs_pkey PRIMARY KEY (id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: model_version_events_version_idx; Type: INDEX; Schema: public; Owner: app_migrator
--

CREATE INDEX model_version_events_version_idx ON public.model_version_events USING btree (model_version_id, created_at);


--
-- Name: model_versions_single_active; Type: INDEX; Schema: public; Owner: app_migrator
--

CREATE UNIQUE INDEX model_versions_single_active ON public.model_versions USING btree (status) WHERE (status = 'active'::text);


--
-- Name: predictions_user_created_idx; Type: INDEX; Schema: public; Owner: app_migrator
--

CREATE INDEX predictions_user_created_idx ON public.predictions USING btree (user_id, created_at DESC);


--
-- Name: refresh_tokens_active_family_idx; Type: INDEX; Schema: public; Owner: app_migrator
--

CREATE INDEX refresh_tokens_active_family_idx ON public.refresh_tokens USING btree (family_id) WHERE (revoked_at IS NULL);


--
-- Name: refresh_tokens_user_idx; Type: INDEX; Schema: public; Owner: app_migrator
--

CREATE INDEX refresh_tokens_user_idx ON public.refresh_tokens USING btree (user_id);


--
-- Name: retrain_jobs_queue_idx; Type: INDEX; Schema: public; Owner: app_migrator
--

CREATE INDEX retrain_jobs_queue_idx ON public.retrain_jobs USING btree (created_at) WHERE (status = 'queued'::text);


--
-- Name: retrain_jobs_single_running; Type: INDEX; Schema: public; Owner: app_migrator
--

CREATE UNIQUE INDEX retrain_jobs_single_running ON public.retrain_jobs USING btree (status) WHERE (status = 'running'::text);


--
-- Name: model_versions model_versions_integrity; Type: TRIGGER; Schema: public; Owner: app_migrator
--

CREATE TRIGGER model_versions_integrity BEFORE INSERT OR UPDATE ON public.model_versions FOR EACH ROW EXECUTE FUNCTION public.model_versions_integrity();


--
-- Name: model_versions model_versions_log_insert; Type: TRIGGER; Schema: public; Owner: app_migrator
--

CREATE TRIGGER model_versions_log_insert AFTER INSERT ON public.model_versions FOR EACH ROW EXECUTE FUNCTION public.log_model_version_event();


--
-- Name: model_versions model_versions_log_status; Type: TRIGGER; Schema: public; Owner: app_migrator
--

CREATE TRIGGER model_versions_log_status AFTER UPDATE OF status ON public.model_versions FOR EACH ROW WHEN ((old.status IS DISTINCT FROM new.status)) EXECUTE FUNCTION public.log_model_version_event();


--
-- Name: model_versions model_versions_role_guard; Type: TRIGGER; Schema: public; Owner: app_migrator
--

CREATE TRIGGER model_versions_role_guard BEFORE UPDATE OF status ON public.model_versions FOR EACH ROW WHEN ((old.status IS DISTINCT FROM new.status)) EXECUTE FUNCTION public.model_versions_role_guard();


--
-- Name: datasets datasets_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.datasets
    ADD CONSTRAINT datasets_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: model_version_events model_version_events_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.model_version_events
    ADD CONSTRAINT model_version_events_actor_user_id_fkey FOREIGN KEY (actor_user_id) REFERENCES public.users(id);


--
-- Name: model_version_events model_version_events_model_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.model_version_events
    ADD CONSTRAINT model_version_events_model_version_id_fkey FOREIGN KEY (model_version_id) REFERENCES public.model_versions(id);


--
-- Name: model_versions model_versions_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.model_versions
    ADD CONSTRAINT model_versions_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.datasets(id);


--
-- Name: predictions predictions_model_version_fkey; Type: FK CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.predictions
    ADD CONSTRAINT predictions_model_version_fkey FOREIGN KEY (model_version) REFERENCES public.model_versions(version);


--
-- Name: predictions predictions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.predictions
    ADD CONSTRAINT predictions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: refresh_tokens refresh_tokens_replaced_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT refresh_tokens_replaced_by_fkey FOREIGN KEY (replaced_by) REFERENCES public.refresh_tokens(id);


--
-- Name: refresh_tokens refresh_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT refresh_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: retrain_jobs retrain_jobs_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.retrain_jobs
    ADD CONSTRAINT retrain_jobs_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.datasets(id);


--
-- Name: retrain_jobs retrain_jobs_model_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.retrain_jobs
    ADD CONSTRAINT retrain_jobs_model_version_id_fkey FOREIGN KEY (model_version_id) REFERENCES public.model_versions(id);


--
-- Name: retrain_jobs retrain_jobs_requested_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: app_migrator
--

ALTER TABLE ONLY public.retrain_jobs
    ADD CONSTRAINT retrain_jobs_requested_by_fkey FOREIGN KEY (requested_by) REFERENCES public.users(id);


--
-- Name: predictions; Type: ROW SECURITY; Schema: public; Owner: app_migrator
--

ALTER TABLE public.predictions ENABLE ROW LEVEL SECURITY;

--
-- Name: predictions predictions_insert_own; Type: POLICY; Schema: public; Owner: app_migrator
--

CREATE POLICY predictions_insert_own ON public.predictions FOR INSERT TO app_ia WITH CHECK (((user_id = public.app_current_user_id()) AND (public.app_current_role() = ANY (ARRAY['user'::text, 'admin'::text]))));


--
-- Name: predictions predictions_select_own_or_admin; Type: POLICY; Schema: public; Owner: app_migrator
--

CREATE POLICY predictions_select_own_or_admin ON public.predictions FOR SELECT TO app_ia USING (((user_id = public.app_current_user_id()) OR (public.app_current_role() = 'admin'::text)));


--
-- Name: retrain_jobs; Type: ROW SECURITY; Schema: public; Owner: app_migrator
--

ALTER TABLE public.retrain_jobs ENABLE ROW LEVEL SECURITY;

--
-- Name: retrain_jobs retrain_jobs_admin_or_worker; Type: POLICY; Schema: public; Owner: app_migrator
--

CREATE POLICY retrain_jobs_admin_or_worker ON public.retrain_jobs TO app_retrain USING ((public.app_current_role() = ANY (ARRAY['admin'::text, 'service'::text]))) WITH CHECK ((public.app_current_role() = ANY (ARRAY['admin'::text, 'service'::text])));


--
-- Name: SCHEMA public; Type: ACL; Schema: -; Owner: pg_database_owner
--

REVOKE USAGE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO app_auth;
GRANT USAGE ON SCHEMA public TO app_ia;
GRANT USAGE ON SCHEMA public TO app_retrain;


--
-- Name: FUNCTION app_current_request_id(); Type: ACL; Schema: public; Owner: app_migrator
--

REVOKE ALL ON FUNCTION public.app_current_request_id() FROM PUBLIC;
GRANT ALL ON FUNCTION public.app_current_request_id() TO app_ia;
GRANT ALL ON FUNCTION public.app_current_request_id() TO app_retrain;


--
-- Name: FUNCTION app_current_role(); Type: ACL; Schema: public; Owner: app_migrator
--

REVOKE ALL ON FUNCTION public.app_current_role() FROM PUBLIC;
GRANT ALL ON FUNCTION public.app_current_role() TO app_ia;
GRANT ALL ON FUNCTION public.app_current_role() TO app_retrain;


--
-- Name: FUNCTION app_current_user_id(); Type: ACL; Schema: public; Owner: app_migrator
--

REVOKE ALL ON FUNCTION public.app_current_user_id() FROM PUBLIC;
GRANT ALL ON FUNCTION public.app_current_user_id() TO app_ia;
GRANT ALL ON FUNCTION public.app_current_user_id() TO app_retrain;


--
-- Name: FUNCTION log_model_version_event(); Type: ACL; Schema: public; Owner: app_migrator
--

REVOKE ALL ON FUNCTION public.log_model_version_event() FROM PUBLIC;


--
-- Name: FUNCTION model_status_transition_allowed(from_status text, to_status text); Type: ACL; Schema: public; Owner: app_migrator
--

REVOKE ALL ON FUNCTION public.model_status_transition_allowed(from_status text, to_status text) FROM PUBLIC;
GRANT ALL ON FUNCTION public.model_status_transition_allowed(from_status text, to_status text) TO app_ia;
GRANT ALL ON FUNCTION public.model_status_transition_allowed(from_status text, to_status text) TO app_retrain;


--
-- Name: FUNCTION model_versions_integrity(); Type: ACL; Schema: public; Owner: app_migrator
--

REVOKE ALL ON FUNCTION public.model_versions_integrity() FROM PUBLIC;


--
-- Name: TABLE datasets; Type: ACL; Schema: public; Owner: app_migrator
--

GRANT SELECT ON TABLE public.datasets TO app_ia;
GRANT SELECT,INSERT ON TABLE public.datasets TO app_retrain;


--
-- Name: TABLE model_version_events; Type: ACL; Schema: public; Owner: app_migrator
--

GRANT SELECT ON TABLE public.model_version_events TO app_ia;
GRANT SELECT ON TABLE public.model_version_events TO app_retrain;


--
-- Name: TABLE model_versions; Type: ACL; Schema: public; Owner: app_migrator
--

GRANT SELECT ON TABLE public.model_versions TO app_ia;
GRANT SELECT,INSERT ON TABLE public.model_versions TO app_retrain;


--
-- Name: COLUMN model_versions.status; Type: ACL; Schema: public; Owner: app_migrator
--

GRANT UPDATE(status) ON TABLE public.model_versions TO app_ia;
GRANT UPDATE(status) ON TABLE public.model_versions TO app_retrain;


--
-- Name: COLUMN model_versions.artifact_sha256; Type: ACL; Schema: public; Owner: app_migrator
--

GRANT UPDATE(artifact_sha256) ON TABLE public.model_versions TO app_retrain;


--
-- Name: COLUMN model_versions.artifact_size_bytes; Type: ACL; Schema: public; Owner: app_migrator
--

GRANT UPDATE(artifact_size_bytes) ON TABLE public.model_versions TO app_retrain;


--
-- Name: COLUMN model_versions.metrics; Type: ACL; Schema: public; Owner: app_migrator
--

GRANT UPDATE(metrics) ON TABLE public.model_versions TO app_retrain;


--
-- Name: COLUMN model_versions.dependencies; Type: ACL; Schema: public; Owner: app_migrator
--

GRANT UPDATE(dependencies) ON TABLE public.model_versions TO app_retrain;


--
-- Name: COLUMN model_versions.quality_policy_version; Type: ACL; Schema: public; Owner: app_migrator
--

GRANT UPDATE(quality_policy_version) ON TABLE public.model_versions TO app_retrain;


--
-- Name: COLUMN model_versions.gate; Type: ACL; Schema: public; Owner: app_migrator
--

GRANT UPDATE(gate) ON TABLE public.model_versions TO app_retrain;


--
-- Name: TABLE predictions; Type: ACL; Schema: public; Owner: app_migrator
--

GRANT SELECT,INSERT ON TABLE public.predictions TO app_ia;


--
-- Name: TABLE refresh_tokens; Type: ACL; Schema: public; Owner: app_migrator
--

GRANT SELECT,INSERT,UPDATE ON TABLE public.refresh_tokens TO app_auth;


--
-- Name: TABLE retrain_jobs; Type: ACL; Schema: public; Owner: app_migrator
--

GRANT SELECT,INSERT,UPDATE ON TABLE public.retrain_jobs TO app_retrain;


--
-- Name: TABLE users; Type: ACL; Schema: public; Owner: app_migrator
--

GRANT SELECT,INSERT,UPDATE ON TABLE public.users TO app_auth;


--
-- PostgreSQL database dump complete
--

\unrestrict fYUVH10i7dWF6IQ5kjl2o1O7xXco0oqOBnQtmnyuYzeSyqVTV1ctF5ukoyvCb7d

