import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing in .env")

STORAGE_ROOT = Path(os.getenv("STORAGE_ROOT", "./storage")).resolve()

UPLOADS_DIR = STORAGE_ROOT / "uploads"
FINGERPRINTS_DIR = STORAGE_ROOT / "fingerprints"

LIBRARY_AUDIO_DIR = STORAGE_ROOT / "library_audio"
LIBRARY_FINGERPRINTS_DIR = STORAGE_ROOT / "library_fingerprints"

SPECTROGRAMS_DIR = STORAGE_ROOT / "spectrograms"

TEMP_DIR = STORAGE_ROOT / "temp"
LOGS_DIR = STORAGE_ROOT / "logs"
EXPORTS_DIR = STORAGE_ROOT / "exports"

# Monitoring (Phase 7)
MONITOR_INBOX_DIR = STORAGE_ROOT / "monitor_inbox"
MONITOR_PROCESSED_DIR = STORAGE_ROOT / "monitor_processed"

# audfprint (Phase 6)
AUDFPRINT_PY = Path(r"C:\Projects\audfprint\audfprint\audfprint.py")
AUDFPRINT_DB = STORAGE_ROOT / "audfprint" / "library.pklz"
AUDFPRINT_LIST = STORAGE_ROOT / "audfprint" / "library_files.txt"

STORAGE_MODE = os.getenv("STORAGE_MODE", "local").lower()  # local | supabase
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "sampledetect")


def ensure_dirs():
    dirs = [
        UPLOADS_DIR,
        FINGERPRINTS_DIR,
        LIBRARY_AUDIO_DIR,
        LIBRARY_FINGERPRINTS_DIR,
        SPECTROGRAMS_DIR,
        TEMP_DIR,
        LOGS_DIR,
        EXPORTS_DIR,
        MONITOR_INBOX_DIR,
        MONITOR_PROCESSED_DIR,
        AUDFPRINT_DB.parent,
    ]
    for p in dirs:
        p.mkdir(parents=True, exist_ok=True)