# 仅启动 Streamlit 前端（http://127.0.0.1:8501）
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
& $py -m streamlit run frontend\streamlit_app.py --server.port 8501 --server.address 127.0.0.1