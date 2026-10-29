-- Vault-only durable storage on the existing PostgreSQL/Neon database.
-- Existing migrations and team-attachment tables are deliberately untouched.
-- The application never creates/recreates these tables at request/startup time.
BEGIN;

CREATE TABLE IF NOT EXISTS public.redocx_vault_state (
    singleton SMALLINT PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    store_id UUID NOT NULL UNIQUE,
    master_key_check TEXT CHECK (master_key_check ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.redocx_vault_items (
    item_id TEXT PRIMARY KEY CHECK (item_id ~ '^[0-9a-f]{32}$'),
    owner_key TEXT NOT NULL CHECK (owner_key ~ '^[0-9a-f]{64}$'),
    item_kind TEXT NOT NULL CHECK (item_kind IN ('file', 'text')),
    metadata_blob BYTEA NOT NULL CHECK (OCTET_LENGTH(metadata_blob) > 28),
    encrypted_size_bytes BIGINT NOT NULL CHECK (encrypted_size_bytes >= 37),
    encrypted_sha256 TEXT NOT NULL CHECK (encrypted_sha256 ~ '^[0-9a-f]{64}$'),
    chunk_count INTEGER NOT NULL CHECK (chunk_count > 0),
    created_at_iso TEXT NOT NULL CHECK (LENGTH(created_at_iso) BETWEEN 20 AND 40),
    updated_at_iso TEXT
);

CREATE INDEX IF NOT EXISTS idx_redocx_vault_owner_created
    ON public.redocx_vault_items (owner_key, created_at_iso DESC, item_id DESC);

CREATE TABLE IF NOT EXISTS public.redocx_vault_content_chunks (
    item_id TEXT NOT NULL REFERENCES public.redocx_vault_items(item_id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    encrypted_chunk BYTEA NOT NULL
        CHECK (OCTET_LENGTH(encrypted_chunk) BETWEEN 1 AND 1048576),
    PRIMARY KEY (item_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS public.redocx_vault_imports (
    source_fingerprint TEXT PRIMARY KEY CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),
    item_count BIGINT NOT NULL CHECK (item_count >= 0),
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Only this explicit migration can provision a new identity. Missing state
-- alongside existing items is an error, never a request to initialise afresh.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.redocx_vault_state WHERE singleton = 1) THEN
        IF EXISTS (SELECT 1 FROM public.redocx_vault_items)
           OR EXISTS (SELECT 1 FROM public.redocx_vault_content_chunks) THEN
            RAISE EXCEPTION 'Vault state is missing while Vault data exists; restore its original state';
        END IF;
        INSERT INTO public.redocx_vault_state(singleton, schema_version, store_id)
        VALUES (1, 1, gen_random_uuid());
    END IF;
END
$$;

-- A committed item must have the complete, contiguous ciphertext. These
-- constraints also reject metadata-only commits and partial chunk deletion.
CREATE OR REPLACE FUNCTION public.verify_redocx_vault_chunks()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    target_id TEXT;
    item_record public.redocx_vault_items%ROWTYPE;
    actual_count BIGINT;
    actual_bytes BIGINT;
    first_chunk INTEGER;
    last_chunk INTEGER;
BEGIN
    target_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.item_id ELSE NEW.item_id END;
    SELECT * INTO item_record FROM public.redocx_vault_items WHERE item_id = target_id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT COUNT(*), COALESCE(SUM(OCTET_LENGTH(encrypted_chunk)), 0),
           MIN(chunk_index), MAX(chunk_index)
    INTO actual_count, actual_bytes, first_chunk, last_chunk
    FROM public.redocx_vault_content_chunks WHERE item_id = target_id;
    IF actual_count <> item_record.chunk_count
       OR actual_bytes <> item_record.encrypted_size_bytes
       OR first_chunk IS DISTINCT FROM 0
       OR last_chunk IS DISTINCT FROM item_record.chunk_count - 1 THEN
        RAISE EXCEPTION 'Vault ciphertext is incomplete' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_redocx_vault_item_complete ON public.redocx_vault_items;
CREATE CONSTRAINT TRIGGER trg_redocx_vault_item_complete
AFTER INSERT OR UPDATE ON public.redocx_vault_items
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION public.verify_redocx_vault_chunks();

DROP TRIGGER IF EXISTS trg_redocx_vault_chunks_complete ON public.redocx_vault_content_chunks;
CREATE CONSTRAINT TRIGGER trg_redocx_vault_chunks_complete
AFTER INSERT OR UPDATE OR DELETE ON public.redocx_vault_content_chunks
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION public.verify_redocx_vault_chunks();

CREATE OR REPLACE FUNCTION public.protect_redocx_vault_identity()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'Vault identity must not be deleted or truncated' USING ERRCODE = '55000';
    END IF;
    IF NEW.singleton IS DISTINCT FROM OLD.singleton
       OR NEW.store_id IS DISTINCT FROM OLD.store_id
       OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
       OR (OLD.master_key_check IS NOT NULL AND NEW.master_key_check IS DISTINCT FROM OLD.master_key_check) THEN
        RAISE EXCEPTION 'Vault identity or master key cannot be replaced implicitly' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_redocx_vault_identity ON public.redocx_vault_state;
CREATE TRIGGER trg_redocx_vault_identity
BEFORE UPDATE OR DELETE ON public.redocx_vault_state FOR EACH ROW
EXECUTE FUNCTION public.protect_redocx_vault_identity();

CREATE OR REPLACE FUNCTION public.deny_redocx_vault_truncate()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Vault tables must not be truncated by runtime cleanup' USING ERRCODE = '55000';
END
$$;

DROP TRIGGER IF EXISTS trg_redocx_vault_state_no_truncate ON public.redocx_vault_state;
CREATE TRIGGER trg_redocx_vault_state_no_truncate BEFORE TRUNCATE ON public.redocx_vault_state
FOR EACH STATEMENT EXECUTE FUNCTION public.deny_redocx_vault_truncate();
DROP TRIGGER IF EXISTS trg_redocx_vault_items_no_truncate ON public.redocx_vault_items;
CREATE TRIGGER trg_redocx_vault_items_no_truncate BEFORE TRUNCATE ON public.redocx_vault_items
FOR EACH STATEMENT EXECUTE FUNCTION public.deny_redocx_vault_truncate();
DROP TRIGGER IF EXISTS trg_redocx_vault_chunks_no_truncate ON public.redocx_vault_content_chunks;
CREATE TRIGGER trg_redocx_vault_chunks_no_truncate BEFORE TRUNCATE ON public.redocx_vault_content_chunks
FOR EACH STATEMENT EXECUTE FUNCTION public.deny_redocx_vault_truncate();


-- Runtime cleanup cannot bulk-delete Vault rows. Explicit owner deletion is
-- scoped to the existing repository transaction and resets at COMMIT/ROLLBACK.
CREATE OR REPLACE FUNCTION public.guard_redocx_vault_owner_delete()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('redocx.vault_delete_owner', TRUE) IS DISTINCT FROM OLD.owner_key THEN
        RAISE EXCEPTION 'Vault deletion requires an explicit owner-scoped transaction' USING ERRCODE = '55000';
    END IF;
    RETURN OLD;
END
$$;
DROP TRIGGER IF EXISTS trg_redocx_vault_owner_delete ON public.redocx_vault_items;
CREATE TRIGGER trg_redocx_vault_owner_delete BEFORE DELETE ON public.redocx_vault_items
FOR EACH ROW EXECUTE FUNCTION public.guard_redocx_vault_owner_delete();

DROP TRIGGER IF EXISTS trg_redocx_vault_imports_no_truncate ON public.redocx_vault_imports;
CREATE TRIGGER trg_redocx_vault_imports_no_truncate BEFORE TRUNCATE ON public.redocx_vault_imports
FOR EACH STATEMENT EXECUTE FUNCTION public.deny_redocx_vault_truncate();

CREATE OR REPLACE FUNCTION public.protect_redocx_vault_import_marker()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Completed Vault import markers are immutable' USING ERRCODE = '55000';
END
$$;
DROP TRIGGER IF EXISTS trg_redocx_vault_import_marker ON public.redocx_vault_imports;
CREATE TRIGGER trg_redocx_vault_import_marker BEFORE UPDATE OR DELETE ON public.redocx_vault_imports
FOR EACH ROW EXECUTE FUNCTION public.protect_redocx_vault_import_marker();

COMMIT;

-- Record this value as VAULT_EXPECTED_STORE_ID on the existing backend service:
SELECT store_id FROM public.redocx_vault_state WHERE singleton = 1;
