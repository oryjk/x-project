from contextlib import contextmanager
import fcntl
from pathlib import Path


@contextmanager
def device_lock(directory):
    from .android import DeviceError
    path = Path(directory); path.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (path / 'device.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DeviceError('该手机正在处理另一个账号，本次跳过') from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
