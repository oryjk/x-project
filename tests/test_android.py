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

    def test_pattern_uses_visible_grid_and_one_continuous_touch(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from x_project.android import PhonePoster
        class Phone:
            device_id = "test-bound"
            def __init__(self): self.scripts=[]; self.actions=[]
            def locked(self): return not self.scripts
            def shell(self, args, **kw):
                self.actions.append(args)
                return "1080x2376" if args[0]=="wm" else "0" if args==["am","get-current-user"] else ""
            def dump(self): return '<hierarchy><node package="com.android.systemui" resource-id="com.android.systemui:id/colorLockPatternView" bounds="[72,1086][1008,1926]"/></hierarchy>'
            def run(self,args,stdin=None,**kw): self.scripts.append(stdin)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); env=root/".env";env.write_text("pwd=12589");env.chmod(0o600)
            phone=Phone(); poster=PhonePoster(phone,"tester",env,root/"state",True,unlock_method="pattern")
            with patch("x_project.android.time.sleep"): poster.unlock()
            script=phone.scripts[0]
            self.assertEqual(script.count("motionevent DOWN"),1)
            self.assertEqual(script.count("motionevent UP"),1)
            self.assertIn("DOWN 228 1226",script)
            self.assertIn("UP 852 1786",script)
            self.assertGreater(script.count("motionevent MOVE"),4)
            self.assertFalse(any(a[:2]==["input","swipe"] for a in phone.actions))
            self.assertFalse((root/"state/unlock.blocked").exists())

    def test_pattern_rejects_invalid_paths_and_unknown_unlock_methods(self):
        import tempfile
        from pathlib import Path
        from x_project.android import PhonePoster
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);env=root/".env"
            for value in ("123", "1230", "11234", "abcdefgh"):
                env.write_text("pwd="+value);env.chmod(0o600)
                poster=PhonePoster(None,"tester",env,root,True,unlock_method="pattern")
                with self.assertRaises(DeviceError) as error: poster._credential()
                self.assertNotIn(value,str(error.exception))
            with self.assertRaises(DeviceError):
                PhonePoster(None,"tester",env,root,True,unlock_method="guess")

    def test_failed_pattern_is_latched_before_input_without_retries(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from x_project.android import PhonePoster
        class Phone:
            device_id="test-bound"
            entries=0
            def locked(self): return True
            def shell(self,args,**kw): return "1080x2376" if args[0]=="wm" else "0" if args==["am","get-current-user"] else ""
            def dump(self): return '<hierarchy><node package="com.android.systemui" resource-id="com.android.systemui:id/colorLockPatternView" bounds="[72,1086][1008,1926]"/></hierarchy>'
            def run(self,args,stdin=None,**kw):
                assert marker.exists()
                self.entries+=1
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);env=root/".env";env.write_text("pwd=12589");env.chmod(0o600)
            marker=root/"state/unlock.blocked";phone=Phone()
            poster=PhonePoster(phone,"tester",env,root/"state",True,unlock_method="pattern")
            with patch("x_project.android.time.sleep"):
                for _ in range(2):
                    with self.assertRaises(DeviceError): poster.unlock()
            self.assertEqual(phone.entries,1)

    def test_pattern_does_not_use_a_full_screen_parent_as_grid(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from x_project.android import PhonePoster
        class Phone:
            device_id="bound-test"
            def locked(self): return True
            def shell(self,args,**kw): return "1080x2376" if args[0]=="wm" else "0" if args==["am","get-current-user"] else ""
            def dump(self): return '<hierarchy><node package="com.android.systemui" resource-id="com.android.systemui:id/oplusos_kgd_security_pattern_view" bounds="[0,0][1080,2376]"/></hierarchy>'
            def run(self,*args,**kw): raise AssertionError("must not inject credentials")
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);env=root/".env";env.write_text("pwd=12589");env.chmod(0o600)
            poster=PhonePoster(Phone(),"tester",env,root/"state",True,"pattern")
            with patch("x_project.android.time.sleep"):
                with self.assertRaises(DeviceError):poster.unlock()
            self.assertFalse((root/"state/unlock.blocked").exists())

    def test_session_restores_sleep_and_timeout_after_unlock_failure(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from x_project.android import PhonePoster
        class Phone:
            device_id="bound-test"
            def __init__(self):self.actions=[]
            def check(self):pass
            def screen_on(self):return False
            def shell(self,args,**kw):self.actions.append(args);return "30000"
        with tempfile.TemporaryDirectory() as d:
            phone=Phone();poster=PhonePoster(phone,"tester",Path(d)/".env",d,True,"pattern")
            with patch.object(poster,"unlock",side_effect=DeviceError("failed")), patch("x_project.android.time.sleep"):
                with self.assertRaises(DeviceError):
                    with poster.session():self.fail("must not enter session")
            self.assertIn(["settings","put","system","screen_off_timeout","30000"],phone.actions)
            self.assertEqual(phone.actions[-1],["input","keyevent","223"])

    def test_profile_receipt_requires_same_post_author_body_and_fresh_time(self):
        from x_project.android import profile_post
        def tree(author="tester",age="· 12秒",text="完整正文"):
            return f'<hierarchy><node package="com.twitter.android"><node text="@tester"/><node><node text="@{author}"/><node text="{age}"/><node content-desc="发帖选项"/><node package="com.twitter.android" text="{text}"/></node></node></hierarchy>'
        self.assertTrue(profile_post(tree(),"完整正文","tester",20))
        self.assertFalse(profile_post(tree(author="other"),"完整正文","tester",20))
        self.assertFalse(profile_post(tree(age="· 1小时"),"完整正文","tester",20))
        self.assertFalse(profile_post(tree(age="· 50秒"),"完整正文","tester",20))
        self.assertFalse(profile_post(tree(text="截断正文"),"完整正文","tester",20))
        self.assertFalse(profile_post(tree().replace('<node text="@tester"/><node text="· 12秒"/>','<node><node text="@other"/></node><node text="· 12秒"/>'),"完整正文","tester",20))

    def test_send_uses_preflight_and_recent_profile_when_toast_was_missed(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from x_project.android import PhonePoster
        empty='<hierarchy/>'
        recent='<hierarchy><node package="com.twitter.android"><node text="@tester"/><node text="· 1秒"/><node content-desc="发帖选项"/><node package="com.twitter.android" text="完整正文"/></node></hierarchy>'
        class Phone:
            def __init__(self):
                self.ui=iter([empty,empty,xml(body="完整正文"),xml(body="完整正文"),empty,empty,empty,recent]);self.actions=[]
            def dump(self):return next(self.ui)
            def shell(self,args):self.actions.append(args);return ""
        with tempfile.TemporaryDirectory() as d:
            phone=Phone();poster=PhonePoster(phone,"tester",Path(d)/".env",d)
            def before():phone.actions.append(["before-submit"])
            with patch("x_project.android.time.sleep"):
                status,_=poster.send("完整正文","完整正文",before)
            self.assertEqual(status,"sent")
            taps=[a for a in phone.actions if a[:2]==["input","tap"]]
            self.assertEqual(len(taps),1)
            self.assertLess(phone.actions.index(["before-submit"]),phone.actions.index(taps[0]))

    def test_send_preserves_an_unrelated_user_draft(self):
        import tempfile
        from pathlib import Path
        from x_project.android import PhonePoster
        class Phone:
            def dump(self):return xml(body="用户正在写的草稿")
            def shell(self,args):raise AssertionError("must not navigate or publish")
        with tempfile.TemporaryDirectory() as d:
            poster=PhonePoster(Phone(),"tester",Path(d)/".env",d)
            with self.assertRaises(DeviceError):poster.send("正式正文","正式正文",lambda:None)

    def test_prepare_waits_for_account_identity_without_changing_or_posting(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from x_project.android import PhonePoster
        class Phone:
            calls=0
            def dump(self):
                self.calls+=1
                return xml().replace("@tester，切换账号","切换账号") if self.calls==1 else xml()
            def shell(self,args):raise AssertionError("must not change draft or tap")
        with tempfile.TemporaryDirectory() as d:
            phone=Phone();poster=PhonePoster(phone,"tester",Path(d)/".env",d)
            with patch("x_project.android.time.sleep"):
                self.assertEqual(poster.prepare("你好，AI。\n第二行"),(1001,211))
            self.assertEqual(phone.calls,2)

    def test_wrong_account_is_rejected_without_waiting_or_switching(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from x_project.android import PhonePoster
        class Phone:
            calls=0
            def dump(self):self.calls+=1;return xml(handle="other")
            def shell(self,args):raise AssertionError("must not switch or tap")
        with tempfile.TemporaryDirectory() as d:
            phone=Phone();poster=PhonePoster(phone,"tester",Path(d)/".env",d)
            with patch("x_project.android.time.sleep") as sleep:
                with self.assertRaises(DeviceError):poster.prepare("你好，AI。\n第二行")
            self.assertEqual(phone.calls,1);sleep.assert_not_called()

    def test_dump_retries_when_uiautomator_did_not_create_its_file(self):
        from x_project.android import Adb
        from unittest.mock import patch
        a=Adb("adb","phone","bound");reads=["cat: no such file", "<hierarchy/>"]
        with patch.object(a,"shell",return_value=""),patch.object(a,"run",side_effect=reads) as run,patch("x_project.android.time.sleep"):
            self.assertEqual(a.dump(),"<hierarchy/>")
            self.assertEqual(run.call_count,2)

    def test_dump_transient_errors_are_bounded(self):
        from x_project.android import Adb
        from unittest.mock import patch
        a=Adb("adb","phone","bound")
        with patch.object(a,"shell",return_value=""),patch.object(a,"run",return_value="cat: no such file") as run,patch("x_project.android.time.sleep"):
            with self.assertRaises(DeviceError):a.dump()
            self.assertEqual(run.call_count,3)

    def test_session_extends_timeout_before_unlock_so_ui_dump_cannot_put_phone_to_sleep(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from x_project.android import PhonePoster
        class Phone:
            def __init__(self):self.actions=[]
            def check(self):pass
            def screen_on(self):return False
            def shell(self,args):self.actions.append(args);return "30000"
        with tempfile.TemporaryDirectory() as d:
            phone=Phone();poster=PhonePoster(phone,"tester",Path(d)/".env",d)
            def unlock():self.assertIn(["settings","put","system","screen_off_timeout","180000"],phone.actions)
            with patch.object(poster,"unlock",side_effect=unlock),patch("x_project.android.time.sleep"):
                with poster.session():pass
            self.assertEqual(phone.actions[-1],["input","keyevent","223"])
            self.assertIn(["settings","put","system","screen_off_timeout","30000"],phone.actions)

    def test_lock_screen_swipe_starts_above_the_system_gesture_bar(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from x_project.android import PhonePoster
        class Phone:
            device_id="bound"
            def __init__(self):self.actions=[];self.entered=False;self.reads=0
            def locked(self):return not self.entered
            def shell(self,args,**kw):self.actions.append(args);return "1080x2376" if args[0]=="wm" else "0" if args==["am","get-current-user"] else ""
            def dump(self):
                self.reads+=1
                if self.reads==1:return '<hierarchy><node package="com.android.systemui" resource-id="com.android.systemui:id/keyguard_long_press"/></hierarchy>'
                return '<hierarchy><node package="com.android.systemui" resource-id="com.android.systemui:id/colorLockPatternView" bounds="[72,1086][1008,1926]"/></hierarchy>'
            def run(self,*args,**kw):self.entered=True
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);env=root/".env";env.write_text("pwd=12589");env.chmod(0o600)
            phone=Phone();poster=PhonePoster(phone,"tester",env,root/"state",True,"pattern")
            with patch("x_project.android.time.sleep"):poster.unlock()
            swipe=next(a for a in phone.actions if a[:2]==["input","swipe"])
            self.assertEqual(int(swipe[3]),2376*85//100)

    def test_lock_ui_read_keeps_screen_awake_until_snapshot_finishes(self):
        import tempfile,threading
        from pathlib import Path
        from x_project.android import PhonePoster
        wake=threading.Event()
        class Phone:
            def shell(self,args,**kw):
                if args==["input","keyevent","224"]:wake.set()
                return ""
            def dump(self):
                if not wake.wait(4):raise DeviceError("screen timed out before snapshot")
                return '<hierarchy/>'
        with tempfile.TemporaryDirectory() as d:
            poster=PhonePoster(Phone(),"tester",Path(d)/".env",d)
            self.assertEqual(poster._unlock_ui().tag,"hierarchy")
            self.assertTrue(wake.is_set())

    def test_pattern_fallback_requests_current_user_credential_when_normal_lockscreen_does_not_reveal_it(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from x_project.android import PhonePoster
        class Phone:
            device_id="bound"
            def __init__(self):self.actions=[];self.drawn=False;self.requested=False
            def locked(self):return not self.drawn
            def shell(self,args,**kw):
                self.actions.append(args)
                if args==["wm","size"]:return "1080x2376"
                if args==["am","get-current-user"]:return "0"
                if args[:3]==["cmd","lock_settings","require-strong-auth"]:self.requested=True
                return ""
            def dump(self):
                if not self.requested:return '<hierarchy><node package="com.android.systemui" resource-id="com.android.systemui:id/keyguard_long_press"/></hierarchy>'
                return '<hierarchy><node package="com.android.systemui" resource-id="com.android.systemui:id/colorLockPatternView" bounds="[72,1086][1008,1926]"/></hierarchy>'
            def run(self,*a,**kw):self.drawn=True
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);env=root/".env";env.write_text("pwd=12589");env.chmod(0o600)
            phone=Phone();poster=PhonePoster(phone,"tester",env,root/"state",True,"pattern")
            with patch("x_project.android.time.sleep"):poster.unlock()
            self.assertTrue(phone.drawn)
            self.assertIn(["cmd","lock_settings","require-strong-auth","--user","0","STRONG_AUTH_REQUIRED_AFTER_USER_LOCKDOWN"],phone.actions)
            self.assertFalse(any(a[:2]==["lock_settings","set-pattern"] for a in phone.actions))

    def test_pattern_requests_credential_before_waiting_for_accessibility_snapshot(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from x_project.android import PhonePoster
        class Phone:
            device_id="bound"
            def __init__(self):self.requested=False;self.drawn=False
            def locked(self):return not self.drawn
            def shell(self,args,**kw):
                if args==["wm","size"]:return "1080x2376"
                if args==["am","get-current-user"]:return "0"
                if args[:3]==["cmd","lock_settings","require-strong-auth"]:self.requested=True
                return ""
            def dump(self):
                assert self.requested, "must expose credential prompt before a slow snapshot"
                return '<hierarchy><node package="com.android.systemui" resource-id="com.android.systemui:id/colorLockPatternView" bounds="[72,1086][1008,1926]"/></hierarchy>'
            def run(self,*a,**kw):self.drawn=True
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);env=root/".env";env.write_text("pwd=12589");env.chmod(0o600)
            phone=Phone();poster=PhonePoster(phone,"tester",env,root/"state",True,"pattern")
            with patch("x_project.android.time.sleep"):poster.unlock()
            self.assertTrue(phone.drawn)
