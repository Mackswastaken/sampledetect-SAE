# SampleDetect SAE - Start Backend
$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\Projects\sampledetect-mvp"
$BackendDir  = Join-Path $ProjectRoot "backend"

Write-Host "Starting backend..." -ForegroundColor Cyan
Set-Location $BackendDir

# Activate venv
& ".\.venv\Scripts\Activate.ps1"

# Start FastAPI
python -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload