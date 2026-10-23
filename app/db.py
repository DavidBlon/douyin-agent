from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterable

from .config import AgentSettings, DB_PATH, ensure_dirs

_LOCK = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
  aweme_id TEXT PRIMARY KEY,
  author TEXT NOT NULL DEFAULT '',
  author_url TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  cover TEXT NOT NULL DEFAULT '',
  stats TEXT NOT NULL DEFAULT '{}',
  comments TEXT NOT NULL DEFAULT '[]',
  seen_at TEXT NOT NULL,
  day TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  video_id TEXT NOT NULL DEFAULT '',
  target_name TEXT NOT NULL DEFAULT '',
  target_url TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  policy TEXT NOT NULL DEFAULT '',
  result TEXT NOT NULL DEFAULT '',
  screenshot TEXT NOT NULL DEFAULT '',
  dry_run INTEGER NOT NULL DEFAULT 0,
  day TEXT NOT NULL,
  created_at TEXT NOT NULL,
  executed_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  level TEXT NOT NULL,
  kind TEXT NOT NULL,
  message TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS replies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  target_key TEXT NOT NULL,
  incoming TEXT NOT NULL,
  reply TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(kind, target_key, incoming)
);

CREATE TABLE IF NOT EXISTS share_runs (
  day TEXT NOT NULL,
  slot TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (day, slot)
);

CREATE TABLE IF NOT EXISTS share_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request TEXT NOT NULL,
  target TEXT NOT NULL,
  video_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(request, target, video_id)
);

CREATE INDEX IF NOT EXISTS idx_actions_day ON actions(day, kind, status);
CREATE INDEX IF NOT EXISTS idx_actions_video ON actions(video_id, kind, status);
CREATE INDEX IF NOT EXISTS idx_actions_created ON actions(created_at);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_replies_day ON replies(created_at);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


@contextmanager
def db() -> Iterable[sqlite3.Connection]:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _LOCK, db() as conn:
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT data FROM settings WHERE id = 1").fetchone()
        if row is None:
            fresh = AgentSettings().model_dump()
            fresh["updated_at"] = now_iso()
            conn.execute(
                "INSERT INTO settings (id, data) VALUES (1, ?)",
                (json.dumps(fresh, ensure_ascii=False),),
            )


def load_settings() -> AgentSettings:
    with _LOCK, db() as conn:
        row = conn.execute("SELECT data FROM settings WHERE id = 1").fetchone()
    data = json.loads(row["data"]) if row else {}
    return AgentSettings.model_validate(data)


def save_settings(settings: AgentSettings) -> AgentSettings:
    settings.updated_at = now_iso()
    payload = settings.model_dump()
    with _LOCK, db() as conn:
        conn.execute(
            """INSERT INTO settings (id, data) VALUES (1, ?)
               ON CONFLICT(id) DO UPDATE SET data = excluded.data""",
            (json.dumps(payload, ensure_ascii=False),),
        )
    return settings


def insert_event(level: str, kind: str, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    ts = now_iso()
    payload = json.dumps(data or {}, ensure_ascii=False, default=str)
    with _LOCK, db() as conn:
        cur = conn.execute(
            "INSERT INTO events (ts, level, kind, message, data) VALUES (?, ?, ?, ?, ?)",
            (ts, level, kind, message, payload),
        )
        event_id = int(cur.lastrowid)
    return {"id": event_id, "ts": ts, "level": level, "kind": kind, "message": message, "data": data or {}}


def recent_events(limit: int = 60) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 300))
    with _LOCK, db() as conn:
        rows = conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    result = []
    for row in reversed(rows):
        item = dict(row)
        try:
            item["data"] = json.loads(item["data"] or "{}")
        except ValueError:
            item["data"] = {}
        result.append(item)
    return result


