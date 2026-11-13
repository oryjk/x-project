"""Explicit ADB onboarding without exposing pairing codes or raw device errors."""
import re
import subprocess

from .android import DeviceError
from .config import endpoint, environment


def _run(config, args, *, stdin=None):
    try:
        result = subprocess.run([config.adb, *args], input=stdin, capture_output=True,
                                text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise DeviceError('ADB 不可用或超时，请检查安装及手机连接') from None
    if result.returncode:
        raise DeviceError('ADB 配对或连接失败，请核对手机显示的地址、端口和有效配对码')
    return result.stdout.strip()


def pair_device(config):
    values = environment(config.root)
    host = values.get('X_ADB_PAIR_HOST') or values.get('X_ADB_HOST') or config.serial.rpartition(':')[0]
    address = endpoint(host, values.get('X_ADB_PAIR_PORT', ''), 'X_ADB_PAIR_HOST/X_ADB_PAIR_PORT')
    code = values.get('X_ADB_PAIR_CODE', '')
    if not re.fullmatch(r'[0-9]{6}', code):
        raise DeviceError('请在本机配置当前有效的六位 X_ADB_PAIR_CODE')
    # The optional adb pair CODE argument is deliberately omitted: stdin only.
    _run(config, ['pair', address], stdin=code + '\n')
    return {'paired': True, 'note': '配对完成；请运行 connect 核对设备，配对码可从本机配置删除'}


def connect_device(config):
    if ':' in config.serial:
        _run(config, ['connect', config.serial])
    if _run(config, ['-s', config.serial, 'get-state']) != 'device':
        raise DeviceError('手机尚未连接或授权')
    device_id = _run(config, ['-s', config.serial, 'shell', 'getprop', 'ro.serialno'])
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', device_id):
        raise DeviceError('无法读取有效的物理设备序列号')
    if config.device_id and device_id != config.device_id:
        raise DeviceError('连接到的手机与绑定设备不同，停止操作')
    return {'connected': True, 'device_id': device_id, 'device_bound': bool(config.device_id),
            'note': '请核对设备并将该序列号填入 X_DEVICE_ID' if not config.device_id else '设备身份匹配'}
