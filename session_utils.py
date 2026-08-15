# -*- coding: utf-8 -*-
"""会话 ID 工具。

从 main.py 中拆出，供会话文件命名/快照使用。
"""
import re
import uuid


def safe_sid(sid):
    """净化会话 ID：仅允许 [0-9a-zA-Z_-]，杜绝快照/会话文件中的恶意 id 逃逸目录。"""
    return re.sub(r"[^0-9a-zA-Z_-]", "", str(sid or ""))


def session_id(session):
    """返回会话 ID，缺失时自动生成短 uuid 并写回 session dict。"""
    if not session.get("id"):
        session["id"] = uuid.uuid4().hex[:12]
    return safe_sid(session["id"])
