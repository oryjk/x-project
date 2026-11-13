import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from zoneinfo import ZoneInfo

from .android import DeviceError
from .content import ContentError, utcnow
from .pipeline import Config, Pipeline, exclusive, cancel_inflight


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _systemctl(*args):
    try:
        result = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("systemd 用户任务暂时没有响应") from None
    if result.returncode:
        raise RuntimeError("systemd 用户任务操作失败，请检查 systemctl --user status x-project.timer")
    return result.stdout.strip()


def _quote_unit(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def install_timer(config, publish):
    if publish and not config.device_id:
        raise DeviceError("自动发布需要先在 config.toml 绑定真实 device_id")
    user_units = Path.home()/".config"/"systemd"/"user"
    user_units.mkdir(parents=True, exist_ok=True)
    runtime_env = config.state/"runtime.env"
    allowed = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy")
    lines = []
    for key in allowed:
        value = os.environ.get(key)
        if value is not None and "\n" not in value and "\r" not in value:
            lines.append(key+'="'+value.replace("\\", "\\\\").replace('"', '\\"')+'"')
    runtime_env.write_text("\n".join(lines)+"\n"); runtime_env.chmod(0o600)
    service = f'''# Managed by x-project
[Unit]
Description=Collect AI news and run one due X post

[Service]
Type=oneshot
WorkingDirectory={str(config.root).replace('%', '%%')}
Environment="PATH={Path.home()}/.local/bin:/usr/local/bin:/usr/bin:/bin"
EnvironmentFile=-{config.state}/runtime.env
ExecStart={_quote_unit(sys.executable)} {_quote_unit(config.root/'x.py')} tick --publish
TimeoutStartSec=600
UMask=0077
'''
    timer = '''# Managed by x-project
[Unit]
Description=Check persisted, randomized X posting schedule every minute

[Timer]
OnCalendar=*-*-* *:*:00
AccuracySec=1s
Persistent=false
Unit=x-project.service

[Install]
WantedBy=timers.target
'''
    for name, text in (("x-project.service", service), ("x-project.timer", timer)):
        target = user_units/name
        if target.exists() and "# Managed by x-project" not in target.read_text():
            raise RuntimeError("已有同名 systemd 任务，未覆盖")
        target.write_text(text)
    config_file = config.root/"config.toml"
    text = config_file.read_text()
    if not re.search(r"(?m)^publish_enabled\s*=", text):
        raise ValueError("配置缺少 publish_enabled")
    config_file.write_text(re.sub(r"(?m)^publish_enabled\s*=.*$", f"publish_enabled = {'true' if publish else 'false'}", text))
    config_file.chmod(0o600)
    _systemctl("daemon-reload")
    _systemctl("reset-failed", "x-project.service", "x-project.timer")
    _systemctl("enable", "--now", "x-project.timer")
    _systemctl("is-active", "x-project.timer")
    return {"timer": "enabled", "publish_enabled": publish, "cadence": "每小时最多3条，小时内随机安排，最少间隔8分钟，不补发", "next": _systemctl("list-timers", "x-project.timer", "--no-pager")}


def parser():
    p = argparse.ArgumentParser(description="AI/科技资讯整理与 Android X 发帖；默认预览")
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="检查连接、设备绑定及本地密码是否可用，不输出密码")
    sub.add_parser("unlock-test", help="在本机测试一次自动解锁并恢复原息屏状态")
    collect = sub.add_parser("collect", help="实时检索、改写、核对来源并加入队列")
    collect.add_argument("--count", type=int, choices=range(1, 4), default=3)
    sub.add_parser("queue", help="查看草稿及来源")
    sub.add_parser("status", help="查看随机时刻和队列状态")
    for name in ("preview", "post"):
        cmd = sub.add_parser(name, help="在手机预览，post --publish 才实际发布")
        choice = cmd.add_mutually_exclusive_group(required=True)
        choice.add_argument("--id", type=int)
        choice.add_argument("--file", type=Path)
        if name == "post": cmd.add_argument("--publish", action="store_true")
    tick = sub.add_parser("tick", help="定时任务入口，单次执行")
    tick.add_argument("--publish", action="store_true")
    enable = sub.add_parser("enable", help="安装并启用用户级定时器；--publish 开启正式自动发布")
    enable.add_argument("--publish", action="store_true")
    sub.add_parser("disable", help="停止定时器并关闭自动发布")
    reset = sub.add_parser("unlock-reset", help="核对本机 PIN 后，解除一次失败造成的保护锁定")
    resolve = sub.add_parser("resolve", help="核对 X 实际结果后处理 unknown，绝不自动重发")
    resolve.add_argument("--id", type=int, required=True)
    result = resolve.add_mutually_exclusive_group(required=True)
    result.add_argument("--sent", action="store_true")
    result.add_argument("--not-sent", action="store_true")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    def stop(_signal, _frame):
        raise SystemExit(143)
    signal.signal(signal.SIGTERM, stop)
    try:
        config = Config.load(args.root)
        # Stopping must remain possible while a long research/device job holds worker.lock.
        if args.command == "disable":
            cancel_inflight(config)
            file = config.root/"config.toml"
            file.write_text(re.sub(r"(?m)^publish_enabled\s*=.*$", "publish_enabled = false", file.read_text()))
            try:
                _systemctl("disable", "--now", "x-project.timer")
                _systemctl("stop", "x-project.service")
            except RuntimeError:
                pass
            print_json({"timer": "disabled-or-not-installed", "publish_enabled": False, "inflight_cancelled": True})
            return 0
        pipeline = Pipeline(config)
        with exclusive(config.state):
            if args.command == "doctor":
                pipeline.poster.adb.check()
                try:
                    pipeline.poster._pin(); pin_configured = True
                except DeviceError: pin_configured = False
                output = {"connected": True, "serial": config.serial, "device_bound": bool(config.device_id),
                          "account": config.account, "locked": pipeline.poster.adb.locked(), "screen_on": pipeline.poster.adb.screen_on(),
                          "pin_configured": pin_configured, "unlock_blocked": (config.state/"unlock.blocked").exists()}
            elif args.command == "unlock-test":
                with pipeline.poster.session():
                    output = {"unlocked": not pipeline.poster.adb.locked(), "note": "未执行任何发布操作"}
            elif args.command == "collect":
                output = pipeline.collect(args.count)
            elif args.command == "queue":
                output = [{"id": r["id"], "status": r["status"], "topic": r["topic"], "note": r["note"], "draft": json.loads(r["draft"])} for r in pipeline.store.rows()]
            elif args.command == "status":
                output = {"account": config.account, "serial": config.serial, "publish_enabled": config.publish_enabled,
                          "unlock_blocked": (config.state/"unlock.blocked").exists(), "queued": pipeline.store.queued_count(),
                          "schedule": [{"at": datetime.fromtimestamp(r["due"], ZoneInfo("Asia/Shanghai")).isoformat(), "status": r["status"]} for r in pipeline.schedule.upcoming(utcnow())]}
            elif args.command in ("preview", "post"):
                publish = getattr(args, "publish", False)
                if args.file is not None and not publish:
                    output = pipeline.preview_text(args.file.read_text(encoding="utf-8"))
                else:
                    post_id = args.id if args.id is not None else pipeline.add(args.file.read_text(encoding="utf-8"))
                    output = pipeline.publish(post_id) if publish else pipeline.preview(post_id)
            elif args.command == "tick":
                output = pipeline.tick(args.publish)
            elif args.command == "enable":
                pipeline.poster.adb.check()
                output = install_timer(config, args.publish)
            elif args.command == "disable":
                file = config.root/"config.toml"
                file.write_text(re.sub(r"(?m)^publish_enabled\s*=.*$", "publish_enabled = false", file.read_text()))
                output = {"timer": "disabled", "publish_enabled": False}
            elif args.command == "unlock-reset":
                (config.state/"unlock.blocked").unlink(missing_ok=True)
                output = {"unlock_blocked": False}
            elif args.command == "resolve":
                pipeline.store.resolve(args.id, args.sent)
                output = {"id": args.id, "status": pipeline.store.get(args.id)["status"]}
            else:
                raise ValueError("unsupported command")
            event = {"at": utcnow().isoformat(), "command": args.command, "result": output}
            if args.command not in ("queue", "preview"):
                with (config.state/"events.jsonl").open("a") as log:
                    log.write(json.dumps(event, ensure_ascii=False)+"\n")
            print_json(output)
        return 0
    except (DeviceError, ContentError, ValueError, OSError, RuntimeError) as error:
        # All device/model errors are normalized before reaching here; no command line or PIN echoes.
        print_json({"error": str(error)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
