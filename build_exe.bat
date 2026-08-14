@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo [首次运行] 正在创建虚拟环境并安装依赖...
    py -3 -m venv .venv || python -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)
.venv\Scripts\python.exe -m pip install pyinstaller
.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --icon app.ico --name WhaleTalk main.py
echo.
echo 打包完成: dist\WhaleTalk.exe
echo 注意：打包前请先清空 config.json 中的 API Key。
pause
