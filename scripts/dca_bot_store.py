"""Private, persistent state for one Feishu bot instance (no desktop imports)."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from typing import Any


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class BotStore:
    def __init__(self, path: Path, seed: dict[str, Any]) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY, revision INTEGER, data TEXT);
                CREATE TABLE IF NOT EXISTS changes (
                    token TEXT PRIMARY KEY, owner TEXT, chat TEXT, revision INTEGER,
                    data TEXT, summary TEXT, expires REAL, status TEXT DEFAULT 'pending');
                CREATE TABLE IF NOT EXISTS audit (
                    revision INTEGER PRIMARY KEY, at TEXT, owner TEXT, summary TEXT, data TEXT);
                CREATE TABLE IF NOT EXISTS inbox (
                    id TEXT PRIMARY KEY, owner TEXT, chat TEXT, command TEXT,
                    status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0,
                    next_attempt REAL DEFAULT 0, received REAL);
                CREATE TABLE IF NOT EXISTS outbox (
                    id TEXT PRIMARY KEY, chat TEXT, payload TEXT, sent INTEGER DEFAULT 0,
                    attempts INTEGER DEFAULT 0, next_attempt REAL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS schedule (day TEXT PRIMARY KEY, completed INTEGER DEFAULT 0);
            """)
            db.execute("INSERT OR IGNORE INTO config VALUES (1, 0, ?)", (encode(seed),))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def read(self) -> tuple[int, dict[str, Any]]:
        with self.connect() as db:
            row = db.execute("SELECT revision, data FROM config WHERE id=1").fetchone()
            return row["revision"], json.loads(row["data"])

    def propose(self, token: str, owner: str, chat: str, revision: int,
                data: dict[str, Any], summary: str, now: float | None = None) -> None:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO changes(token,owner,chat,revision,data,summary,expires) VALUES(?,?,?,?,?,?,?)",
                       (token, owner, chat, revision, encode(data), summary, (now or time.time()) + 600))

    def confirm(self, token: str, owner: str, chat: str, *, cancel: bool = False,
                now: float | None = None) -> str:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM changes WHERE token=? AND owner=? AND chat=?", (token, owner, chat)).fetchone()
            if row is None:
                return "未找到你在本会话发起的变更。请重新提交指令。"
            if row["status"] != "pending":
                return "此变更已处理：" + row["status"]
            if row["expires"] < (now or time.time()):
                return "变更预览已过期，请重新提交指令。"
            if cancel:
                db.execute("UPDATE changes SET status='已取消' WHERE token=?", (token,))
                return "已取消，本次未修改配置。"
            current = db.execute("SELECT revision FROM config WHERE id=1").fetchone()[0]
            if current != row["revision"]:
                return "配置已被其他操作更新。请重新发起变更，核对最新值。"
            revision = current + 1
            db.execute("UPDATE config SET revision=?, data=? WHERE id=1", (revision, row["data"]))
            db.execute("INSERT INTO audit VALUES(?,?,?,?,?)", (
                revision, datetime.now(timezone.utc).isoformat(), owner, row["summary"], row["data"],
            ))
            db.execute("UPDATE changes SET status='已生效' WHERE token=?", (token,))
            return f"配置已保存（版本 {revision}）。\n{row['summary']}"

    def history(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT revision,at,summary FROM audit ORDER BY revision DESC LIMIT 10")]

    def enqueue(self, event_id: str, owner: str, chat: str, command: str) -> bool:
        with self.connect() as db:
            cursor = db.execute("INSERT OR IGNORE INTO inbox(id,owner,chat,command,received) VALUES(?,?,?,?,?)",
                                (event_id, owner, chat, command, time.time()))
            return cursor.rowcount == 1

    def next_event(self) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM inbox WHERE status='pending' AND next_attempt<=? ORDER BY received LIMIT 1",
                             (time.time(),)).fetchone()
            return dict(row) if row else None

    def complete_event(self, event_id: str, chat: str, responses: list[dict[str, Any]]) -> None:
        with self.connect() as db:
            for i, response in enumerate(responses):
                self._queue(db, f"reply:{event_id}:{i}", chat, response)
            db.execute("UPDATE inbox SET status='done' WHERE id=?", (event_id,))

    def defer_event(self, event_id: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE inbox SET attempts=attempts+1,next_attempt=? WHERE id=?", (time.time() + 60, event_id))

    @staticmethod
    def _queue(db: sqlite3.Connection, key: str, chat: str, payload: dict[str, Any]) -> None:
        db.execute("INSERT OR IGNORE INTO outbox(id,chat,payload) VALUES(?,?,?)", (key, chat, encode(payload)))

    def queue(self, key: str, chat: str, payload: dict[str, Any]) -> None:
        with self.connect() as db:
            self._queue(db, key, chat, payload)

    def next_delivery(self) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM outbox WHERE sent=0 AND next_attempt<=? ORDER BY rowid LIMIT 1",
                             (time.time(),)).fetchone()
            if row:
                result = dict(row)
                result["payload"] = json.loads(result["payload"])
                return result
            return None

    def delivery_result(self, key: str, success: bool) -> None:
        with self.connect() as db:
            db.execute("UPDATE outbox SET sent=?,attempts=attempts+1,next_attempt=? WHERE id=?",
                       (int(success), time.time() + 60, key))

    def schedule_done(self, day: str) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM schedule WHERE day=? AND completed=1", (day,)).fetchone() is not None

    def finish_schedule(self, day: str) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO schedule VALUES(?,1)", (day,))

    def queue_stats(self) -> tuple[int, int]:
        with self.connect() as db:
            return (db.execute("SELECT count(*) FROM inbox WHERE status='pending'").fetchone()[0],
                    db.execute("SELECT count(*) FROM outbox WHERE sent=0").fetchone()[0])
