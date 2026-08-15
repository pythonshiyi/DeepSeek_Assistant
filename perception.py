# -*- coding: utf-8 -*-
"""全局感知：文件夹监控 + 剪贴板感知（后台守护线程，可选启用）。

- FolderWatcher：轮询指定目录，新文件出现时回调（丢文件即自动处理）。
- ClipboardWatcher：轮询剪贴板文本，内容变化时回调（主动感知用户复制了什么）。
两个组件均为 daemon 线程、静默失败；不启用时不消耗资源。
"""
import logging
import os
import threading
import time

logger = logging.getLogger("whaletalk.perception")


class FolderWatcher:
    """监控目录新文件（轮询 mtime/size 快照，不做事件驱动依赖）。"""

    def __init__(self, dirs, on_new_file, interval=3.0):
        self.dirs = [os.path.abspath(os.path.expanduser(str(d))) for d in (dirs or []) if str(d or "").strip()]
        self.on_new_file = on_new_file
        self.interval = max(1.0, min(60.0, float(interval or 3.0)))
        self._stop = threading.Event()
        self._thread = None
        self._seen = set()

    def start(self):
        if self._thread is not None:
            return
        self._seen = self._snapshot()
        self._thread = threading.Thread(target=self._run, name="folder-watcher", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _snapshot(self):
        seen = set()
        for d in self.dirs:
            try:
                for fn in os.listdir(d):
                    full = os.path.join(d, fn)
                    try:
                        st = os.stat(full)
                        seen.add((os.path.normcase(full), st.st_mtime_ns, st.st_size))
                    except OSError:
                        continue
            except OSError:
                continue
        return seen

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                current = self._snapshot()
                new_files = {p for (p, _m, _s) in current} - {p for (p, _m, _s) in self._seen}
                for path in sorted(new_files):
                    if os.path.isfile(path):
                        try:
                            self.on_new_file(path)
                        except Exception:
                            logger.exception("文件夹监控回调失败")
                self._seen = current
            except Exception:
                logger.exception("文件夹监控轮询失败")


class ClipboardWatcher:
    """轮询剪贴板文本，内容变化时回调。"""

    def __init__(self, on_text, interval=2.0):
        self.on_text = on_text
        self.interval = max(1.0, min(30.0, float(interval or 2.0)))
        self._stop = threading.Event()
        self._thread = None
        self._last = ""

    def start(self):
        if self._thread is not None:
            return
        try:
            import pyperclip
            self._last = str(pyperclip.paste() or "")
        except Exception:
            self._last = ""
        self._thread = threading.Thread(target=self._run, name="clipboard-watcher", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                import pyperclip
                text = str(pyperclip.paste() or "")
            except Exception:
                continue
            if text and text != self._last:
                prev, self._last = self._last, text
                try:
                    self.on_text(text, prev)
                except Exception:
                    logger.exception("剪贴板回调失败")
