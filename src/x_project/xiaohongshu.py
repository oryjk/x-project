"""Fail-closed visible Xiaohongshu image-note adapter, unverified on real devices.

Only one PNG/JPEG cover is imported via standard ACTION_SEND/MediaStore. Android
SEND_MULTIPLE needs a Parcelable ArrayList<Uri>, which adb am cannot construct;
we deliberately refuse multiple covers rather than invent intent extras. Apps
may ignore EXTRA_TITLE/TEXT, omit stable account identity, or expose different
accessibility layouts: these cases stop, without typing or guessing coordinates.
The caller owns the inherited device session and durable Store submitting/unknown
state. Existing editors are preview-only: sending requires a new import during
that send call. A digest binds the requested local bytes and fields to that
import; it cannot identify visible thumbnails or detect a human changing them.
An instance also cannot submit twice after any attempted publish tap.
"""
from pathlib import Path
import hashlib
import re
import time
import uuid

from .android import DeviceError, PhonePoster, _root, _same_text

PACKAGE = "com.xingin.xhs"
PUBLISH = {"发布", "发布笔记"}
PROGRESS = {"下一步", "完成"}


def _nodes(root):
    return [node for node in root.iter("node") if node.get("package") == PACKAGE]


def _labels(node):
    return {node.get(key, "").strip() for key in ("text", "content-desc")}


def _active(root, node):
    parents = {child: parent for parent in root.iter() for child in parent}
    while node is not None:
        if node.get("enabled") == "false" or node.get("visible-to-user") == "false":
            return False
        node = parents.get(node)
    return True


def _account(root, expected):
    identities = set()
    for node in _nodes(root):
        if not _active(root, node):
            continue
        for label in _labels(node):
            match = re.fullmatch(r"小红书号\s*[:：]\s*([A-Za-z0-9_.-]+)", label)
            if match:
                identities.add(match[1])
    if identities != {expected}:
        raise DeviceError("无法核对当前小红书号，或账号与配置不一致；请在可见界面核对账号")


def _target(root, labels):
    targets = [node for node in _nodes(root) if _labels(node) & labels]
    if len(targets) != 1:
        raise DeviceError("无法唯一识别小红书操作按钮，停止操作")
    parents = {child: parent for parent in root.iter() for child in parent}
    current = targets[0]
    button = None
    while current is not None:
        if current.tag == "node" and (current.get("enabled") == "false" or
                                       current.get("visible-to-user") == "false"):
            raise DeviceError("小红书操作按钮不可用或不可见")
        if button is None and current.get("clickable") == "true":
            button = current
        current = parents.get(current)
    if button is None or button.get("enabled") != "true" or button.get("package") != PACKAGE:
        raise DeviceError("小红书操作按钮没有确认可用的点击容器")
    bounds = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", button.get("bounds", ""))
    if not bounds:
        raise DeviceError("小红书操作按钮位置无法识别")
    x1, y1, x2, y2 = map(int, bounds.groups())
    if x2 <= x1 or y2 <= y1:
        raise DeviceError("小红书操作按钮不在可见区域")
    return (x1 + x2) // 2, (y1 + y2) // 2


def _inspect_fields(root, draft, account):
    nodes = _nodes(root)
    _account(root, account)
    editors = [node for node in nodes if node.get("class") == "android.widget.EditText"]
    if len(editors) != 2:
        raise DeviceError("无法识别小红书标题和正文编辑框，保留当前草稿")
    matched_fields = set()
    for labels, expected in (({"标题", "添加标题", "填写标题会有更多赞哦"}, draft.title),
                              ({"正文", "添加正文"}, draft.text)):
        fields = [node for node in editors if ({node.get("content-desc", ""), node.get("hint", "")} & labels)]
        if len(fields) != 1 or not _same_text(fields[0].get("text", ""), expected):
            raise DeviceError("小红书标题或正文全文不一致，保留当前草稿并停止提交")
        if fields[0] in matched_fields:
            raise DeviceError("小红书标题和正文编辑框角色不明确")
        matched_fields.add(fields[0])
        if fields[0].get("enabled") != "true" or not _active(root, fields[0]):
            raise DeviceError("小红书编辑框不可用或不可见")
    counts = []
    for node in nodes:
        if not _active(root, node):
            continue
        for label in _labels(node):
            match = re.fullmatch(r"(?:已选(?:择)?\s*(\d+)\s*张|共\s*(\d+)\s*张图片)", label)
            if match:
                counts.append(int(match[1] or match[2]))
    if not counts or set(counts) != {len(draft.images)}:
        raise DeviceError("无法核对小红书已选图片数量，停止提交")


