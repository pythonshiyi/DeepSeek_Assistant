# -*- coding: utf-8 -*-
"""可选依赖清单（依赖状态对话框）。

从 main.py 中拆出，供依赖检测/UI 展示使用。
格式：(导入名, 显示名, 影响功能, 安装命令)
"""

OPTIONAL_DEPS = [
    ("PIL", "Pillow", "图片处理/应用内图片预览/OCR/图表/图标", "pip install pillow"),
    ("pystray", "pystray", "系统托盘常驻", "pip install pystray"),
    ("playwright", "playwright", "浏览器操作/网页截图", "pip install playwright && playwright install chromium"),
    ("faster_whisper", "faster-whisper", "语音转文字", "pip install faster-whisper"),
    ("fitz", "PyMuPDF", "PDF 提取", "pip install PyMuPDF"),
    ("reportlab", "reportlab", "PDF 生成", "pip install reportlab"),
    ("docx", "python-docx", "Word 读写", "pip install python-docx"),
    ("pptx", "python-pptx", "PPT 读取", "pip install python-pptx"),
    ("feedparser", "feedparser", "RSS 聚合/每日简报/公众号写作", "pip install feedparser"),
    ("qrcode", "qrcode", "二维码生成", "pip install qrcode"),
    ("pyzbar", "pyzbar", "二维码识别", "pip install pyzbar（另需系统 zbar）"),
    ("diskcache", "diskcache", "KV 存储", "pip install diskcache"),
    ("imageio_ffmpeg", "imageio-ffmpeg", "音视频处理（内置 ffmpeg）", "pip install imageio-ffmpeg"),
    ("markdown", "markdown", "公众号写作 HTML 输出", "pip install markdown"),
    ("win32com", "pywin32", "语音朗读/语音合成", "pip install pywin32"),
    ("tkinterdnd2", "tkinterdnd2", "文件拖拽到输入框", "pip install tkinterdnd2"),
    ("tiktoken", "tiktoken", "精确 token 估算（缺省回退字符估算）", "pip install tiktoken"),
]
