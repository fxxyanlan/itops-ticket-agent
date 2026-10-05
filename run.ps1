# =============================================================================
# IT 运维智能工单自动处理 Agent —— 一键启动脚本
# 用法：在项目根目录执行  .\run.ps1
# 流程：环境/依赖 → .env → 初始化数据库 → 启动后端(8000) → 启动前端(8501)
# =============================================================================

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$backendPort = 8000
$frontendPort = 8501
$backendProc = $null

function Info($m) { Write-Host "[一键启动] $m" -ForegroundColor Cyan }
function Warn2($m) { Write-Host "[提示] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[错误] $m" -ForegroundColor Red }

# 判断本机端口是否已被占用
function Test-Port($port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try { $client.Connect("127.0.0.1", $port); return $true }
    catch { return $false }
    finally { $client.Close() }
}

# 探测后端健康检查接口是否可用（用 .NET 请求，避免 PowerShell 命令在非交互下弹提示）
function Test-Health($port) {
    try {
        $req = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:$port/api/health")
        $req.Timeout = 2000
        $resp = $req.GetResponse()
        $resp.Close()
        return $true
    } catch { return $false }
}

# ---------------------------------------------------------------------------
# 1. 虚拟环境（不存在则自动创建）
# ---------------------------------------------------------------------------
if (-not (Test-Path $venvPython)) {
    Info "未发现虚拟环境，正在创建 .venv ..."
    py -m venv .venv
    if (-not (Test-Path $venvPython)) {
        Fail "虚拟环境创建失败：请确认已安装 Python 3.13（py 启动器可用）"
        exit 1
    }
}

# ---------------------------------------------------------------------------
# 2. 依赖（导入自检失败则自动安装）
# ---------------------------------------------------------------------------
& $venvPython -c "import importlib.util as u, sys; sys.exit(0 if all(u.find_spec(m) for m in ('fastapi','langgraph','streamlit')) else 1)"
if ($LASTEXITCODE -ne 0) {
    Info "检测到依赖缺失，开始安装 requirements.txt ..."
    # pip 可能向 stderr 输出进度，临时切换错误策略避免误判为脚本失败
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $venvPython -m pip install -r requirements.txt
    $ErrorActionPreference = $prevEap
    if ($LASTEXITCODE -ne 0) {
        Fail "依赖安装失败，可改用国内镜像重试："
        Fail "  .\.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple"
        exit 1
    }
}

# ---------------------------------------------------------------------------
# 3. 配置文件 .env（不存在则从模板生成）
# ---------------------------------------------------------------------------
if (-not (Test-Path (Join-Path $root ".env"))) {
    Copy-Item (Join-Path $root ".env.example") (Join-Path $root ".env")
    Warn2 "已由 .env.example 生成 .env（LLM_MODE=auto：未填 Key 时自动走离线 mock）"
}

# ---------------------------------------------------------------------------
# 4. 初始化数据库（建表 + 种子数据，幂等，可重复执行）
# ---------------------------------------------------------------------------
Info "初始化数据库 ..."
& $venvPython scripts\init_db.py

# ---------------------------------------------------------------------------
# 5. 启动后端并等待健康检查
# ---------------------------------------------------------------------------
if (Test-Port $backendPort) {
    Warn2 "端口 $backendPort 已被占用，跳过后端启动（视为后端已在运行）"
} else {
    Info "启动 FastAPI 后端：http://127.0.0.1:$backendPort/docs（日志：logs\backend.*.log）"
    $backendProc = Start-Process -FilePath $venvPython `
        -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$backendPort" `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $root "logs\backend.out.log") `
        -RedirectStandardError (Join-Path $root "logs\backend.err.log") `
        -PassThru

    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        if (Test-Health $backendPort) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if ($ready) { Info "后端已就绪（健康检查通过）" }
    else { Warn2 "后端健康检查超时，请查看后端窗口的输出" }
}

# ---------------------------------------------------------------------------
# 6. 启动前端（前台运行；Ctrl+C 退出并自动关闭后端）
# ---------------------------------------------------------------------------
if (Test-Port $frontendPort) {
    Fail "端口 $frontendPort 已被占用，请先关闭已运行的前端进程后重试"
    if ($backendProc -and -not $backendProc.HasExited) { Stop-Process -Id $backendProc.Id -Force -ErrorAction SilentlyContinue }
    exit 1
}

Info "启动 Streamlit 前端：http://127.0.0.1:$frontendPort"
Warn2 "浏览器将自动打开；按 Ctrl+C 停止前端，脚本会同时关闭后端。"

# 延迟打开浏览器（等 Streamlit 就绪），用隐藏的后台进程避免阻塞主流程
Start-Process powershell -WindowStyle Hidden -ArgumentList `
    "-NoProfile", "-Command", "Start-Sleep -Seconds 6; Start-Process 'http://127.0.0.1:$frontendPort'" | Out-Null

try {
    & $venvPython -m streamlit run frontend\streamlit_app.py --server.port $frontendPort --server.address 127.0.0.1 --server.headless true
}
finally {
    if ($backendProc -and -not $backendProc.HasExited) {
        Info "正在关闭后端（PID $($backendProc.Id)）..."
        Stop-Process -Id $backendProc.Id -Force -ErrorAction SilentlyContinue
    }
    Info "已退出。"
}