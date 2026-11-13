import random
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from x_project.schedule import Schedule

class ScheduleTest(unittest.TestCase):
    def test_hourly_plan_is_persisted_randomized_and_spaced_across_hours(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schedule.sqlite3"
            scheduler = Schedule(path, rng=random.Random(7))
            now = datetime(2026, 11, 13, 4, 0, tzinfo=timezone.utc)
            first = scheduler.plan(now)
            second = scheduler.plan(now.replace(hour=5))
            self.assertEqual(len(first), 3)
            self.assertEqual(len(second), 3)
            self.assertTrue(all(b-a >= 480 for a,b in zip(first+second, (first+second)[1:])))
            self.assertNotEqual([int(t % 3600) for t in first], [int(t % 3600) for t in second])
            self.assertEqual(Schedule(path).plan(now), first)

    def test_missed_jobs_are_skipped_and_no_burst_catchup_occurs(self):
        with tempfile.TemporaryDirectory() as directory:
            s = Schedule(Path(directory) / "schedule.sqlite3", rng=random.Random(7))
            now = datetime(2026, 11, 13, 4, 0, tzinfo=timezone.utc)
            times = s.plan(now)
            late = datetime.fromtimestamp(times[1]+180, timezone.utc)
            self.assertIsNone(s.take_due(late))
            last = datetime.fromtimestamp(times[2]+10, timezone.utc)
            self.assertEqual(s.take_due(last), times[2])
            self.assertIsNone(s.take_due(last))

    def test_each_hour_leaves_ten_minutes_for_last_live_research(self):
        with tempfile.TemporaryDirectory() as d:
            scheduler=Schedule(Path(d)/"schedule.sqlite3",rng=random.Random(3))
            now=datetime(2026,11,13,4,0,tzinfo=timezone.utc)
            times=scheduler.plan(now)
            self.assertLessEqual(times[-1]-int(now.timestamp()),50*60)

    def test_pending_legacy_last_slot_is_moved_earlier_without_changing_claimed_slots(self):
        with tempfile.TemporaryDirectory() as d:
            scheduler=Schedule(Path(d)/"schedule.sqlite3")
            now=datetime(2026,11,13,4,0,tzinfo=timezone.utc);hour=int(now.timestamp())
            with scheduler.connection() as db:
                db.executemany("insert into schedule(due,hour,status) values(?,?,?)",[(hour+18*60,hour,"claimed"),(hour+38*60,hour,"pending"),(hour+57*60,hour,"pending")])
            self.assertEqual(scheduler.plan(now),[hour+18*60,hour+38*60,hour+50*60])
            with scheduler.connection() as db:
                self.assertEqual(db.execute("select status from schedule where due=?",(hour+18*60,)).fetchone()[0],"claimed")
