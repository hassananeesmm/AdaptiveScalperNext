-- 0007_news: news event cache + provider health tracking (directive
-- sections 38-44). `news_events` is the TERTIARY "last-known-good local
-- cache" — every successful live provider fetch upserts into it, so a
-- later live-provider outage still has real, recently-true data to fall
-- back to (never silently treated as "no news" — see
-- news/blocking.py's ProviderHealth handling).

CREATE TABLE news_events (
    id                      INTEGER PRIMARY KEY,
    event_id                TEXT NOT NULL,
    provider                TEXT NOT NULL,
    provider_event_id       TEXT,
    scheduled_at_utc        INTEGER NOT NULL,
    country                 TEXT NOT NULL,
    currency                TEXT,
    title                   TEXT NOT NULL,
    normalized_event_type   TEXT,
    impact                  TEXT NOT NULL,
    actual                  TEXT,
    forecast                TEXT,
    previous                TEXT,
    retrieved_at_utc        INTEGER NOT NULL,
    source_reliability      TEXT NOT NULL,
    revision                INTEGER NOT NULL DEFAULT 0,
    source_identifier       TEXT NOT NULL,
    UNIQUE (event_id, provider)
);

CREATE INDEX idx_news_events_scheduled ON news_events (scheduled_at_utc);
CREATE INDEX idx_news_events_currency_impact ON news_events (currency, impact);

CREATE TABLE news_provider_state (
    provider                TEXT PRIMARY KEY,
    last_success_at_utc     INTEGER,
    last_attempt_at_utc     INTEGER,
    last_error              TEXT,
    health                  TEXT NOT NULL DEFAULT 'UNAVAILABLE'
);
