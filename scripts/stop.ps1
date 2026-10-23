# 停止 Douyin Agent 守护进程。
#
# 先用 RunHiddenConsole 的 /k 信号让守护进程和子进程一起退出，
# 再兜底清理残留的服务进程、专用 Chrome 和 Playwright 驱动。

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "service_lib.ps1")

Write-Host ""
Write-Host "Douyin Agent 守护进程停止" -ForegroundColor White
Write-Host "------------------------------------------" -ForegroundColor DarkGray

$running = Get-ServiceProcess
if ($running.Count -eq 0) {
    Write-Warn "没有发现运行中的实例。"
}

# 1. 通过信号名停止守护进程（守护进程与子进程会一起退出）
if (Test-Path $RhcPath) {
    Write-Step "发送停止信号（$ServiceName）..."
    & $RhcPath /k /n $ServiceName 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "停止信号未生效（exit=$LASTEXITCODE），改用强制清理。"
    }
} else {
    Write-Warn "找不到 RunHiddenConsole：$RhcPath，改用强制清理。"
}

# 2. 等待端口释放
$deadline = (Get-Date).AddSeconds(10)
while ((Get-Date) -lt $deadline) {
    if ((Get-PortOwner).Count -eq 0) { break }
    Start-Sleep -Milliseconds 500
}

# 3. 兜底：结束残留的服务进程（含守护进程本身）
$leftover = Get-ServiceProcess
if ($leftover.Count -gt 0) {
    Write-Warn "仍有 $($leftover.Count) 个服务进程残留，强制结束。"
    Stop-ProcessTree -Ids @($leftover.ProcessId) -Label "服务进程"
}

# 4. 兜底：结束专用 Chrome 与 Playwright 驱动
$browser = Get-ServiceBrowserProcess
if ($browser.Count -gt 0) {
    Write-Warn "清理 $($browser.Count) 个浏览器/驱动进程。"
    Stop-ProcessTree -Ids @($browser.ProcessId) -Label "浏览器进程"
}

# 5. 汇总
Start-Sleep -Milliseconds 500
$still = Get-PortOwner
if ($still.Count -eq 0) {
    Write-Ok "端口 $ServicePort 已释放，服务已完全停止。"
} else {
    Write-Warn "端口 $ServicePort 仍被占用（PID $($still -join ', ')），请检查是否有其它程序在使用。"
}
Write-Host ""
