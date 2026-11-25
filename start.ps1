param([switch]$NoBrowser, [switch]$Watch, [switch]$EnableAutoStart)
$ErrorActionPreference = 'Stop'
$stateDirectory = Join-Path $env:LOCALAPPDATA 'ETFPersonalWallpaper'
New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
if ($Watch) {
    $watchMutex = New-Object System.Threading.Mutex($false, 'Local\ETFPersonalWallpaperSupervisor')
    $ownsMutex = $false
    try {
        try { $ownsMutex = $watchMutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $ownsMutex = $true }
        if (-not $ownsMutex) { return }
        $PID | Set-Content -LiteralPath (Join-Path $stateDirectory 'supervisor.pid')
        while ($true) {
            try { & $PSCommandPath -NoBrowser | Out-Null }
            catch { Add-Content -LiteralPath (Join-Path $stateDirectory 'supervisor-error.log') -Value "$(Get-Date -Format o) $($_.Exception.Message)" }
            Start-Sleep -Seconds 15
        }
    } finally {
        if ($ownsMutex) { $watchMutex.ReleaseMutex() }
        $watchMutex.Dispose()
    }
    return
}
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
if ($EnableAutoStart) {
    $powershellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $startupDirectory = [Environment]::GetFolderPath('Startup')
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path $startupDirectory 'ETF Personal Wallpaper.lnk'))
    $shortcut.TargetPath = $powershellPath
    $shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $PSCommandPath + '" -Watch'
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.WindowStyle = 7
    $shortcut.Description = 'ETF wallpaper local quotes: start at login and restart if the server exits.'
    $shortcut.Save()
    Start-Process -FilePath $powershellPath -ArgumentList $shortcut.Arguments -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
}
if (-not $NoBrowser) { Start-Process $url }
Write-Output "ETF board ready: $url"
