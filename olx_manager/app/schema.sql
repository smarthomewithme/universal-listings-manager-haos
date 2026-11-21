CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
    platform TEXT NOT NULL DEFAULT 'olx', content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft', revision INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS draft_listing ON drafts(status, updated_at);
CREATE TABLE IF NOT EXISTS media_assets (
    id TEXT PRIMARY KEY, mime TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
    original BLOB NOT NULL, thumbnail BLOB NOT NULL, size INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS draft_media (
    draft_id TEXT NOT NULL REFERENCES drafts(id), asset_id TEXT NOT NULL REFERENCES media_assets(id),
    filename TEXT NOT NULL, position INTEGER NOT NULL, rotation INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (draft_id, asset_id)
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY, draft_id TEXT NOT NULL REFERENCES drafts(id), revision INTEGER NOT NULL,
    digest TEXT NOT NULL, approved_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
    actor TEXT NOT NULL, revoked_at INTEGER, UNIQUE (draft_id, revision)
);
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY, draft_id TEXT REFERENCES drafts(id), actor TEXT NOT NULL,
    event TEXT NOT NULL, revision INTEGER, happened_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_by_draft ON audit_events(draft_id, id);
CREATE TABLE IF NOT EXISTS provider_accounts (
    platform TEXT PRIMARY KEY, encrypted BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS marketplace_listings (
    id TEXT PRIMARY KEY, draft_id TEXT NOT NULL REFERENCES drafts(id), revision INTEGER NOT NULL,
    digest TEXT NOT NULL, mode TEXT NOT NULL, operation TEXT NOT NULL, account_id TEXT NOT NULL, status TEXT NOT NULL,
    provider_id TEXT, provider_status TEXT, provider_url TEXT, error TEXT, created_at INTEGER NOT NULL,
    UNIQUE (draft_id, mode, revision, operation)
);
CREATE UNIQUE INDEX IF NOT EXISTS only_one_olx_submission ON marketplace_listings(draft_id) WHERE mode='olx' AND operation='create' AND status!='failed';
CREATE UNIQUE INDEX IF NOT EXISTS no_identical_olx_submission ON marketplace_listings(account_id, digest) WHERE mode='olx' AND operation='create' AND status!='failed';
CREATE UNIQUE INDEX IF NOT EXISTS unique_external_advert ON marketplace_listings(account_id, provider_id) WHERE mode='olx' AND operation='create' AND status!='failed';
CREATE TABLE IF NOT EXISTS requests (
    actor TEXT NOT NULL, action TEXT NOT NULL, key TEXT NOT NULL, input_hash TEXT NOT NULL,
    entity_id TEXT NOT NULL, PRIMARY KEY (actor, action, key)
);
CREATE TABLE IF NOT EXISTS price_suggestions (
    draft_id TEXT NOT NULL REFERENCES drafts(id), payload TEXT NOT NULL, created_at INTEGER NOT NULL,
    PRIMARY KEY (draft_id)
);
CREATE TABLE IF NOT EXISTS legacy_media (
    draft_id TEXT NOT NULL REFERENCES drafts(id), filename TEXT NOT NULL,
    position INTEGER NOT NULL, PRIMARY KEY(draft_id, position)
);
CREATE TABLE IF NOT EXISTS publication_media (
    attempt_id TEXT NOT NULL REFERENCES marketplace_listings(id),
    asset_id TEXT NOT NULL REFERENCES media_assets(id),
    rotation INTEGER NOT NULL, PRIMARY KEY(attempt_id, asset_id)
);
