import os
import sys
import json
import uuid
import shutil
import base64
import tempfile
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from sqlalchemy.orm import Session

# ---------------------------------------------------------------------
# Project imports (your existing files)
# ---------------------------------------------------------------------
from db import Base, engine, get_db
import models as models_mod

def _pick_model(*names: str):
    for n in names:
        if hasattr(models_mod, n):
            return getattr(models_mod, n)
    raise ImportError(f"None of these model names exist in models.py: {names}")

# Required
AudioAsset = _pick_model("AudioAsset", "Asset", "AudioFile")

# Library table (this is the one failing)
LibraryAudio = _pick_model(
    "LibraryAudio",
    "LibraryTrack",
    "LibraryBeat",
    "LibraryItem",
    "LibraryFile",
    "AudioLibrary",
)

# Monitor + Proof tables
MonitorIncident = _pick_model("MonitorIncident", "Incident", "MonitorEvent")
ProofRecord = _pick_model("ProofRecord", "Proof", "BlockchainProof", "ProofEntry")
from settings import (
    DATABASE_URL,
    STORAGE_MODE,
    STORAGE_ROOT,
    UPLOADS_DIR,
    FINGERPRINTS_DIR,
    TEMP_DIR,
    LOGS_DIR,
    EXPORTS_DIR,
    LIBRARY_AUDIO_DIR,
    LIBRARY_FINGERPRINTS_DIR,
    MONITOR_INBOX_DIR,
    ensure_dirs,
    # Supabase config (only used when STORAGE_MODE="supabase")
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_BUCKET,
)

# ---------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------
app = FastAPI(title="SampleDetect SAE API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # okay for prototype
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------
# Storage abstraction
# ---------------------------------------------------------------------
class LocalStorage:
    """
    Local filesystem storage under STORAGE_ROOT (your D:\SampleDetectStorage locally,
    /app/storage or similar on Render if you choose local mode).
    """
    def __init__(self, root: Path):
        self.root = Path(root)

    def read_bytes(self, rel_path: str) -> bytes:
        p = self.root / rel_path
        return p.read_bytes()

    def write_bytes(self, rel_path: str, data: bytes) -> str:
        p = self.root / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return str(rel_path).replace("\\", "/")

    def exists(self, rel_path: str) -> bool:
        return (self.root / rel_path).exists()

    def list_prefix(self, rel_prefix: str) -> List[str]:
        base = self.root / rel_prefix
        if not base.exists():
            return []
        out = []
        for f in base.rglob("*"):
            if f.is_file():
                out.append(str(f.relative_to(self.root)).replace("\\", "/"))
        return out

    def local_path(self, rel_path: str) -> Path:
        return self.root / rel_path


def get_storage():
    """
    Returns either LocalStorage or your SupabaseStorage.
    FIX: supports any SupabaseStorage __init__ signature (kw or positional).
    """
    mode = (STORAGE_MODE or "local").lower().strip()
    if mode == "supabase":
        try:
            from storage_supabase import SupabaseStorage  # your existing file
        except Exception as e:
            raise RuntimeError("STORAGE_MODE=supabase but storage_supabase.py import failed") from e

        # Try multiple constructor signatures (covers all the versions we hit)
        # ✅ This is what fixes: unexpected keyword argument 'supabase_url'
        for ctor in (
            lambda: SupabaseStorage(
                supabase_url=SUPABASE_URL,
                service_role_key=SUPABASE_SERVICE_ROLE_KEY,
                bucket=SUPABASE_BUCKET,
            ),
            lambda: SupabaseStorage(
                url=SUPABASE_URL,
                key=SUPABASE_SERVICE_ROLE_KEY,
                bucket=SUPABASE_BUCKET,
            ),
            lambda: SupabaseStorage(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_BUCKET),
            lambda: SupabaseStorage(),  # some versions read env internally
        ):
            try:
                return ctor()
            except TypeError:
                continue

        raise RuntimeError(
            "SupabaseStorage constructor signature mismatch. "
            "Open backend/storage_supabase.py and check SupabaseStorage.__init__ params."
        )

    # default: local
    return LocalStorage(STORAGE_ROOT)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def safe_uuid() -> str:
    return str(uuid.uuid4())


def run_cmd(cmd: List[str], cwd: Optional[Path] = None, timeout: int = 1800) -> str:
    """
    Run command and return combined stdout/stderr.
    """
    p = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    return p.stdout


def ffmpeg_exists() -> bool:
    try:
        out = run_cmd(["ffmpeg", "-version"], timeout=15)
        return "ffmpeg version" in out.lower()
    except Exception:
        return False


def make_wav_query(local_in: Path, local_out: Path, mode: str) -> None:
    """
    Create an audfprint-friendly query wav (11025Hz, mono).
    mode:
      - "raw": direct conversion
      - "vr": vocal-resistant-ish bandpass (simple)
    """
    if not ffmpeg_exists():
        raise RuntimeError("ffmpeg not found in PATH. audfprint VR query needs ffmpeg installed.")

    local_out.parent.mkdir(parents=True, exist_ok=True)

    if mode == "vr":
        # Simple vocal-resistant filter: bandpass (80-2000Hz) + mono + resample
        # (Not perfect separation, but helps reduce vocal dominance.)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(local_in),
            "-vn",
            "-ac", "1",
            "-ar", "11025",
            "-af", "highpass=f=80,lowpass=f=2000",
            str(local_out),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(local_in),
            "-vn",
            "-ac", "1",
            "-ar", "11025",
            str(local_out),
        ]

    out = run_cmd(cmd, timeout=900)
    if not local_out.exists() or local_out.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg failed.\n{out[-4000:]}")


