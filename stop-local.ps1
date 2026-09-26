$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot 'data/local-bot.pid'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Host 'Shoe Bot is not running.'; exit }
$botProcessId = [int](Get-Content -LiteralPath $pidFile -Raw)
$botProcess = Get-Process -Id $botProcessId -ErrorAction SilentlyContinue
$expectedPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$pythonHome = (Get-Content (Join-Path $PSScriptRoot '.venv/pyvenv.cfg') | Where-Object { $_ -match '^home = ' }) -replace '^home = ', ''
$basePython = Join-Path $pythonHome 'python.exe'
if ($botProcess -and ($botProcess.Path -eq $expectedPython -or $botProcess.Path -eq $basePython)) {
    Stop-Process -Id $botProcessId
    Write-Host 'Shoe Bot stopped.'
} else { Write-Host 'The saved bot process is no longer running.' }
