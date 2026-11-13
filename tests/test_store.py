import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from x_project.store import Store
from test_content import draft

class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "queue.sqlite3")
        self.now = datetime(2026, 11, 13, 4, 0, tzinfo=timezone.utc)

    def test_same_source_with_tracking_changes_is_not_requeued(self):
        self.assertTrue(self.store.enqueue(draft(), self.now))
        self.assertFalse(self.store.enqueue(draft("另一种说法", "https://example.org/release?utm_campaign=test"), self.now))

    def test_three_hourly_attempts_with_eight_minute_minimum_gap(self):
        self.store.enqueue(draft(), self.now)
        for i in range(3):
            d = draft(f"另一则独立消息 {i}", f"https://example.org/other-{i}"); d.topic = f"独立事件 {i}"
            self.store.enqueue(d, self.now)
        first = self.store.claim(self.now)
        self.assertIsNotNone(first)
        self.assertIsNone(self.store.claim(self.now.replace(minute=7)))
        self.assertIsNotNone(self.store.claim(self.now.replace(minute=9)))
        self.assertIsNotNone(self.store.claim(self.now.replace(minute=29)))
        self.assertIsNone(self.store.claim(self.now.replace(minute=59)))
        self.assertIsNotNone(self.store.claim(self.now.replace(hour=5)))

    def test_uncertain_submission_is_never_retried_after_a_crash(self):
        self.store.enqueue(draft(), self.now); row = self.store.claim(self.now)
        self.store.submitting(row["id"])
        recovered = Store(self.store.path); recovered.recover()
        self.assertEqual(recovered.get(row["id"])["status"], "unknown")
        self.assertIsNone(recovered.claim(self.now.replace(minute=20)))

    def test_pre_submit_block_keeps_post_but_consumes_slot(self):
        self.store.enqueue(draft(), self.now); row = self.store.claim(self.now)
        self.store.defer(row["id"], "device locked")
        self.assertEqual(self.store.get(row["id"])["status"], "queued")
        self.assertIsNone(self.store.claim(self.now))
        self.assertEqual(self.store.claim(self.now.replace(minute=20))["id"], row["id"])

    def test_actual_submission_clock_enforces_gap_after_slow_preparation(self):
        from x_project.content import ContentError
        self.store.enqueue(draft(), self.now)
        second = draft("第二则独立消息", "https://example.org/second"); second.topic = "second"
        self.store.enqueue(second, self.now)
        first = self.store.claim(self.now)
        self.store.submitting(first["id"], self.now.replace(minute=2))
        self.store.finish(first["id"], "sent")
        next_row = self.store.claim(self.now.replace(minute=8))
        self.assertIsNotNone(next_row)
        with self.assertRaises(ContentError): self.store.submitting(next_row["id"], self.now.replace(minute=8))

    def test_no_catch_up_if_preparation_runs_past_due_deadline(self):
        from x_project.content import ContentError
        self.store.enqueue(draft(), self.now); row = self.store.claim(self.now)
        with self.assertRaises(ContentError):
            self.store.submitting(row["id"], self.now.replace(minute=11), due_at=self.now.timestamp())

    def test_cross_hour_delayed_submission_counts_in_the_hour_it_is_actually_clicked(self):
        from x_project.content import ContentError
        for i in range(4):
            item = draft(f"独立消息 {i}", f"https://example.org/item-{i}"); item.topic = f"event-{i}"
            self.store.enqueue(item, self.now)
        next_hour = self.now.replace(hour=5)
        first = self.store.claim(self.now.replace(minute=40))
        self.store.submitting(first["id"], next_hour); self.store.finish(first["id"], "sent")
        for minute in (10,20):
            row = self.store.claim(next_hour.replace(minute=minute))
            self.store.submitting(row["id"], next_hour.replace(minute=minute)); self.store.finish(row["id"], "sent")
        last = self.store.claim(next_hour.replace(minute=30))
        self.assertIsNotNone(last)
        with self.assertRaises(ContentError): self.store.submitting(last["id"], next_hour.replace(minute=30))

    def test_send_window_does_not_roll_into_next_hour(self):
        from x_project.content import ContentError
        self.store.enqueue(draft(),self.now);row=self.store.claim(self.now.replace(minute=57))
        with self.assertRaises(ContentError):
            self.store.submitting(row["id"],self.now.replace(hour=self.now.hour+1),due_at=self.now.replace(minute=57).timestamp())
