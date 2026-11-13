"""Device and publishing-account registry. Contains no stored credential values."""
import json
import fcntl
import hashlib
import os
from pathlib import Path
import re
import tomllib

from .config import Config, boolean, endpoint, environment, write_private


def bind_namespaces(root, configs):
    """A one-time legacy adoption must survive deleting obsolete env keys."""
    state = root / '.state'; state.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = state / 'namespaces.json'
    with (state / 'namespaces.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            registry = json.loads(path.read_text()) if path.exists() else {'accounts': {}, 'devices': {}, 'endpoints': {}}
            if not isinstance(registry, dict) or any(not isinstance(registry.get(key), dict) for key in ('accounts', 'devices', 'endpoints')):
                raise ValueError()
            before = json.dumps(registry, sort_keys=True)
            for config in configs:
                identity = config.account.lstrip('@').casefold()
                akey = config.platform + ':' + identity
                digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
                account_ns = registry['accounts'].setdefault(akey, '.' if config.legacy_state else f'accounts/{config.platform}/{digest}')
                if not isinstance(account_ns, str) or not re.fullmatch(r'\.|accounts/(?:x|xiaohongshu)/[0-9a-f]{24}', account_ns):
                    raise ValueError()
                config.namespace = account_ns
                dkey = ('id:' + config.device_id) if config.device_id else ('endpoint:' + config.serial)
                device_ns = registry['devices'].get(dkey)
                endpoint_alias = registry['endpoints'].get(config.serial, {})
                if not isinstance(endpoint_alias, dict):
                    raise ValueError()
                if device_ns is None and (not endpoint_alias.get('device_id') or endpoint_alias.get('device_id') == config.device_id or not config.device_id):
                    device_ns = endpoint_alias.get('namespace')
                if device_ns is None:
                    digest = hashlib.sha256((config.device_id or config.serial).encode()).hexdigest()[:24]
                    device_ns = '.' if config.legacy_device_state else 'devices/' + digest
                if not isinstance(device_ns, str) or not re.fullmatch(r'\.|devices/[0-9a-f]{24}', device_ns):
                    raise ValueError()
                registry['devices'][dkey] = device_ns
                registry['endpoints'][config.serial] = {'device_id': config.device_id, 'namespace': device_ns}
                config.device_namespace = device_ns
            if not path.exists() or json.dumps(registry, sort_keys=True) != before:
                write_private(path, json.dumps(registry, ensure_ascii=False, sort_keys=True))
        except (ValueError, TypeError, KeyError):
            raise ValueError('账号/设备状态映射无效，停止运行以保护历史配额与失败记录') from None


def names(value, label):
    result = [part.strip() for part in value.split(',')]
    if not result or len(result) > 32 or any(not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', name) for name in result):
        raise ValueError(f'{label} 需要1–32个小写字母、数字、下划线组成的名称，以字母开头')
    if len(set(result)) != len(result):
        raise ValueError(f'{label} 名称重复')
    return result


def integer(value, label, minimum, maximum):
    if not re.fullmatch(r'[0-9]{1,3}', str(value)) or not minimum <= int(value) <= maximum:
        raise ValueError(f'{label} 必须在 {minimum}–{maximum} 之间')
    return int(value)


def load_profiles(root):
    values = environment(root)
    device_names = names(values.get('X_DEVICES', ''), 'X_DEVICES')
    profile_names = names(values['X_PROFILES'], 'X_PROFILES')
    old = {}
    legacy = root / 'config.toml'
    if legacy.exists():
        try:
            old = tomllib.loads(legacy.read_text(encoding='utf-8'))
        except tomllib.TOMLDecodeError:
            raise ValueError('config.toml 格式无效') from None
    old_account = values.get('X_ACCOUNT') or old.get('account', '')
    old_device = values.get('X_DEVICE_ID') or old.get('device_id', '')
    devices = {}
    for name in device_names:
        prefix = f'X_DEVICE_{name.upper()}_'
        serial = values.get(prefix + 'ADB_SERIAL', '')
        layer = os.environ if any(os.environ.get(prefix + key) for key in ('ADB_SERIAL', 'ADB_HOST', 'ADB_PORT')) else values
        if not layer.get(prefix + 'ADB_SERIAL') and (layer.get(prefix + 'ADB_HOST') or layer.get(prefix + 'ADB_PORT')):
            serial = endpoint(values.get(prefix + 'ADB_HOST', ''), values.get(prefix + 'ADB_PORT', ''), prefix + 'ADB_HOST/ADB_PORT')
        if not re.fullmatch(r'[A-Za-z0-9\[][A-Za-z0-9_.:\[\]-]*', serial):
            raise ValueError(prefix + 'ADB_SERIAL 或 ADB_HOST/ADB_PORT 未配置或无效')
        if ':' in serial:
            host, _, port = serial.rpartition(':'); serial = endpoint(host, port, prefix + 'ADB_SERIAL')
        method = values.get(prefix + 'UNLOCK_METHOD', 'pin')
        if method not in ('pin', 'pattern'):
            raise ValueError(prefix + 'UNLOCK_METHOD 必须为 pin 或 pattern')
        adb = values.get(prefix + 'ADB_PATH') or values.get('X_ADB_PATH') or old.get('adb', 'adb')
        device_id = values.get(prefix + 'DEVICE_ID', '')
        if any(char in str(adb) + device_id for char in '\x00\r\n'):
            raise ValueError(prefix + '设备路径或身份格式无效')
        devices[name] = dict(serial=serial, device_id=device_id, adb=adb,
                             allow_unlock=boolean(values.get(prefix + 'ALLOW_UNLOCK', 'false'), prefix + 'ALLOW_UNLOCK'),
                             unlock_method=method)
    configs = []; seen = set()
    for name in profile_names:
        prefix = f'X_PROFILE_{name.upper()}_'
        platform = values.get(prefix + 'PLATFORM', 'x')
        if platform not in ('x', 'xiaohongshu'):
            raise ValueError(prefix + 'PLATFORM 必须为 x 或 xiaohongshu')
        account = values.get(prefix + 'ACCOUNT', '')
        pattern = r'@?[A-Za-z0-9_]{1,15}' if platform == 'x' else r'[A-Za-z0-9_-]{1,64}'
        if not re.fullmatch(pattern, account):
            raise ValueError(prefix + 'ACCOUNT 无效；小红书应填写可核对的小红书号')
        identity = (platform, account.lstrip('@').casefold())
        if identity in seen:
            raise ValueError('同一平台账号重复配置')
        seen.add(identity)
        device = values.get(prefix + 'DEVICE', '')
        if device not in devices:
            raise ValueError(prefix + 'DEVICE 未在 X_DEVICES 中定义')
        hourly = integer(values.get(prefix + 'HOURLY_LIMIT', '3' if platform == 'x' else '1'), prefix + 'HOURLY_LIMIT', 0, 3)
        gap = integer(values.get(prefix + 'MIN_INTERVAL_MINUTES', '8' if platform == 'x' else '30'), prefix + 'MIN_INTERVAL_MINUTES', 8, 60)
        if hourly > 1 and (hourly - 1) * gap > 46:
            raise ValueError(prefix + '频率无法在每小时的有效发送窗口安排')
        try:
            images = json.loads(values.get(prefix + 'IMAGES') or '[]')
            if not isinstance(images, list) or any(not isinstance(p, str) or not p for p in images):
                raise ValueError()
            images = tuple(str((root / p).resolve()) for p in images)
        except (ValueError, TypeError):
            raise ValueError(prefix + 'IMAGES 必须为本机图片路径的JSON数组') from None
        font = values.get(prefix + 'FONT_PATH', '')
        font = str((root / font).resolve()) if font else ''
        same_account = platform == 'x' and account.lstrip('@').casefold() == old_account.lstrip('@').casefold()
        config = Config(root=root, account=account, persona=values.get(prefix + 'PERSONA') or values.get('X_PERSONA') or '专业中文，核实事实，说明实际意义，不编造亲测。',
                        codex=values.get('X_CODEX_PATH') or 'codex', profile=name, platform=platform,
                        device_name=device, hourly_limit=hourly, min_interval_minutes=gap, images=images, font_path=font,
                        publish_enabled=boolean(values.get(prefix + 'ENABLED', 'false'), prefix + 'ENABLED'),
                        legacy_state=same_account,
                        legacy_device_state=bool(old_device and devices[device]['device_id'] == old_device), **devices[device])
        configs.append(config)
    bind_namespaces(root, configs)
    for config in configs:
        marker = config.state / 'publish-enabled'
        if marker.exists():
            config.publish_enabled = boolean(marker.read_text().strip(), '自动发布运行开关')
        global_stop = root / '.state' / 'global-disabled'
        if global_stop.exists() and boolean(global_stop.read_text().strip(), '全局停止开关'):
            config.publish_enabled = False
        if not config.hourly_limit:
            config.publish_enabled = False
    return configs
