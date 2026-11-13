from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from x_project.cli import main
from x_project.cli import install_timer
from x_project.pipeline import Config, exclusive


class CliTest(unittest.TestCase):
    def test_generated_units_are_accepted_by_systemd_parser(self):
        import shutil, subprocess
        if not shutil.which("systemd-analyze"):
            self.skipTest("systemd not installed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/"config.toml").write_text("publish_enabled = false\n")
            config = Config(root, "tester", "adb", "test", False, False, "AI", "bound")
            with patch("x_project.cli.Path.home", return_value=root), patch("x_project.cli._systemctl", return_value="active"):
                install_timer(config, False)
            units = root/".config"/"systemd"/"user"
            run = subprocess.run(["systemd-analyze", "--user", "verify", str(units/"x-project.service"), str(units/"x-project.timer")], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)

    def test_stop_persists_cancellation_even_while_an_inflight_worker_holds_the_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/"config.toml").write_text('account="tester"\nadb="adb"\nserial="test"\ndevice_id="bound"\nallow_unlock=false\npublish_enabled=true\npersona="AI"\n')
            config = Config.load(root)
            with exclusive(config.state), patch("x_project.cli._systemctl", return_value=""), redirect_stdout(StringIO()):
                self.assertEqual(main(["--root", str(root), "disable"]), 0)
            self.assertFalse(Config.load(root).publish_enabled)
            self.assertTrue((config.state/"cancel-version").read_text())
