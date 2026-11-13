"""Android boundary. UI actions use verified semantics, never fixed post coordinates."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import shlex
import subprocess
import threading
import time
import unicodedata
import uuid
import xml.etree.ElementTree as ET

from .config import parse_dotenv, read_dotenv, PIN_KEYS, PATTERN_KEYS

PACKAGE = "com.twitter.android"


class DeviceError(RuntimeError):
    pass


class ComposerNotReady(DeviceError):
    pass


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


def profile_post(xml, body, account, max_age=None):
    """Match one actual post container, never unrelated feed/profile text.

    A missing max_age is used only for the duplicate preflight. Receipt checking
    requires a relative timestamp no older than the current submission window.
    """
    root = _root(xml)
    parents = {child: parent for parent in root.iter() for child in parent}
    handle = "@"+account.lstrip("@").casefold()
    for node in _nodes(root):
        if not _same_text(node.get("text", ""), body):
            continue
        container = parents.get(node)
        if container is None:
            continue
        nodes = list(container.iter("node"))
        handles = [n.get("text", "").casefold() for n in nodes
                   if re.fullmatch(r"@[A-Za-z0-9_]+", n.get("text", ""))]
        options = [n for n in nodes if n.get("content-desc", "") in ("发帖选项", "Post options", "Tweet options")]
        if handles != [handle] or len(options) != 1:
            continue
        if max_age is None:
            return True
        for n in nodes:
            text = n.get("text", "").strip()
            if text in ("· 刚刚", "· now", "· Now"):
                return True
            match = re.fullmatch(r"·\s*(\d+)\s*(秒|分钟|s|m)", text)
            if match:
                age = int(match[1]) * (60 if match[2] in ("分钟", "m") else 1)
                if age <= min(max_age, 120):
                    return True
    return False


def inspect_composer(xml, expected_text, expected_account):
    root = _root(xml)
    nodes = _nodes(root)
    editors = [n for n in nodes if n.get("class") == "android.widget.EditText"]
    if not editors:
        raise ComposerNotReady("X 编辑器还在加载")
    if len(editors) != 1 or not _same_text(editors[0].get("text", ""), expected_text):
        raise DeviceError("编辑框全文与待发文案不一致，停止提交")
    accounts = set()
    for n in nodes:
        desc = n.get("content-desc", "")
        if re.search(r"切换账号|switch account", desc, re.I):
            match = re.search(r"@([A-Za-z0-9_]+)", desc)
            if match:
                accounts.add(match.group(1).casefold())
    if not accounts:
        raise ComposerNotReady("X 发帖账号身份还未加载")
    if accounts != {expected_account.lstrip("@").casefold()}:
        raise DeviceError("当前发帖账号与配置不一致或无法识别")
    targets = [n for n in nodes if n.get("content-desc", "") in ("发帖", "发布", "Post", "Tweet")]
    if not targets:
        raise ComposerNotReady("X 发布按钮还未加载")
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
        for attempt in range(3):
            remote = f"/sdcard/x-project-{uuid.uuid4().hex}.xml"
            try:
                self.shell(["uiautomator", "dump", "--compressed", remote], timeout=18)
                xml = self.run(["exec-out", "cat", remote])
                _root(xml)
                return xml
            except DeviceError:
                if attempt == 2:
                    raise DeviceError("手机界面持续无法读取，请待界面稳定后重试") from None
                time.sleep(.5)
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
    def __init__(self, adb, account, env_file, state_dir, allow_unlock=False, unlock_method="pin"):
        if unlock_method not in ("pin", "pattern"):
            raise DeviceError("unlock_method 必须是 pin 或 pattern")
        self.unlock_method = unlock_method
        self.adb = adb
        self.account = account.lstrip("@")
        self.env_file = Path(env_file)
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.allow_unlock = allow_unlock

    def _credential(self):
        if not self.allow_unlock:
            raise DeviceError("手机已锁屏，自动解锁未配置")
        try:
            values = read_dotenv(self.env_file)
        except ValueError:
            raise DeviceError('.env 权限过宽，请先 chmod 600 .env') from None
        keys = PATTERN_KEYS if self.unlock_method == 'pattern' else PIN_KEYS
        exported = {key: os.environ[key] for key in keys if key in os.environ}
        if exported:
            values = exported
        pins = {values[key] for key in keys if values.get(key)}
        if len(pins) != 1:
            raise DeviceError("本机密码配置缺失或有冲突")
        pin = pins.pop()
        if self.unlock_method == "pattern":
            if not re.fullmatch(r"[1-9]{4,9}", pin) or len(set(pin)) != len(pin):
                raise DeviceError("图案须为 4–9 个不重复的九宫格点位，编号为 1–9")
        elif not re.fullmatch(r"[0-9]{4,16}", pin):
            raise DeviceError("当前仅支持 4–16 位数字 PIN；其他锁屏方式请手动解锁")
        return pin

    def _pin(self):
        return self._credential()

    def _pattern_bounds(self, ui):
        nodes = [n for n in ui.iter("node") if n.get("package") == "com.android.systemui"
                 and n.get("resource-id", "").split("/")[-1] in ("colorLockPatternView", "lockPatternView")]
        if len(nodes) != 1:
            return None
        match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", nodes[0].get("bounds", ""))
        return tuple(map(int, match.groups())) if match else None

    def _draw_pattern(self, pattern, bounds):
        x1, y1, x2, y2 = bounds
        if x2 <= x1 or y2 <= y1:
            raise DeviceError("图案区域无效，停止输入")
        points = []
        for digit in pattern:
            row, col = divmod(int(digit)-1, 3)
            points.append((round(x1+(col+.5)*(x2-x1)/3), round(y1+(row+.5)*(y2-y1)/3)))
        # One pointer contact, interpolated through each segment. No secret in host argv.
        start_x, start_y = points[0]
        end_x, end_y = points[-1]
        commands = ["set -e", f"trap 'input touchscreen motionevent UP {end_x} {end_y}' EXIT",
                    f"input touchscreen motionevent DOWN {start_x} {start_y}"]
        for (ax, ay), (bx, by) in zip(points, points[1:]):
            steps = max(1, (max(abs(bx-ax), abs(by-ay))+59)//60)
            for step in range(1, steps+1):
                x = round(ax+(bx-ax)*step/steps); y = round(ay+(by-ay)*step/steps)
                commands.append(f"input touchscreen motionevent MOVE {x} {y}")
        self.adb.run(["shell", "sh", "-s"], timeout=45, stdin="\n".join(commands)+"\n")

    def _unlock_ui(self):
        # Keyguard may override the normal screen timeout with a shorter one.
        # Only wake signals run during this read; stop before drawing a pattern.
        stop = threading.Event()
        def pulse():
            while not stop.wait(2):
                try:
                    self.adb.shell(["input", "keyevent", "224"], timeout=5)
                except DeviceError:
                    return
        worker = threading.Thread(target=pulse, daemon=True)
        worker.start()
        try:
            return _root(self.adb.dump())
        finally:
            stop.set()
            worker.join()

    def unlock(self):
        if not self.adb.locked():
            return
        if not getattr(self.adb, "device_id", ""):
            raise DeviceError("自动解锁前必须绑定物理设备")
        marker = self.state_dir / "unlock.blocked"
        if marker.exists():
            raise DeviceError("上次自动解锁未成功，须核对后运行 unlock-reset")
        pin = self._credential()
        size = re.findall(r"(\d+)x(\d+)", self.adb.shell(["wm", "size"]))
        if not size:
            raise DeviceError("无法确认手机尺寸")
        width, height = map(int, size[-1])
        self.adb.shell(["input", "keyevent", "82"])
        time.sleep(0.4)
        if not self.adb.locked():
            return
        ui = self._unlock_ui()
        if self.unlock_method == "pattern":
            bounds = self._pattern_bounds(ui)
            if bounds is None:
                self.adb.shell(["input", "swipe", str(width//2), str(height*85//100), str(width//2), str(height//4), "400"])
                time.sleep(.5)
                if not self.adb.locked():
                    return
                bounds = self._pattern_bounds(self._unlock_ui())
            if bounds is None or not (0 <= bounds[0] < bounds[2] <= width and 0 <= bounds[1] < bounds[3] <= height):
                raise DeviceError("未确认系统图案区域，不输入图案")
            marker.write_text("核对手机后手动运行 unlock-reset。\n")
            marker.chmod(0o600)
            self._draw_pattern(pin, bounds)
            for _ in range(8):
                time.sleep(.4)
                if not self.adb.locked():
                    marker.unlink(missing_ok=True)
                    return
            raise DeviceError("自动解锁未成功，已停止后续尝试")
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
            # Unlock may need multiple slow accessibility snapshots. Keep the
            # screen awake through that work too, then restore on every exit.
            self.adb.shell(["settings", "put", "system", "screen_off_timeout", "180000"])
            if not was_on:
                self.adb.shell(["input", "keyevent", "224"])
                time.sleep(1.2)
            self.unlock()
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
            return self._wait_composer(text, existing)
        result = self.adb.shell(["am", "start", "-W", "-a", "android.intent.action.SEND", "-t", "text/plain",
                                 "-n", "com.twitter.android/com.twitter.composer.ComposerActivity", "--es", "android.intent.extra.TEXT", text])
        if "Error:" in result or "Exception" in result:
            raise DeviceError("X 编辑器无法打开")
        time.sleep(0.5)
        return self._wait_composer(text)

    def _wait_composer(self, text, initial=None):
        for attempt in range(4):
            xml = initial if attempt == 0 and initial is not None else self.adb.dump()
            try:
                return inspect_composer(xml, text, self.account)
            except ComposerNotReady:
                if attempt == 3:
                    raise DeviceError("X 编辑器或账号身份持续未加载，请检查手机网络后重试") from None
                time.sleep(.5)

    def send(self, text, body, before_submit):
        # Preserve unrelated drafts before navigating to the account profile.
        existing = self.adb.dump()
        if any(n.get("class") == "android.widget.EditText" for n in _nodes(_root(existing))):
            self._wait_composer(text, existing)
        self._open_profile()
        if profile_post(self.adb.dump(), body, self.account):
            raise DeviceError("账号主页已有相同正文，停止提交以避免重复")
        self.prepare(text)
        # Refresh semantics immediately before the only irreversible tap.
        x, y = self._wait_composer(text)
        before_submit()
        started = time.monotonic()
        self.adb.shell(["input", "tap", str(x), str(y)])
        for _ in range(3):
            time.sleep(0.8)
            xml = self.adb.dump()
            nodes = _nodes(_root(xml))
            if not any(n.get("class") == "android.widget.EditText" for n in nodes) and publication_receipt(xml):
                return "sent", "X 显示明确发布成功回执"
        self._open_profile()
        if profile_post(self.adb.dump(), body, self.account, time.monotonic()-started+5):
            return "sent", "主页同一帖子核对作者、完整正文及本次提交时间；提交前未出现该正文"
        return "unknown", "已点击发布，但无法确认回执；核对 X 后运行 resolve，禁止自动重发"

    def _open_profile(self):
        result = self.adb.shell(["am", "start", "-W", "-a", "android.intent.action.VIEW", "-d",
                                 "https://x.com/"+self.account, "-p", PACKAGE])
        if "Error:" in result or "Exception" in result:
            raise DeviceError("无法打开绑定账号主页")
        time.sleep(2)
