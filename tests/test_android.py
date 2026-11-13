import unittest
from x_project.android import inspect_composer, DeviceError, shell_command, parse_dotenv, protected_pin_prompt, publication_receipt

def xml(body="你好，AI。&#10;第二行", handle="tester", enabled="true", extra=""):
    import xml.etree.ElementTree as E
    root = E.fromstring(f'''<hierarchy><node package="com.twitter.android" bounds="[0,0][1140,2616]">
      <node class="android.widget.ImageView" content-desc="@{handle}，切换账号" enabled="true"/>
      <node class="android.widget.EditText" text="{body}" enabled="true" bounds="[20,400][1100,900]"/>
      <node clickable="true" enabled="{enabled}" bounds="[901,133][1101,289]">
        <node class="android.view.View" content-desc="发帖" enabled="true" bounds="[901,152][1101,269]"/>
      </node>{extra}</node></hierarchy>''')
    for n in root.iter("node"):
        if not n.get("package"): n.set("package", "com.twitter.android")
    return E.tostring(root, encoding="unicode")

class AndroidTest(unittest.TestCase):
    def test_composer_requires_full_text_correct_account_and_effective_enabled_button(self):
        target = inspect_composer(xml(), "你好，AI。\n第二行", "tester")
        self.assertEqual(target, (1001, 211))
        for tree, text, handle in ((xml(handle="other"), "你好，AI。\n第二行", "tester"), (xml(), "不同内容", "tester"), (xml(enabled="false"), "你好，AI。\n第二行", "tester")):
            with self.assertRaises(DeviceError): inspect_composer(tree, text, handle)

    def test_two_editors_or_a_dialog_cannot_be_mistaken_for_ready_composer(self):
        with self.assertRaises(DeviceError): inspect_composer(xml(extra='<node class="android.widget.EditText" text="另一草稿"/>'), "你好，AI。\n第二行", "tester")

    def test_unicode_and_shell_metacharacters_are_one_remote_argument(self):
        import shlex
        text = "你好\n引号 '\" 和 $(id) `whoami` ; %s"
        command = shell_command(["am", "start", "--es", "android.intent.extra.TEXT", text])
        self.assertEqual(shlex.split(command)[-1], text)

    def test_dotenv_password_is_literal_never_shell_evaluated(self):
        values = parse_dotenv("pwd='123456'\nOTHER=$(touch /tmp/should-not-exist)\n#comment\n")
        self.assertEqual(values["pwd"], "123456")
        self.assertEqual(values["OTHER"], "$(touch /tmp/should-not-exist)")

    def test_failed_pin_attempt_is_latched_and_never_automatically_retried(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from x_project.android import PhonePoster
        class FakeAdb:
            device_id = "bound-test-device"
            def __init__(self): self.entries = 0
            def locked(self): return True
            def shell(self, args): return "Physical size: 1080x2376" if args[0] == "wm" else ""
            def dump(self): return '<hierarchy><node class="android.widget.EditText" password="true"/></hierarchy>'
            def run(self, *args, **kwargs): self.entries += 1; return ""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); env = root/".env"; env.write_text("pwd=123456\n"); env.chmod(0o600)
            adb = FakeAdb(); poster = PhonePoster(adb, "tester", env, root/"state", True)
            with patch("x_project.android.time.sleep"):
                with self.assertRaises(DeviceError) as error: poster.unlock()
                self.assertNotIn("123456", str(error.exception))
                with self.assertRaises(DeviceError): poster.unlock()
            self.assertEqual(adb.entries, 1)

    def test_protected_pin_panel_requires_current_visible_and_showing_signals(self):
        self.assertTrue(protected_pin_prompt("  mBouncerVisible=true\n  mIsBouncerShowing: true\n"))
        self.assertFalse(protected_pin_prompt("2026-11-13 log: mBouncerVisible=true\n  mIsBouncerShowing: true\n"))
        self.assertFalse(protected_pin_prompt("mBouncerVisible=true\nmIsBouncerShowing: false"))

    def test_feed_words_are_not_a_publication_receipt(self):
        feed = '<hierarchy><node package="com.twitter.android" class="android.widget.TextView" text="Post sent"/></hierarchy>'
        toast = '<hierarchy><node package="com.twitter.android" class="android.widget.Toast" text="Your post was sent"/></hierarchy>'
        self.assertFalse(publication_receipt(feed))
        self.assertTrue(publication_receipt(toast))

    def test_device_binding_cannot_be_skipped(self):
        from x_project.android import Adb
        from unittest.mock import patch
        adb = Adb("adb", "phone", "")
        with patch.object(adb, "run", return_value="device"):
            with self.assertRaises(DeviceError): adb.check()
