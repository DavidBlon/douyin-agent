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

CREATE TABLE IF NOT EXISTS works (
  aweme_id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  cover TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'video',
  published_at TEXT NOT NULL DEFAULT '',
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_stats (
  aweme_id TEXT NOT NULL,
  day TEXT NOT NULL,
  plays INTEGER NOT NULL DEFAULT 0,
  likes INTEGER NOT NULL DEFAULT 0,
  comments INTEGER NOT NULL DEFAULT 0,
  shares INTEGER NOT NULL DEFAULT 0,
  favorites INTEGER NOT NULL DEFAULT 0,
  raw TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  PRIMARY KEY (aweme_id, day)
);

CREATE TABLE IF NOT EXISTS account_stats (
  day TEXT PRIMARY KEY,
  followers INTEGER NOT NULL DEFAULT 0,
  following INTEGER NOT NULL DEFAULT 0,
  likes INTEGER NOT NULL DEFAULT 0,
  works INTEGER NOT NULL DEFAULT 0,
  raw TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS content_plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_date TEXT NOT NULL,
  slot TEXT NOT NULL DEFAULT '',
  pillar TEXT NOT NULL DEFAULT '',
  topic TEXT NOT NULL DEFAULT '',
  angle TEXT NOT NULL DEFAULT '',
  script TEXT NOT NULL DEFAULT '',
  caption TEXT NOT NULL DEFAULT '',
  hashtags TEXT NOT NULL DEFAULT '[]',
  cover_text TEXT NOT NULL DEFAULT '',
  cover_bg TEXT NOT NULL DEFAULT '',
  cover_path TEXT NOT NULL DEFAULT '',
  video_path TEXT NOT NULL DEFAULT '',
  music_note TEXT NOT NULL DEFAULT '',
  source_notes TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'idea',
  compliance TEXT NOT NULL DEFAULT '{}',
  publish_at TEXT NOT NULL DEFAULT '',
  aweme_id TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts (
  target_key TEXT PRIMARY KEY,
  name TEXT NOT NULL DEFAULT '',
  avatar TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '[]',
  stage TEXT NOT NULL DEFAULT 'new',
  note TEXT NOT NULL DEFAULT '',
  score INTEGER NOT NULL DEFAULT 0,
  last_incoming TEXT NOT NULL DEFAULT '',
  last_reply TEXT NOT NULL DEFAULT '',
  last_seen TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_actions_day ON actions(day, kind, status);
CREATE INDEX IF NOT EXISTS idx_actions_video ON actions(video_id, kind, status);
CREATE INDEX IF NOT EXISTS idx_actions_created ON actions(created_at);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_replies_day ON replies(created_at);
CREATE INDEX IF NOT EXISTS idx_work_stats_day ON work_stats(day);
CREATE INDEX IF NOT EXISTS idx_content_plans_date ON content_plans(plan_date, status);
CREATE INDEX IF NOT EXISTS idx_contacts_stage ON contacts(stage, updated_at);
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
        # 旧库升级：新增列不会自动出现在已存在的表里。
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(content_plans)")}
        if "cover_bg" not in columns:
            conn.execute("ALTER TABLE content_plans ADD COLUMN cover_bg TEXT NOT NULL DEFAULT ''")
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


def _json_value(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return default


def upsert_work(work: dict[str, Any], *, day: str | None = None) -> None:
    aweme_id = str(work.get("aweme_id") or "").strip()
    if not aweme_id:
        return
    now = now_iso()
    with _LOCK, db() as conn:
        conn.execute(
            """INSERT INTO works (aweme_id, title, cover, url, kind, published_at, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(aweme_id) DO UPDATE SET
                 title=excluded.title, cover=excluded.cover, url=excluded.url,
                 kind=excluded.kind, published_at=excluded.published_at, last_seen=excluded.last_seen""",
            (
                aweme_id,
                str(work.get("title") or "")[:1000],
                str(work.get("cover") or ""),
                str(work.get("url") or ""),
                str(work.get("kind") or "video"),
                str(work.get("published_at") or ""),
                now,
                now,
            ),
        )


def list_works_with_latest(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    with _LOCK, db() as conn:
        rows = conn.execute(
            """SELECT w.*, s.day AS stats_day, s.plays, s.likes, s.comments, s.shares, s.favorites
               FROM works w
               LEFT JOIN work_stats s ON s.aweme_id = w.aweme_id
                 AND s.day = (SELECT MAX(day) FROM work_stats WHERE aweme_id = w.aweme_id)
               ORDER BY COALESCE(NULLIF(w.published_at, ''), w.last_seen) DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def work_history(aweme_id: str, limit: int = 30) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 120))
    with _LOCK, db() as conn:
        rows = conn.execute(
            "SELECT * FROM work_stats WHERE aweme_id=? ORDER BY day DESC LIMIT ?",
            (aweme_id, limit),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["raw"] = _json_value(item.get("raw"), {})
        out.append(item)
    return out


def latest_work_stats(aweme_id: str) -> dict[str, Any] | None:
    with _LOCK, db() as conn:
        row = conn.execute(
            "SELECT * FROM work_stats WHERE aweme_id=? ORDER BY day DESC LIMIT 1",
            (aweme_id,),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["raw"] = _json_value(item.get("raw"), {})
    return item


def save_work_stats(aweme_id: str, stats: dict[str, Any], *, day: str | None = None) -> None:
    if not aweme_id:
        return
    day = day or today_key()
    with _LOCK, db() as conn:
        conn.execute(
            """INSERT INTO work_stats
               (aweme_id, day, plays, likes, comments, shares, favorites, raw, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(aweme_id, day) DO UPDATE SET
                 plays=excluded.plays, likes=excluded.likes, comments=excluded.comments,
                 shares=excluded.shares, favorites=excluded.favorites, raw=excluded.raw,
                 created_at=excluded.created_at""",
            (
                aweme_id,
                day,
                int(stats.get("plays") or 0),
                int(stats.get("likes") or 0),
                int(stats.get("comments") or 0),
                int(stats.get("shares") or 0),
                int(stats.get("favorites") or 0),
                json.dumps(stats.get("raw") or stats, ensure_ascii=False, default=str),
                now_iso(),
            ),
        )


def save_account_stats(stats: dict[str, Any], *, day: str | None = None) -> None:
    day = day or today_key()
    with _LOCK, db() as conn:
        conn.execute(
            """INSERT INTO account_stats (day, followers, following, likes, works, raw, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(day) DO UPDATE SET
                 followers=excluded.followers, following=excluded.following,
                 likes=excluded.likes, works=excluded.works, raw=excluded.raw,
                 created_at=excluded.created_at""",
            (
                day,
                int(stats.get("followers") or 0),
                int(stats.get("following") or 0),
                int(stats.get("likes") or 0),
                int(stats.get("works") or 0),
                json.dumps(stats.get("raw") or stats, ensure_ascii=False, default=str),
                now_iso(),
            ),
        )


def latest_account_stats() -> dict[str, Any] | None:
    with _LOCK, db() as conn:
        row = conn.execute("SELECT * FROM account_stats ORDER BY day DESC LIMIT 1").fetchone()
    if not row:
        return None
    item = dict(row)
    item["raw"] = _json_value(item.get("raw"), {})
    return item


def analytics_overview(limit: int = 30) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    with _LOCK, db() as conn:
        account_row = conn.execute("SELECT * FROM account_stats ORDER BY day DESC LIMIT 1").fetchone()
        work_rows = conn.execute(
            """SELECT w.*, s.day AS stats_day, s.plays, s.likes, s.comments, s.shares, s.favorites
               FROM works w
               LEFT JOIN work_stats s ON s.aweme_id = w.aweme_id
                 AND s.day = (SELECT MAX(day) FROM work_stats WHERE aweme_id = w.aweme_id)
               ORDER BY COALESCE(s.plays, 0) DESC, w.last_seen DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        total_row = conn.execute(
            """SELECT COALESCE(SUM(plays),0) AS plays, COALESCE(SUM(likes),0) AS likes,
                      COALESCE(SUM(comments),0) AS comments, COALESCE(SUM(shares),0) AS shares
               FROM work_stats s
               WHERE s.day = (SELECT MAX(day) FROM work_stats WHERE aweme_id = s.aweme_id)"""
        ).fetchone()
        count_row = conn.execute("SELECT COUNT(*) AS n FROM works").fetchone()
        trend_rows = conn.execute(
            """SELECT day, SUM(plays) AS plays, SUM(likes) AS likes,
                      SUM(comments) AS comments, SUM(shares) AS shares
               FROM work_stats GROUP BY day ORDER BY day DESC LIMIT 14"""
        ).fetchall()
    account = dict(account_row) if account_row else {
        "day": "", "followers": 0, "following": 0, "likes": 0, "works": 0
    }
    if account_row:
        account["raw"] = _json_value(account.get("raw"), {})
    works = [dict(r) for r in work_rows]
    totals = dict(total_row) if total_row else {"plays": 0, "likes": 0, "comments": 0, "shares": 0}
    for key in ("plays", "likes", "comments", "shares"):
        totals[key] = int(totals.get(key) or 0)
    trend = [dict(r) for r in reversed(trend_rows)]
    for row in trend:
        for key in ("plays", "likes", "comments", "shares"):
            row[key] = int(row.get(key) or 0)
    return {
        "account": account,
        "totals": totals,
        "work_count": int(count_row["n"]) if count_row else 0,
        "works": works,
        "trend": trend,
        "updated_at": now_iso(),
    }


_PLAN_FIELDS = {
    "plan_date", "slot", "pillar", "topic", "angle", "script", "caption",
    "hashtags", "cover_text", "cover_bg", "cover_path", "video_path", "music_note",
    "source_notes", "status", "compliance", "publish_at", "aweme_id", "error",
}


def _plan_public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["hashtags"] = _json_value(item.get("hashtags"), [])
    item["compliance"] = _json_value(item.get("compliance"), {})
    return item


def create_content_plan(fields: dict[str, Any]) -> int:
    now = now_iso()
    data = {key: fields.get(key, "") for key in _PLAN_FIELDS}
    data["plan_date"] = str(fields.get("plan_date") or today_key())
    data["status"] = str(fields.get("status") or "idea")
    data["hashtags"] = json.dumps(fields.get("hashtags") or [], ensure_ascii=False)
    data["compliance"] = json.dumps(fields.get("compliance") or {}, ensure_ascii=False)
    with _LOCK, db() as conn:
        cur = conn.execute(
            """INSERT INTO content_plans
               (plan_date, slot, pillar, topic, angle, script, caption, hashtags, cover_text,
                cover_bg, cover_path, video_path, music_note, source_notes, status, compliance,
                publish_at, aweme_id, error, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["plan_date"], data["slot"], data["pillar"], data["topic"], data["angle"],
                data["script"], data["caption"], data["hashtags"], data["cover_text"],
                data["cover_bg"], data["cover_path"], data["video_path"], data["music_note"], data["source_notes"],
                data["status"], data["compliance"], data["publish_at"], data["aweme_id"],
                data["error"], now, now,
            ),
        )
        return int(cur.lastrowid)


def update_content_plan(plan_id: int, **fields: Any) -> dict[str, Any] | None:
    updates = {k: v for k, v in fields.items() if k in _PLAN_FIELDS}
    if "hashtags" in updates:
        updates["hashtags"] = json.dumps(updates["hashtags"] or [], ensure_ascii=False)
    if "compliance" in updates:
        updates["compliance"] = json.dumps(updates["compliance"] or {}, ensure_ascii=False)
    if updates:
        updates["updated_at"] = now_iso()
        columns = ", ".join(f"{k}=?" for k in updates)
        with _LOCK, db() as conn:
            conn.execute(f"UPDATE content_plans SET {columns} WHERE id=?", (*updates.values(), plan_id))
    return get_content_plan(plan_id)


def get_content_plan(plan_id: int) -> dict[str, Any] | None:
    with _LOCK, db() as conn:
        row = conn.execute("SELECT * FROM content_plans WHERE id=?", (plan_id,)).fetchone()
    return _plan_public(row) if row else None


def list_content_plans(
    date_from: str = "",
    date_to: str = "",
    status: str = "",
    limit: int = 200,
) -> list[dict[str, Any]]:
    where = []
    params: list[Any] = []
    if date_from:
        where.append("plan_date >= ?")
        params.append(date_from)
    if date_to:
        where.append("plan_date <= ?")
        params.append(date_to)
    if status:
        where.append("status = ?")
        params.append(status)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with _LOCK, db() as conn:
        rows = conn.execute(
            f"SELECT * FROM content_plans{clause} ORDER BY plan_date DESC, id DESC LIMIT ?",
            (*params, max(1, min(limit, 500))),
        ).fetchall()
    return [_plan_public(row) for row in rows]


def due_content_plans(now_value: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    now_value = now_value or now_iso()
    with _LOCK, db() as conn:
        rows = conn.execute(
            """SELECT * FROM content_plans
               WHERE status='approved' AND publish_at != '' AND publish_at <= ?
               ORDER BY publish_at ASC LIMIT ?""",
            (now_value, max(1, min(limit, 50))),
        ).fetchall()
    return [_plan_public(row) for row in rows]


def upsert_contact(
    target_key: str,
    *,
    name: str = "",
    avatar: str = "",
    last_incoming: str = "",
    last_reply: str = "",
    seen: bool = True,
) -> None:
    target_key = (target_key or "").strip()
    if not target_key:
        return
    now = now_iso()
    with _LOCK, db() as conn:
        conn.execute(
            """INSERT INTO contacts (target_key, name, avatar, tags, stage, note, score,
                                     last_incoming, last_reply, last_seen, updated_at)
               VALUES (?, ?, ?, '[]', 'new', '', 0, ?, ?, ?, ?)
               ON CONFLICT(target_key) DO UPDATE SET
                 name=CASE WHEN excluded.name != '' THEN excluded.name ELSE contacts.name END,
                 avatar=CASE WHEN excluded.avatar != '' THEN excluded.avatar ELSE contacts.avatar END,
                 last_incoming=CASE WHEN excluded.last_incoming != '' THEN excluded.last_incoming ELSE contacts.last_incoming END,
                 last_reply=CASE WHEN excluded.last_reply != '' THEN excluded.last_reply ELSE contacts.last_reply END,
                 last_seen=CASE WHEN ? THEN excluded.last_seen ELSE contacts.last_seen END,
                 updated_at=excluded.updated_at""",
            (target_key, name, avatar, last_incoming, last_reply, now if seen else "", now, int(seen)),
        )


def get_contact(target_key: str) -> dict[str, Any] | None:
    with _LOCK, db() as conn:
        row = conn.execute("SELECT * FROM contacts WHERE target_key=?", (target_key,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["tags"] = _json_value(item.get("tags"), [])
    return item


def list_contacts(q: str = "", stage: str = "", tag: str = "", limit: int = 200) -> list[dict[str, Any]]:
    where = []
    params: list[Any] = []
    if q:
        where.append("(name LIKE ? OR last_incoming LIKE ? OR note LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    if stage:
        where.append("stage=?")
        params.append(stage)
    if tag:
        where.append("tags LIKE ?")
        params.append(f'%"{tag}"%')
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with _LOCK, db() as conn:
        rows = conn.execute(
            f"SELECT * FROM contacts{clause} ORDER BY updated_at DESC LIMIT ?",
            (*params, max(1, min(limit, 500))),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["tags"] = _json_value(item.get("tags"), [])
        out.append(item)
    return out


def update_contact(
    target_key: str,
    *,
    tags: list[str] | None = None,
    stage: str | None = None,
    note: str | None = None,
    score: int | None = None,
) -> dict[str, Any] | None:
    updates: dict[str, Any] = {}
    if tags is not None:
        updates["tags"] = json.dumps(tags, ensure_ascii=False)
    if stage is not None:
        updates["stage"] = stage
    if note is not None:
        updates["note"] = note[:2000]
    if score is not None:
        updates["score"] = int(score)
    if updates:
        updates["updated_at"] = now_iso()
        columns = ", ".join(f"{k}=?" for k in updates)
        with _LOCK, db() as conn:
            conn.execute(f"UPDATE contacts SET {columns} WHERE target_key=?", (*updates.values(), target_key))
    return get_contact(target_key)


def delete_contact(target_key: str) -> None:
    with _LOCK, db() as conn:
        conn.execute("DELETE FROM contacts WHERE target_key=?", (target_key,))


def crm_overview() -> dict[str, Any]:
    with _LOCK, db() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM contacts").fetchone()["n"]
        rows = conn.execute("SELECT stage, COUNT(*) AS n FROM contacts GROUP BY stage").fetchall()
        recent = conn.execute(
            "SELECT * FROM contacts ORDER BY updated_at DESC LIMIT 8"
        ).fetchall()
    stages = {str(r["stage"]): int(r["n"]) for r in rows}
    recent_items = []
    for row in recent:
        item = dict(row)
        item["tags"] = _json_value(item.get("tags"), [])
        recent_items.append(item)
    return {"total": int(total), "stages": stages, "recent": recent_items}
