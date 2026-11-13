"""Synthetic UI/ADB only; these tests never contact a phone."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from x_project.android import DeviceError
from x_project.xiaohongshu import XiaohongshuPoster, inspect_note, publication_receipt

PACKAGE = "com.xingin.xhs"


def ui(title="标题", body="完整正文\nhttps://example.com/source", account="tester", count=1,
       button="发布", enabled="true", extra="", editor=True):
    root = ET.Element("hierarchy")
    app = ET.SubElement(root, "node", {"package": PACKAGE, "enabled": "true"})
    ET.SubElement(app, "node", {"text": "小红书号：" + account})
    if editor:
        for label, value in (("标题", title), ("正文", body)):
            ET.SubElement(app, "node", {"class": "android.widget.EditText", "content-desc": label,
                                      "text": value, "enabled": "true"})
        ET.SubElement(app, "node", {"text": f"已选{count}张"})
        target = ET.SubElement(app, "node", {"clickable": "true", "enabled": enabled,
                                            "bounds": "[800,100][1000,200]"})
        ET.SubElement(target, "node", {"text": button, "enabled": "true"})
    if extra:
        app.append(ET.fromstring(extra))
    for node in app.iter("node"):
        node.set("package", PACKAGE)
    return ET.tostring(root, encoding="unicode")


def feed(extra=""):
    return ui(editor=False,extra='<node text="我" clickable="true" enabled="true" bounds="[800,2000][1000,2100]">'+extra+'</node>').replace("小红书号：tester", "推荐")


class FakeAdb:
    def __init__(self, frames, fail_tap=False):
        self.frames = iter(frames)
        self.actions = []
        self.fail_tap = fail_tap
    def dump(self):
        return next(self.frames)
    def shell(self, args, **kwargs):
        self.actions.append(args)
        if self.fail_tap and args[:2] == ["input", "tap"]:
            raise DeviceError("untrusted raw adb text")
        if args[:2] == ["content", "insert"]:
            return "Inserted content://media/external/images/media/45"
        return ""
    def run(self, args, **kwargs):
        self.actions.append(args)
        return ""


class XiaohongshuTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.cover = self.root / "cover.png"
        # Small valid PNG; no optional image library required.
        import base64
        self.cover.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
        self.draft = SimpleNamespace(title="标题", text="完整正文\nhttps://example.com/source",
                                     images=[str(self.cover)], platform="xiaohongshu")

    def poster(self, phone):
        return XiaohongshuPoster(phone, "tester", self.root / "unused.env", self.root / "state")

    def test_exact_title_body_account_and_selected_count(self):
        self.assertEqual(inspect_note(ui(), self.draft, "tester"), (900, 150))
        for frame in (ui(title="其他标题"), ui(body="截断正文"), ui(account="other"), ui(count=2),
                      ui().replace("小红书号：tester", "tester"), ui().replace(PACKAGE, "other.app")):
            with self.subTest(frame=frame), self.assertRaises(DeviceError):
                inspect_note(frame, self.draft, "tester")

    def test_disabled_ancestor_and_duplicate_submit_stop(self):
        duplicate='<node text="发布" enabled="true" clickable="true" bounds="[1,1][9,9]"/>'
        for frame in (ui(enabled="false"), ui(extra=duplicate),
                      ui().replace('bounds="[800,100][1000,200]"', 'bounds="[0,0][0,0]"'),
                      ui().replace('package="com.xingin.xhs" enabled="true"', 'package="com.xingin.xhs" enabled="false"', 1)):
            with self.subTest(frame=frame), self.assertRaises(DeviceError):
                inspect_note(frame, self.draft, "tester")

    def test_existing_unrelated_draft_is_preserved_without_navigation(self):
        phone=FakeAdb([ui(body="用户自己的草稿")])
        with self.assertRaises(DeviceError): self.poster(phone).prepare_note(self.draft)
        self.assertEqual(phone.actions, [])

    def test_ready_preview_requires_no_mutation(self):
        phone=FakeAdb([ui()])
        self.assertEqual(self.poster(phone).prepare_note(self.draft), (900,150))
        self.assertEqual(phone.actions, [])

    def test_share_uses_content_uri_metadata_and_explicit_package(self):
        phone=FakeAdb([ui(editor=False), ui()])
        self.poster(phone).prepare_note(self.draft)
        self.assertTrue(any(action[0] == "push" for action in phone.actions))
        intent=next(action for action in phone.actions if action[:2] == ["am", "start"])
        self.assertIn("com.xingin.xhs", intent)
        self.assertIn("content://media/external/images/media/45", intent)
        self.assertIn("--grant-read-uri-permission", intent)
        self.assertIn(self.draft.title, intent)
        self.assertIn(self.draft.text, intent)
        self.assertFalse(any("file://" in arg for arg in intent))

    def test_share_needs_stable_account_before_media_transfer(self):
        phone=FakeAdb([ui(editor=False).replace("小红书号：tester", "昵称tester")])
        with self.assertRaises(DeviceError): self.poster(phone).prepare_note(self.draft)
        self.assertEqual(phone.actions, [])

    def test_missing_invalid_or_multiple_images_stop_before_device_access(self):
        for images in ([], [str(self.root / "missing.png")], [str(self.cover)] * 2, [str(self.cover)] * 10):
            self.draft.images=images
            with self.subTest(images=images), self.assertRaises(DeviceError):
                self.poster(FakeAdb([])).prepare_note(self.draft)
        bad=self.root / "not.png"; bad.write_text("not an image")
        self.draft.images=[str(bad)]
        with self.assertRaises(DeviceError): self.poster(FakeAdb([])).prepare_note(self.draft)

    def test_missing_or_ignored_metadata_fails_without_typing_or_publish(self):
        phone=FakeAdb([ui(editor=False), ui(title=""), ui(title="")])
        with self.assertRaises(DeviceError): self.poster(phone).prepare_note(self.draft)
        self.assertFalse(any(a[:2] in (["input", "text"], ["input", "tap"]) for a in phone.actions))

    def test_semantic_next_requires_full_fields_and_stops_on_unknown_labels(self):
        phone=FakeAdb([ui(button="下一步"), ui()])
        self.assertEqual(self.poster(phone).prepare_note(self.draft), (900,150))
        self.assertEqual(phone.actions, [["input","tap","900","150"]])
        for frame in (ui(button="继续"), ui(button="下一步", body="错误")):
            phone=FakeAdb([frame])
            with self.assertRaises(DeviceError): self.poster(phone).prepare_note(self.draft)
            self.assertEqual(phone.actions, [])

    def test_callback_before_only_irreversible_tap_and_explicit_receipt(self):
        receipt='<hierarchy><node package="com.xingin.xhs" class="android.widget.Toast" text="笔记发布成功"/></hierarchy>'
        phone=FakeAdb([ui(editor=False), ui(), ui(), ui(), receipt]); poster=self.poster(phone)
        def before(): phone.actions.append(["before-submit"])
        with patch("x_project.xiaohongshu.time.sleep"):
            self.assertEqual(poster.send_note(self.draft,before)[0], "sent")
        self.assertEqual(phone.actions[-2:], [["before-submit"],["input","tap","900","150"]])
        with self.assertRaises(DeviceError): poster.send_note(self.draft,before)

    def test_callback_failure_never_taps(self):
        phone=FakeAdb([ui(editor=False),ui(),ui()]); poster=self.poster(phone)
        def before(): raise DeviceError("configuration disabled")
        with self.assertRaises(DeviceError): poster.send_note(self.draft,before)
        self.assertFalse(any(a[:2]==["input","tap"] for a in phone.actions))

    def test_unknown_and_adb_failure_never_retry_or_leak_raw_errors(self):
        for failure in (False, True):
            phone=FakeAdb([ui(editor=False),ui(),ui(),ui(),'<hierarchy/>','<hierarchy/>','<hierarchy/>'],failure)
            poster=self.poster(phone)
            with patch("x_project.xiaohongshu.time.sleep"):
                status,note=poster.send_note(self.draft,lambda:None)
            self.assertEqual(status,"unknown")
            self.assertNotIn("untrusted",note)
            with self.assertRaises(DeviceError): poster.send_note(self.draft,lambda:None)
            self.assertEqual(len([a for a in phone.actions if a[:2]==["input","tap"]]),1)

    def test_receipt_is_not_feed_text_or_other_application(self):
        self.assertFalse(publication_receipt(ui(editor=False,extra='<node text="发布成功"/>')))
        self.assertFalse(publication_receipt('<hierarchy><node package="other" class="android.widget.Toast" text="发布成功"/></hierarchy>'))
        self.assertTrue(publication_receipt('<hierarchy><node package="com.xingin.xhs" class="android.widget.Toast" text="发布成功"/></hierarchy>'))

    def test_plain_text_entry_points_are_rejected(self):
        poster=self.poster(FakeAdb([]))
        with self.assertRaises(DeviceError): poster.prepare("text")
        with self.assertRaises(DeviceError): poster.send("text","body",lambda:None)

    def test_hidden_identity_or_fields_and_ambiguous_field_roles_are_refused(self):
        for frame in (ui().replace('text="小红书号：tester"', 'text="小红书号：tester" visible-to-user="false"'),
                      ui().replace('content-desc="正文"', 'content-desc="正文" visible-to-user="false"'),
                      ui().replace('content-desc="标题"', 'content-desc="标题" hint="正文"')):
            with self.subTest(frame=frame), self.assertRaises(DeviceError):
                inspect_note(frame,self.draft,"tester")

    def test_post_callback_change_is_checked_without_tap(self):
        phone=FakeAdb([ui(editor=False),ui(),ui(),ui(account="other")])
        poster=self.poster(phone)
        with self.assertRaises(DeviceError): poster.send_note(self.draft,lambda:None)
        self.assertFalse(any(a[:2]==["input","tap"] for a in phone.actions))

    def test_preflight_adb_failure_does_not_expose_raw_output(self):
        phone=FakeAdb([]);poster=self.poster(phone)
        frames=[ui(editor=False),ui(),DeviceError("raw sensitive stderr")]
        with patch.object(phone,"dump",side_effect=frames):
            with self.assertRaises(DeviceError) as error:poster.send_note(self.draft,lambda:None)
        self.assertNotIn("raw sensitive",str(error.exception))
        self.assertFalse(any(a[:2]==["input","tap"] for a in phone.actions))

    def test_media_uri_failure_stops_before_intent_and_hides_device_output(self):
        phone=FakeAdb([ui(editor=False)])
        original=phone.shell
        phone.shell=lambda args,**kw: "Error: raw sensitive stderr" if args[:2]==["content","insert"] else original(args,**kw)
        with self.assertRaises(DeviceError) as error:self.poster(phone).prepare_note(self.draft)
        self.assertNotIn("raw sensitive",str(error.exception))
        self.assertFalse(any(a[:2]==["am","start"] for a in phone.actions))

    def test_single_editor_cannot_claim_both_title_and_body_roles(self):
        self.draft.text=self.draft.title
        frame=ui(body="另一编辑框").replace('content-desc="标题"','content-desc="标题" hint="正文"').replace('content-desc="正文"','content-desc="其他"')
        with self.assertRaises(DeviceError):inspect_note(frame,self.draft,"tester")

    def test_hidden_toast_is_not_a_receipt(self):
        self.assertFalse(publication_receipt('<hierarchy><node package="com.xingin.xhs" class="android.widget.Toast" text="发布成功" visible-to-user="false"/></hierarchy>'))

    def test_foreign_app_launches_then_opens_verified_profile_before_sharing(self):
        foreign='<hierarchy><node package="com.twitter.android" class="android.widget.EditText" text="X草稿"/></hierarchy>'
        phone=FakeAdb([foreign,feed(),ui(editor=False),ui()])
        with patch("x_project.xiaohongshu.time.sleep"):
            self.assertEqual(self.poster(phone).prepare_note(self.draft),(900,150))
        launch=phone.actions[0]
        self.assertEqual(launch[:2],["am","start"])
        for arg in ("android.intent.action.MAIN","android.intent.category.LAUNCHER",PACKAGE):
            self.assertIn(arg,launch)
        self.assertEqual(phone.actions[1],["input","tap","900","2050"])
        self.assertEqual(phone.actions[2][0],"push")
        share=next(a for a in phone.actions if "android.intent.action.SEND" in a)
        self.assertIn(PACKAGE,share)
        self.assertFalse(any("com.twitter.android" in a for a in phone.actions))

    def test_feed_opens_profile_once_then_shares(self):
        phone=FakeAdb([feed(),ui(editor=False),ui()])
        with patch("x_project.xiaohongshu.time.sleep"):
            self.poster(phone).prepare_note(self.draft)
        self.assertEqual(phone.actions[0],["input","tap","900","2050"])
        self.assertFalse(any("android.intent.action.MAIN" in a for a in phone.actions))

    def test_wrong_profile_identity_stops_without_share_or_switch(self):
        phone=FakeAdb([feed(),ui(editor=False,account="other")])
        with patch("x_project.xiaohongshu.time.sleep"),self.assertRaises(DeviceError):
            self.poster(phone).prepare_note(self.draft)
        self.assertEqual(phone.actions,[["input","tap","900","2050"]])

    def test_launching_exposes_and_preserves_unrelated_xhs_draft(self):
        phone=FakeAdb(['<hierarchy/>',ui(body="用户的小红书草稿")])
        with patch("x_project.xiaohongshu.time.sleep"),self.assertRaises(DeviceError):
            self.poster(phone).prepare_note(self.draft)
        self.assertEqual(len(phone.actions),1)
        self.assertIn("android.intent.action.MAIN",phone.actions[0])

    def test_launching_resumes_matching_xhs_draft_without_profile_or_share(self):
        phone=FakeAdb(['<hierarchy/>',ui()])
        with patch("x_project.xiaohongshu.time.sleep"):
            self.assertEqual(self.poster(phone).prepare_note(self.draft),(900,150))
        self.assertEqual(len(phone.actions),1)
        self.assertIn("android.intent.action.MAIN",phone.actions[0])

    def test_launch_error_is_safe_and_not_retried(self):
        phone=FakeAdb(['<hierarchy/>'])
        def shell(args,**kw):
            phone.actions.append(args)
            return "Error: untrusted launch stderr"
        phone.shell=shell
        with self.assertRaises(DeviceError) as error:self.poster(phone).prepare_note(self.draft)
        self.assertNotIn("untrusted",str(error.exception))
        self.assertEqual(len(phone.actions),1)
        self.assertIn("android.intent.action.MAIN",phone.actions[0])

    def test_unknown_or_ambiguous_profile_tabs_stop_without_repeated_open(self):
        duplicate='<node text="我" clickable="true" enabled="true" bounds="[1,1][9,9]"/>'
        unknown=feed().replace('text="我"','text="未知"')
        for frame in (unknown,feed(extra=duplicate),feed().replace('enabled="true" bounds="[800,2000]', 'enabled="false" bounds="[800,2000]')):
            phone=FakeAdb([frame])
            with self.subTest(frame=frame),self.assertRaises(DeviceError):self.poster(phone).prepare_note(self.draft)
            self.assertEqual(phone.actions,[])

    def test_new_instance_cannot_submit_unowned_matching_editor(self):
        phone=FakeAdb([ui(),ui(),ui()]); poster=self.poster(phone)
        callback=[]
        with self.assertRaises(DeviceError):poster.send_note(self.draft,lambda:callback.append(True))
        self.assertEqual(phone.actions,[])
        self.assertEqual(callback,[])

    def test_preview_cover_a_then_requested_b_with_same_fields_never_submits(self):
        phone=FakeAdb([ui(editor=False),ui(),ui(),ui(),ui()]);poster=self.poster(phone)
        poster.prepare_note(self.draft)
        cover_b=self.root / "cover-b.png"
        import struct, zlib
        def chunk(kind,data):
            return struct.pack(">I",len(data))+kind+data+struct.pack(">I",zlib.crc32(kind+data))
        cover_b.write_bytes(b"\x89PNG\r\n\x1a\n"+
                            chunk(b"IHDR",struct.pack(">2I5B",1,1,8,2,0,0,0))+
                            chunk(b"IDAT",zlib.compress(b"\x00\xff\x00\x00"))+chunk(b"IEND",b""))
        self.assertNotEqual(cover_b.read_bytes(),self.cover.read_bytes())
        self.draft.images=[str(cover_b)]
        actions_before=list(phone.actions);callback=[]
        with self.assertRaises(DeviceError):poster.send_note(self.draft,lambda:callback.append(True))
        self.assertEqual(phone.actions,actions_before)
        self.assertEqual(callback,[])

    def test_same_instance_preview_also_requires_fresh_send_import(self):
        phone=FakeAdb([ui(editor=False),ui(),ui(),ui(),ui()]);poster=self.poster(phone)
        poster.prepare_note(self.draft)
        actions_before=list(phone.actions)
        with self.assertRaises(DeviceError):poster.send_note(self.draft,lambda:None)
        self.assertEqual(phone.actions,actions_before)

    def test_file_mutation_during_import_invalidates_ownership_before_callback(self):
        phone=FakeAdb([ui(editor=False),ui(),ui(),ui()]);poster=self.poster(phone)
        original=phone.run
        def mutate(args,**kw):
            result=original(args,**kw)
            if args[0]=="push":
                data=bytearray(self.cover.read_bytes());data[40]^=1;self.cover.write_bytes(data)
            return result
        phone.run=mutate;callback=[]
        with self.assertRaises(DeviceError):poster.send_note(self.draft,lambda:callback.append(True))
        self.assertEqual(callback,[])
        self.assertFalse(any(a[:2]==["input","tap"] for a in phone.actions))

    def test_file_or_draft_changes_in_callback_prevent_submit(self):
        import copy
        original=self.cover.read_bytes()
        for change in ("file","title","body","images"):
            self.cover.write_bytes(original)
            draft=copy.deepcopy(self.draft)
            phone=FakeAdb([ui(editor=False),ui(),ui(),ui()]);poster=self.poster(phone)
            def before():
                if change=="file":
                    data=bytearray(original);data[40]^=1;self.cover.write_bytes(data)
                elif change=="title":draft.title="其他标题"
                elif change=="body":draft.text="其他正文"
                else:
                    duplicate=self.root / "alias.png";duplicate.write_bytes(original)
                    draft.images=[str(duplicate)]
            with self.subTest(change=change),self.assertRaises(DeviceError):poster.send_note(draft,before)
            self.assertFalse(any(a[:2]==["input","tap"] for a in phone.actions))
