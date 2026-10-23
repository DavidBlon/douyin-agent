# Douyin Agent 守护进程共享配置与工具函数。
# start.ps1 / stop.ps1 都通过点号方式引入（. path\to\service_lib.ps1）。

$ServiceRoot    = Split-Path $PSScriptRoot -Parent
$ServiceName    = "douyin-agent"
$ServiceHost    = "127.0.0.1"
$ServicePort    = 8787

# RunHiddenConsole 路径：可用环境变量 RHC_PATH 覆盖
if ($env:RHC_PATH) {
    $RhcPath = $env:RHC_PATH
} else {
    $RhcPath = "D:\RunHiddenConsole\x64\RunHiddenConsole.exe"
}

$ServiceLogDir    = Join-Path $ServiceRoot "data\logs"
$ServiceLogFile   = Join-Path $ServiceLogDir "server.log"   # 业务日志（追加）
$ServiceDaemonLog = Join-Path $ServiceLogDir "daemon.log"   # RHC 控制台输出（每次启动重写）
$ServicePidFile   = Join-Path $ServiceLogDir "douyin-agent.pid"
$ServiceRunner    = Join-Path $ServiceRoot "scripts\run_server.py"
$ServicePython    = Join-Path $ServiceRoot ".venv\Scripts\python.exe"
$ServiceProfile   = Join-Path $ServiceRoot "data\chrome-profile"

function Write-Step { param([string]$Message) Write-Host "[*] $Message" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Message) Write-Host "[+] $Message" -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host "[!] $Message" -ForegroundColor Yellow }
function Write-Err  { param([string]$Message) Write-Host "[x] $Message" -ForegroundColor Red }

# 本项目自己的进程：命令行里同时出现项目目录和 app.main:app / run_server.py
function Get-ServiceProcess {
    $root = $ServiceRoot
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $cmd = $_.CommandLine
            if (-not $cmd) { return $false }
            (($cmd -like "*$root*") -or ($cmd -like "*douyin-agent*")) -and
            (($cmd -like "*app.main:app*") -or ($cmd -like "*run_server.py*"))
        }
}

# 本项目专用 Chrome 和 Playwright 驱动进程
function Get-ServiceBrowserProcess {
    $profile = $ServiceProfile
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $cmd = $_.CommandLine
            if (-not $cmd) { return $false }
            ($_.Name -eq "chrome.exe" -and $cmd -like "*$profile*") -or
            ($_.Name -eq "node.exe" -and $cmd -like "*playwright*driver*" -and $cmd -like "*douyin-agent*")
        }
}

# 占用服务端口的进程
function Get-PortOwner {
    $owners = @()
    $conns = Get-NetTCPConnection -LocalPort $ServicePort -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $conns) {
        if ($owners -notcontains $conn.OwningProcess) { $owners += $conn.OwningProcess }
    }
    $owners
}

# 结束进程树（taskkill 一次性带 /T，浏览器子进程也会跟着收掉）
function Stop-ProcessTree {
    param([int[]]$Ids, [string]$Label = "进程")
    foreach ($id in $Ids) {
        if ($id -le 0) { continue }
        Write-Step "结束 $Label PID=$id"
        & taskkill.exe /PID $id /T /F 2>&1 | Out-Null
    }
}
