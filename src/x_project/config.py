"""Portable configuration; phone secrets are never exported by the loader."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import tomllib
import uuid


PIN_KEYS = ('X_PHONE_PIN', 'PHONE_PIN', 'ANDROID_PIN', 'PHONE_PASSWORD', 'LOCK_SCREEN_PASSWORD', 'pwd')
PATTERN_KEYS = ('X_PHONE_PATTERN', 'PHONE_PATTERN', 'ANDROID_PATTERN', 'pwd')
CONFIG_KEYS = {
    'X_ACCOUNT': 'account', 'X_ADB_PATH': 'adb', 'X_ADB_SERIAL': 'serial',
    'X_DEVICE_ID': 'device_id', 'X_ALLOW_UNLOCK': 'allow_unlock',
    'X_UNLOCK_METHOD': 'unlock_method', 'X_PUBLISH_ENABLED': 'publish_enabled',
    'X_PERSONA': 'persona', 'X_CODEX_PATH': 'codex',
}
RUNTIME_KEYS = ('PATH', 'CODEX_HOME', 'LANG', 'LC_ALL', 'TZ', 'HTTP_PROXY', 'HTTPS_PROXY',
                'ALL_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
                'SSL_CERT_FILE', 'SSL_CERT_DIR', 'NODE_EXTRA_CA_CERTS')
TIMER_KEYS = set(CONFIG_KEYS) | set(PIN_KEYS) | set(PATTERN_KEYS) | set(RUNTIME_KEYS) | {'X_ADB_HOST', 'X_ADB_PORT'}


def parse_dotenv(text):
    values = {}
    for line in text.splitlines():
        match = re.match(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$', line)
        if not match:
            continue
        key, value = match.groups()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = re.sub(r'\s+#.*$', '', value)
        values[key] = value
    return values


def read_dotenv(path):
    path = Path(path)
    if not path.exists():
        return {}
    if path.stat().st_mode & 0o077:
        raise ValueError('.env 权限过宽，请先 chmod 600 .env')
    return parse_dotenv(path.read_text(encoding='utf-8'))


def environment(root):
    return {**read_dotenv(Path(root) / '.env'), **os.environ}


def load_runtime_environment(root):
    """Only transport/runtime settings enter the parent process environment."""
    values = read_dotenv(Path(root) / '.env')
    for key in RUNTIME_KEYS:
        if values.get(key) and key not in os.environ:
            os.environ[key] = values[key]


def write_private(path, text):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}-{uuid.uuid4().hex}.tmp')
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def boolean(value, name):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() in ('true', '1', 'yes', 'on'):
            return True
        if value.lower() in ('false', '0', 'no', 'off'):
            return False
    raise ValueError(f'{name} 必须为 true 或 false')


def endpoint(host, port, name):
    if not isinstance(host, str) or not re.fullmatch(r'(?:[A-Za-z0-9][A-Za-z0-9_.-]*|\[[0-9a-fA-F:]+\])', host):
        raise ValueError(f'{name} 主机地址格式无效')
    if not isinstance(port, str) or not re.fullmatch(r'[0-9]{1,5}', port) or not 1 <= int(port) <= 65535:
        raise ValueError(f'{name} 端口必须在 1–65535 之间')
    return f'{host}:{int(port)}'


@dataclass
class Config:
    root: Path
    account: str
    adb: str
    serial: str
    allow_unlock: bool
    publish_enabled: bool
    persona: str
    device_id: str = ''
    unlock_method: str = 'pin'
    codex: str = 'codex'
    profile: str = ''
    platform: str = 'x'
    device_name: str = ''
    hourly_limit: int = 3
    min_interval_minutes: int = 8
    images: tuple[str, ...] = ()
    font_path: str = ''
    legacy_state: bool = False
    legacy_device_state: bool = False
    namespace: str = ''
    device_namespace: str = ''

    @classmethod
    def load(cls, root, profile=None):
        root = Path(root).resolve()
        if environment(root).get('X_PROFILES'):
            configs = cls.load_all(root)
            if profile is None and len(configs) == 1:
                return configs[0]
            for config in configs:
                if config.profile == profile:
                    return config
            raise ValueError('请使用 --profile 选择有效账号，或使用 --all')
        if profile:
            raise ValueError('没有配置 X_PROFILES，无法选择 --profile')
        legacy = root / 'config.toml'
        values = dict(account='', adb='adb', serial='', allow_unlock=False,
                      publish_enabled=False, persona='AI/科技专家视角，中文简洁直说，事实与个人判断分开。',
                      device_id='', unlock_method='pin', codex='codex')
        if legacy.exists():
            try:
                old = tomllib.loads(legacy.read_text(encoding='utf-8'))
            except tomllib.TOMLDecodeError:
                raise ValueError('config.toml 格式无效') from None
            if set(old) - set(values):
                raise ValueError('config.toml 包含未知配置项')
            values.update(old)
        env = environment(root)
        for key, field in CONFIG_KEYS.items():
            if env.get(key) or (key in env and field in ('allow_unlock', 'publish_enabled')):
                values[field] = env[key]
        connection_keys = ('X_ADB_SERIAL', 'X_ADB_HOST', 'X_ADB_PORT')
        connection_layer = os.environ if any(os.environ.get(key) for key in connection_keys) else env
        if not connection_layer.get('X_ADB_SERIAL') and (connection_layer.get('X_ADB_HOST') or connection_layer.get('X_ADB_PORT')):
            values['serial'] = endpoint(env.get('X_ADB_HOST', ''), env.get('X_ADB_PORT', ''), 'X_ADB_HOST/X_ADB_PORT')
        if not isinstance(values['account'], str) or not re.fullmatch(r'@?[A-Za-z0-9_]{1,15}', values['account']):
            raise ValueError('请配置有效的 X_ACCOUNT（或 config.toml 的 account）')
        serial = values['serial']
        if not isinstance(serial, str) or not re.fullmatch(r'[A-Za-z0-9\[][A-Za-z0-9_.:\[\]-]*', serial):
            raise ValueError('请配置 X_ADB_HOST 和 X_ADB_PORT，或 X_ADB_SERIAL')
        if ':' in serial:
            host, _, port = serial.rpartition(':')
            values['serial'] = endpoint(host, port, 'X_ADB_SERIAL')
        for name in ('allow_unlock', 'publish_enabled'):
            values[name] = boolean(values[name], 'X_' + name.upper())
        if values['unlock_method'] not in ('pin', 'pattern'):
            raise ValueError('X_UNLOCK_METHOD 必须为 pin 或 pattern')
        for name in ('adb', 'codex', 'device_id', 'persona'):
            if not isinstance(values[name], str) or any(c in values[name] for c in '\x00\r\n'):
                raise ValueError(f'{name} 配置格式无效')
        marker = root / '.state' / 'publish-enabled'
        if marker.exists():
            values['publish_enabled'] = boolean(marker.read_text().strip(), '自动发布运行开关')
        return cls(root=root, **values)

    @property
    def state(self):
        path = self.root / '.state'
        if self.namespace:
            path = path / self.namespace
        elif self.profile and not self.legacy_state:
            identity = hashlib.sha256(self.account.lstrip('@').casefold().encode()).hexdigest()[:24]
            path = path / 'accounts' / self.platform / identity
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    @classmethod
    def load_all(cls, root):
        from .profiles import load_profiles
        root = Path(root).resolve()
        if not environment(root).get('X_PROFILES'):
            return [cls.load(root)]
        return load_profiles(root)

    @property
    def credential_prefix(self):
        return f'X_DEVICE_{self.device_name.upper()}_' if self.device_name else ''

    @property
    def device_state(self):
        if self.device_namespace:
            return self.root / '.state' / self.device_namespace
        if not self.profile or self.legacy_device_state:
            return self.root / '.state'
        identity = hashlib.sha256((self.device_id or self.serial).encode()).hexdigest()[:24]
        return self.root / '.state' / 'devices' / identity

    def device_values(self):
        values = environment(self.root)
        if not self.credential_prefix:
            return values
        suffixes = ('ADB_HOST', 'ADB_PORT', 'ADB_SERIAL', 'ADB_PAIR_HOST', 'ADB_PAIR_PORT',
                    'ADB_PAIR_CODE', 'PHONE_PIN', 'PHONE_PATTERN')
        return {'X_' + key: values.get(self.credential_prefix + key, '') for key in suffixes}

    def set_publish_enabled(self, enabled):
        write_private(self.state / 'publish-enabled', 'true\n' if enabled else 'false\n')
        self.publish_enabled = enabled
