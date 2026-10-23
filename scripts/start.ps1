# 启动 Douyin Agent 守护进程。
#
# 方式：RunHiddenConsole 隐藏拉起 scripts/run_server.py（无黑窗、崩溃自动重启），
# 服务常驻后台，日志写入 data\logs\server.log。

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "service_lib.ps1")

Write-Host ""
Write-Host "Douyin Agent 守护进程启动" -ForegroundColor White
Write-Host "------------------------------------------" -ForegroundColor DarkGray

# 1. RunHiddenConsole
if (-not (Test-Path $RhcPath)) {
    Write-Err "找不到 RunHiddenConsole：$RhcPath"
    Write-Err "可用环境变量 RHC_PATH 指定完整路径。"
    exit 1
}

# 2. 日志目录
if (-not (Test-Path $ServiceLogDir)) {
    New-Item -ItemType Directory -Force -Path $ServiceLogDir | Out-Null
}

# 3. 前端构建
$webIndex = Join-Path $ServiceRoot "web\dist\index.html"
if (-not (Test-Path $webIndex)) {
    Write-Step "构建前端（web/dist 不存在）..."
    Push-Location (Join-Path $ServiceRoot "web")
    try {
        npm install
        npm run build
    } finally {
        Pop-Location
    }
    if (-not (Test-Path $webIndex)) {
        Write-Err "前端构建失败：$webIndex 不存在"
        exit 1
    }
}

# 4. Python 环境
function Get-UvPath {
    $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $uv) {
        $candidate = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
        if (Test-Path $candidate) { $uv = $candidate }
    }
    $uv
}

function Test-PythonDeps {
    # PS 5.1 下原生命令的 stderr 会被包成错误记录，这里临时放宽策略，
    # 否则依赖缺失时的报错会直接中断脚本，uv sync 自愈就走不到。
    $ErrorActionPreference = "Continue"
    & $ServicePython -c "import uvicorn, fastapi, playwright" 2>&1 | Out-Null
    $code = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    $code -eq 0
}

if (-not (Test-Path $ServicePython)) {
    Write-Step "创建 Python 虚拟环境..."
    $uv = Get-UvPath
    if (-not $uv) {
        Write-Err "找不到 uv，请先安装 uv 并执行 uv sync"
        exit 1
    }
    & $uv sync
}

if (-not (Test-PythonDeps)) {
    Write-Step "依赖缺失，执行 uv sync ..."
    $uv = Get-UvPath
    if (-not $uv) {
        Write-Err "找不到 uv，请先安装 uv 并执行 uv sync"
        exit 1
    }
    & $uv sync
    if (-not (Test-PythonDeps)) {
        Write-Err "Python 依赖不完整，请手动执行：uv sync"
        exit 1
    }
}

# 5. 端口检查：旧实例先停掉，别人占用的端口直接报错
$owners = Get-PortOwner
if ($owners.Count -gt 0) {
    $mine = @()
    foreach ($id in $owners) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
        if ($proc -and $proc.CommandLine -and
            (($proc.CommandLine -like "*$ServiceRoot*") -or ($proc.CommandLine -like "*douyin-agent*")) -and
            (($proc.CommandLine -like "*app.main:app*") -or ($proc.CommandLine -like "*run_server.py*"))) {
            $mine += $proc
        }
    }
    if ($mine.Count -gt 0) {
        Write-Warn "检测到本项目的旧实例（PID $($mine.ProcessId -join ', ')），先停掉再启动。"
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "stop.ps1") | Out-Null
        Start-Sleep -Seconds 1
    } else {
        Write-Err "端口 $ServicePort 已被其它程序占用（PID $($owners -join ', ')），请先释放该端口。"
        exit 1
    }
}

# 6. 交给 RunHiddenConsole 拉起守护进程
Write-Step "启动守护进程（RunHiddenConsole，崩溃自动重启）..."
$rhcArgs = @(
    "/l", "/r", "/n", $ServiceName,
    "/o", $ServiceDaemonLog,
    "/p", $ServicePidFile,
    $ServicePython, $ServiceRunner,
    "--host", $ServiceHost,
    "--port", "$ServicePort",
    "--app-dir", $ServiceRoot,
    "--log-file", $ServiceLogFile
)
& $RhcPath @rhcArgs
if ($LASTEXITCODE -ne 0) {
    Write-Err "RunHiddenConsole 启动失败（exit=$LASTEXITCODE）"
    exit 1
}

# 7. 等待服务就绪（顺带盯着子进程是否在反复重启）
$url = "http://${ServiceHost}:$ServicePort/"
$healthUrl = "http://${ServiceHost}:$ServicePort/api/health"
$deadline = (Get-Date).AddSeconds(40)
$ready = $false
$lastPid = ""
$restarts = 0
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500

    if (Test-Path $ServicePidFile) {
        $currentPid = (Get-Content $ServicePidFile -Raw -ErrorAction SilentlyContinue).Trim()
        if ($currentPid -and $currentPid -ne $lastPid) {
            if ($lastPid) { $restarts++ }
            $lastPid = $currentPid
            if ($restarts -ge 3) {
                Write-Err "子进程反复重启（已重启 $restarts 次），启动失败。详见 $ServiceLogFile"
                exit 1
            }
        }
    }

    try {
        $response = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 3 -UseBasicParsing
        if ($response.StatusCode -eq 200) { $ready = $true; break }
    } catch {
        # 还没起来，继续等
    }
}

if (-not $ready) {
    Write-Err "等待服务就绪超时（40 秒），启动失败。详见 $ServiceLogFile"
    exit 1
}

# 8. 打开控制台页面
Start-Process $url | Out-Null

Write-Host ""
Write-Ok "服务已启动：http://${ServiceHost}:$ServicePort/"
Write-Host "    进程 PID : $lastPid"
Write-Host "    业务日志 : $ServiceLogFile"
Write-Host "    守护日志 : $ServiceDaemonLog"
Write-Host "    停止服务 : 双击 stop.bat"
Write-Host ""
