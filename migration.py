# -*- coding: utf-8 -*-
"""旧数据目录迁移。

从 main.py 中拆出，负责旧版本数据目录到新目录的整体迁移。
"""
import os
import shutil


def migrate_legacy_data(legacy_dir, data_dir, is_empty_shell):
    """首次运行新版本时，将旧数据目录（DeepSeek_Assistant）整体迁移到 WhaleTalk。

    仅当旧目录存在时执行；若新目录已存在但只是空壳（模块加载刚创建的空结构、
    不含任何文件），先移除空壳再迁移；新目录含真实数据或删除失败则不迁移
    （不阻塞启动，新目录自动重建）。
    """
    try:
        if not os.path.isdir(legacy_dir):
            return False
        if os.path.exists(data_dir):
            if not is_empty_shell(data_dir):
                return False
            shutil.rmtree(data_dir, ignore_errors=True)
            if os.path.exists(data_dir):
                return False
        os.makedirs(os.path.dirname(data_dir), exist_ok=True)
        os.rename(legacy_dir, data_dir)
        return True
    except Exception as e:
        print(f"[鲸语] 旧数据目录迁移失败（不影响使用）: {e}")
        return False
