CREATE TABLE IF NOT EXISTS apps (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    author TEXT NOT NULL,
    version INTEGER NOT NULL,
    icon_key TEXT NOT NULL,
    bundle_key TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    downloads INTEGER NOT NULL DEFAULT 0,
    published_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS apps_category_name ON apps(category, name);
CREATE INDEX IF NOT EXISTS apps_published_at ON apps(published_at DESC);

CREATE TABLE IF NOT EXISTS submissions (
    id TEXT PRIMARY KEY,
    app_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    author TEXT NOT NULL,
    version INTEGER NOT NULL,
    icon_key TEXT NOT NULL,
    bundle_key TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    submitted_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'published', 'rejected'))
);

CREATE INDEX IF NOT EXISTS submissions_status_time
    ON submissions(status, submitted_at DESC);
