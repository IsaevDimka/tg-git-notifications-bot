"""SQLite persistence. One aiosqlite connection per process; aiosqlite serialises access."""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite

from app.models import (
    Ball,
    Event,
    ReviewItem,
    Role,
    event_from_payload,
    event_to_payload,
    item_from_dict,
    item_to_dict,
)
from app.storage.schema import MIGRATIONS
from app.timeutil import iso, parse_ts


@dataclass(frozen=True)
class User:
    tg_id: int
    chat_id: int
    username: str
    lang: str
    tz: str | None
    status: str  # pending | active | rejected | blocked
    is_admin: bool
    poll_interval: int
    quiet_enabled: bool
    quiet_from: str
    quiet_to: str
    quiet_weekends: bool
    muted_kinds: frozenset[str]
    digest_enabled: bool = True
    digest_time: str = "10:00"
    digest_last: str | None = None  # local date (YYYY-MM-DD) of the last daily summary
    mute_bots: bool = True
    mute_drafts: bool = True  # events on drafts I'm reviewing wait until the draft is ready
    muted_projects: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Account:
    id: int
    tg_id: int
    kind: str
    host: str
    username: str
    token_enc: str
    synced: bool
    last_poll_at: str | None
    last_ok_at: str | None
    last_error: str | None
    mentions_cursor: str | None


@dataclass(frozen=True)
class Watched:
    account_id: int
    key: str
    role: Role
    item: ReviewItem
    updated_at_remote: datetime
    snapshot: dict
    ball: Ball
    ball_since: datetime


@dataclass(frozen=True)
class StoredEvent:
    id: int
    tg_id: int
    account_id: int
    event: Event
    created_at: datetime
    delivered_at: datetime | None
    read_at: datetime | None
    snoozed_until: datetime | None


_USER_FIELDS = frozenset(
    {
        "chat_id", "username", "lang", "tz", "status", "is_admin", "poll_interval",
        "quiet_enabled", "quiet_from", "quiet_to", "quiet_weekends", "muted_kinds",
        "digest_enabled", "digest_time", "digest_last", "mute_bots", "mute_drafts", "muted_projects",
    }
)
_ACCOUNT_FIELDS = frozenset({"synced", "last_poll_at", "last_ok_at", "last_error", "mentions_cursor"})


def _ts(value: str | None) -> datetime | None:
    return parse_ts(value) if value else None


def _user(r) -> User:
    return User(
        tg_id=r["tg_id"],
        chat_id=r["chat_id"],
        username=r["username"],
        lang=r["lang"],
        tz=r["tz"],
        status=r["status"],
        is_admin=bool(r["is_admin"]),
        poll_interval=r["poll_interval"],
        quiet_enabled=bool(r["quiet_enabled"]),
        quiet_from=r["quiet_from"],
        quiet_to=r["quiet_to"],
        quiet_weekends=bool(r["quiet_weekends"]),
        muted_kinds=frozenset(json.loads(r["muted_kinds"])),
        digest_enabled=bool(r["digest_enabled"]),
        digest_time=r["digest_time"],
        digest_last=r["digest_last"],
        mute_bots=bool(r["mute_bots"]),
        mute_drafts=bool(r["mute_drafts"]),
        muted_projects=frozenset(json.loads(r["muted_projects"])),
    )


def _account(r) -> Account:
    return Account(
        id=r["id"],
        tg_id=r["tg_id"],
        kind=r["kind"],
        host=r["host"],
        username=r["username"],
        token_enc=r["token_enc"],
        synced=bool(r["synced"]),
        last_poll_at=r["last_poll_at"],
        last_ok_at=r["last_ok_at"],
        last_error=r["last_error"],
        mentions_cursor=r["mentions_cursor"],
    )


def _watched(r) -> Watched:
    return Watched(
        account_id=r["account_id"],
        key=r["key"],
        role=Role(r["role"]),
        item=item_from_dict(json.loads(r["item_json"])),
        updated_at_remote=parse_ts(r["updated_at_remote"]),
        snapshot=json.loads(r["snapshot_json"]),
        ball=Ball(r["ball"]),
        ball_since=parse_ts(r["ball_since"]),
    )


def _event(r) -> StoredEvent:
    return StoredEvent(
        id=r["id"],
        tg_id=r["tg_id"],
        account_id=r["account_id"],
        event=event_from_payload(r["kind"], r["dedup"], json.loads(r["payload_json"])),
        created_at=parse_ts(r["created_at"]),
        delivered_at=_ts(r["delivered_at"]),
        read_at=_ts(r["read_at"]),
        snoozed_until=_ts(r["snoozed_until"]),
    )


