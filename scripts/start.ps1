$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

if (-not (Test-Path "web\dist\index.html")) {
  Write-Host "Building web..."
  Push-Location web
  npm install
  npm run build
  Pop-Location
}

$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
  $candidate = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
  if (Test-Path $candidate) { $uv = $candidate }
}
if (-not $uv) { throw "uv not found. Install uv or run: uv sync" }

Write-Host "Starting Douyin Agent on http://127.0.0.1:8787/"
Start-Process powershell -ArgumentList "-NoProfile", "-Command", "Start-Sleep 2; Start-Process 'http://127.0.0.1:8787/'" | Out-Null
& $uv run uvicorn app.main:app --host 127.0.0.1 --port 8787
