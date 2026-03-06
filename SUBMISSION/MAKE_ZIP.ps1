# SampleDetect SAE - Build a clean export folder (ZIP-ready)
$ErrorActionPreference = "Stop"

$ProjectRoot = "C:\Projects\sampledetect-mvp"
$OutDir      = Join-Path $ProjectRoot "SUBMISSION_EXPORT"

Write-Host "Building clean export folder..." -ForegroundColor Cyan

# Fresh output
if (Test-Path $OutDir) {
  Remove-Item -Recurse -Force $OutDir
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

function Copy-IfExists($src, $dst) {
  if (Test-Path $src) {
    New-Item -ItemType Directory -Force -Path (Split-Path $dst -Parent) | Out-Null
    Copy-Item -Force $src $dst
    Write-Host "  + $src" -ForegroundColor DarkGray
  } else {
    Write-Host "  ! missing: $src" -ForegroundColor Yellow
  }
}

function Copy-Folder($src, $dst) {
  if (Test-Path $src) {
    New-Item -ItemType Directory -Force -Path (Split-Path $dst -Parent) | Out-Null
    Copy-Item -Recurse -Force $src $dst
    Write-Host "  + $src" -ForegroundColor DarkGray
  } else {
    Write-Host "  ! missing folder: $src" -ForegroundColor Yellow
  }
}

# -------------------------
# 1) SUBMISSION folder (scripts + docs)
# -------------------------
$subSrc = Join-Path $ProjectRoot "SUBMISSION"
$subDst = Join-Path $OutDir "SUBMISSION"
Copy-Folder $subSrc $subDst

# -------------------------
# 2) Backend (only key files)
# -------------------------
$backendSrc = Join-Path $ProjectRoot "backend"
$backendDst = Join-Path $OutDir "backend"

New-Item -ItemType Directory -Force -Path $backendDst | Out-Null

Copy-IfExists (Join-Path $backendSrc "main.py")            (Join-Path $backendDst "main.py")
Copy-IfExists (Join-Path $backendSrc "settings.py")        (Join-Path $backendDst "settings.py")
Copy-IfExists (Join-Path $backendSrc "db.py")              (Join-Path $backendDst "db.py")
Copy-IfExists (Join-Path $backendSrc "models.py")          (Join-Path $backendDst "models.py")
Copy-IfExists (Join-Path $backendSrc "audfprint_runner.py") (Join-Path $backendDst "audfprint_runner.py")
Copy-IfExists (Join-Path $backendSrc "requirements.txt")   (Join-Path $backendDst "requirements.txt")
Copy-IfExists (Join-Path $backendSrc ".env.example")       (Join-Path $backendDst ".env.example")

# Optional extras if present
Copy-IfExists (Join-Path $backendSrc "README.md")          (Join-Path $backendDst "README.md")

# -------------------------
# 3) Frontend (source + config, no node_modules)
# -------------------------
$frontSrc = Join-Path $ProjectRoot "frontend"
$frontDst = Join-Path $OutDir "frontend"

New-Item -ItemType Directory -Force -Path $frontDst | Out-Null

# Copy important folders
Copy-Folder (Join-Path $frontSrc "app")    (Join-Path $frontDst "app")
Copy-Folder (Join-Path $frontSrc "public") (Join-Path $frontDst "public")

# Copy important files
Copy-IfExists (Join-Path $frontSrc "package.json")        (Join-Path $frontDst "package.json")
Copy-IfExists (Join-Path $frontSrc "package-lock.json")   (Join-Path $frontDst "package-lock.json")
Copy-IfExists (Join-Path $frontSrc "next.config.js")      (Join-Path $frontDst "next.config.js")
Copy-IfExists (Join-Path $frontSrc "next.config.mjs")     (Join-Path $frontDst "next.config.mjs")
Copy-IfExists (Join-Path $frontSrc "tsconfig.json")       (Join-Path $frontDst "tsconfig.json")
Copy-IfExists (Join-Path $frontSrc ".env.local.example")  (Join-Path $frontDst ".env.local.example")
Copy-IfExists (Join-Path $frontSrc "README.md")           (Join-Path $frontDst "README.md")

# -------------------------
# 4) Add a short top-level README pointer
# -------------------------
$topReadme = @"
SampleDetect SAE — Export Folder

Open SUBMISSION\README_RUNME.md for instructions.

This export intentionally excludes:
- backend\.env, frontend\.env.local (secrets)
- backend\.venv and frontend\node_modules (large)
- your D:\SampleDetectStorage data (local audio + generated files)

To run:
1) Install deps (README_RUNME)
2) Run SUBMISSION\set_storage_root.ps1
3) Run SUBMISSION\start_all.ps1
"@
Set-Content -Encoding utf8 (Join-Path $OutDir "README_FIRST.txt") $topReadme

Write-Host ""
Write-Host "✅ Export created at: $OutDir" -ForegroundColor Green
Write-Host "Next: Right-click SUBMISSION_EXPORT -> Send to -> Compressed (zipped) folder" -ForegroundColor Yellow