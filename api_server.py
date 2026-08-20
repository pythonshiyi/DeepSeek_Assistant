# -*- coding: utf-8 -*-
"""开发者模式：本地 HTTP API 沙箱。

提供一组受 token 保护的本地接口，供用户/开发者把 WhaleTalk 接入自己的自动化流程：
- GET  /health              健康检查
- GET  /v1/tools            返回当前可用工具名列表
- POST /v1/chat             非流式对话（JSON：{messages, model?}）

安全约定：
- 仅监听 127.0.0.1（不暴露到局域网）。
- 必须携带 Bearer token（启动时生成或由配置指定）。
- 请求体上限 1MB，messages 条数/长度受限。
"""
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger("whaletalk.api")

MAX_BODY = 1_000_000
MAX_ROUNDS = 10

# 由 start_server 注入的运行时状态（模块级，单实例）
_SERVER = None
_THREAD = None
_TOKEN = ""
_TOOLS_PROVIDER = None
_CHAT_PROVIDER = None
_PORT = 8745


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _auth(self):
        auth = self.headers.get("Authorization", "")
        expected = f"Bearer {_TOKEN}"
        try:
            import hmac
            return hmac.compare_digest(auth.strip(), expected)
        except Exception:
            return False

    def _json(self, code, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length <= 0 or length > MAX_BODY:
                return None
            return json.loads(self.rfile.read(length).decode("utf-8", errors="replace"))
        except Exception:
            return None

    def do_GET(self):
        if not self._auth():
            self._json(401, {"error": "unauthorized"})
            return
        if self.path == "/health":
            self._json(200, {"ok": True, "service": "whaletalk-api-sandbox"})
            return
        if self.path == "/v1/tools":
            names = sorted(_TOOLS_PROVIDER() if _TOOLS_PROVIDER else [])
            self._json(200, {"tools": names})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self._auth():
            self._json(401, {"error": "unauthorized"})
            return
        if self.path == "/v1/chat":
            body = self._read_body()
            if body is None:
                self._json(400, {"error": "invalid json or body too large"})
                return
            messages = body.get("messages") or []
            if not isinstance(messages, list) or not messages or len(messages) > 200:
                self._json(400, {"error": "messages must be a non-empty list (max 200)"})
                return
            for m in messages:
                if not isinstance(m, dict) or m.get("role") not in ("user", "assistant", "system"):
                    self._json(400, {"error": "invalid message role"})
                    return
                if not isinstance(m.get("content"), str) or len(m["content"]) > 100000:
                    self._json(400, {"error": "invalid/invalid message content"})
                    return
            if _CHAT_PROVIDER is None:
                self._json(503, {"error": "chat provider not configured"})
                return
            try:
                text = _CHAT_PROVIDER(messages, body.get("model"))
                self._json(200, {"content": text or ""})
            except Exception as e:
                logger.exception("API chat 失败")
                self._json(500, {"error": str(e)})
            return
        self._json(404, {"error": "not found"})


def start_server(port=8745, token="", tools_provider=None, chat_provider=None):
    """启动本地 API 沙箱。token 为空时自动生成。返回 (port, token, error)。"""
    global _SERVER, _THREAD, _TOKEN, _TOOLS_PROVIDER, _CHAT_PROVIDER, _PORT
    if _SERVER is not None:
        return _PORT, _TOKEN, None
    import secrets
    token = (token or "").strip() or ("wt_" + secrets.token_hex(16))
    _TOKEN = token
    _TOOLS_PROVIDER = tools_provider
    _CHAT_PROVIDER = chat_provider
    _PORT = int(port or 8745)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", _PORT), _Handler)
    except Exception as e:
        return None, "", str(e)
    _SERVER = server
    _THREAD = threading.Thread(target=server.serve_forever, daemon=True)
    _THREAD.start()
    logger.info("本地 API 沙箱已启动：http://127.0.0.1:%s", _PORT)
    return _PORT, _TOKEN, None


def stop_server():
    global _SERVER, _THREAD
    if _SERVER is not None:
        try:
            _SERVER.shutdown()
            _SERVER.server_close()
        except Exception:
            pass
        _SERVER = None
        _THREAD = None
        return True
    return False


def is_running():
    return _SERVER is not None