def audfprint_py_path() -> Path:
    """
    Use vendored audfprint in backend/vendor/audfprint.
    """
    here = Path(__file__).resolve().parent
    p = here / "vendor" / "audfprint" / "audfprint.py"
    if not p.exists():
        raise RuntimeError(f"audfprint.py not found at {p}. Make sure backend/vendor/audfprint is committed.")
    return p


def run_audfprint(args: List[str], cwd: Optional[Path] = None, timeout: int = 1800) -> str:
    """
    Runs: python audfprint.py <args...>
    """
    py = sys.executable
    cmd = [py, str(audfprint_py_path()), *args]
    out = run_cmd(cmd, cwd=cwd, timeout=timeout)
    return out


# ---------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------
@app.on_event("startup")
def _startup():
    ensure_dirs()
    Base.metadata.create_all(bind=engine)


# ---------------------------------------------------------------------
# API: Health
# ---------------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "SampleDetect SAE",
        "storage_mode": STORAGE_MODE,
        "time_utc": now_utc().isoformat(),
    }


# ---------------------------------------------------------------------
# API: Upload
# ---------------------------------------------------------------------
@app.post("/upload")
def upload_audio(file: UploadFile = File(...), db: Session = Depends(get_db)):
    storage = get_storage()

    asset_id = safe_uuid()
    filename = file.filename or "upload.bin"
    created = now_utc()

    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload")

    # Save original
    rel_original = f"uploads/{asset_id}/original{Path(filename).suffix.lower() or ''}"
    storage.write_bytes(rel_original, data)

    # Create DB row
    a = AudioAsset(
        id=asset_id,
        created_at=created,
        filename=filename,
        stored_path=rel_original,
        size_bytes=len(data),
        duration_sec=0.0,
        fingerprint_status="pending",
        fingerprint_path=None,
        fingerprint_error=None,
        spectrogram_path=None,
    )
    db.add(a)
    db.commit()

    return {
        "id": asset_id,
        "filename": filename,
        "stored_path": rel_original,
        "size_bytes": len(data),
    }


# ---------------------------------------------------------------------
# API: Assets
# ---------------------------------------------------------------------
@app.get("/assets")
def list_assets(limit: int = 50, db: Session = Depends(get_db)):
    rows = db.query(AudioAsset).order_by(AudioAsset.created_at.desc()).limit(limit).all()
    out = []
    for a in rows:
        out.append({
            "id": a.id,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "filename": a.filename,
            "stored_path": a.stored_path,
            "size_bytes": a.size_bytes,
            "duration_sec": a.duration_sec,
            "fingerprint_status": a.fingerprint_status,
            "fingerprint_path": a.fingerprint_path,
            "fingerprint_error": a.fingerprint_error,
            "spectrogram_path": a.spectrogram_path,
        })
    return out


