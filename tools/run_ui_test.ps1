# Local UI test: wipe the local database, start the service, seed a realistic
# 60-student event, then drive the real pages with Playwright (uses Edge).
#
#   .\tools\run_ui_test.ps1
#
# Screenshots end up in tmp\ui\.
param(
    [int]$Port = 8931,
    [switch]$KeepData
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== stopping any local instance ==" -ErrorAction SilentlyContinue
Get-Process python -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -like "*python*" } |
    ForEach-Object {
        try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch { }
    }
Start-Sleep -Milliseconds 700

if (-not $KeepData) {
    Write-Host "== resetting data =="
    Remove-Item -Recurse -Force "data" -ErrorAction SilentlyContinue
    New-Item -ItemType Directory "data" | Out-Null
}

$env:PYTHONIOENCODING = "utf-8"
Write-Host "== starting service on 127.0.0.1:$Port =="
Start-Process -FilePath "python" `
    -ArgumentList "app.py", "--host", "127.0.0.1", "--port", "$Port" `
    -RedirectStandardOutput "data\out.log" -RedirectStandardError "data\err.log" -NoNewWindow
Start-Sleep -Seconds 2

if (-not (Test-Path "data\owner_password.txt")) { throw "service did not boot; see data\err.log" }
$pw = (Get-Content "data\owner_password.txt" | Where-Object { $_ -like "password:*" }) -replace "^password:\s*", ""
Write-Host "   owner password: $pw"

if (-not $KeepData) {
    Write-Host "== seeding demo event =="
    python tools\seed_demo.py "http://127.0.0.1:$Port" $pw
}

Write-Host "== running UI test =="
python tools\ui_test.py "http://127.0.0.1:$Port" $pw
exit $LASTEXITCODE
