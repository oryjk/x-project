"""Android boundary. UI actions use verified semantics, never fixed post coordinates."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
import unicodedata
import uuid
import xml.etree.ElementTree as ET

PACKAGE = "com.twitter.android"


class DeviceError(RuntimeError):
    pass


def parse_dotenv(text):
    values = {}
    for line in text.splitlines():
        match = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not match:
            continue
        key, value = match.groups()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = re.sub(r"\s+#.*$", "", value)
        values[key] = value
    return values


def shell_command(args):
    return shlex.join([str(arg) for arg in args])


def protected_pin_prompt(system_ui):
    """OPlus can hide credential controls from both screenshots and accessibility.

    Only current anchored system state counts; timestamped historical logs don't.
    """
    return bool(re.search(r"(?m)^\s*mBouncerVisible=true\s*$", system_ui) and
                re.search(r"(?m)^\s*(?:mIsBouncerShowing:\s*true|isBouncerShowing\(\):\s*true)\s*$", system_ui))


def _root(xml):
    try:
        return ET.fromstring(xml)
    except ET.ParseError as e:
        raise DeviceError("手机界面数据不完整") from e


def _nodes(root):
    return [n for n in root.iter("node") if n.get("package") == PACKAGE]


def _same_text(a, b):
    return unicodedata.normalize("NFC", a.replace("\r\n", "\n")) == unicodedata.normalize("NFC", b.replace("\r\n", "\n"))


def publication_receipt(xml):
    root = _root(xml)
    parents = {c: p for p in root.iter() for c in p}
    messages = {"你的帖子已发送", "你的帖子已发布", "帖子已发送", "帖子已发布", "Your post was sent", "Your Tweet was sent", "Post sent"}
    for node in _nodes(root):
        text = node.get("text", "").strip().rstrip("。.!")
        if text not in messages:
            continue
        current = node
        while current is not None:
            if current.get("class") == "android.widget.Toast" or current.get("resource-id") in (
                "com.twitter.android:id/snackbar_text", "com.twitter.android:id/snackbar_text_view"):
                return True
            current = parents.get(current)
    return False


def inspect_composer(xml, expected_text, expected_account):
    root = _root(xml)
    nodes = _nodes(root)
    editors = [n for n in nodes if n.get("class") == "android.widget.EditText"]
    if len(editors) != 1 or not _same_text(editors[0].get("text", ""), expected_text):
        raise DeviceError("编辑框全文与待发文案不一致，停止提交")
    accounts = set()
    for n in nodes:
        desc = n.get("content-desc", "")
        if re.search(r"切换账号|switch account", desc, re.I):
            match = re.search(r"@([A-Za-z0-9_]+)", desc)
            if match:
                accounts.add(match.group(1).casefold())
    if accounts != {expected_account.lstrip("@").casefold()}:
        raise DeviceError("当前发帖账号与配置不一致或无法识别")
    targets = [n for n in nodes if n.get("content-desc", "") in ("发帖", "发布", "Post", "Tweet")]
    if len(targets) != 1:
        raise DeviceError("无法唯一识别发布按钮")
    target = targets[0]
    parents = {child: parent for parent in root.iter() for child in parent}
    node = target
    button = None
    while node is not None:
        if node.get("enabled") == "false":
            raise DeviceError("发布按钮不可用，可能超长或正在加载")
        if button is None and node.get("clickable") == "true":
            button = node
        node = parents.get(node)
    if button is None:
        raise DeviceError("发布按钮没有可点击的容器")
    bounds = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", button.get("bounds", ""))
    if not bounds:
        raise DeviceError("发布按钮位置无法识别")
    x1, y1, x2, y2 = map(int, bounds.groups())
    if x2 <= x1 or y2 <= y1:
        raise DeviceError("发布按钮不在可见区域")
    return ((x1 + x2) // 2, (y1 + y2) // 2)


class Adb:
    def __init__(self, binary, serial, device_id=""):
        self.binary = binary
        self.serial = serial
        self.device_id = device_id

    def run(self, args, timeout=20, stdin=None):
        try:
            result = subprocess.run([self.binary, "-s", self.serial, *args], input=stdin,
                                    capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise DeviceError("ADB 不可用或响应超时，请检查手机连接") from None
        if result.returncode:
            # Never interpolate subprocess args/stderr: a secret may have been entered via stdin.
            raise DeviceError(f"ADB 操作失败（退出码 {result.returncode}）")
        return result.stdout

    def shell(self, args, timeout=20):
        return self.run(["shell", shell_command(args)], timeout)

    def check(self):
        if not self.device_id:
            raise DeviceError("操作手机前必须绑定物理设备 device_id")
        try:
            self.run(["get-state"])
        except DeviceError:
            try:
                subprocess.run([self.binary, "connect", self.serial], capture_output=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                raise DeviceError("无法重新连接无线调试") from None
        if self.run(["get-state"]).strip() != "device":
            raise DeviceError("手机尚未连接或授权")
        if self.device_id and self.shell(["getprop", "ro.serialno"]).strip() != self.device_id:
            raise DeviceError("连接到的手机与绑定设备不同，停止操作")

    def dump(self):
        remote = f"/sdcard/x-project-{uuid.uuid4().hex}.xml"
        try:
            self.shell(["uiautomator", "dump", "--compressed", remote], timeout=18)
            xml = self.run(["exec-out", "cat", remote])
            _root(xml)
            return xml
        finally:
            try:
                self.shell(["rm", "-f", remote])
            except DeviceError:
                pass

    def locked(self):
        policy = self.shell(["dumpsys", "window", "policy"])
        section = policy.partition("KeyguardServiceDelegate")[2]
        match = re.search(r"\bshowing=(true|false)\b", section)
        if not match:
            # Some OPlus builds omit policy output briefly during a transition.
            windows = self.shell(["dumpsys", "window"])
            match = re.search(r"mShowingLockscreen=(true|false)", windows)
        if not match:
            raise DeviceError("无法确认锁屏状态，停止手机操作")
        return match.group(1) == "true"

    def screen_on(self):
        power = self.shell(["dumpsys", "power"])
        match = re.search(r"mWakefulness=(\w+)", power)
        if not match:
            raise DeviceError("无法确认屏幕状态")
        return match.group(1) == "Awake"


class PhonePoster:
    def __init__(self, adb, account, env_file, state_dir, allow_unlock=False):
        self.adb = adb
        self.account = account.lstrip("@")
        self.env_file = Path(env_file)
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.allow_unlock = allow_unlock

    def _pin(self):
        if not self.allow_unlock or not self.env_file.exists():
            raise DeviceError("手机已锁屏，自动解锁未配置")
        if self.env_file.stat().st_mode & 0o077:
            raise DeviceError(".env 权限过宽，请先 chmod 600 .env")
        values = parse_dotenv(self.env_file.read_text())
        keys = ("X_PHONE_PIN", "PHONE_PIN", "ANDROID_PIN", "PHONE_PASSWORD", "LOCK_SCREEN_PASSWORD", "pwd")
        pins = {values[key] for key in keys if values.get(key)}
        if len(pins) != 1:
            raise DeviceError("本机密码配置缺失或有冲突")
        pin = pins.pop()
        if not re.fullmatch(r"[0-9]{4,16}", pin):
            raise DeviceError("当前仅支持 4–16 位数字 PIN；其他锁屏方式请手动解锁")
        return pin

    def unlock(self):
        if not self.adb.locked():
            return
        if not getattr(self.adb, "device_id", ""):
            raise DeviceError("自动输入 PIN 前必须绑定物理设备")
        marker = self.state_dir / "unlock.blocked"
        if marker.exists():
            raise DeviceError("上次自动解锁未成功，须核对后运行 unlock-reset")
        pin = self._pin()
        size = re.findall(r"(\d+)x(\d+)", self.adb.shell(["wm", "size"]))
        if not size:
            raise DeviceError("无法确认手机尺寸")
        width, height = map(int, size[-1])
        self.adb.shell(["input", "keyevent", "82"])
        time.sleep(0.4)
        self.adb.shell(["input", "swipe", str(width // 2), str(height * 85 // 100), str(width // 2), str(height * 25 // 100), "400"])
        time.sleep(0.5)
        if not self.adb.locked():
            return
        ui = _root(self.adb.dump())
        fields = [n for n in ui.iter("node") if n.get("password") == "true" and "EditText" in n.get("class", "")]
        if len(fields) != 1:
            system_ui = self.adb.shell(["dumpsys", "activity", "service", "com.android.systemui/.SystemUIService"], timeout=25)
            if not getattr(self.adb, "device_id", "") or not protected_pin_prompt(system_ui):
                raise DeviceError("未确认系统密码面板，不输入密码")
        # Set the latch BEFORE entering anything. Even process termination must not retry a bad PIN.
        marker.write_text("核对手机后手动运行 unlock-reset。\n")
        marker.chmod(0o600)
        self.adb.shell(["input", "keyevent", *(["67"] * 20)])
        # Password does not appear in host argv, exceptions, logs or model input.
        commands = "input keyevent " + " ".join(str(7 + int(c)) for c in pin) + "\n"
        self.adb.run(["shell", "sh", "-s"], stdin=commands)
        time.sleep(0.6)
        if self.adb.locked():
            self.adb.shell(["input", "keyevent", "66"])
        for _ in range(8):
            time.sleep(0.4)
            if not self.adb.locked():
                marker.unlink(missing_ok=True)
                return
        raise DeviceError("自动解锁未成功，已停止后续尝试")

    @contextmanager
    def session(self):
        self.adb.check()
        was_on = self.adb.screen_on()
        timeout = self.adb.shell(["settings", "get", "system", "screen_off_timeout"]).strip()
        try:
            if not was_on:
                self.adb.shell(["input", "keyevent", "224"])
                time.sleep(1.2)
            self.unlock()
            self.adb.shell(["settings", "put", "system", "screen_off_timeout", "180000"])
            yield self
        finally:
            try:
                if timeout.isdigit():
                    self.adb.shell(["settings", "put", "system", "screen_off_timeout", timeout])
                if not was_on:
                    self.adb.shell(["input", "keyevent", "223"])
            except DeviceError:
                pass

    def prepare(self, text):
        existing = self.adb.dump()
        editors = [n for n in _nodes(_root(existing)) if n.get("class") == "android.widget.EditText"]
        if editors:
            # Only an exact preview of THIS post can be resumed. Never overwrite a user's draft.
            return inspect_composer(existing, text, self.account)
        result = self.adb.shell(["am", "start", "-W", "-a", "android.intent.action.SEND", "-t", "text/plain",
                                 "-n", "com.twitter.android/com.twitter.composer.ComposerActivity", "--es", "android.intent.extra.TEXT", text])
        if "Error:" in result or "Exception" in result:
            raise DeviceError("X 编辑器无法打开")
        time.sleep(0.5)
        return inspect_composer(self.adb.dump(), text, self.account)

    def send(self, text, body, before_submit):
        self.prepare(text)
        # Refresh semantics immediately before the only irreversible tap.
        x, y = inspect_composer(self.adb.dump(), text, self.account)
        before_submit()
        self.adb.shell(["input", "tap", str(x), str(y)])
        for _ in range(3):
            time.sleep(0.8)
            xml = self.adb.dump()
            nodes = _nodes(_root(xml))
            if not any(n.get("class") == "android.widget.EditText" for n in nodes) and publication_receipt(xml):
                return "sent", "X 显示明确发布成功回执"
        # Feed/profile text cannot prove a new post belongs to this submission.
        return "unknown", "已点击发布，但无法确认回执；核对 X 后运行 resolve，禁止自动重发"