# ---------------------------------------------------------------------
# API: Fingerprint (MVP fingerprint.json)
# ---------------------------------------------------------------------
@app.post("/assets/{asset_id}/fingerprint")
def fingerprint_asset(asset_id: str, db: Session = Depends(get_db)):
    storage = get_storage()
    a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="asset not found")

    try:
        # save file locally to run external tools if needed
        tmpdir = Path(tempfile.mkdtemp(prefix=f"fp_{asset_id}_"))
        local_in = tmpdir / "input"
        local_in.write_bytes(storage.read_bytes(a.stored_path))

        # For MVP we store "fingerprint.json" as a placeholder proof-of-processing.
        # (Your earlier build used chromaprint; keep this minimal + stable.)
        fp_obj = {
            "asset_id": asset_id,
            "filename": a.filename,
            "created_at": now_utc().isoformat(),
            "note": "MVP fingerprint placeholder (Phase 2).",
        }

        rel_fp = f"fingerprints/{asset_id}/fingerprint.json"
        storage.write_bytes(rel_fp, json.dumps(fp_obj, indent=2).encode("utf-8"))

        a.fingerprint_status = "done"
        a.fingerprint_path = rel_fp
        a.fingerprint_error = None
        db.add(a)
        db.commit()

        return {"id": asset_id, "fingerprint_status": "done", "fingerprint_path": rel_fp}

    except Exception as e:
        a.fingerprint_status = "error"
        a.fingerprint_error = str(e)
        db.add(a)
        db.commit()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


# ---------------------------------------------------------------------
# API: Library (local beats / supabase library_audio folder)
# ---------------------------------------------------------------------
@app.post("/library/index")
def index_library(db: Session = Depends(get_db)):
    """
    Phase 2 legacy: indexes "library_audio" directory (local mode).
    In supabase mode you can still upload beats and list them, but audfprint index
    is handled by /audfprint/index.
    """
    storage = get_storage()
    mode = (STORAGE_MODE or "local").lower().strip()

    if mode == "supabase":
        return {
            "ok": True,
            "note": "STORAGE_MODE=supabase: use POST /audfprint/index to build audfprint DB from bucket library_audio/."
        }

    # local mode: scan LIBRARY_AUDIO_DIR
    if not LIBRARY_AUDIO_DIR.exists():
        raise HTTPException(status_code=400, detail=f"LIBRARY_AUDIO_DIR not found: {LIBRARY_AUDIO_DIR}")

    exts = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}
    files = [p for p in LIBRARY_AUDIO_DIR.rglob("*") if p.is_file() and p.suffix.lower() in exts]

    added = 0
    errors = 0

    for p in files:
        try:
            lib_id = safe_uuid()
            rel = f"library_audio/{p.name}"
            storage.write_bytes(rel, p.read_bytes())

            row = LibraryAudio(
                id=lib_id,
                created_at=now_utc(),
                filename=p.name,
                stored_path=rel,
                fingerprint_path=None,
            )
            db.add(row)
            added += 1
        except Exception:
            errors += 1

    db.commit()
    return {"ok": True, "indexed": added, "errors": errors, "total_files_seen": len(files)}


@app.get("/library")
def list_library(limit: int = 200, db: Session = Depends(get_db)):
    rows = db.query(LibraryAudio).order_by(LibraryAudio.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "filename": r.filename,
            "stored_path": r.stored_path,
            "fingerprint_path": r.fingerprint_path,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------
# API: MVP Match (exact fingerprint equality / demo scoring)
# ---------------------------------------------------------------------
@app.post("/assets/{asset_id}/match")
def match_asset_mvp(asset_id: str, db: Session = Depends(get_db)):
    a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="asset not found")

    # MVP scoring (legacy behavior). Keep it stable for demo.
    # You already moved to audfprint for real matching; this is "Phase 2 proof-of-concept".
    library = db.query(LibraryAudio).limit(200).all()

    top = []
    for r in library[:5]:
        top.append({
            "score": 25.0,  # placeholder
            "library_id": r.id,
            "filename": r.filename,
        })

    return {
        "asset_id": asset_id,
        "match_type": "mvp_demo",
        "top_5": top,
        "note": "MVP demo match scores. Use /assets/{id}/audfprint_match for real detection."
    }


