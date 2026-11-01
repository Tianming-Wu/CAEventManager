# Deploy the lottery service to lab812.
#
#   .\deploy.ps1             # sync + restart
#   .\deploy.ps1 -NoRestart  # sync only
#
# Only source files are copied; the remote data/ and config.json (which holds the
# secret URL id) are never touched.  The remote step verifies that every file
# landed and that the modules import before restarting, so a forgotten file can
# never leave a broken service running.
param(
    [string]$Remote = "laffey@lab812",
    [string]$Dest = "apps/lottery",
    [switch]$NoRestart
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

# Collected, not hand-maintained: a forgotten module used to reach the server
# and only the import preflight caught it.
$files = @(
    (Get-ChildItem -File -Filter *.py | Select-Object -ExpandProperty Name)
    (Get-ChildItem -File -Filter *.md | Select-Object -ExpandProperty Name)
    "run.sh", "service.sh"
)
# Collected too: tpl / static / panel_static / tools - a hand-written list here
# silently skipped a new asset directory once already.
$skip = @("data", "tmp", "__pycache__", ".git", ".vscode")
$dirs = @(Get-ChildItem -Directory | Where-Object { $skip -notcontains $_.Name } |
          Select-Object -ExpandProperty Name)

$tarball = $null
Push-Location $root
try {
    $missing = $files | Where-Object { -not (Test-Path $_) }
    if ($missing) { throw "missing files: $($missing -join ', ')" }

    Write-Host "== normalising line endings =="
    & python tools\normalize_eol.py
    if ($LASTEXITCODE -ne 0) { throw "line-ending normalisation failed" }

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $tarball = Join-Path $env:TEMP "lottery-$stamp.tgz"
    if (Test-Path $tarball) { Remove-Item $tarball -Force }

    Write-Host "== packing =="
    & tar -czf $tarball @files @dirs
    if ($LASTEXITCODE -ne 0) { throw "tar failed" }
    Write-Host ("   {0:N0} bytes -> {1}" -f (Get-Item $tarball).Length, $tarball)

    # The container has no sftp-server subsystem, so scp does not work here:
    # ship the tarball as base64 over ssh stdin instead.
    Write-Host "== uploading =="
    $b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($tarball))
    $remoteTar = "/tmp/lottery-$stamp.tgz"
    $b64 | & ssh $Remote "tr -d '\r' | base64 -d > $remoteTar"
    if ($LASTEXITCODE -ne 0) { throw "upload failed" }

    Write-Host "== extracting =="
    $existence = ($files | ForEach-Object { "test -f '$_' || { echo 'MISSING $_' >&2; exit 1; }" }) -join "`n"
    $template = @'
set -eu
mkdir -p ~/__DEST__
cd ~/__DEST__
tar -xzf __TAR__ -C ~/__DEST__
rm -f __TAR__
sed -i 's/\r$//' run.sh service.sh 2>/dev/null || true
chmod +x run.sh service.sh
find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
__EXISTENCE__
python3 -c "import app, db, model, auth, localconfig, checkin, exporter, panel" || {
    echo "PREFLIGHT FAILED: deployed modules do not import" >&2
    exit 1
}
echo "ok: all files present, imports clean"
'@
    $script = $template.Replace("__DEST__", $Dest).Replace("__TAR__", $remoteTar).Replace("__EXISTENCE__", $existence)

    $out = $script | & ssh $Remote "sh -s" 2>&1
    $out | ForEach-Object { Write-Host "   remote: $_" }
    if ($LASTEXITCODE -ne 0) { throw "remote extract/preflight failed" }

    if (-not $NoRestart) {
        Write-Host "== restarting =="
        $out = & ssh $Remote "cd ~/$Dest && ./service.sh restart && ./service.sh status" 2>&1
        $out | ForEach-Object { Write-Host "   $_" }
        if ($LASTEXITCODE -ne 0) { throw "restart failed" }
    }
}
finally {
    Pop-Location
    if ($tarball -and (Test-Path $tarball)) { Remove-Item $tarball -Force }
}

Write-Host "deploy done."
