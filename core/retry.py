"""
通用重试:对瞬时错误(网络抖动 / 429 / 5xx)做指数退避重试。
只重试「瞬时」错误;4xx 之类的客户端错误立即抛出,不浪费时间。
"""

import time
import functools
import requests

_RETRY_STATUS = {429, 500, 502, 503, 504}


def _is_transient(e: Exception) -> bool:
    if isinstance(e, requests.exceptions.HTTPError):
        resp = getattr(e, "response", None)
        return resp is not None and resp.status_code in _RETRY_STATUS
    if isinstance(e, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    # anthropic 等 SDK 的异常按类名 + 状态码判断,避免硬依赖其异常类
    if type(e).__name__ in {
        "RateLimitError", "APIConnectionError", "APITimeoutError",
        "InternalServerError", "OverloadedError", "APIStatusError",
    }:
        status = getattr(e, "status_code", None)
        return status is None or status in _RETRY_STATUS
    return False


def retry(tries: int = 4, base_delay: float = 2.0):
    """装饰器:失败后指数退避重试(2s → 4s → 8s),仅对瞬时错误。"""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for i in range(tries):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    if i == tries - 1 or not _is_transient(e):
                        raise
                    delay = base_delay * (2 ** i)
                    print(f"[retry] {fn.__name__} 第{i+1}次失败({type(e).__name__}),{delay:.0f}s 后重试…")
                    time.sleep(delay)
        return wrapper
    return deco