def upsert_video(video: dict[str, Any]) -> None:
    aweme_id = str(video.get("aweme_id") or "").strip()
    if not aweme_id:
        return
    payload = {
        "aweme_id": aweme_id,
        "author": str(video.get("author") or ""),
        "author_url": str(video.get("author_url") or ""),
        "title": str(video.get("title") or "")[:1000],
        "description": str(video.get("description") or "")[:4000],
        "url": str(video.get("url") or ""),
        "cover": str(video.get("cover") or ""),
        "stats": json.dumps(video.get("stats") or {}, ensure_ascii=False),
        "comments": json.dumps(video.get("comments") or [], ensure_ascii=False),
        "seen_at": now_iso(),
        "day": today_key(),
    }
    with _LOCK, db() as conn:
        conn.execute(
            """INSERT INTO videos
               (aweme_id, author, author_url, title, description, url, cover, stats, comments, seen_at, day)
               VALUES (:aweme_id, :author, :author_url, :title, :description, :url, :cover, :stats, :comments, :seen_at, :day)
               ON CONFLICT(aweme_id) DO UPDATE SET
                 author=excluded.author, author_url=excluded.author_url, title=excluded.title,
                 description=excluded.description, url=excluded.url, cover=excluded.cover,
                 stats=excluded.stats, comments=excluded.comments, seen_at=excluded.seen_at, day=excluded.day""",
            payload,
        )


