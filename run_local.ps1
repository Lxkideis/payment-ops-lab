param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Create .venv and install requirements-lock.txt first; see README.md.'
}
& $taskPython scripts/local_db.py start
if ($LASTEXITCODE -ne 0) { throw 'Database startup failed.' }
& $taskPython scripts/seed_demo.py
if ($LASTEXITCODE -ne 0) { throw 'Synthetic-data verification failed.' }
Write-Output "Open http://127.0.0.1:$Port (Ctrl+C stops the web server)."
& $taskPython -m uvicorn app.main:app --host 127.0.0.1 --port $Port
