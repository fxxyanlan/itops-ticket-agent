# 仅启动 FastAPI 后端（http://127.0.0.1:8000/docs）
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
& $py scripts\init_db.py
& $py -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000