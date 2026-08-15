# -*- coding: utf-8 -*-
"""持久化工具：原子 JSON 写入。

从 main.py 中拆出，供配置/快照/会话/统计等高频落盘路径复用。
"""
import json
import logging
import os
import tempfile


def atomic_json_write(path, data, indent=1, compact=False):
    """原子写 JSON（唯一临时文件 + os.replace），失败返回 False。

    compact=True 使用紧凑分隔符（快照/会话等大文件体积减半，读写更快）。
    唯一临时文件（mkstemp）防止并发写同一路径互相截断。
    """
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(path) or ".",
            prefix=os.path.basename(path) + ".",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                if compact:
                    json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
                else:
                    json.dump(data, f, ensure_ascii=False, indent=indent)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        os.replace(tmp, path)
        return True
    except Exception:
        logging.exception("原子 JSON 写入失败: %s", path)
        return False
