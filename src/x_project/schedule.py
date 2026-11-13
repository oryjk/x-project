"""Three persisted, uneven publication times per hour, with no catch-up bursts."""
from contextlib import contextmanager
from pathlib import Path
import random
import sqlite3


class Schedule:
    def __init__(self, path, rng=None):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.rng = rng or random.SystemRandom()
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS schedule(due INTEGER PRIMARY KEY,hour INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending')")
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def plan(self, now):
        hour = int(now.timestamp()) // 3600 * 3600
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = [r[0] for r in db.execute("SELECT due FROM schedule WHERE hour=? ORDER BY due", (hour,))]
            if rows:
                return rows
            previous = db.execute("SELECT max(due) FROM schedule WHERE due<?", (hour,)).fetchone()[0]
            first_min = max(4, (previous + 480-hour+59)//60 if previous else 4)
            first = self.rng.randrange(first_min, 19)
            second = self.rng.randrange(max(26, first+8), 40)
            candidates = [m for m in range(max(47, second+8), 59) if m-second != second-first]
            third = self.rng.choice(candidates)
            due_times = [hour + minute*60 for minute in (first, second, third)]
            db.executemany("INSERT INTO schedule(due,hour) VALUES(?,?)", [(t, hour) for t in due_times])
            return due_times

    def take_due(self, now):
        self.plan(now)
        epoch = int(now.timestamp())
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE schedule SET status='missed' WHERE status='pending' AND due<?", (epoch-120,))
            row = db.execute("SELECT due FROM schedule WHERE status='pending' AND due<=? ORDER BY due LIMIT 1", (epoch,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE schedule SET status='claimed' WHERE due=?", (row[0],))
            return row[0]

    def upcoming(self, now):
        self.plan(now)
        with self.connection() as db:
            return [{"due": r[0], "status": r[1]} for r in db.execute("SELECT due,status FROM schedule WHERE hour>=? ORDER BY due LIMIT 9", (int(now.timestamp())//3600*3600,))]