def inspect_note(xml, draft, account):
    """Return only a unique effectively enabled publish control's visible center."""
    root = _root(xml)
    _inspect_fields(root, draft, account)
    return _target(root, PUBLISH)


def publication_receipt(xml):
    root = _root(xml)
    if any(node.get("class") == "android.widget.EditText" for node in _nodes(root)):
        return False
    # A matching phrase in the feed is not a receipt. No profile heuristic here.
    return any(_active(root, node) and node.get("class") == "android.widget.Toast" and
               node.get("text", "").strip().rstrip("。.!！") in {"发布成功", "笔记发布成功"}
               for node in _nodes(root))


def _cover(draft):
    if (getattr(draft, "platform", None) != "xiaohongshu" or
            not isinstance(getattr(draft, "title", None), str) or
            not draft.title.strip() or len(draft.title) > 20 or
            not isinstance(draft.text, str) or not draft.text.strip() or len(draft.text) > 1000 or
            "\x00" in draft.title + draft.text):
        raise DeviceError("小红书图文标题或正文格式不正确")
    images = getattr(draft, "images", None)
    if not isinstance(images, list) or not 1 <= len(images) <= 9:
        raise DeviceError("小红书图文需要 1–9 张本机 PNG/JPEG 图片")
    if len(images) != 1:
        raise DeviceError("当前可验证的 ADB 分享路径仅支持一张封面；多图尚未适配")
    try:
        path = Path(images[0]).resolve(strict=True)
        if not path.is_file() or not 32 <= path.stat().st_size <= 20 * 1024 * 1024:
            raise ValueError
        with path.open("rb") as stream:
            header = stream.read(24)
            stream.seek(-12, 2)
            tail = stream.read()
        if (path.suffix.lower() == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n") and
                header[12:16] == b"IHDR" and tail[4:8] == b"IEND"):
            return path, "image/png"
        if path.suffix.lower() in {".jpg", ".jpeg"} and header.startswith(b"\xff\xd8\xff") and tail.endswith(b"\xff\xd9"):
            return path, "image/jpeg"
    except (OSError, TypeError, ValueError):
        pass
    raise DeviceError("封面必须是可读取的本机 PNG/JPEG 文件，单张不超过20MB")


def _note_signature(draft):
    path, mime = _cover(draft)
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise DeviceError("封面无法读取，停止提交") from None
    return (draft.platform, draft.title, draft.text, tuple(draft.images), str(path), mime, digest)


