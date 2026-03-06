# SampleDetect SAE - Start Frontend
$ErrorActionPreference = "Stop"

$ProjectRoot  = "C:\Projects\sampledetect-mvp"
$FrontendDir  = Join-Path $ProjectRoot "frontend"

Write-Host "Starting frontend..." -ForegroundColor Cyan
Set-Location $FrontendDir

npm run dev