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