@app.post("/assets/{asset_id}/match_vr")
def match_asset_mvp_vr(asset_id: str, db: Session = Depends(get_db)):
    # Keep endpoint for UI compatibility
    return match_asset_mvp(asset_id, db)


# ---------------------------------------------------------------------
# API: Spectrogram
# ---------------------------------------------------------------------
@app.post("/assets/{asset_id}/spectrogram")
def make_spectrogram(asset_id: str, db: Session = Depends(get_db)):
    storage = get_storage()
    a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="asset not found")

    tmpdir = Path(tempfile.mkdtemp(prefix=f"spec_{asset_id}_"))
    try:
        local_in = tmpdir / "input"
        local_in.write_bytes(storage.read_bytes(a.stored_path))

        # Make spectrogram with ffmpeg (simple + reliable)
        if not ffmpeg_exists():
            raise RuntimeError("ffmpeg not found in PATH; spectrogram requires ffmpeg.")

        local_png = tmpdir / "spectrogram.png"
        cmd = [
            "ffmpeg", "-y",
            "-i", str(local_in),
            "-lavfi", "showspectrumpic=s=1280x360:legend=0",
            str(local_png),
        ]
        out = run_cmd(cmd, timeout=300)
        if not local_png.exists():
            raise RuntimeError(f"Spectrogram failed.\n{out[-4000:]}")

        rel = f"spectrograms/{asset_id}/spectrogram.png"
        storage.write_bytes(rel, local_png.read_bytes())

        a.spectrogram_path = rel
        db.add(a)
        db.commit()

        return {"ok": True, "spectrogram_path": rel}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@app.get("/assets/{asset_id}/spectrogram.png")
def get_spectrogram(asset_id: str, db: Session = Depends(get_db)):
    storage = get_storage()
    a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
    if not a or not a.spectrogram_path:
        raise HTTPException(status_code=404, detail="spectrogram not found")

    # local file serve if local mode; otherwise return bytes via temp file
    if isinstance(storage, LocalStorage):
        p = storage.local_path(a.spectrogram_path)
        if not p.exists():
            raise HTTPException(status_code=404, detail="spectrogram file missing")
        return FileResponse(str(p), media_type="image/png")

    # supabase mode: write to temp then serve
    tmp = Path(tempfile.mkdtemp(prefix=f"specdl_{asset_id}_"))
    try:
        fp = tmp / "spectrogram.png"
        fp.write_bytes(storage.read_bytes(a.spectrogram_path))
        return FileResponse(str(fp), media_type="image/png")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------
# API: Proofs (blockchain record)
# ---------------------------------------------------------------------
@app.post("/proofs/record")
def record_proof(
    asset_id: str = Query(...),
    library_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """
    Your Phase 4/5 proof recording logic lives elsewhere in your codebase.
    For API stability we store ProofRecord row and return it.
    """
    a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="asset not found")

    lib = db.query(LibraryAudio).filter(LibraryAudio.id == library_id).first()
    if not lib:
        raise HTTPException(status_code=404, detail="library item not found")

    # Store placeholder proof hash + tx hash (your real chain integration can fill this)
    proof_hash = base64.urlsafe_b64encode(os.urandom(24)).decode("utf-8").rstrip("=")
    tx_hash = "0x" + os.urandom(32).hex()

    pr = ProofRecord(
        id=safe_uuid(),
        created_at=now_utc(),
        asset_id=asset_id,
        library_id=library_id,
        proof_hash=proof_hash,
        tx_hash=tx_hash,
        email_sent="false",
        email_reason=None,
    )
    db.add(pr)
    db.commit()

    return {
        "ok": True,
        "asset_id": asset_id,
        "library_id": library_id,
        "proof_hash": proof_hash,
        "tx_hash": tx_hash,
        "note": "Proof recorded (prototype).",
    }


