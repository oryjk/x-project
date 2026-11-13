"""Durable queue: once submission may have happened, never retry automatically."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3

from .content import ContentError, Draft, canonical_url, utcnow


class SubmissionBlocked(ContentError):
    pass


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
                topic TEXT NOT NULL, draft TEXT NOT NULL, created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued', note TEXT NOT NULL DEFAULT '',
                submitted_at TEXT, sent_at TEXT);
            CREATE TABLE IF NOT EXISTS sources (
                url TEXT PRIMARY KEY, post_id INTEGER NOT NULL REFERENCES posts(id));
            CREATE TABLE IF NOT EXISTS slots (
                slot INTEGER PRIMARY KEY, post_id INTEGER NOT NULL REFERENCES posts(id));
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, draft, now=None):
        now = now or utcnow()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            urls = [canonical_url(s.url) for s in draft.sources]
            if any(db.execute("SELECT 1 FROM sources WHERE url=?", (url,)).fetchone() for url in urls):
                return False
            if db.execute("SELECT 1 FROM posts WHERE topic=? AND created_at>?", (draft.topic, (now - timedelta(days=3)).isoformat())).fetchone():
                return False
            try:
                row = db.execute("INSERT INTO posts(fingerprint,topic,draft,created_at) VALUES(?,?,?,?)", (
                    draft.fingerprint, draft.topic, json.dumps(draft.to_dict(), ensure_ascii=False), now.isoformat()))
                db.executemany("INSERT INTO sources(url,post_id) VALUES(?,?)", [(url, row.lastrowid) for url in urls])
            except sqlite3.IntegrityError:
                return False
            return True

    def recover(self):
        with self.connection() as db:
            db.execute("UPDATE posts SET status='unknown', note='提交后进程中断，需人工核对' WHERE status='submitting'")
            db.execute("UPDATE posts SET status='queued', note='提交前进程中断' WHERE status='prepared'")

    def queued_count(self):
        with self.connection() as db:
            return db.execute("SELECT count(*) FROM posts WHERE status='queued'").fetchone()[0]

    def rows(self, limit=20):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM posts ORDER BY id DESC LIMIT ?", (limit,))]

    def get(self, post_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
            return dict(row) if row else None

    def claim(self, now=None, post_id=None):
        now = now or utcnow()
        slot = int(now.timestamp())
        hour = slot // 3600 * 3600
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM slots WHERE slot=?", (slot,)).fetchone():
                return None
            if db.execute("SELECT count(*) FROM slots WHERE slot>=? AND slot<?", (hour, hour+3600)).fetchone()[0] >= 3:
                return None
            last = db.execute("SELECT max(slot) FROM slots").fetchone()[0]
            if last is not None and slot-last < 480:
                return None
            # An unresolved send blocks further automation until explicitly resolved.
            if db.execute("SELECT 1 FROM posts WHERE status IN ('unknown','submitting')").fetchone():
                return None
            if post_id is None:
                row = db.execute("SELECT * FROM posts WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            else:
                row = db.execute("SELECT * FROM posts WHERE id=? AND status='queued'", (post_id,)).fetchone()
            if row is None:
                return None
            db.execute("INSERT INTO slots(slot,post_id) VALUES(?,?)", (slot, row["id"]))
            db.execute("UPDATE posts SET status='prepared' WHERE id=?", (row["id"],))
            return dict(row)

    def submitting(self, post_id, now=None, due_at=None):
        now = now or utcnow()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if due_at is not None and not 0 <= now.timestamp()-due_at <= 120:
                raise SubmissionBlocked("发布时间已过期或尚未到，不补发")
            last = db.execute("SELECT max(submitted_at) FROM posts WHERE submitted_at IS NOT NULL").fetchone()[0]
            if last and (now - datetime.fromisoformat(last)).total_seconds() < 480:
                raise SubmissionBlocked("距离上次实际提交不足8分钟")
            start = now.replace(minute=0, second=0, microsecond=0)
            count = db.execute("SELECT count(*) FROM posts WHERE submitted_at>=? AND submitted_at<?", (start.isoformat(), (start+timedelta(hours=1)).isoformat())).fetchone()[0]
            if count >= 3:
                raise SubmissionBlocked("当前小时实际提交已达到3条")
            result = db.execute("UPDATE posts SET status='submitting', submitted_at=? WHERE id=? AND status='prepared'", (now.isoformat(), post_id))
            if result.rowcount != 1:
                raise RuntimeError("草稿状态不允许提交")

    def finish(self, post_id, status, note=""):
        if status not in ("sent", "unknown"):
            raise ValueError("invalid result")
        with self.connection() as db:
            db.execute("UPDATE posts SET status=?, note=?, sent_at=? WHERE id=? AND status='submitting'", (status, note, utcnow().isoformat() if status == "sent" else None, post_id))

    def defer(self, post_id, reason):
        with self.connection() as db:
            db.execute("UPDATE posts SET status='queued',note=? WHERE id=? AND status='prepared'", (reason, post_id))

    def reject(self, post_id, reason):
        with self.connection() as db:
            db.execute("UPDATE posts SET status='rejected',note=? WHERE id=? AND status IN ('queued','prepared')", (reason, post_id))

    def resolve(self, post_id, sent):
        with self.connection() as db:
            db.execute("UPDATE posts SET status=?,note='用户核对结果',sent_at=? WHERE id=? AND status='unknown'", (
                "sent" if sent else "queued", utcnow().isoformat() if sent else None, post_id))

    def draft(self, post_id):
        row = self.get(post_id)
        if not row:
            raise ValueError("找不到草稿")
        return Draft.from_dict(json.loads(row["draft"]))
