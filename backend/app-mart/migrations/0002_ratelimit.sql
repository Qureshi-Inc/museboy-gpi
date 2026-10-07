CREATE TABLE IF NOT EXISTS submission_limits (
    token_hash TEXT NOT NULL,
    ip TEXT NOT NULL,
    ts INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS submission_limits_window
    ON submission_limits(token_hash, ip, ts);
