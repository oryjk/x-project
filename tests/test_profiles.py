import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from x_project.config import Config


class ProfilesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.values = {
            'X_DEVICES': 'alpha,beta', 'X_PROFILES': 'one,two,red',
            'X_DEVICE_ALPHA_ADB_SERIAL': 'phone-a', 'X_DEVICE_ALPHA_DEVICE_ID': 'bound-a',
            'X_DEVICE_BETA_ADB_HOST': '192.0.2.2', 'X_DEVICE_BETA_ADB_PORT': '39001',
            'X_DEVICE_BETA_DEVICE_ID': 'bound-b',
            'X_PROFILE_ONE_DEVICE': 'alpha', 'X_PROFILE_ONE_PLATFORM': 'x', 'X_PROFILE_ONE_ACCOUNT': 'tester1',
            'X_PROFILE_TWO_DEVICE': 'beta', 'X_PROFILE_TWO_PLATFORM': 'x', 'X_PROFILE_TWO_ACCOUNT': 'tester2',
            'X_PROFILE_RED_DEVICE': 'alpha', 'X_PROFILE_RED_PLATFORM': 'xiaohongshu', 'X_PROFILE_RED_ACCOUNT': 'red_user',
            'X_DEVICE_ALPHA_PHONE_PIN': '111111', 'X_DEVICE_BETA_PHONE_PIN': '222222',
        }
        env = patch.dict(os.environ, self.values, clear=True); env.start(); self.addCleanup(env.stop)

    def test_devices_map_to_accounts_and_new_profiles_are_disabled(self):
        one, two, red = Config.load_all(self.root)
        self.assertEqual((one.serial, two.serial), ('phone-a', '192.0.2.2:39001'))
        self.assertEqual((one.platform, red.platform), ('x', 'xiaohongshu'))
        self.assertEqual((red.hourly_limit, red.min_interval_minutes), (1, 30))
        self.assertTrue(all(not c.publish_enabled for c in (one, two, red)))
        self.assertEqual(one.device_state, red.device_state)
        self.assertNotEqual(one.state, red.state)
        self.assertNotEqual(one.state, two.state)
        self.assertNotIn('111111', repr(one)); self.assertNotIn('222222', repr(two))

    def test_profile_selection_is_required_for_ambiguous_target(self):
        with self.assertRaisesRegex(ValueError, 'profile'):
            Config.load(self.root)
        self.assertEqual(Config.load(self.root, 'two').account, 'tester2')

    def test_account_state_survives_profile_rename_and_other_account_is_independent(self):
        one = Config.load(self.root, 'one'); one.set_publish_enabled(True)
        state = one.state
        with patch.dict(os.environ, {'X_PROFILES': 'renamed,two', 'X_PROFILE_RENAMED_PLATFORM': 'x', 'X_PROFILE_RENAMED_DEVICE': 'alpha', 'X_PROFILE_RENAMED_ACCOUNT': 'tester1'}):
            renamed = Config.load(self.root, 'renamed')
            self.assertEqual(renamed.state, state)
            self.assertTrue(renamed.publish_enabled)
            self.assertFalse(Config.load(self.root, 'two').publish_enabled)

    def test_duplicate_platform_account_is_rejected(self):
        with patch.dict(os.environ, {'X_PROFILE_TWO_ACCOUNT': '@TESTER1'}):
            with self.assertRaisesRegex(ValueError, '重复'):
                Config.load_all(self.root)

    def test_cadence_is_per_account_and_invalid_values_fail_closed(self):
        with patch.dict(os.environ, {'X_PROFILE_TWO_HOURLY_LIMIT': '2', 'X_PROFILE_TWO_MIN_INTERVAL_MINUTES': '20'}):
            one, two, _ = Config.load_all(self.root)
            self.assertEqual((one.hourly_limit, two.hourly_limit, two.min_interval_minutes), (3, 2, 20))
        for key, value in [('HOURLY_LIMIT', '4'), ('MIN_INTERVAL_MINUTES', '2'), ('ENABLED', ''), ('PLATFORM', 'unknown')]:
            with self.subTest(key=key), patch.dict(os.environ, {'X_PROFILE_ONE_' + key: value}):
                with self.assertRaises(ValueError): Config.load_all(self.root)

    def test_unknown_device_and_path_traversal_names_are_rejected(self):
        for patch_values in ({'X_PROFILE_ONE_DEVICE': 'missing'}, {'X_PROFILES': '../escape'}):
            with self.subTest(values=patch_values), patch.dict(os.environ, patch_values):
                with self.assertRaises(ValueError): Config.load_all(self.root)

    def test_existing_account_adopts_legacy_state_and_freeze(self):
        with patch.dict(os.environ, {'X_ACCOUNT': '@tester1', 'X_ADB_SERIAL': 'phone-a', 'X_DEVICE_ID': 'bound-a'}):
            (self.root / '.state').mkdir()
            (self.root / '.state' / 'publish-enabled').write_text('false\n')
            one = Config.load(self.root, 'one')
            self.assertEqual(one.state, self.root / '.state')
            self.assertEqual(one.device_state, self.root / '.state')
            self.assertFalse(one.publish_enabled)

    def test_device_values_do_not_fall_back_to_other_device_or_global_secret(self):
        with patch.dict(os.environ, {'X_PHONE_PIN': '333333', 'X_DEVICE_ALPHA_ADB_PAIR_CODE': '123456'}):
            alpha = Config.load(self.root, 'one'); beta = Config.load(self.root, 'two')
            self.assertEqual(alpha.device_values()['X_PHONE_PIN'], '111111')
            self.assertEqual(beta.device_values()['X_PHONE_PIN'], '222222')
            self.assertEqual(alpha.device_values()['X_ADB_PAIR_CODE'], '123456')
            self.assertFalse(beta.device_values().get('X_ADB_PAIR_CODE'))

    def test_legacy_namespace_adoption_survives_removing_obsolete_global_keys(self):
        with patch.dict(os.environ, {'X_ACCOUNT': 'tester1', 'X_DEVICE_ID': 'bound-a'}):
            one = Config.load(self.root, 'one')
            (one.device_state / 'unlock.blocked').touch()
            one.set_publish_enabled(False)
            from datetime import datetime, timezone
            from x_project.store import Store
            from x_project.content import Draft
            store = Store(one.state / 'queue.sqlite3')
            now = datetime.now(timezone.utc)
            post_id = store.enqueue(Draft('待核对正文', 'uncertain', []), now)
            store.claim(now, post_id); store.submitting(post_id, now); store.finish(post_id, 'unknown')
            state, device_state = one.state, one.device_state
        # Only old global keys disappear; the account/device registrations remain unchanged.
        one = Config.load(self.root, 'one')
        self.assertEqual(one.state, state)
        self.assertEqual(one.device_state, device_state)
        self.assertTrue((one.device_state / 'unlock.blocked').exists())
        self.assertFalse(one.publish_enabled)
        self.assertTrue(Store(one.state / 'queue.sqlite3').has_unresolved())

    def test_unbound_device_aliases_share_endpoint_lock_namespace(self):
        with patch.dict(os.environ, {'X_DEVICE_ALPHA_DEVICE_ID': '', 'X_DEVICE_BETA_DEVICE_ID': '',
                                     'X_DEVICE_BETA_ADB_SERIAL': 'phone-a'}):
            one = Config.load(self.root, 'one'); two = Config.load(self.root, 'two')
            self.assertEqual(one.device_state, two.device_state)
