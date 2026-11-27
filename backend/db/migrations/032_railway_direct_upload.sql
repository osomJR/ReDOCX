BEGIN;
-- Apply using your normal PostgreSQL migration process before enabling uploads.
CREATE TABLE IF NOT EXISTS redocx_staged_uploads (
    id uuid PRIMARY KEY,
    owner_id text NOT NULL,
    feature text NOT NULL,
    manifest text NOT NULL,
    total_bytes bigint NOT NULL CHECK (total_bytes > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    cleanup_at timestamptz NOT NULL,
    claimed boolean NOT NULL DEFAULT false,
    cleaned boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS redocx_staged_uploads_created_idx ON redocx_staged_uploads (created_at);
CREATE INDEX IF NOT EXISTS redocx_staged_uploads_owner_idx ON redocx_staged_uploads (owner_id, created_at);
CREATE INDEX IF NOT EXISTS redocx_staged_uploads_cleanup_idx ON redocx_staged_uploads (cleanup_at) WHERE NOT cleaned;

CREATE TABLE IF NOT EXISTS redocx_staged_downloads (
    id uuid PRIMARY KEY,
    size_bytes bigint NOT NULL CHECK(size_bytes > 0),
    owner_digest text NOT NULL CHECK(owner_digest ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    cleanup_at timestamptz NOT NULL,
    cleaned boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS redocx_staged_downloads_due_idx ON redocx_staged_downloads(cleanup_at) WHERE NOT cleaned;
CREATE INDEX IF NOT EXISTS redocx_staged_downloads_created_idx ON redocx_staged_downloads(created_at);
CREATE INDEX IF NOT EXISTS redocx_staged_downloads_owner_idx ON redocx_staged_downloads(owner_digest,created_at);
COMMIT;