class XiaohongshuPoster(PhonePoster):
    def prepare(self, text):
        raise DeviceError("小红书必须使用图文草稿 prepare_note")

    def send(self, text, body, before_submit):
        raise DeviceError("小红书必须使用图文草稿 send_note")

    def _share_cover(self, path, mime, draft):
        remote = f"/sdcard/Pictures/x-project-{uuid.uuid4().hex}{path.suffix.lower()}"
        self.adb.run(["push", str(path), remote])
        result = self.adb.shell(["content", "insert", "--uri", "content://media/external/images/media",
                                 "--bind", "_data:s:" + remote, "--bind", "mime_type:s:" + mime])
        match = re.fullmatch(r"\s*Inserted (content://media/external/images/media/\d+)\s*", result)
        if not match:
            raise DeviceError("系统未返回可分享的图片 URI，停止操作；请检查媒体库权限")
        # Keep the imported file: deleting it can invalidate an app's saved draft.
        result = self.adb.shell(["am", "start", "-W", "-a", "android.intent.action.SEND",
                                 "-t", mime, "-p", PACKAGE, "--grant-read-uri-permission",
                                 "--eu", "android.intent.extra.STREAM", match[1],
                                 "--es", "android.intent.extra.TITLE", draft.title,
                                 "--es", "android.intent.extra.TEXT", draft.text])
        if re.search(r"error|exception|permission denial", result, re.I):
            raise DeviceError("小红书不支持当前图片分享入口，停止操作")

    def _open_account(self, xml, draft):
        """At most one standard launch and one semantic profile-tab action."""
        root = _root(xml)
        if not any(_active(root, node) for node in _nodes(root)):
            result = self.adb.shell(["am", "start", "-W", "-a", "android.intent.action.MAIN",
                                     "-c", "android.intent.category.LAUNCHER", "-p", PACKAGE])
            if re.search(r"error|exception|permission denial|unable to resolve|does not exist", result, re.I):
                raise DeviceError("小红书启动失败，停止操作")
            time.sleep(.5)
            xml = self.adb.dump()
            root = _root(xml)
        # Launch can restore a background editor. Never leave or overwrite it.
        if any(node.get("class") == "android.widget.EditText" for node in _nodes(root)):
            _inspect_fields(root, draft, self.account)
            return xml
        identities_visible = any(label.startswith("小红书号")
                                 for node in _nodes(root) if _active(root, node)
                                 for label in _labels(node))
        if not identities_visible:
            x, y = _target(root, {"我"})
            self.adb.shell(["input", "tap", str(x), str(y)])
            time.sleep(.3)
            xml = self.adb.dump()
            root = _root(xml)
            if any(node.get("class") == "android.widget.EditText" for node in _nodes(root)):
                _inspect_fields(root, draft, self.account)
                return xml
        _account(root, self.account)
        return xml

    def prepare_note(self, draft):
        self._verified_import = None
        path, mime = _cover(draft)  # Reject unsupported media before any device I/O.
        expected = _note_signature(draft)
        imported = False
        try:
            xml = self.adb.dump()
            root = _root(xml)
            if not any(node.get("class") == "android.widget.EditText" for node in _nodes(root)):
                xml = self._open_account(xml, draft)
                root = _root(xml)
                if not any(node.get("class") == "android.widget.EditText" for node in _nodes(root)):
                    self._share_cover(path, mime, draft)
                    imported = True
                    xml = self.adb.dump()
            for _ in range(3):
                root = _root(xml)
                _inspect_fields(root, draft, self.account)
                if any(_labels(node) & PUBLISH for node in _nodes(root)):
                    target = _target(root, PUBLISH)
                    if imported:
                        if _note_signature(draft) != expected:
                            raise DeviceError("导入期间封面或草稿发生变化，停止操作")
                        self._verified_import = expected
                    return target
                x, y = _target(root, PROGRESS)
                self.adb.shell(["input", "tap", str(x), str(y)])
                time.sleep(.3)
                xml = self.adb.dump()
            raise DeviceError("小红书编辑流程尚未适配，保留草稿并停止操作")
        except DeviceError:
            # Never propagate arbitrary device output into a caller's status/log.
            raise DeviceError("小红书图文预览未通过账号、全文、图片或按钮核验；已保留草稿，未提交") from None

    def _current_note(self, draft):
        try:
            return inspect_note(self.adb.dump(), draft, self.account)
        except DeviceError:
            self._verified_import = None
            raise DeviceError("小红书提交前界面核验失败，停止操作") from None

    def _require_import(self, draft):
        signature = getattr(self, "_verified_import", None)
        try:
            current = _note_signature(draft) if signature is not None else None
        except DeviceError:
            self._verified_import = None
            raise
        if signature is None or signature != current:
            self._verified_import = None
            raise DeviceError("当前笔记没有本次提交的封面导入核验；请人工关闭现有草稿后重新导入，禁止直接发送已有编辑器")

    def send_note(self, draft, before_submit):
        if getattr(self, "_submission_attempted", False):
            raise DeviceError("本实例已尝试提交小红书笔记，禁止重发；须先人工核对结果")
        self._verified_import = None
        self.prepare_note(draft)
        self._require_import(draft)
        self._current_note(draft)
        self._require_import(draft)
        before_submit()  # Caller durably records submitting and checks authorization.
        x, y = self._current_note(draft)
        self._require_import(draft)
        self._submission_attempted = True  # Set BEFORE the irreversible command.
        try:
            self.adb.shell(["input", "tap", str(x), str(y)])
            for _ in range(3):
                time.sleep(.8)
                if publication_receipt(self.adb.dump()):
                    return "sent", "小红书显示明确发布成功回执"
        except DeviceError:
            pass
        return "unknown", "已尝试点击发布，但无法确认回执；请人工核对后 resolve，禁止自动重发"
