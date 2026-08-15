# -*- coding: utf-8 -*-
"""进程工具：进程树终止。

从 deepseek_client.py 中拆出，供 run_python / run_command / 后台进程管理等复用。
"""
import os
import subprocess


def kill_tree(proc):
    """终止整个进程树：Windows 上 kill() 只杀直接子进程，pip/pytest/服务器
    派生的孙进程会残留（继续占端口/CPU）。taskkill /T 递归，失败回退 kill()。"""
    try:
        if os.name == "nt" and proc.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=5,
            )
            return
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