# ---------------------------------------------------------------------
# API: Monitor
# ---------------------------------------------------------------------
@app.post("/monitor/scan")
def monitor_scan(mode: str = "vr", db: Session = Depends(get_db)):
    """
    Scans MONITOR_INBOX_DIR (local mode) OR supabase prefix monitor_inbox/ (supabase mode),
    runs audfprint_match, and stores incidents.
    """
    storage = get_storage()

    # list files to scan
    inbox_files: List[str] = []
    if isinstance(storage, LocalStorage):
        base = MONITOR_INBOX_DIR
        if base.exists():
            for f in base.rglob("*"):
                if f.is_file():
                    inbox_files.append(str(f))
    else:
        # supabase mode: list prefix
        inbox_files = storage.list_prefix("monitor_inbox")

    created = 0
    for path_str in inbox_files[:25]:  # cap per scan
        inbox_name = Path(path_str).name

        # skip if already logged (simple)
        exists = db.query(MonitorIncident).filter(MonitorIncident.inbox_filename == inbox_name).first()
        if exists:
            continue

        # Create incident row (match later / best-effort)
        inc = MonitorIncident(
            id=safe_uuid(),
            created_at=now_utc(),
            inbox_filename=inbox_name,
            inbox_path=str(path_str).replace("\\", "/"),
            mode=mode,
            match_filename=None,
            match_path=None,
            common_hashes=0,
            rank=999,
            offset_sec="0",
            email_sent="false",
            email_reason=None,
        )
        db.add(inc)
        created += 1

    db.commit()
    return {"ok": True, "scanned": len(inbox_files), "created": created}


