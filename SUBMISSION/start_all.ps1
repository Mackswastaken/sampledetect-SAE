# SampleDetect SAE - Start Everything (Backend + Frontend)
$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "Launching SampleDetect SAE..." -ForegroundColor Green

# Start backend in a new PowerShell window
Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Here "start_backend.ps1")

# Small delay so backend starts first
Start-Sleep -Seconds 2

# Start frontend in a new PowerShell window
Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Here "start_frontend.ps1")

Write-Host "Done. Open: http://localhost:3000" -ForegroundColor Yellow