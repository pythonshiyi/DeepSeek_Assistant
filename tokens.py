import logging
from collections import OrderedDict

_encoder = None
_tried = False

PER_MESSAGE_OVERHEAD = 4
BASE_OVERHEAD = 3

_MSG_CACHE = OrderedDict()
_MSG_CACHE_MAX = 2048


def get_encoder():
    global _encoder, _tried
    if _tried:
        return _encoder
    _tried = True
    try:
        import tiktoken

        _encoder = tiktoken.get_encoding("o200k_base")
    except Exception:
        logging.warning("tiktoken 不可用，token 估算回退为字符数估算", exc_info=True)
        _encoder = False
    return _encoder


def estimate_text_tokens(text):
    if not isinstance(text, str):
        return 0  # 非字符串内容（多模态列表等）不计文本 token
    enc = get_encoder()
    if enc:
        try:
            return len(enc.encode(text or ""))
        except Exception:
            pass
    return int(len(text or "") / 1.5)


def _message_tokens(msg):
    total = PER_MESSAGE_OVERHEAD
    total += estimate_text_tokens(msg.get("content") or "")
    total += estimate_text_tokens(msg.get("reasoning_content") or "")
    # 视觉图片 token 估算：官方规则每张图片缩放后上限 384 token
    total += 384 * (len(msg.get("images") or ()) if msg.get("images") else 0)
    for tc in msg.get("tool_calls") or ():
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            total += estimate_text_tokens(fn.get("arguments") or "")
        elif isinstance(tc, str):
            total += estimate_text_tokens(tc)
    return total


def message_token_counts(messages):
    """返回每条消息的估算 token 数（含每条消息固定开销）。

    使用基于对象身份的缓存：内容字符串对象未被替换时直接复用历史估算，
    避免对大上下文反复进行 tiktoken 全量编码。含 tool_calls 的消息每次重算。
    """
    counts = []
    for msg in messages:
        cached = _MSG_CACHE.get(id(msg))
        if cached is not None:
            holder, content, reasoning, count = cached
            if (
                holder is msg
                and content is msg.get("content")
                and reasoning is msg.get("reasoning_content")
                and not msg.get("tool_calls")
            ):
                _MSG_CACHE.move_to_end(id(msg))
                counts.append(count)
                continue
        count = _message_tokens(msg)
        if not msg.get("tool_calls"):
            _MSG_CACHE[id(msg)] = (msg, msg.get("content"), msg.get("reasoning_content"), count)
            if len(_MSG_CACHE) > _MSG_CACHE_MAX:
                _MSG_CACHE.popitem(last=False)  # 仅淘汰最旧一条，避免整表清空导致全量重算
        counts.append(count)
    return counts


def clear_cache():
    """上下文裁剪/压缩后调用：缓存强引用被删除的消息对象，
    避免被裁剪的大消息（数百 KB）滞留内存。"""
    _MSG_CACHE.clear()


def estimate_messages_tokens(messages):
    return BASE_OVERHEAD + sum(message_token_counts(messages))
