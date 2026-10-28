$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$port = if ($env:TAPO_WEB_PORT) { [int]$env:TAPO_WEB_PORT } else { 8088 }
$bindAddress = if ($env:TAPO_WEB_HOST) { $env:TAPO_WEB_HOST } else { '127.0.0.1' }
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Install dependencies in .venv first (see README.md).' }
foreach ($tool in @('ffmpeg', 'ffprobe')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool is missing from PATH." }
}
try {
    $health = Invoke-RestMethod "http://127.0.0.1:$port/health" -TimeoutSec 2
    if ($health -eq 'ok') { Write-Host "Already running: http://localhost:$port"; exit 0 }
} catch {}
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'logs') | Out-Null
$serverProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'app.server', '--host', $bindAddress, '--port', "$port") -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'logs\server.log') -RedirectStandardError (Join-Path $PSScriptRoot 'logs\server-error.log') -PassThru
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    Start-Sleep -Milliseconds 500
    if ($serverProcess.HasExited) { throw 'Server exited. See logs/server-error.log.' }
    try {
        Invoke-RestMethod "http://127.0.0.1:$port/health" -TimeoutSec 1 | Out-Null
        Write-Host "Running: http://localhost:$port"
        exit 0
    } catch {}
}
throw 'Startup timed out. See logs/server-error.log.'