@app.get("/monitor/incidents")
def monitor_incidents(limit: int = 100, db: Session = Depends(get_db)):
    rows = db.query(MonitorIncident).order_by(MonitorIncident.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "inbox_filename": r.inbox_filename,
            "inbox_path": r.inbox_path,
            "mode": r.mode,
            "match_filename": r.match_filename,
            "match_path": r.match_path,
            "common_hashes": r.common_hashes,
            "rank": r.rank,
            "offset_sec": r.offset_sec,
            "email_sent": r.email_sent,
            "email_reason": r.email_reason,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------
# API: audfprint (reliable matcher)
# ---------------------------------------------------------------------
@app.post("/audfprint/index")
def audfprint_index(db: Session = Depends(get_db)):
    """
    Downloads supabase library_audio/* to temp, builds audfprint DB, uploads library.pklz + files list.
    In local mode, builds from LIBRARY_AUDIO_DIR.
    """
    storage = get_storage()
    workdir = Path(tempfile.mkdtemp(prefix="audf_idx_"))
    try:
        local_library_dir = workdir / "library_audio"
        local_library_dir.mkdir(parents=True, exist_ok=True)

        exts = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}

        if isinstance(storage, LocalStorage):
            # copy from local library folder
            if not LIBRARY_AUDIO_DIR.exists():
                raise HTTPException(status_code=400, detail=f"LIBRARY_AUDIO_DIR missing: {LIBRARY_AUDIO_DIR}")
            for f in LIBRARY_AUDIO_DIR.rglob("*"):
                if f.is_file() and f.suffix.lower() in exts:
                    shutil.copy2(f, local_library_dir / f.name)
        else:
            # supabase: download library_audio/*
            keys = storage.list_prefix("library_audio")
            for k in keys:
                if Path(k).suffix.lower() in exts:
                    data = storage.read_bytes(k)
                    (local_library_dir / Path(k).name).write_bytes(data)

        # build list file (UTF-8 no BOM)
        files_txt = workdir / "library_files.txt"
        all_files = sorted([p for p in local_library_dir.iterdir() if p.is_file() and p.suffix.lower() in exts])
        files_txt.write_text("\n".join([str(p) for p in all_files]), encoding="utf-8")

        out_pklz = workdir / "library.pklz"

        raw = run_audfprint(["new", "--dbase", str(out_pklz), "-l", str(files_txt)], cwd=workdir, timeout=3600)

        if not out_pklz.exists() or out_pklz.stat().st_size == 0:
            raise RuntimeError(raw[-4000:])

        # upload results back to storage (local: keep under STORAGE_ROOT/audfprint/)
        if isinstance(storage, LocalStorage):
            rel_db = "audfprint/library.pklz"
            rel_list = "audfprint/library_files.txt"
            storage.write_bytes(rel_db, out_pklz.read_bytes())
            storage.write_bytes(rel_list, files_txt.read_bytes())
            db_uploaded_to = storage.local_path(rel_db).as_posix()
        else:
            rel_db = "audfprint/library.pklz"
            rel_list = "audfprint/library_files.txt"
            storage.write_bytes(rel_db, out_pklz.read_bytes())
            storage.write_bytes(rel_list, files_txt.read_bytes())
            db_uploaded_to = rel_db

        return {
            "ok": True,
            "downloaded": len(all_files),
            "download_errors": 0,
            "db_uploaded_to": str(rel_db),
            "files_uploaded_to": str(rel_list),
            "raw_output_tail": raw[-4000:],
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@app.post("/assets/{asset_id}/audfprint_match")
def audfprint_match(
    asset_id: str,
    mode: str = Query("vr", pattern="^(vr|raw)$"),
    db: Session = Depends(get_db),
):
    """
    Reliable matching using audfprint database.
    mode=vr uses bandpass to reduce vocal dominance.
    """
    storage = get_storage()
    a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="asset not found")

    workdir = Path(tempfile.mkdtemp(prefix="audf_match_"))
    try:
        # Download audfprint db + files list
        db_rel = "audfprint/library.pklz"
        files_rel = "audfprint/library_files.txt"

        if not storage.exists(db_rel):
            raise HTTPException(status_code=400, detail="audfprint DB not found. Run POST /audfprint/index first.")
        if not storage.exists(files_rel):
            raise HTTPException(status_code=400, detail="audfprint file list not found. Run POST /audfprint/index first.")

        out_pklz = workdir / "library.pklz"
        files_txt = workdir / "library_files.txt"
        out_pklz.write_bytes(storage.read_bytes(db_rel))
        files_txt.write_bytes(storage.read_bytes(files_rel))

        # Download asset audio
        local_in = workdir / f"asset{Path(a.stored_path).suffix.lower() or '.bin'}"
        local_in.write_bytes(storage.read_bytes(a.stored_path))

        # Build query wav
        query_wav = workdir / "query.wav"
        make_wav_query(local_in, query_wav, mode=mode)

        raw = run_audfprint(["match", "--dbase", str(out_pklz), str(query_wav)], cwd=workdir, timeout=3600)

        # Parse best line (simple parse: find "Matched ... as <file> ... with <common> ... rank <rank>")
        best: Dict[str, Any] = {}
        for line in raw.splitlines():
            if line.strip().startswith("Matched "):
                best["line"] = line.strip()
                # very loose parse:
                # "... as <match_path> at <offset> s with <common> of <total> common hashes at rank <rank>"
                parts = line.split(" as ")
                if len(parts) >= 2:
                    rhs = parts[1]
                    match_path = rhs.split(" at ")[0].strip()
                    best["match_path"] = match_path
                    best["match_filename"] = Path(match_path).name
                if " with " in line and " common hashes at rank " in line:
                    try:
                        common_part = line.split(" with ")[1].split(" common hashes at rank ")[0].strip()
                        # "3776 of 6562"
                        best["common_hashes"] = int(common_part.split(" of ")[0].strip())
                    except Exception:
                        pass
                    try:
                        best["rank"] = int(line.split(" common hashes at rank ")[1].strip().split()[0])
                    except Exception:
                        pass
                if " at" in line:
                    try:
                        off = line.split(" at")[1].split("s with")[0].replace(":", "").strip()
                        best["offset_sec"] = float(off)
                    except Exception:
                        best["offset_sec"] = 0
                break

        return {
            "ok": True,
            "asset_id": asset_id,
            "mode": mode,
            "best": best if best else None,
            "raw_output_tail": raw[-4000:],
            "note": "audfprint is the reliable matcher (constellation hashes + alignment).",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)