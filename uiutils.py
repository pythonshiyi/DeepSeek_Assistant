# -*- coding: utf-8 -*-
"""UI 工具纯函数/类（无 Tk 依赖或仅标准库）。

从 main.py 中拆出的可复用小件，降低主文件体积并便于独立测试。
"""

MAX_BLOCKS = 8000  # 会话 blocks 渲染文档模型上限（防长对话无界增长）


def index_num(idx):
    """把 Tk 索引 "line.char" 转成可比较的数值键 (line, char)，供 bisect 使用。"""
    ln, _, ch = str(idx).partition(".")
    try:
        return (int(ln), int(ch or "0"))
    except (TypeError, ValueError):
        return (0, 0)


class CappedList(list):
    """有上限的 list：append/extend 后自动裁剪超出部分。

    摊还 O(1)：超限时一次删掉超出的部分（del 头删为 O(n)，逐条删是 O(n²)）。
    on_trim：发生裁剪时的回调（无参），用于向用户提示早期内容被裁剪。
    """

    def __init__(self, items=(), maxlen=MAX_BLOCKS, on_trim=None):
        super().__init__(items)
        self.maxlen = maxlen
        self.on_trim = on_trim
        self._trim()

    def append(self, item):
        super().append(item)
        self._trim()

    def _trim(self):
        if len(self) > self.maxlen:
            del self[: len(self) - self.maxlen]
            if self.on_trim is not None:
                try:
                    self.on_trim()
                except Exception:
                    pass

    def extend(self, items):
        super().extend(items)
        self._trim()
