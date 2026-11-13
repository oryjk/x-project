"""Bounded account workers: research can overlap, physical sessions are locked."""
from concurrent.futures import ThreadPoolExecutor

from .android import DeviceError
from .content import ContentError


def run_profiles(configs, operation, *, workers=4):
    def run(config):
        summary = {'profile': config.profile or 'default', 'platform': config.platform, 'account': config.account}
        try:
            return {**summary, **operation(config)}
        except (DeviceError, ContentError, ValueError) as error:
            return {**summary, 'status': 'failed', 'note': str(error)}
        except Exception:
            return {**summary, 'status': 'failed', 'note': '账号执行失败，未影响其他账号'}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(configs) or 1))) as executor:
        return list(executor.map(run, configs))
