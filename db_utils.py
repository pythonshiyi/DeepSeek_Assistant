# -*- coding: utf-8 -*-
"""数据库工具：只读校验、SQL 预览、表格格式化。

从 deepseek_client.py 中拆出的纯函数/常量，供数据库查询/执行工具复用。
"""
import re

# 单元格显示截断上限（防超长单元格撑爆上下文）
TABLE_CELL_MAX = 100

# 只读查询禁止的服务器端功能关键字（前缀白名单可被其绕过，读写服务器文件 / DoS）：
# MySQL: SELECT ... INTO OUTFILE/DUMPFILE、LOAD_FILE、SLEEP
# PostgreSQL: lo_export / pg_read_file / pg_write_file / pg_sleep
DB_FORBIDDEN_KEYWORDS = (
    "INTO OUTFILE",
    "INTO DUMPFILE",
    "LOAD_FILE",
    "LO_EXPORT",
    "LO_IMPORT",
    "PG_READ_FILE",
    "PG_WRITE_FILE",
    "PG_READ_BINARY_FILE",
    "PG_SLEEP",
    "SLEEP(",
    "BENCHMARK(",
    "PG_DATABASE_SIZE",
    "DEFAULT_TABLESPACE",
)


def readonly_stmt(sql):
    """判断 SQL 是否为安全的只读语句（SELECT/SHOW/DESC/PRAGMA/EXPLAIN）。"""
    stmt = str(sql or "").strip()
    if not stmt:
        return False
    upper = stmt.upper()
    if not upper.startswith(("SELECT", "SHOW", "DESC", "PRAGMA", "EXPLAIN")):
        return False
    # 分号隔离的附加语句（SELECT 1; DROP TABLE ...）：带内部分号的整句拒绝
    if ";" in stmt.rstrip(";"):
        return False
    for kw in DB_FORBIDDEN_KEYWORDS:
        if kw in upper:
            return False
    return True


def db_preview_sql(stmt):
    """把 UPDATE/DELETE 改写为等价的 SELECT（用于变更行数预览）。"""
    m = re.match(r"(UPDATE|DELETE)\s+", stmt, re.I)
    wm = re.search(r"\bWHERE\b", stmt, re.I)
    if not m or not wm:
        return None
    if m.group(1).upper() == "UPDATE":
        sm = re.search(r"\bSET\b", stmt, re.I)
        table_part = stmt[m.end():sm.start()] if sm else stmt[m.end():wm.start()]
        return "SELECT * FROM " + table_part + stmt[wm.start():]
    return "SELECT * FROM " + stmt[m.end():wm.start()] + stmt[wm.start():]


def table_to_md(rows, cell_max=TABLE_CELL_MAX):
    """把 list[list] 转 Markdown 表格（含单元格截断与空行过滤）。"""
    rows = [[str(c).strip() for c in r] for r in rows]
    rows = [[c[:cell_max] + ("…" if len(c) > cell_max else "") for c in r] for r in rows]
    if not rows:
        return "（空表格）"
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join("---" for _ in rows[0]) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)
