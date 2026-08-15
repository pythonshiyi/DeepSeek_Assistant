# -*- coding: utf-8 -*-
"""网络工具：共享 httpx 客户端、安全请求/流式请求（含逐跳 SSRF 校验）。

从 deepseek_client.py 中拆出，供搜索/抓取/API 调用等联网工具复用。
"""
import atexit
import threading

import httpx

from security import _safe_url

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

FETCH_URL_TIMEOUT = 10

_HTTP_CLIENT = None
_HTTP_CLIENT_LOCK = threading.Lock()
_HTTP_CLIENT_SHUTDOWN = False


def _shutdown_http_client():
    """退出时关闭连接池（防止句柄滞留）。持锁：与后台工具线程的 get() 互斥。

    先置关闭标记再 close，避免退出过程中后台线程在 close 后重建新客户端。
    """
    global _HTTP_CLIENT, _HTTP_CLIENT_SHUTDOWN
    with _HTTP_CLIENT_LOCK:
        _HTTP_CLIENT_SHUTDOWN = True
        if _HTTP_CLIENT is not None:
            try:
                _HTTP_CLIENT.close()
            except Exception:
                pass
            _HTTP_CLIENT = None


atexit.register(_shutdown_http_client)


def _http_client():
    """模块级复用 httpx.Client（线程安全）：联网工具不再每次新建连接池，
    省掉每次调用的 TCP+TLS 握手（50-300ms/次）。"""
    global _HTTP_CLIENT
    with _HTTP_CLIENT_LOCK:
        if _HTTP_CLIENT_SHUTDOWN:
            raise RuntimeError("HTTP client is shutting down")
        if _HTTP_CLIENT is None:
            _HTTP_CLIENT = httpx.Client(
                follow_redirects=True,
                headers={"User-Agent": DEFAULT_UA},
                limits=httpx.Limits(max_keepalive_connections=8, max_connections=16),
                timeout=FETCH_URL_TIMEOUT,
            )
        return _HTTP_CLIENT


def _safe_redirect_url(current, location, allow_loopback=True):
    """拼接重定向 URL 并做 SSRF 校验；非法返回 None。"""
    try:
        next_url = str(httpx.URL(current).join(location))
    except Exception:
        return None
    if _safe_url(next_url, allow_loopback=allow_loopback):
        return None
    return next_url