def list_videos(limit: int = 30) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    with _LOCK, db() as conn:
        rows = conn.execute("SELECT * FROM videos ORDER BY seen_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["stats"] = json.loads(item["stats"] or "{}")
            item["comments"] = json.loads(item["comments"] or "[]")
        except ValueError:
            pass
        out.append(item)
    return out


def add_action(
    *,
    kind: str,
    video_id: str = "",
    target_name: str = "",
    target_url: str = "",
    content: str = "",
    status: str = "queued",
    policy: str = "",
    dry_run: bool = False,
) -> int:
    with _LOCK, db() as conn:
        cur = conn.execute(
            """INSERT INTO actions
               (kind, video_id, target_name, target_url, content, status, policy, dry_run, day, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (kind, video_id, target_name, target_url, content, status, policy, int(dry_run), today_key(), now_iso()),
        )
        return int(cur.lastrowid)


def update_action(action_id: int, **fields: Any) -> None:
    allowed = {"status", "result", "screenshot", "policy", "executed_at", "content"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    if "status" in updates and "executed_at" not in updates and updates["status"] in ("sent", "uncertain", "failed", "blocked", "dry_run"):
        updates["executed_at"] = now_iso()
    columns = ", ".join(f"{k}=?" for k in updates)
    with _LOCK, db() as conn:
        conn.execute(f"UPDATE actions SET {columns} WHERE id=?", (*updates.values(), action_id))


def get_action(action_id: int) -> dict[str, Any] | None:
    with _LOCK, db() as conn:
        row = conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
    return dict(row) if row else None


def list_actions(
    page: int = 1,
    size: int = 20,
    kind: str = "",
    status: str = "",
    day: str = "",
) -> dict[str, Any]:
    page = max(1, int(page or 1))
    size = max(5, min(int(size or 20), 100))
    where = []
    params: list[Any] = []
    if kind:
        where.append("kind=?")
        params.append(kind)
    if status:
        where.append("status=?")
        params.append(status)
    if day:
        where.append("day=?")
        params.append(day)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with _LOCK, db() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) AS n FROM actions{clause}", params).fetchone()["n"])
        rows = conn.execute(
            f"SELECT * FROM actions{clause} ORDER BY id DESC LIMIT ? OFFSET ?",
            (*params, size, (page - 1) * size),
        ).fetchall()
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "size": size,
        "pages": max(1, (total + size - 1) // size),
    }


def recent_actions(limit: int = 12) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    with _LOCK, db() as conn:
        rows = conn.execute("SELECT * FROM actions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def count_actions_today(kind: str, statuses: tuple[str, ...] = ("sent", "uncertain")) -> int:
    placeholders = ",".join("?" for _ in statuses)
    with _LOCK, db() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM actions WHERE day=? AND kind=? AND status IN ({placeholders})",
            (today_key(), kind, *statuses),
        ).fetchone()
    return int(row["n"])


def count_dm_today(target_name: str) -> int:
    with _LOCK, db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM actions WHERE day=? AND kind='dm' AND target_name=? AND status IN ('sent','uncertain')",
            (today_key(), target_name),
        ).fetchone()
    return int(row["n"])


def reply_exists(
    kind: str,
    target_key: str,
    incoming: str,
    *,
    max_age_seconds: int | None = None,
) -> bool:
    sql = "SELECT created_at FROM replies WHERE kind=? AND target_key=? AND incoming=? ORDER BY id DESC LIMIT 1"
    with _LOCK, db() as conn:
        row = conn.execute(sql, (kind, target_key, incoming.strip())).fetchone()
    if row is None:
        return False
    if max_age_seconds is None:
        return True
    try:
        replied_at = datetime.fromisoformat(str(row["created_at"]))
    except ValueError:
        return True
    return (datetime.now() - replied_at).total_seconds() <= max_age_seconds


def add_reply(
    *,
    kind: str,
    target_key: str,
    incoming: str,
    reply: str,
    status: str = "sent",
) -> int:
    with _LOCK, db() as conn:
        cur = conn.execute(
            """INSERT INTO replies (kind, target_key, incoming, reply, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(kind, target_key, incoming) DO UPDATE SET
                   reply=excluded.reply, status=excluded.status, created_at=excluded.created_at""",
            (kind, target_key, incoming.strip(), reply, status, now_iso()),
        )
        return int(cur.lastrowid or 0)


def count_replies_today(kind: str) -> int:
    with _LOCK, db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM replies WHERE kind=? AND substr(created_at, 1, 10)=? AND status IN ('sent','uncertain')",
            (kind, today_key()),
        ).fetchone()
    return int(row["n"])


def count_replies_today_for_target(kind: str, target_key: str) -> int:
    with _LOCK, db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM replies WHERE kind=? AND target_key=? AND substr(created_at, 1, 10)=? AND status IN ('sent','uncertain')",
            (kind, target_key, today_key()),
        ).fetchone()
    return int(row["n"])


def recent_replies(limit: int = 6) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 50))
    with _LOCK, db() as conn:
        rows = conn.execute("SELECT * FROM replies ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def share_ran(day: str, slot: str) -> bool:
    with _LOCK, db() as conn:
        row = conn.execute("SELECT 1 AS n FROM share_runs WHERE day=? AND slot=?", (day, slot)).fetchone()
    return row is not None


def mark_share_run(day: str, slot: str) -> None:
    with _LOCK, db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO share_runs (day, slot, created_at) VALUES (?, ?, ?)",
            (day, slot, now_iso()),
        )


def shared_video_ids_for_request(request: str, target: str) -> set[str]:
    with _LOCK, db() as conn:
        rows = conn.execute(
            "SELECT video_id FROM share_history WHERE request=? AND target=?",
            (request.strip(), target.strip()),
        ).fetchall()
    return {str(r["video_id"]) for r in rows}


def add_share_history(request: str, target: str, video_id: str) -> None:
    with _LOCK, db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO share_history (request, target, video_id, created_at) VALUES (?, ?, ?, ?)",
            (request.strip(), target.strip(), video_id, now_iso()),
        )


def has_action(video_id: str, kind: str, statuses: tuple[str, ...] = ("sent", "uncertain")) -> bool:
    if not video_id:
        return False
    placeholders = ",".join("?" for _ in statuses)
    with _LOCK, db() as conn:
        row = conn.execute(
            f"SELECT 1 AS n FROM actions WHERE video_id=? AND kind=? AND status IN ({placeholders}) LIMIT 1",
            (video_id, kind, *statuses),
        ).fetchone()
    return row is not None


def comment_text_exists(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    with _LOCK, db() as conn:
        row = conn.execute(
            "SELECT 1 AS n FROM actions WHERE kind='comment' AND content=? AND status IN ('sent','uncertain') LIMIT 1",
            (text,),
        ).fetchone()
    return row is not None


def latest_video() -> dict[str, Any] | None:
    with _LOCK, db() as conn:
        row = conn.execute("SELECT * FROM videos ORDER BY seen_at DESC LIMIT 1").fetchone()
    if not row:
        return None
    item = dict(row)
    try:
        item["stats"] = json.loads(item["stats"] or "{}")
        item["comments"] = json.loads(item["comments"] or "[]")
    except ValueError:
        pass
    return item


def today_counts() -> dict[str, int]:
    return {
        "like": count_actions_today("like"),
        "comment": count_actions_today("comment"),
        "dm": count_actions_today("dm"),
        "publish": count_actions_today("publish"),
    }
