from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from x_project.cli import main, install_timer
from x_project.config import Config, parse_dotenv


class MultiCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        values = {'PATH': os.environ['PATH'], 'HOME': str(self.root), 'X_DEVICES': 'phone', 'X_PROFILES': 'blue,red',
                  'X_DEVICE_PHONE_ADB_SERIAL': 'phone', 'X_DEVICE_PHONE_DEVICE_ID': 'bound',
                  'X_DEVICE_PHONE_PHONE_PIN': '123456', 'X_DEVICE_PHONE_ADB_PAIR_CODE': '654321',
                  'X_PROFILE_BLUE_PLATFORM': 'x', 'X_PROFILE_BLUE_ACCOUNT': 'tester', 'X_PROFILE_BLUE_DEVICE': 'phone',
                  'X_PROFILE_RED_PLATFORM': 'xiaohongshu', 'X_PROFILE_RED_ACCOUNT': 'red_id', 'X_PROFILE_RED_DEVICE': 'phone'}
        env = patch.dict(os.environ, values, clear=True); env.start(); self.addCleanup(env.stop)

    def command(self, *args):
        output = StringIO()
        with redirect_stdout(output): code = main(['--root', str(self.root), *args])
        return code, json.loads(output.getvalue())

    def test_profiles_and_all_status_are_scoped_and_do_not_expose_secrets(self):
        code, rows = self.command('profiles')
        self.assertEqual(code, 0); self.assertEqual([r['profile'] for r in rows], ['blue', 'red'])
        code, rows = self.command('--all', 'status')
        self.assertEqual(code, 0); self.assertEqual([r['platform'] for r in rows], ['x', 'xiaohongshu'])
        self.assertNotIn('123456', json.dumps(rows)); self.assertNotIn('654321', json.dumps(rows))

    def test_disabled_accounts_do_not_research(self):
        with patch('x_project.research.CodexResearcher.generate', side_effect=AssertionError('no research')):
            code, rows = self.command('--all', 'tick', '--publish')
        self.assertEqual(code, 0); self.assertTrue(all(r['status'] == 'disabled' for r in rows))

    def test_targeted_disable_keeps_other_account_and_shared_timer_running(self):
        blue, red = Config.load_all(self.root)
        blue.set_publish_enabled(True); red.set_publish_enabled(True)
        with patch('x_project.cli._systemctl', side_effect=AssertionError('do not stop shared timer')):
            code, output = self.command('--profile', 'red', 'disable')
        self.assertEqual(code, 0)
        self.assertTrue(Config.load(self.root, 'blue').publish_enabled)
        self.assertFalse(Config.load(self.root, 'red').publish_enabled)

    def test_emergency_all_disable_survives_lost_configuration(self):
        blue, red = Config.load_all(self.root)
        blue.set_publish_enabled(True); red.set_publish_enabled(True)
        with patch.dict(os.environ, {'X_PROFILES': ''}), patch('x_project.cli._systemctl', return_value='inactive'):
            code, _ = self.command('--all', 'disable')
        self.assertEqual(code, 0)
        self.assertFalse(Config.load(self.root, 'blue').publish_enabled)
        self.assertFalse(Config.load(self.root, 'red').publish_enabled)

    def test_shared_timer_carries_all_namespaces_but_no_pairing_codes(self):
        config = Config.load(self.root, 'blue')
        with patch('x_project.cli.Path.home', return_value=self.root), patch('x_project.cli._systemctl', return_value='active'):
            install_timer(config, False)
        runtime = self.root / '.state' / 'runtime.env'
        values = parse_dotenv(runtime.read_text())
        self.assertEqual(values['X_PROFILES'], 'blue,red')
        self.assertEqual(values['X_DEVICE_PHONE_PHONE_PIN'], '123456')
        self.assertNotIn('X_DEVICE_PHONE_ADB_PAIR_CODE', values)
        service = (self.root / '.config' / 'systemd' / 'user' / 'x-project.service').read_text()
        self.assertIn('--all tick --publish', service)

    def test_zero_cadence_cannot_be_enabled_accidentally(self):
        from x_project.android import DeviceError
        config = Config.load(self.root, 'blue'); config.hourly_limit = 0
        with patch('x_project.cli.Path.home', return_value=self.root), patch('x_project.cli._systemctl', return_value='active'):
            with self.assertRaises(DeviceError):
                install_timer(config, True)
        self.assertFalse((config.state / 'publish-enabled').exists())
