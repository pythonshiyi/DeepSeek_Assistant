# -*- coding: utf-8 -*-
"""API Key 加密存储：Windows DPAPI（CryptProtectData）。

- 磁盘上 config.json 的 api_key 存为 "dpapi:" 前缀 + base64 密文。
- 内存中始终是明文（load 时解密、save 时加密，save 不修改内存 cfg）。
- 旧版明文 key 自动兼容：无前缀时按明文原样返回，下次保存自动加密。
- DPAPI 与当前 Windows 用户绑定，密文不可跨机器/跨用户使用。
"""
import base64
import ctypes
import ctypes.wintypes
import logging

PREFIX = "dpapi:"
_UI_FORBIDDEN = 0x1


class CryptError(Exception):
    """DPAPI 加密失败（fail-closed 专用异常）。"""


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _crypt(data, protect):
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    out = _DATA_BLOB()
    fn = (
        ctypes.windll.crypt32.CryptProtectData
        if protect
        else ctypes.windll.crypt32.CryptUnprotectData
    )
    ok = fn(
        ctypes.byref(blob), None, None, None, None,
        _UI_FORBIDDEN, ctypes.byref(out),
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def encrypt(text):
    """明文 -> 'dpapi:' + base64 密文（空串原样返回）。

    加密失败（DPAPI 不可用/ACL 异常）时抛 CryptError，绝不返回明文。
    调用方（save_config）捕获后跳过 api_key 字段，磁盘保持原密文，
    保证「明文密钥永不落盘」的安全承诺（fail-closed）。
    """
    if not text:
        return text
    try:
        raw = _crypt(str(text).encode("utf-8"), True)
        return PREFIX + base64.b64encode(raw).decode("ascii")
    except Exception:
        logging.exception("API Key DPAPI 加密失败，本次保存将跳过 api_key（明文不落盘）")
        raise CryptError("DPAPI 加密失败")


def decrypt(token):
    """'dpapi:' 密文 -> 明文；无前缀按旧明文原样返回。

    解密失败（换用户/换机器/ACL 变化）时记录日志并返回空串，
    避免把密文当明文 key 使用导致 API 认证静默失败。
    """
    if not token or not str(token).startswith(PREFIX):
        return token
    try:
        raw = base64.b64decode(str(token)[len(PREFIX):])
        return _crypt(raw, False).decode("utf-8")
    except Exception:
        logging.exception("API Key DPAPI 解密失败（可能换用户/换机器），请重新填写")
        return ""
