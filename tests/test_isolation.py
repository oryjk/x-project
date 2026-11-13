import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class IsolationTest(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "linux", "Linux Landlock is required for live research")
    def test_research_child_cannot_read_synthetic_secret_but_can_read_research_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); secret = root/"project"/".env"; secret.parent.mkdir()
            secret.write_text("synthetic-secret-do-not-read")
            work = root/"work"; work.mkdir(); (work/"evidence.txt").write_text("public source")
            wrapper = Path(__file__).resolve().parents[1]/"src"/"x_project"/"isolation.py"
            program = "from pathlib import Path; import sys; print(Path(sys.argv[1]).read_text());\ntry: Path(sys.argv[2]).read_text()\nexcept PermissionError: print('SECRET_BLOCKED')\nelse: raise SystemExit('READ_LEAK')"
            run = subprocess.run([sys.executable, str(wrapper), "--read-root", str(work), "--", sys.executable, "-c", program, str(work/"evidence.txt"), str(secret)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("public source", run.stdout)
            self.assertIn("SECRET_BLOCKED", run.stdout)
            self.assertNotIn("synthetic-secret-do-not-read", run.stdout)
