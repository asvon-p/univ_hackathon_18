"""블로킹 함수를 시간 제한과 함께 실행합니다.

주의: 파이썬 스레드는 강제로 멈출 수 없어서, 시간이 초과돼도 원래 작업은 뒤에서 끝까지 실행됩니다.
응답만 먼저 돌려주는 방식입니다.
"""

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="adapter")


class AdapterTimeout(Exception):
    pass


def run_with_timeout(fn, timeout_sec: float, *args, **kwargs):
    future = _POOL.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=timeout_sec)
    except FutureTimeout as e:
        raise AdapterTimeout(f"{timeout_sec}s 초과") from e
