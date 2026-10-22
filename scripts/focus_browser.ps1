$procs = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
  Where-Object {
    $_.CommandLine -like "*douyin-agent*chrome-profile*" -and
    $_.CommandLine -notlike "*--type=*"
  }
if (-not $procs) {
  Write-Output "NO_DEDICATED_CHROME_PROCESS"
  exit 0
}
foreach ($proc in $procs) {
  $p = Get-Process -Id $proc.ProcessId -ErrorAction SilentlyContinue
  $title = if ($p) { $p.MainWindowTitle } else { "" }
  $handle = if ($p) { $p.MainWindowHandle } else { 0 }
  Write-Output ("PID={0} TITLE={1} HWND={2}" -f $proc.ProcessId, $title, $handle)
  $ws = New-Object -ComObject WScript.Shell
  try { $ws.AppActivate($proc.ProcessId) | Out-Null } catch {}
}
