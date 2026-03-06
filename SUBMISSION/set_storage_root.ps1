# SampleDetect SAE - Set Storage Root (portable)
# This updates backend\.env STORAGE_ROOT to a user-chosen folder.

$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\Projects\sampledetect-mvp"
$BackendEnv  = Join-Path $ProjectRoot "backend\.env"

Write-Host ""
Write-Host "SampleDetect SAE - Storage Root Setup" -ForegroundColor Cyan
Write-Host "Example paths:" -ForegroundColor DarkGray
Write-Host "  C:\SampleDetectStorage" -ForegroundColor DarkGray
Write-Host "  D:\SampleDetectStorage" -ForegroundColor DarkGray
Write-Host ""

$root = Read-Host "Enter STORAGE_ROOT folder path"
if ([string]::IsNullOrWhiteSpace($root)) { throw "No path entered." }

# Create folder if missing
New-Item -ItemType Directory -Force -Path $root | Out-Null

# Ensure required subfolders exist
$subs = @(
  "uploads",
  "fingerprints",
  "spectrograms",
  "temp",
  "logs",
  "exports",
  "library_audio",
  "library_fingerprints",
  "audfprint",
  "monitor_inbox",
  "monitor_processed"
)

foreach ($s in $subs) {
  New-Item -ItemType Directory -Force -Path (Join-Path $root $s) | Out-Null
}

if (!(Test-Path $BackendEnv)) { throw "backend\.env not found at: $BackendEnv" }

# Read env lines
$lines = Get-Content $BackendEnv -ErrorAction Stop

# Replace or add STORAGE_ROOT
$found = $false
$updated = $lines | ForEach-Object {
  if ($_ -match '^\s*STORAGE_ROOT=') {
    $found = $true
    "STORAGE_ROOT=$root"
  } else {
    $_
  }
}

if (-not $found) {
  $updated += "STORAGE_ROOT=$root"
}

Set-Content -Path $BackendEnv -Value $updated -Encoding utf8

Write-Host ""
Write-Host "✅ STORAGE_ROOT set to: $root" -ForegroundColor Green
Write-Host "✅ Folders ensured under that path." -ForegroundColor Green
Write-Host ""
Write-Host "Next: run start_all.ps1" -ForegroundColor Yellow