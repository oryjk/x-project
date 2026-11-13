import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from x_project.pipeline import Config


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def write_env(self, text):
        path = self.root / '.env'
        path.write_text(text)
        path.chmod(0o600)

    def test_fresh_checkout_needs_only_dotenv_and_does_not_export_phone_secrets(self):
        self.write_env('X_ACCOUNT=@tester\nX_ADB_HOST=192.0.2.5\nX_ADB_PORT=37123\nX_DEVICE_ID=bound\nX_UNLOCK_METHOD=pattern\nX_ALLOW_UNLOCK=true\nX_PHONE_PATTERN=12589\n')
        config = Config.load(self.root)
        self.assertEqual((config.account, config.serial, config.device_id), ('@tester', '192.0.2.5:37123', 'bound'))
        self.assertEqual((config.adb, config.unlock_method), ('adb', 'pattern'))
        self.assertTrue(config.allow_unlock)
        self.assertFalse(config.publish_enabled)
        self.assertNotIn('X_PHONE_PATTERN', os.environ)
        self.assertNotIn('12589', repr(config))
        self.assertFalse((self.root / 'config.toml').exists())

    def test_exported_variables_work_without_any_config_file(self):
        with patch.dict(os.environ, {'X_ACCOUNT': 'tester', 'X_ADB_SERIAL': 'usb-device', 'X_ADB_PATH': '/opt/android/adb', 'X_CODEX_PATH': '/opt/codex'}):
            config = Config.load(self.root)
        self.assertEqual((config.serial, config.adb, config.codex), ('usb-device', '/opt/android/adb', '/opt/codex'))

    def test_environment_overrides_dotenv_and_legacy_toml(self):
        (self.root / 'config.toml').write_text('account="old"\nserial="old-device"\nadb="old-adb"\nallow_unlock=false\npublish_enabled=false\npersona="old"\n')
        self.write_env('X_ACCOUNT=dotenv\nX_ADB_HOST=192.0.2.9\nX_ADB_PORT=39001\nX_ALLOW_UNLOCK=true\n')
        with patch.dict(os.environ, {'X_ACCOUNT': 'exported', 'X_ADB_PORT': '39002'}):
            config = Config.load(self.root)
        self.assertEqual((config.account, config.serial, config.adb), ('exported', '192.0.2.9:39002', 'old-adb'))
        self.assertTrue(config.allow_unlock)

    def test_exported_connection_mode_overrides_dotenv_serial(self):
        self.write_env('X_ACCOUNT=tester\nX_ADB_SERIAL=old-usb-device\n')
        with patch.dict(os.environ, {'X_ADB_HOST': '192.0.2.8', 'X_ADB_PORT': '39003'}):
            self.assertEqual(Config.load(self.root).serial, '192.0.2.8:39003')

    def test_empty_safety_flags_cannot_inherit_enabled_legacy_settings(self):
        (self.root / 'config.toml').write_text('account="tester"\nserial="usb-device"\nallow_unlock=true\npublish_enabled=true\n')
        for key in ('X_ALLOW_UNLOCK', 'X_PUBLISH_ENABLED'):
            with self.subTest(key=key), patch.dict(os.environ, {key: ''}):
                with self.assertRaises(ValueError):
                    Config.load(self.root)

    def test_disable_survives_reloading_and_overrides_exported_publish_flag(self):
        self.write_env('X_ACCOUNT=tester\nX_ADB_SERIAL=usb-device\nX_PUBLISH_ENABLED=true\n')
        config = Config.load(self.root)
        original = (self.root / '.env').read_text()
        config.set_publish_enabled(False)
        with patch.dict(os.environ, {'X_PUBLISH_ENABLED': 'true'}):
            self.assertFalse(Config.load(self.root).publish_enabled)
        self.assertEqual((self.root / '.env').read_text(), original)
        config.set_publish_enabled(True)
        self.assertTrue(Config.load(self.root).publish_enabled)

    def test_bad_inputs_are_rejected_without_echoing_values(self):
        base = {'X_ACCOUNT': 'tester', 'X_ADB_HOST': '192.0.2.5', 'X_ADB_PORT': '39001'}
        for key, value in [('X_ADB_PORT', '0'), ('X_ADB_PORT', '65536'), ('X_ADB_PORT', 'secret-invalid'), ('X_ALLOW_UNLOCK', 'secret-invalid'), ('X_ACCOUNT', 'bad name'), ('X_UNLOCK_METHOD', 'unknown')]:
            with self.subTest(key=key, value=value), patch.dict(os.environ, {**base, key: value}):
                with self.assertRaises(ValueError) as caught:
                    Config.load(self.root)
                self.assertNotIn(value, str(caught.exception))

    def test_missing_required_fields_have_actionable_error(self):
        with self.assertRaisesRegex(ValueError, 'X_ACCOUNT'):
            Config.load(self.root)

    def test_private_dotenv_required_before_reading(self):
        self.write_env('X_ACCOUNT=tester\nX_ADB_SERIAL=usb-device\n')
        (self.root / '.env').chmod(0o644)
        with self.assertRaisesRegex(ValueError, '600'):
            Config.load(self.root)
