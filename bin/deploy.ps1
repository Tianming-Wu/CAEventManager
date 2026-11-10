# Deploy the service to a host over ssh.
#
#   .\bin\deploy.ps1 -Remote user@host
#   .\bin\deploy.ps1 -Remote user@host -NoRestart   # sync only
#
# Only source files are copied; the remote data/ and config.json (which holds the
# secret URL id) are never touched.  The remote step verifies that every file
# landed and that the modules import before restarting, so a forgotten file can
# never leave a broken service running.
#
# Runs under either shell: the upload goes through `cmd /c type`, not a PowerShell
# string pipe, because Windows PowerShell 5.1 hands native commands an empty stdin
# for a large string.
param(
    [Parameter(Mandatory = $true)]
    [string]$Remote,
    [string]$Dest = "apps/eventmanager",
    [switch]$NoRestart
)

$ErrorActionPreference = "Stop"
# 脚本在 bin/ 下，仓库根目录是它的上一级：打包、清理与行尾归一化都在根目录做
$root = Split-Path -Parent $PSScriptRoot

# Collected, not hand-maintained: a forgotten module used to reach the server
# and only the import preflight caught it.
$files = @(
    (Get-ChildItem -File -Filter *.py | Select-Object -ExpandProperty Name)
    (Get-ChildItem -File -Filter *.md | Select-Object -ExpandProperty Name)
    "bin/run.sh", "bin/service.sh"
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
    # ship the tarball over ssh stdin instead.  It goes through `cmd /c type` so
    # the bytes are passed through untouched - handing a base64 string to ssh
    # from Window PowerShell 5.1 reaches the far side empty.
    Write-Host "== uploading =="
    $remoteTar = "/tmp/lottery-$stamp.tgz"
    $null = & cmd /c "type `"$tarball`" | ssh $Remote `"cat > $remoteTar`""
    if ($LASTEXITCODE -ne 0) { throw "upload failed" }

    Write-Host "== extracting =="
    $existence = ($files | ForEach-Object { "test -f '$_' || { echo 'MISSING $_' >&2; exit 1; }" }) -join "`n"
    $template = @'
set -eu
mkdir -p ~/__DEST__
cd ~/__DEST__
# 这次包里没有的旧文件要清掉：改过名、挪过目录的文件否则会一直留着，
# 变成文档清单里的幽灵条目。data/ 与 config.json 不参与，实例自己装的东西也不参与。
tar -tzf __TAR__ | sed -e 's|^\./||' -e '/\/$/d' | sort > /tmp/lottery-incoming.txt
find . -type f \
    -not -path './data/*' -not -path './modules/*' -not -path './templates/*' \
    -not -path './screen_packs/*' -not -path './packs/*' -not -name 'config.json' \
    | sed 's|^\./||' | sort > /tmp/lottery-present.txt
comm -23 /tmp/lottery-present.txt /tmp/lottery-incoming.txt > /tmp/lottery-stale.txt || true
while IFS= read -r stale; do
    [ -n "$stale" ] || continue
    rm -f "$stale"
    echo "pruned $stale"
done < /tmp/lottery-stale.txt
rm -f /tmp/lottery-incoming.txt /tmp/lottery-present.txt /tmp/lottery-stale.txt
tar -xzf __TAR__ -C ~/__DEST__
rm -f __TAR__
sed -i 's/\r$//' bin/run.sh bin/service.sh 2>/dev/null || true
chmod +x bin/run.sh bin/service.sh
find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
__EXISTENCE__
python3 -c "import sys; sys.path.insert(0, 'core'); import app, db, model, auth, localconfig, checkin, exporter, panel, packs" || {
    echo "PREFLIGHT FAILED: deployed modules do not import" >&2
    exit 1
}
echo "ok: all files present, imports clean"
'@
    $script = $template.Replace("__DEST__", $Dest).Replace("__TAR__", $remoteTar).Replace("__EXISTENCE__", $existence)

    # Same reason as the upload: the remote end reads this as a file, so it is
    # written out as UTF-8 without a BOM and piped as bytes.
    $scriptPath = Join-Path $env:TEMP "lottery-$stamp.sh"
    [IO.File]::WriteAllText($scriptPath, $script.Replace("`r`n", "`n"),
        (New-Object System.Text.UTF8Encoding $false))
    try {
        $out = & cmd /c "type `"$scriptPath`" | ssh $Remote `"sh -s`" 2>&1"
        $out | ForEach-Object { Write-Host "   remote: $_" }
        if ($LASTEXITCODE -ne 0) { throw "remote extract/preflight failed" }
    } finally {
        Remove-Item $scriptPath -Force -ErrorAction SilentlyContinue
    }

    if (-not $NoRestart) {
        Write-Host "== restarting =="
        $out = & ssh $Remote "cd ~/$Dest && ./bin/service.sh restart && ./bin/service.sh status" 2>&1
        $out | ForEach-Object { Write-Host "   $_" }
        if ($LASTEXITCODE -ne 0) { throw "restart failed" }
    }
}
finally {
    Pop-Location
    if ($tarball -and (Test-Path $tarball)) { Remove-Item $tarball -Force }
}

Write-Host "deploy done."
