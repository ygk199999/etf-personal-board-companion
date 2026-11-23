param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$stateDirectory = Join-Path $env:LOCALAPPDATA 'ETFPersonalWallpaper'
New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
$url = 'http://127.0.0.1:8766'
$running = $false
try {
    $existing = Invoke-RestMethod -Uri "$url/api/state" -TimeoutSec 2
    $running = $null -ne $existing.account -and $null -ne $existing.settings
} catch {}
if (-not $running) {
    $pythonPath = (& py -3.13 -c 'import sys; print(sys.executable)').Trim()
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Python 3.13을 찾지 못했습니다.' }
    $serverFile = Join-Path $PSScriptRoot 'server.py'
    $process = Start-Process -FilePath $pythonPath -ArgumentList @('"' + $serverFile + '"') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $stateDirectory 'server.log') -RedirectStandardError (Join-Path $stateDirectory 'server-error.log')
    $process.Id | Set-Content -LiteralPath (Join-Path $stateDirectory 'server.pid')
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 300
        try { $null = Invoke-RestMethod -Uri "$url/api/state" -TimeoutSec 1; $running = $true; break } catch {}
        if ($process.HasExited) { break }
    }
}
if (-not $running) { throw '서버를 시작하지 못했습니다. 로컬 상태 폴더의 server-error.log를 확인하세요.' }
if (-not $NoBrowser) { Start-Process $url }
Write-Output "ETF board ready: $url"
