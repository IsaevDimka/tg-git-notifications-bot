"""Schema migrations, applied in order; PRAGMA user_version records how many ran."""

MIGRATIONS: list[str] = [
    """
    CREATE TABLE users (
        tg_id INTEGER PRIMARY KEY,
        chat_id INTEGER NOT NULL,
        username TEXT NOT NULL DEFAULT '',
        lang TEXT NOT NULL DEFAULT 'en',
        tz TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        is_admin INTEGER NOT NULL DEFAULT 0,
        poll_interval INTEGER NOT NULL,
        quiet_enabled INTEGER NOT NULL DEFAULT 1,
        quiet_from TEXT NOT NULL DEFAULT '22:00',
        quiet_to TEXT NOT NULL DEFAULT '09:00',
        quiet_weekends INTEGER NOT NULL DEFAULT 1,
        muted_kinds TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL
    );
    CREATE TABLE accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER NOT NULL REFERENCES users(tg_id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        host TEXT NOT NULL,
        username TEXT NOT NULL,
        token_enc TEXT NOT NULL,
        synced INTEGER NOT NULL DEFAULT 0,
        last_poll_at TEXT,
        last_ok_at TEXT,
        last_error TEXT,
        mentions_cursor TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (tg_id, kind, host)
    );
    CREATE TABLE watched (
        account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        role TEXT NOT NULL,
        item_json TEXT NOT NULL,
        updated_at_remote TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        ball TEXT NOT NULL,
        ball_since TEXT NOT NULL,
        PRIMARY KEY (account_id, key)
    );
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER NOT NULL REFERENCES users(tg_id) ON DELETE CASCADE,
        account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        item_key TEXT NOT NULL,
        kind TEXT NOT NULL,
        dedup TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        delivered_at TEXT,
        read_at TEXT,
        snoozed_until TEXT,
        UNIQUE (tg_id, dedup)
    );
    CREATE INDEX events_pending ON events (tg_id, delivered_at);
    """,
    """
    ALTER TABLE users ADD COLUMN digest_enabled INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE users ADD COLUMN digest_time TEXT NOT NULL DEFAULT '10:00';
    ALTER TABLE users ADD COLUMN digest_last TEXT;
    """,
    """
    ALTER TABLE users ADD COLUMN mute_bots INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE users ADD COLUMN mute_drafts INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE users ADD COLUMN muted_projects TEXT NOT NULL DEFAULT '[]';
    """,
    """
    CREATE TABLE watch_refs (
        account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        project TEXT NOT NULL,
        iid INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (account_id, project, iid)
    );
    """,
    """
    ALTER TABLE accounts ADD COLUMN rate_remaining INTEGER;
    """,
    """
    ALTER TABLE users ADD COLUMN evening_enabled INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN evening_time TEXT NOT NULL DEFAULT '18:00';
    ALTER TABLE users ADD COLUMN evening_last TEXT;
    """,
]
