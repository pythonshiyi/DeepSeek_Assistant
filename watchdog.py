# -*- coding: utf-8 -*-
"""鲸语常驻守护（7×24 值守）：崩溃自动拉起 + 开机自启配套。

用法：
    pythonw watchdog.py        # 源码运行常驻守护（可注册到开机自启）
    python watchdog.py --once  # 只启动一次，退出不重启（调试用）

逻辑：
- 启动 main.py；进程退出后按退避间隔自动重启（3s/6s/12s/…/60s 封顶）。
- 正常退出码 0 时默认也重启（常驻语义）；连续正常退出也不停止。
- 如需停止：任务管理器结束 watchdog.py 与 main.py，或删除开机自启项。
"""
import os
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_SCRIPT = os.path.join(BASE_DIR, "main.py")


def main():
    once = "--once" in sys.argv
    delays = [3, 6, 12, 24, 60]
    attempt = 0
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    cmd = [pythonw, MAIN_SCRIPT] if os.path.isfile(pythonw) else [sys.executable, MAIN_SCRIPT]
    while True:
        try:
            proc = subprocess.Popen(
                cmd, cwd=BASE_DIR,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            )
            code = proc.wait()
        except Exception:
            code = -1
        if once:
            break
        delay = delays[min(attempt, len(delays) - 1)]
        attempt += 1
        time.sleep(delay)


if __name__ == "__main__":
    main()
