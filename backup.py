# -*- coding: utf-8 -*-
"""项目备份脚本：每次大版本更新前运行，生成完整源码快照压缩包。

用法：
    python backup.py            # 生成 backups/WhaleTalk_vX.Y.Z_时间戳.zip
    python backup.py --prune N  # 生成备份后，仅保留最近 N 个备份（默认 20）
"""
import argparse
import datetime
import os
import re
import sys
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
EXCLUDE_DIRS = {".venv", "__pycache__", "dist", "build", "backups", ".git", ".idea", ".vscode", "evolutions"}
EXCLUDE_EXTS = {".pyc", ".log", ".zip"}
EXCLUDE_FILES = {".clean_exit"}


def current_version():
    try:
        with open(os.path.join(BASE_DIR, "main.py"), encoding="utf-8") as f:
            src = f.read()
        m = re.search(r'^VERSION\s*=\s*"([\d.]+)"', src, re.MULTILINE)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "unknown"


def make_backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    # 微秒精度：同一秒内重复备份不再静默覆盖（ZipFile "w" 直接截断旧文件）
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    name = f"WhaleTalk_v{current_version()}_{ts}.zip"
    path = os.path.join(BACKUP_DIR, name)
    count = 0
    try:
        # compresslevel=1：源码/文本压缩体积仅增 ~5%，CPU 省 2-3 倍（zip 大头是 I/O）
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for root, dirs, files in os.walk(BASE_DIR):
                dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
                for fn in sorted(files):
                    if fn in EXCLUDE_FILES or fn.endswith(tuple(EXCLUDE_EXTS)):
                        continue
                    full = os.path.join(root, fn)
                    arc = os.path.relpath(full, BASE_DIR)
                    zf.write(full, arc)
                    count += 1
    except Exception:
        # 压缩中途异常：清理半成品 zip，不留损坏备份
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    size = os.path.getsize(path)
    print(f"备份完成：{path}")
    print(f"包含 {count} 个文件，{size / 1024:.1f} KB")
    return path


def prune(keep=20):
    zips = sorted(
        f for f in os.listdir(BACKUP_DIR) if f.startswith("WhaleTalk_v") and f.endswith(".zip")
    )
    removed = 0
    for f in zips[:-keep] if keep > 0 else []:
        try:
            os.remove(os.path.join(BACKUP_DIR, f))
            removed += 1
        except OSError:
            pass
    if removed:
        print(f"已清理 {removed} 个旧备份（保留最近 {keep} 个）")


def main():
    parser = argparse.ArgumentParser(description="鲸语 WhaleTalk 项目备份")
    parser.add_argument("--prune", type=int, default=20, help="保留最近 N 个备份（默认 20）")
    args = parser.parse_args()
    print(f"当前版本：v{current_version()}")
    path = make_backup()
    prune(args.prune)
    print("提示：备份包含 config.json（含 API Key），请勿外传备份文件。")


if __name__ == "__main__":
    main()
