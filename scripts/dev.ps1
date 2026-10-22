$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
  $candidate = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
  if (Test-Path $candidate) { $uv = $candidate }
}
if (-not $uv) { throw "uv not found." }

Set-Location $root
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root'; & '$uv' run uvicorn app.main:app --host 127.0.0.1 --port 8787 --reload"
Set-Location (Join-Path $root "web")
npm run dev