async def _migrate(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA user_version") as cur:
        (version,) = await cur.fetchone()
    for number, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        await db.executescript(f"{sql}\nPRAGMA user_version = {number};")
    await db.commit()


class Store:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    @classmethod
    async def open(cls, path: Path | str) -> "Store":
        db = await aiosqlite.connect(str(path))
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            await db.execute("PRAGMA journal_mode = WAL")
        await _migrate(db)
        return cls(db)

    async def close(self) -> None:
        await self.db.close()

    async def _one(self, sql: str, args: tuple = ()):
        async with self.db.execute(sql, args) as cur:
            return await cur.fetchone()

    async def _all(self, sql: str, args: tuple = ()) -> list:
        async with self.db.execute(sql, args) as cur:
            return list(await cur.fetchall())

    async def _write(self, sql: str, args: tuple = ()) -> aiosqlite.Cursor:
        cur = await self.db.execute(sql, args)
        await self.db.commit()
        return cur

    async def _write_many(self, sql: str, rows: Iterable[tuple]) -> None:
        await self.db.executemany(sql, list(rows))
        await self.db.commit()

    # ---- users -------------------------------------------------------------------------------

    async def register_user(
        self, tg_id: int, chat_id: int, username: str, lang: str, preapproved: bool, poll_interval: int,
        now: datetime,
    ) -> tuple[User, bool]:
        """Insert-if-absent in ONE statement, so two simultaneous first /starts can't both become admin."""
        cur = await self._write(
            """INSERT INTO users (tg_id, chat_id, username, lang, status, is_admin, poll_interval, created_at)
               SELECT ?, ?, ?, ?,
                      CASE WHEN ? OR NOT EXISTS (SELECT 1 FROM users WHERE is_admin = 1)
                           THEN 'active' ELSE 'pending' END,
                      NOT EXISTS (SELECT 1 FROM users WHERE is_admin = 1),
                      ?, ?
               WHERE true
               ON CONFLICT (tg_id) DO NOTHING""",
            (tg_id, chat_id, username, lang, int(preapproved), poll_interval, iso(now)),
        )
        user = await self.get_user(tg_id)
        assert user is not None
        return user, cur.rowcount == 1

    async def get_user(self, tg_id: int) -> User | None:
        row = await self._one("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
        return _user(row) if row else None

    async def admins(self) -> list[User]:
        rows = await self._all("SELECT * FROM users WHERE is_admin = 1 AND status = 'active' ORDER BY tg_id")
        return [_user(r) for r in rows]

    async def active_users(self) -> list[User]:
        return [_user(r) for r in await self._all("SELECT * FROM users WHERE status = 'active' ORDER BY tg_id")]

    async def update_user(self, tg_id: int, **fields) -> None:
        unknown = set(fields) - _USER_FIELDS
        if unknown:
            raise ValueError(f"unknown user fields: {sorted(unknown)}")
        for name in ("muted_kinds", "muted_projects"):
            if name in fields:
                fields[name] = json.dumps(sorted(fields[name]))
        cols = ", ".join(f"{name} = ?" for name in fields)
        await self._write(f"UPDATE users SET {cols} WHERE tg_id = ?", (*fields.values(), tg_id))

    # ---- accounts ----------------------------------------------------------------------------

    async def add_account(
        self, tg_id: int, kind: str, host: str, username: str, token_enc: str, now: datetime
    ) -> Account:
        """Connecting the same host again replaces the token and restarts the baseline sync."""
        await self.db.execute(
            """INSERT INTO accounts (tg_id, kind, host, username, token_enc, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (tg_id, kind, host) DO UPDATE SET
                 username = excluded.username, token_enc = excluded.token_enc, synced = 0,
                 last_poll_at = NULL, last_error = NULL, mentions_cursor = NULL""",
            (tg_id, kind, host, username, token_enc, iso(now)),
        )
        row = await self._one(
            "SELECT * FROM accounts WHERE tg_id = ? AND kind = ? AND host = ?", (tg_id, kind, host)
        )
        await self.db.execute("DELETE FROM watched WHERE account_id = ?", (row["id"],))
        await self.db.commit()
        return _account(row)

    async def accounts_for(self, tg_id: int) -> list[Account]:
        return [_account(r) for r in await self._all("SELECT * FROM accounts WHERE tg_id = ? ORDER BY id", (tg_id,))]

    async def get_account(self, account_id: int) -> Account | None:
        row = await self._one("SELECT * FROM accounts WHERE id = ?", (account_id,))
        return _account(row) if row else None

    async def delete_account(self, account_id: int, tg_id: int) -> bool:
        cur = await self._write("DELETE FROM accounts WHERE id = ? AND tg_id = ?", (account_id, tg_id))
        return cur.rowcount == 1

    async def update_account(self, account_id: int, **fields) -> None:
        unknown = set(fields) - _ACCOUNT_FIELDS
        if unknown:
            raise ValueError(f"unknown account fields: {sorted(unknown)}")
        cols = ", ".join(f"{name} = ?" for name in fields)
        await self._write(f"UPDATE accounts SET {cols} WHERE id = ?", (*fields.values(), account_id))

    async def due_accounts(self, now: datetime) -> list[Account]:
        rows = await self._all(
            """SELECT a.*, u.poll_interval AS interval FROM accounts a
               JOIN users u ON u.tg_id = a.tg_id
               WHERE u.status = 'active' ORDER BY a.id"""
        )
        due = []
        for r in rows:
            last = _ts(r["last_poll_at"])
            if last is None or now - last >= timedelta(seconds=r["interval"]):
                due.append(_account(r))
        return due

    # ---- watched -----------------------------------------------------------------------------

    async def get_watched(self, account_id: int, key: str) -> Watched | None:
        row = await self._one("SELECT * FROM watched WHERE account_id = ? AND key = ?", (account_id, key))
        return _watched(row) if row else None

    async def list_watched(self, account_id: int) -> list[Watched]:
        rows = await self._all("SELECT * FROM watched WHERE account_id = ? ORDER BY key", (account_id,))
        return [_watched(r) for r in rows]

    async def put_watched(self, w: Watched) -> None:
        await self._write(
            """INSERT INTO watched (account_id, key, role, item_json, updated_at_remote, snapshot_json, ball,
                                    ball_since)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (account_id, key) DO UPDATE SET
                 role = excluded.role, item_json = excluded.item_json,
                 updated_at_remote = excluded.updated_at_remote, snapshot_json = excluded.snapshot_json,
                 ball = excluded.ball, ball_since = excluded.ball_since""",
            (
                w.account_id, w.key, str(w.role), json.dumps(item_to_dict(w.item)), iso(w.updated_at_remote),
                json.dumps(w.snapshot), str(w.ball), iso(w.ball_since),
            ),
        )

    async def delete_watched(self, account_id: int, key: str) -> None:
        await self._write("DELETE FROM watched WHERE account_id = ? AND key = ?", (account_id, key))

    async def watched_for_user(self, tg_id: int) -> list[Watched]:
        rows = await self._all(
            """SELECT w.* FROM watched w JOIN accounts a ON a.id = w.account_id
               WHERE a.tg_id = ? ORDER BY w.key""",
            (tg_id,),
        )
        return [_watched(r) for r in rows]

    # ---- events ------------------------------------------------------------------------------

    async def add_event(self, tg_id: int, account_id: int, ev: Event, now: datetime) -> int | None:
        """Returns the new id, or None when an event with the same dedup key already exists."""
        cur = await self._write(
            """INSERT INTO events (tg_id, account_id, item_key, kind, dedup, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (tg_id, dedup) DO NOTHING""",
            (tg_id, account_id, ev.item_key, str(ev.kind), ev.dedup, json.dumps(event_to_payload(ev)), iso(now)),
        )
        return cur.lastrowid if cur.rowcount == 1 else None

    async def pending_events(self, tg_id: int, now: datetime) -> list[StoredEvent]:
        rows = await self._all(
            """SELECT * FROM events WHERE tg_id = ? AND delivered_at IS NULL
               AND (snoozed_until IS NULL OR snoozed_until <= ?) ORDER BY id""",
            (tg_id, iso(now)),
        )
        return [_event(r) for r in rows]

    async def get_event(self, event_id: int, tg_id: int) -> StoredEvent | None:
        row = await self._one("SELECT * FROM events WHERE id = ? AND tg_id = ?", (event_id, tg_id))
        return _event(row) if row else None

    async def mark_delivered(self, ids: Iterable[int], now: datetime) -> None:
        await self._write_many("UPDATE events SET delivered_at = ? WHERE id = ?", ((iso(now), i) for i in ids))

    async def mark_read(self, ids: Iterable[int], now: datetime) -> None:
        await self._write_many("UPDATE events SET read_at = ? WHERE id = ?", ((iso(now), i) for i in ids))

    async def mark_item_read(self, tg_id: int, item_key: str, now: datetime) -> None:
        await self._write(
            "UPDATE events SET read_at = ? WHERE tg_id = ? AND item_key = ? AND read_at IS NULL",
            (iso(now), tg_id, item_key),
        )

    async def mark_all_read(self, tg_id: int, now: datetime) -> None:
        await self._write("UPDATE events SET read_at = ? WHERE tg_id = ? AND read_at IS NULL", (iso(now), tg_id))

    async def snooze(self, event_id: int, until: datetime) -> None:
        await self._write(
            "UPDATE events SET delivered_at = NULL, snoozed_until = ? WHERE id = ?", (iso(until), event_id)
        )

    async def unread_counts(self, tg_id: int) -> dict[str, int]:
        rows = await self._all(
            "SELECT item_key, COUNT(*) AS n FROM events WHERE tg_id = ? AND read_at IS NULL GROUP BY item_key",
            (tg_id,),
        )
        return {r["item_key"]: r["n"] for r in rows}

    async def unread_mention_count(self, tg_id: int) -> int:
        row = await self._one(
            "SELECT COUNT(*) FROM events WHERE tg_id = ? AND read_at IS NULL AND item_key LIKE 'mention:%'",
            (tg_id,),
        )
        return row[0]
