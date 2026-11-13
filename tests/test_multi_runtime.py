from datetime import datetime, timedelta, timezone
from pathlib import Path
import random
import tempfile
import unittest

from x_project.schedule import Schedule
from x_project.store import Store, SubmissionBlocked
from test_content import draft


class MultiRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    def test_each_account_has_its_own_quota_and_custom_actual_submit_interval(self):
        a = Store(self.root / 'a.db', hourly_limit=1, min_interval_minutes=20)
        b = Store(self.root / 'b.db', hourly_limit=1, min_interval_minutes=20)
        for store in (a, b):
            store.enqueue(draft(), self.now)
            row = store.claim(self.now)
            self.assertIsNotNone(row)
            store.submitting(row['id'], self.now)
            store.finish(row['id'], 'sent')
            other = draft('独立消息', 'https://example.org/new'); other.topic = 'other'
            store.enqueue(other, self.now)
            self.assertIsNone(store.claim(self.now + timedelta(minutes=25)))

    def test_actual_submit_uses_account_minimum_interval(self):
        store = Store(self.root / 'a.db', hourly_limit=3, min_interval_minutes=20)
        first = store.enqueue(draft(), self.now)
        store.claim(self.now, first); store.submitting(first, self.now); store.finish(first, 'sent')
        self.assertEqual(store.next_eligible_at(self.now), self.now + timedelta(minutes=20))

    def test_schedule_respects_custom_count_gap_and_cross_hour_spacing(self):
        s = Schedule(self.root / 'schedule.db', rng=random.Random(3), hourly_limit=2, min_interval_minutes=20)
        first = s.plan(self.now); second = s.plan(self.now + timedelta(hours=1))
        self.assertEqual(len(first), 2); self.assertEqual(len(second), 2)
        joined = first + second
        self.assertTrue(all(b - a >= 1200 for a, b in zip(joined, joined[1:])))
        self.assertTrue(all(t % 3600 <= 3000 for t in joined))

    def test_zero_cadence_has_no_slots_and_reduced_limit_cancels_pending_slots(self):
        self.assertEqual(Schedule(self.root / 'off.db', hourly_limit=0).plan(self.now), [])
        path = self.root / 'schedule.db'
        Schedule(path).plan(self.now)
        s = Schedule(path, hourly_limit=1)
        self.assertEqual(len(s.plan(self.now)), 1)
        with s.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM schedule WHERE status='pending'").fetchone()[0], 1)

    def test_device_lock_is_shared_and_another_device_remains_available(self):
        from x_project.locks import device_lock
        from x_project.android import DeviceError
        with device_lock(self.root / 'phone-a'):
            with self.assertRaises(DeviceError):
                with device_lock(self.root / 'phone-a'): pass
            with device_lock(self.root / 'phone-b'): pass

    def test_device_credential_prefix_prevents_global_or_other_phone_fallback(self):
        import os
        from unittest.mock import patch
        from x_project.android import PhonePoster, DeviceError
        env = self.root / '.env'
        env.write_text('X_PHONE_PIN=333333\nX_DEVICE_A_PHONE_PIN=111111\nX_DEVICE_B_PHONE_PIN=222222\n'); env.chmod(0o600)
        with patch.dict(os.environ, {}, clear=True):
            a = PhonePoster(None, 'tester', env, self.root / 'a', True, credential_prefix='X_DEVICE_A_')
            b = PhonePoster(None, 'tester', env, self.root / 'b', True, credential_prefix='X_DEVICE_B_')
            c = PhonePoster(None, 'tester', env, self.root / 'c', True, credential_prefix='X_DEVICE_C_')
            self.assertEqual(a._credential(), '111111'); self.assertEqual(b._credential(), '222222')
            with self.assertRaises(DeviceError): c._credential()

    def test_coordinator_isolates_failed_accounts(self):
        from types import SimpleNamespace
        from x_project.coordinator import run_profiles
        from x_project.android import DeviceError
        configs = [SimpleNamespace(profile='offline', platform='x', account='one'), SimpleNamespace(profile='good', platform='xiaohongshu', account='two')]
        def operation(config):
            if config.profile == 'offline': raise DeviceError('设备离线')
            return {'status': 'waiting'}
        result = run_profiles(configs, operation)
        self.assertEqual(result[0]['status'], 'failed')
        self.assertEqual(result[1]['status'], 'waiting')
