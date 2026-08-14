# -*- coding: utf-8 -*-
"""跨模块共享的纯函数与常量（无 GUI / 无 API 依赖）。

从 main.py / deepseek_client.py / taskpanel.py 中抽取，消除重复实现漂移：
- cron 5 字段表达式引擎（校验 / 匹配）
- 本地绝对路径正则 PATH_RE
- Windows OCR PowerShell 脚本（占位符统一 @PATH@，避免与 $ 变量名冲突）
"""
import re
from datetime import datetime

# ============================ 峰谷定价 ============================
# DeepSeek 峰谷定价：高峰时段（北京时间 9:00-12:00 / 14:00-18:00），
# 空闲时段价格为高峰时段价格的一半（2026-08-17 起生效）
PEAK_HOURS = ((9, 12), (14, 18))


def is_peak_hour(now=None):
    """判断当前是否为 DeepSeek 高峰计费时段。"""
    try:
        h = (now or datetime.now()).hour
        return any(a <= h < b for a, b in PEAK_HOURS)
    except Exception:
        return False

# ============================ cron 引擎 ============================
# cron 5 字段的值域（分/时/日/月/周），weekday 1=周一…7=周日
CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (1, 7))


def cron_int(s):
    """字符串安全转 int；失败返回 None。"""
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def cron_field_match(field, value):
    """匹配 cron 单字段（* / 逗号 / 连字符 / 步进）。"""
    field = str(field).strip()
    if field in ("*", "?"):
        return True
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        if "/" in part:
            base, _, step = part.partition("/")
            try:
                step = int(step)
            except ValueError:
                continue
            start = 0 if base in ("*", "?") else cron_int(base)
            if start is None:
                continue
            if value >= start and (value - start) % step == 0:
                return True
        elif "-" in part:
            lo, _, hi = part.partition("-")
            lo_v, hi_v = cron_int(lo), cron_int(hi)
            if lo_v is not None and hi_v is not None and lo_v <= value <= hi_v:
                return True
        else:
            v = cron_int(part)
            if v is not None and v == value:
                return True
    return False


def cron_field_ok(field, pos=0):
    """cron 字段语法校验：仅允许数字、*、,、-、/，且值域合法（添加定时任务时防止非法表达式）。"""
    field = str(field or "").strip()
    if not field:
        return False
    if field in ("*", "?"):
        return True
    lo, hi = CRON_RANGES[pos]
    for part in field.split(","):
        part = part.strip()
        if not part:
            return False
        if "/" in part:
            base, _, step = part.partition("/")
            if cron_int(step) is None or not (1 <= cron_int(step) <= hi):
                return False
            if base not in ("*", "?", "") and not (
                cron_int(base) is not None and lo <= cron_int(base) <= hi
            ):
                return False
        elif "-" in part:
            l, _, r = part.partition("-")
            lv, rv = cron_int(l), cron_int(r)
            if lv is None or rv is None or not (lo <= lv <= hi and lo <= rv <= hi and lv <= rv):
                return False
        else:
            v = cron_int(part)
            if v is None or not (lo <= v <= hi):
                return False
    return True


def cron_match(expr, now):
    """匹配 5 字段 cron 表达式（分 时 日 月 周，周 1=周一…7=周日）。"""
    try:
        fields = str(expr).strip().split()
        if len(fields) != 5:
            return False
        minute, hour, day, month, weekday = fields
        return (
            cron_field_match(minute, now.minute)
            and cron_field_match(hour, now.hour)
            and cron_field_match(day, now.day)
            and cron_field_match(month, now.month)
            and cron_field_match(weekday, now.isoweekday())
        )
    except Exception:
        return False


# ============================ 本地路径正则 ============================
# 形如 C:\path\file 的 Windows 绝对路径（文件/目录均可命中）
PATH_RE = re.compile(
    r"[A-Za-z]:[\\/][^\s'\"()<>|,;（）【】《》，。、；：？！]+"
)

# ============================ Windows OCR 脚本 ============================
# 占位符用 @PATH@ 而非 $path：用户路径若含字面 "$path" 会被顺序 replace 二次替换污染
OCR_IMAGE_PS = r"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}
[Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync('@PATH@')) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) { '当前系统语言不支持 OCR' } else {
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $result.Text
}
""".lstrip()
