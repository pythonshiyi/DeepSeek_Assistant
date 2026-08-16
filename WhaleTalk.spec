# -*- mode: python ; coding: utf-8 -*-
# 鲸语 WhaleTalk TkUI 打包配置（PyInstaller）
# 用法：pyinstaller WhaleTalk.spec --noconfirm
# 大型可选依赖（playwright / faster-whisper / PyMuPDF 等）未安装时自动禁用，
# 此处显式排除，保持体积可控。


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('static', 'static'),
        ('templates', 'templates'),
        ('sample_plugins', 'sample_plugins'),
        ('evolutions', 'evolutions'),
        ('app.ico', '.'),
    ],
    hiddenimports=[
        'tiktoken_ext.openai_public',
        'tkinterdnd2',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'playwright',
        'faster_whisper',
        'pyzbar',
        'psycopg2',
        'fitz',
        'reportlab',
        'imageio_ffmpeg',
        'curl_cffi',
        'pptx',
        'PySide6',
        'PyQt5',
        'IPython',
        'pytest',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='WhaleTalk',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app.ico'],
)
