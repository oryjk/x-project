import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from zoneinfo import ZoneInfo

from .android import DeviceError
from .content import ContentError, utcnow
from .pipeline import Config, Pipeline, exclusive, cancel_inflight
from .config import RUNTIME_KEYS, TIMER_KEYS, load_runtime_environment, write_private
from .coordinator import run_profiles
from .locks import device_lock
from .device_setup import pair_device, connect_device


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


def missing_dependencies(config, *, timer=False):
    missing = []
    if sys.version_info < (3, 11):
        missing.append('python3.11+')
    if sys.platform != 'linux':
        missing.append('Linux/Landlock')
    for name, binary in (('adb', config.adb), ('codex', config.codex)):
        if not shutil.which(binary):
            missing.append(name)
    if timer and not shutil.which('systemctl'):
        missing.append('systemd')
    return missing


def codex_authenticated(config):
    allowed = set(RUNTIME_KEYS) | {'HOME'}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    try:
        result = subprocess.run([config.codex, 'login', 'status'], capture_output=True,
                                env=env, timeout=15)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def install_timer(config, publish):
    if publish and config.hourly_limit == 0:
        raise DeviceError('账号额度为0，须先配置有效频率才能启用自动发布')
    if publish and not config.device_id:
        raise DeviceError("自动发布需要先配置 X_DEVICE_ID（或 config.toml 的 device_id）绑定真实设备")
    user_units = Path.home()/".config"/"systemd"/"user"
    user_units.mkdir(parents=True, exist_ok=True)
    runtime_env = config.root / '.state' / 'runtime.env'
    lines = []
    keys = set(TIMER_KEYS) | {'X_PROFILES', 'X_DEVICES'}
    keys.update(key for key in os.environ if key.startswith(('X_DEVICE_', 'X_PROFILE_')))
    for key in sorted(keys):
        if 'ADB_PAIR_' in key or key.endswith('PAIR_CODE'):
            continue
        value = os.environ.get(key)
        if value is not None:
            if any(char in value for char in '\x00\r\n'):
                raise ValueError(f'{key} 含有无法保存到定时任务的控制字符')
            lines.append(key+'="'+value.replace("\\", "\\\\").replace('"', '\\"')+'"')
    write_private(runtime_env, "\n".join(lines)+"\n")
    selector = '--all ' if config.profile else ''
    service = f'''# Managed by x-project
[Unit]
Description=Collect AI news and run one due X post

[Service]
Type=oneshot
WorkingDirectory={str(config.root).replace('%', '%%')}
EnvironmentFile={str(runtime_env).replace('%', '%%')}
ExecStart={_quote_unit(sys.executable)} {_quote_unit(Path(__file__).resolve().parents[2]/'x.py')} --root {_quote_unit(config.root)} {selector}tick --publish
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
    stop_marker = config.root / '.state' / 'global-disabled'
    if stop_marker.exists() and stop_marker.read_text().strip() == 'true':
        # Re-enabling one account must not revive other accounts after a global stop.
        for existing in Config.load_all(config.root):
            existing.set_publish_enabled(False)
    write_private(stop_marker, 'false\n')
    config.set_publish_enabled(publish)
    _systemctl("daemon-reload")
    for name in ("x-project.service", "x-project.timer"):
        if _systemctl("show", name, "--property=ActiveState", "--value") == "failed":
            _systemctl("reset-failed", name)
    _systemctl("enable", "--now", "x-project.timer")
    _systemctl("is-active", "x-project.timer")
    return {"timer": "enabled", "publish_enabled": publish, "cadence": f"每小时最多{config.hourly_limit}条，最少间隔{config.min_interval_minutes}分钟，不补发", "next": _systemctl("list-timers", "x-project.timer", "--no-pager")}


def parser():
    p = argparse.ArgumentParser(description="AI/科技资讯整理与多账号 Android 发布；默认预览")
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    selection = p.add_mutually_exclusive_group()
    selection.add_argument('--profile', help='选择配置的发布账号')
    selection.add_argument('--all', action='store_true', dest='all_profiles', help='汇总或执行所有账号')
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser('profiles', help='列出设备与发布账号映射，不显示凭据')
    sub.add_parser('pair', help='使用本机环境配置配对手机；配对码仅通过标准输入传递')
    sub.add_parser('connect', help='连接手机并显示设备序列号，已有绑定时核对身份')
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
        cmd.add_argument('--title', default='', help='小红书图文标题')
        cmd.add_argument('--image', action='append', default=[], help='本机PNG/JPEG封面（当前发布端一张）')
        if name == "post": cmd.add_argument("--publish", action="store_true")
    tick = sub.add_parser("tick", help="定时任务入口，单次执行")
    tick.add_argument("--publish", action="store_true")
    enable = sub.add_parser("enable", help="安装并启用用户级定时器；--publish 开启正式自动发布")
    enable.add_argument("--publish", action="store_true")
    sub.add_parser("disable", help="停止定时器并关闭自动发布")
    reset = sub.add_parser("unlock-reset", help="核对本机解锁凭据后，解除一次失败造成的保护锁定")
    resolve = sub.add_parser("resolve", help="核对平台实际结果后处理 unknown，绝不自动重发")
    resolve.add_argument("--id", type=int, required=True)
    result = resolve.add_mutually_exclusive_group(required=True)
    result.add_argument("--sent", action="store_true")
    result.add_argument("--not-sent", action="store_true")
    return p


def profile_summary(config):
    return {"profile": config.profile or "default", "platform": config.platform,
            "account": config.account, "device": config.device_name or "default",
            "publish_enabled": config.publish_enabled, "hourly_limit": config.hourly_limit,
            "min_interval_minutes": config.min_interval_minutes}


def disable_profiles(args):
    root = Path(args.root).resolve()
    if args.profile:
        config = Config.load(root, args.profile)
        cancel_inflight(config); config.set_publish_enabled(False)
        return {**profile_summary(config), "status": "disabled", "inflight_cancelled": True}
    # Global stop does not depend on configuration being present or valid.
    from types import SimpleNamespace
    state = root / '.state'; state.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_private(state / 'global-disabled', 'true\n')
    directories = [state, *state.glob('accounts/*/*')]
    for directory in directories:
        if directory.is_dir() and directory.resolve().is_relative_to(state.resolve()):
            cancel_inflight(SimpleNamespace(state=directory))
            write_private(directory / 'publish-enabled', 'false\n')
    try:
        _systemctl('disable', '--now', 'x-project.timer')
        _systemctl('stop', 'x-project.service')
    except RuntimeError:
        pass
    return {"timer": "disabled-or-not-installed", "publish_enabled": False, "inflight_cancelled": True}


def run_command(config, args):
    if args.command == 'tick' and config.profile and not config.publish_enabled:
        return {'status': 'disabled', 'publish_enabled': False}
    if args.command in ('pair', 'connect'):
        with exclusive(config.state), device_lock(config.device_state):
            return pair_device(config) if args.command == 'pair' else connect_device(config)
    if args.command in ('doctor', 'enable'):
        missing = missing_dependencies(config, timer=args.command == 'enable')
        if missing:
            return {'ready': False, 'status': 'failed', 'missing_dependencies': missing,
                    'note': '请安装依赖或配置程序路径；当前命令未操作手机'}
    pipeline = Pipeline(config)
    with exclusive(config.state):
        if args.command == 'doctor':
            with device_lock(config.device_state):
                pipeline.poster.adb.check()
                try:
                    pipeline.poster._credential(); credential_configured = True
                except DeviceError:
                    credential_configured = False
                output = {"connected": True, "serial": config.serial, "device_bound": bool(config.device_id),
                          "account": config.account, "locked": pipeline.poster.adb.locked(), "screen_on": pipeline.poster.adb.screen_on(),
                          "unlock_method": config.unlock_method, "credential_configured": credential_configured,
                          "unlock_blocked": (config.device_state / 'unlock.blocked').exists()}
            output['codex_authenticated'] = codex_authenticated(config)
            output['ready'] = output['codex_authenticated'] and not output['unlock_blocked'] and (
                not output['locked'] or (config.allow_unlock and credential_configured))
            if not output['codex_authenticated']:
                output['note'] = '请先执行 codex login 完成登录'
        elif args.command == 'unlock-test':
            with pipeline.poster.session():
                output = {"unlocked": not pipeline.poster.adb.locked(), "note": "未执行任何发布操作"}
        elif args.command == 'collect':
            output = pipeline.collect(args.count)
        elif args.command == 'queue':
            output = [{"id": r['id'], "status": r['status'], "topic": r['topic'], "note": r['note'],
                       "draft": json.loads(r['draft'])} for r in pipeline.store.rows()]
        elif args.command == 'status':
            output = {**profile_summary(config), "serial": config.serial,
                      "unlock_blocked": (config.device_state / 'unlock.blocked').exists(),
                      "queued": pipeline.store.queued_count(), "unresolved": pipeline.store.has_unresolved(),
                      "schedule": [{"at": datetime.fromtimestamp(r['due'], ZoneInfo('Asia/Shanghai')).isoformat(), "status": r['status']}
                                   for r in pipeline.schedule.upcoming(utcnow())]}
        elif args.command in ('preview', 'post'):
            publish = getattr(args, 'publish', False)
            images = [str(path.resolve()) for path in map(Path, args.image)]
            if args.file is not None and not publish:
                output = pipeline.preview_text(args.file.read_text(encoding='utf-8'), args.title, images)
            else:
                post_id = args.id if args.id is not None else pipeline.add(args.file.read_text(encoding='utf-8'), args.title, images)
                output = pipeline.publish(post_id) if publish else pipeline.preview(post_id)
        elif args.command == 'tick':
            output = pipeline.tick(args.publish)
        elif args.command == 'enable':
            with device_lock(config.device_state):
                pipeline.poster.adb.check()
            output = install_timer(config, args.publish)
        elif args.command == 'unlock-reset':
            with device_lock(config.device_state):
                (config.device_state / 'unlock.blocked').unlink(missing_ok=True)
            output = {'unlock_blocked': False}
        elif args.command == 'resolve':
            pipeline.store.resolve(args.id, args.sent)
            output = {'id': args.id, 'status': pipeline.store.get(args.id)['status']}
        else:
            raise ValueError('unsupported command')
        if args.command not in ('queue', 'preview'):
            event = {"at": utcnow().isoformat(), "command": args.command, "result": output}
            with (config.state / 'events.jsonl').open('a') as log:
                log.write(json.dumps(event, ensure_ascii=False) + '\n')
        return output


def main(argv=None):
    args = parser().parse_args(argv)
    def stop(_signal, _frame):
        raise SystemExit(143)
    signal.signal(signal.SIGTERM, stop)
    try:
        if args.command == 'disable':
            print_json(disable_profiles(args))
            return 0
        load_runtime_environment(args.root)
        if args.command == 'profiles':
            print_json([profile_summary(config) for config in Config.load_all(args.root)])
            return 0
        aggregate = args.all_profiles
        if not args.profile and args.command == 'status':
            aggregate = aggregate or len(Config.load_all(args.root)) > 1
        if aggregate:
            if args.command not in ('status', 'queue', 'tick', 'doctor', 'enable'):
                raise ValueError('该命令需要 --profile 选择单个账号，不支持 --all')
            configs = Config.load_all(args.root)
            def operation(config):
                result = run_command(config, args)
                return {'items': result} if isinstance(result, list) else result
            output = run_profiles(configs, operation, workers=1 if args.command == 'enable' else 4)
            print_json(output)
            return 1 if any(row.get('status') == 'failed' or row.get('ready') is False for row in output) else 0
        config = Config.load(args.root, args.profile)
        output = run_command(config, args)
        print_json(output)
        return 1 if isinstance(output, dict) and (output.get('ready') is False or output.get('status') == 'failed') else 0
    except (DeviceError, ContentError, ValueError, OSError, RuntimeError) as error:
        print_json({'error': str(error)})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
