# -*- coding: utf-8 -*-
"""企业微信智能机器人（长连接模式）通道。

基于官方 wecom-aibot-python-sdk（模块名 aibot）：
- Bot ID + Secret → WebSocket 长连接（wss://openws.work.weixin.qq.com）
- 接收单聊/群聊@消息，并支持回复与主动 send_message（需会话 chatid）
- 可选依赖：pip install wecom-aibot-python-sdk

本模块只做通道封装，回调由 main.py 注入（消息 → UI 队列 → AI）。
"""
import asyncio
import json
import logging
import threading
import uuid

logger = logging.getLogger("whaletalk.wecom_aibot")


class AibotListener:
    """后台线程运行 asyncio 事件循环，维护企业微信智能机器人长连接。"""

    def __init__(self, bot_id, secret, on_message=None, on_status=None):
        self.bot_id = str(bot_id or "").strip()
        self.secret = str(secret or "").strip()
        self.on_message = on_message or (lambda chatid, user, text, frame: None)
        self.on_status = on_status or (lambda msg: None)
        self._thread = None
        self._loop = None
        self._client = None
        self._started = False

    def start(self):
        if self._started:
            return
        self._started = True
        self._thread = threading.Thread(
            target=self._run_loop, name="wecom-aibot", daemon=True
        )
        self._thread.start()

    def stop(self):
        if not self._started:
            return
        self._started = False
        loop = self._loop
        if loop is not None and not loop.is_closed():
            def _graceful_stop():
                try:
                    if self._client is not None:
                        self._client.disconnect()
                except Exception:
                    pass
                finally:
                    try:
                        loop.stop()
                    except Exception:
                        pass

            try:
                loop.call_soon_threadsafe(_graceful_stop)
            except Exception:
                pass

    def is_running(self):
        return self._started

    def _run_loop(self):
        try:
            from aibot import WSClient, WSClientOptions
        except ImportError:
            self.on_status("缺少依赖 wecom-aibot-python-sdk：pip install wecom-aibot-python-sdk")
            self._started = False
            return
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            self._client = WSClient(
                WSClientOptions(
                    bot_id=self.bot_id,
                    secret=self.secret,
                    max_reconnect_attempts=-1,
                    heartbeat_interval=30000,
                    request_timeout=15000,
                )
            )
            self._client.on("connected", lambda: self.on_status("企业微信智能机器人已连接"))
            self._client.on("authenticated", lambda: self.on_status("企业微信智能机器人已认证"))
            self._client.on("disconnected", lambda r: self.on_status(f"企业微信智能机器人断开：{r}"))
            self._client.on("message.text", self._on_text)
            loop.run_until_complete(self._client.connect())
            loop.run_forever()
        except Exception as e:
            logger.exception("企业微信智能机器人长连接异常")
            self.on_status(f"企业微信智能机器人长连接异常：{e}")
            self._started = False
        finally:
            try:
                if self._client is not None:
                    self._client.disconnect()
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass

    def _on_text(self, frame):
        try:
            body = frame.get("body") or {}
            chatid = str(body.get("chatid") or "")
            frm = body.get("from") or {}
            if isinstance(frm, dict):
                user = str(frm.get("name") or frm.get("userid") or "?")
            else:
                user = str(frm or "?")
            txt = ""
            text_obj = body.get("text") or {}
            if isinstance(text_obj, dict):
                txt = str(text_obj.get("content") or "")
            elif isinstance(body.get("content"), str):
                txt = str(body["content"])
            txt = txt.strip()
            if txt:
                self.on_message(chatid, user, txt, frame)
        except Exception:
            logger.exception("处理企业微信消息失败")

    def _run_coro(self, coro, timeout=20):
        loop = self._loop
        if loop is None or loop.is_closed():
            return None
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return fut.result(timeout=timeout)
        except Exception as e:
            logger.warning("企业微信发送失败：%s", e)
            return None

    @staticmethod
    def _reply_body(text):
        """回复消息体：智能机器人长连接只支持 stream（官方 SDK reply_stream 同构）。"""
        stream = {
            "id": uuid.uuid4().hex,
            "finish": True,
            "content": str(text)[:4000],
        }
        return {"msgtype": "stream", "stream": stream}

    @staticmethod
    def _send_body(text):
        """主动发送消息体：send_message 支持 markdown / template_card。"""
        return {"msgtype": "markdown", "markdown": {"content": str(text)[:4000]}}

    def reply_text(self, frame, text):
        """回复当前收到的消息（长连接 RESPONSE，智能机器人使用 stream 类型）。"""
        if self._client is None:
            return False
        return self._run_coro(self._client.reply(frame, self._reply_body(text))) is not None

    def send_text(self, chatid, text):
        """主动发送消息到指定会话（chatid 来自收到的消息/缓存；send_message 支持 markdown）。"""
        if self._client is None or not chatid:
            return False
        return self._run_coro(self._client.send_message(chatid, self._send_body(text))) is not None


# 模块级单例（由 main 注入配置后启动）
_LISTENER = None


def ensure_listener(bot_id, secret, on_message=None, on_status=None):
    """获取/重建全局监听器。配置变化时调用 stop 后再启动。"""
    global _LISTENER
    if _LISTENER is None or _LISTENER.bot_id != str(bot_id or "").strip() or _LISTENER.secret != str(secret or "").strip():
        if _LISTENER is not None:
            _LISTENER.stop()
        _LISTENER = AibotListener(bot_id, secret, on_message=on_message, on_status=on_status)
    return _LISTENER


def stop_listener():
    global _LISTENER
    if _LISTENER is not None:
        _LISTENER.stop()
        _LISTENER = None
