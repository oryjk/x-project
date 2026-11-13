from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from x_project.android import PhonePoster, DeviceError
from x_project.cli import main, install_timer
from x_project.config import Config, parse_dotenv


class DeploymentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {'PATH': os.environ['PATH'], 'HOME': str(self.root)}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.adb = self.root / 'fake-adb'
        self.adb.write_text('''#!/usr/bin/env python3
import json, sys
from pathlib import Path
root = Path(__file__).parent
args = sys.argv[1:]
with (root / 'adb-calls.jsonl').open('a') as out:
    out.write(json.dumps({'args': args, 'stdin': sys.stdin.read() if args[0] == 'pair' else ''}) + '\\n')
if (root / 'fail-adb').exists():
    print('123456 leaked stderr', file=sys.stderr)
    raise SystemExit(1)
if 'get-state' in args: print('device')
elif 'ro.serialno' in args: print('bound-device')
else: print('connected')
''')
        self.adb.chmod(0o700)
        self.env_text = f'X_ACCOUNT=tester\nX_ADB_PATH="{self.adb}"\nX_ADB_HOST=192.0.2.5\nX_ADB_PORT=37001\nX_ADB_PAIR_PORT=37002\nX_ADB_PAIR_CODE=123456\n'
        self.write_env(self.env_text)

    def write_env(self, text):
        env = self.root / '.env'
        env.write_text(text)
        env.chmod(0o600)

    def command(self, name):
        output = StringIO()
        with redirect_stdout(output):
            code = main(['--root', str(self.root), name])
        return code, output.getvalue()

    def calls(self):
        path = self.root / 'adb-calls.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_pair_reads_code_from_dotenv_and_sends_it_only_on_stdin(self):
        code, output = self.command('pair')
        self.assertEqual(code, 0, output)
        self.assertEqual(self.calls(), [{'args': ['pair', '192.0.2.5:37002'], 'stdin': '123456\n'}])
        self.assertNotIn('123456', output)
        events = self.root / '.state' / 'events.jsonl'
        if events.exists():
            self.assertNotIn('123456', events.read_text())

    def test_pair_failure_hides_adb_output_and_never_retries(self):
        (self.root / 'fail-adb').touch()
        code, output = self.command('pair')
        self.assertEqual(code, 1)
        self.assertNotIn('123456', output)
        self.assertNotIn('leaked stderr', output)
        self.assertEqual(len(self.calls()), 1)

    def test_connect_can_discover_identity_without_binding_and_rejects_mismatch(self):
        code, output = self.command('connect')
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output)['device_id'], 'bound-device')
        self.write_env(self.env_text + 'X_DEVICE_ID=wrong-device\n')
        code, output = self.command('connect')
        self.assertEqual(code, 1)
        self.assertIn('绑定', output)
        self.assertFalse((self.root / 'config.toml').exists())

    def test_exported_lock_secret_overrides_dotenv_without_being_added_to_config(self):
        self.write_env(self.env_text + 'X_ALLOW_UNLOCK=true\npwd=111111\n')
        config = Config.load(self.root)
        poster = PhonePoster(None, config.account, self.root / '.env', self.root / '.state', True)
        with patch.dict(os.environ, {'X_PHONE_PIN': '222222'}):
            self.assertEqual(poster._credential(), '222222')
        self.assertNotIn('111111', repr(config))
        self.assertNotIn('222222', repr(config))

    def test_exported_secret_works_without_dotenv(self):
        (self.root / '.env').unlink()
        poster = PhonePoster(None, 'tester', self.root / '.env', self.root / '.state', True, 'pattern')
        with patch.dict(os.environ, {'X_PHONE_PATTERN': '12589'}):
            self.assertEqual(poster._credential(), '12589')

    def test_timer_and_disable_work_without_toml_and_preserve_env(self):
        self.write_env(self.env_text + 'X_DEVICE_ID=bound-device\n')
        config = Config.load(self.root)
        with patch('x_project.cli.Path.home', return_value=self.root), patch('x_project.cli._systemctl', return_value='active'), patch.dict(os.environ, {'X_ACCOUNT': 'tester', 'X_PHONE_PIN': '222222', 'X_ADB_PAIR_CODE': '123456', 'CODEX_HOME': '/opt/codex-auth'}):
            self.assertEqual(install_timer(config, True)['timer'], 'enabled')
        self.assertTrue(Config.load(self.root).publish_enabled)
        runtime = self.root / '.state' / 'runtime.env'
        values = parse_dotenv(runtime.read_text())
        self.assertEqual(values['X_ACCOUNT'], 'tester')
        self.assertEqual(values['X_PHONE_PIN'], '222222')
        self.assertEqual(values['CODEX_HOME'], '/opt/codex-auth')
        self.assertEqual(values['PATH'], os.environ['PATH'])
        self.assertNotIn('X_ADB_PAIR_CODE', values)
        self.assertEqual(runtime.stat().st_mode & 0o777, 0o600)
        with patch('x_project.cli._systemctl', return_value='inactive'):
            code, output = self.command('disable')
        self.assertEqual(code, 0, output)
        self.assertFalse(Config.load(self.root).publish_enabled)
        self.assertEqual((self.root / '.env').read_text(), self.env_text + 'X_DEVICE_ID=bound-device\n')
        self.assertFalse((self.root / 'config.toml').exists())

    def test_status_runs_from_fresh_env_only_installation(self):
        code, output = self.command('status')
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output)['account'], 'tester')
        self.assertNotIn('123456', output)
        self.assertEqual(self.calls(), [])

    def test_doctor_reports_missing_dependencies_without_touching_phone(self):
        self.write_env(self.env_text + 'X_CODEX_PATH=/missing/codex\nX_DEVICE_ID=bound-device\n')
        code, output = self.command('doctor')
        self.assertEqual(code, 1)
        self.assertIn('codex', json.loads(output)['missing_dependencies'])
        self.assertEqual(self.calls(), [])

    def test_dotenv_transport_settings_do_not_export_phone_secrets(self):
        self.write_env(self.env_text + 'CODEX_HOME=/opt/test-auth\nHTTPS_PROXY=http://127.0.0.1:9999\nX_PHONE_PIN=123456\n')
        code, output = self.command('status')
        self.assertEqual(code, 0, output)
        self.assertEqual(os.environ['CODEX_HOME'], '/opt/test-auth')
        self.assertEqual(os.environ['HTTPS_PROXY'], 'http://127.0.0.1:9999')
        self.assertNotIn('X_PHONE_PIN', os.environ)
        self.assertNotIn('X_ADB_PAIR_CODE', os.environ)

    def test_downloaded_checkout_runs_from_another_directory_without_toml(self):
        checkout = self.root / 'download with spaces %'
        checkout.mkdir()
        project = Path(__file__).resolve().parents[1]
        shutil.copy(project / 'x.py', checkout / 'x.py')
        shutil.copytree(project / 'src', checkout / 'src', ignore=shutil.ignore_patterns('__pycache__'))
        env = checkout / '.env'
        env.write_text(self.env_text)
        env.chmod(0o600)
        run = subprocess.run([sys.executable, str(checkout / 'x.py'), 'status'], cwd=self.root,
                             capture_output=True, text=True, env=dict(os.environ), timeout=10)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(json.loads(run.stdout)['account'], 'tester')
        self.assertTrue((checkout / '.state' / 'schedule.sqlite3').exists())
        self.assertFalse((checkout / 'config.toml').exists())
        self.assertEqual(self.calls(), [])

    def test_generated_timer_command_works_with_separate_configuration_directory(self):
        config = Config.load(self.root)
        with patch('x_project.cli.Path.home', return_value=self.root), patch('x_project.cli._systemctl', return_value='active'):
            install_timer(config, False)
        service = self.root / '.config' / 'systemd' / 'user' / 'x-project.service'
        command = next(line.partition('=')[2] for line in service.read_text().splitlines() if line.startswith('ExecStart='))
        run = subprocess.run(shlex.split(command), cwd=self.root, capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(json.loads(run.stdout)['status'], 'waiting')
        self.assertEqual(self.calls(), [])

    def test_disable_works_after_configuration_or_exported_variables_are_removed(self):
        Config.load(self.root).set_publish_enabled(True)
        (self.root / '.env').unlink()
        with patch('x_project.cli._systemctl', return_value='inactive'):
            code, output = self.command('disable')
        self.assertEqual(code, 0, output)
        self.assertEqual((self.root / '.state' / 'publish-enabled').read_text(), 'false\n')
        self.assertTrue((self.root / '.state' / 'cancel-version').exists())
        self.assertEqual(self.calls(), [])

    def test_timer_rejects_unserializable_exported_secrets_without_echoing_them(self):
        config = Config.load(self.root)
        with patch('x_project.cli.Path.home', return_value=self.root), patch('x_project.cli._systemctl', return_value='active'), patch.dict(os.environ, {'X_PHONE_PIN': 'sensitive\nvalue'}):
            with self.assertRaises(ValueError) as caught:
                install_timer(config, False)
        self.assertNotIn('sensitive', str(caught.exception))
