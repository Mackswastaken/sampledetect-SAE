import os
import sys
import json
import uuid
import base64
import shutil
import hashlib
import tempfile
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from dotenv import load_dotenv

from sqlalchemy import (
    create_engine,
    Column,
    String,
    Integer,
    Float,
    DateTime,
    Text,
)
from sqlalchemy.orm import sessionmaker, declarative_base

# ----------------------------
# Optional deps (only used if configured)
# ----------------------------
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
except Exception:
    SendGridAPIClient = None
    Mail = None

try:
    from web3 import Web3
except Exception:
    Web3 = None

try:
    from supabase import create_client as create_supabase_client
except Exception:
    create_supabase_client = None

# Spectrogram deps
try:
    import numpy as np  # type: ignore
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # type: ignore
    from scipy.io import wavfile  # type: ignore
except Exception:
    np = None
    plt = None
    wavfile = None


load_dotenv()

# ----------------------------
# App
# ----------------------------
app = FastAPI(title="SampleDetect SAE API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # MVP
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# ENV / SETTINGS
# ----------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing in .env / environment variables")

STORAGE_MODE = os.getenv("STORAGE_MODE", "local").lower()  # local | supabase
STORAGE_ROOT = Path(os.getenv("STORAGE_ROOT", "./storage")).resolve()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "sampledetect")

# Email
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")

# Blockchain
AMOY_RPC_URL = os.getenv("AMOY_RPC_URL", "")
AMOY_PRIVATE_KEY = os.getenv("AMOY_PRIVATE_KEY", "")
PROOF_REGISTRY_ADDRESS = os.getenv("PROOF_REGISTRY_ADDRESS", "")

# Audfprint vendored path (CRITICAL for Render)
AUDPRINT_PY = str((Path(__file__).parent / "vendor" / "audfprint" / "audfprint.py").resolve())

# ----------------------------
# Local storage layout (only used when STORAGE_MODE=local)
# ----------------------------
UPLOADS_DIR = STORAGE_ROOT / "uploads"
FINGERPRINTS_DIR = STORAGE_ROOT / "fingerprints"
TEMP_DIR = STORAGE_ROOT / "temp"
EXPORTS_DIR = STORAGE_ROOT / "exports"
LOGS_DIR = STORAGE_ROOT / "logs"

LIBRARY_AUDIO_DIR = STORAGE_ROOT / "library_audio"
LIBRARY_FINGERPRINTS_DIR = STORAGE_ROOT / "library_fingerprints"

MONITOR_INBOX_DIR = STORAGE_ROOT / "monitor_inbox"

# Local audfprint output location (on Render we prefer /tmp, but this is fine locally)
AUDPRINT_DIR = STORAGE_ROOT / "audfprint"
AUDPRINT_DB = AUDPRINT_DIR / "library.pklz"
AUDPRINT_FILES_TXT = AUDPRINT_DIR / "library_files.txt"


def ensure_local_dirs():
    for p in [
        UPLOADS_DIR,
        FINGERPRINTS_DIR,
        TEMP_DIR,
        EXPORTS_DIR,
        LOGS_DIR,
        LIBRARY_AUDIO_DIR,
        LIBRARY_FINGERPRINTS_DIR,
        MONITOR_INBOX_DIR,
        AUDPRINT_DIR,
    ]:
        p.mkdir(parents=True, exist_ok=True)


# ----------------------------
# Supabase helpers
# ----------------------------
def supabase():
    if STORAGE_MODE != "supabase":
        return None
    if not create_supabase_client:
        raise RuntimeError("supabase-py not installed. Install supabase in requirements.txt.")
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing")
    return create_supabase_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def sb_upload_bytes(path_key: str, data: bytes, content_type: str = "application/octet-stream"):
    sb = supabase()
    if not sb:
        raise RuntimeError("Supabase not configured")
    # upsert=True overwrites if exists
    res = sb.storage.from_(SUPABASE_BUCKET).upload(
        path=path_key,
        file=data,
        file_options={"content-type": content_type, "upsert": "true"},
    )
    return res


def sb_download_bytes(path_key: str) -> bytes:
    sb = supabase()
    if not sb:
        raise RuntimeError("Supabase not configured")
    data = sb.storage.from_(SUPABASE_BUCKET).download(path_key)
    # supabase-py returns bytes
    return data


def sb_list(prefix: str) -> List[Dict[str, Any]]:
    sb = supabase()
    if not sb:
        raise RuntimeError("Supabase not configured")

    # Supabase uses "folder" + "search"
    # We implement a simple recursive listing by splitting prefix.
    folder = prefix.strip("/")

    if "/" in folder:
        parent, child = folder.rsplit("/", 1)
    else:
        parent, child = "", folder

    # If prefix points to a folder itself:
    base_path = folder if folder else ""
    # list() expects folder path, not prefix wildcard
    items = sb.storage.from_(SUPABASE_BUCKET).list(path=base_path)
    # items include files in that folder
    return items


def sb_recursive_collect(prefix: str) -> List[str]:
    """
    Recursively collect full object keys under a prefix.
    """
    sb = supabase()
    if not sb:
        raise RuntimeError("Supabase not configured")

    prefix = prefix.strip("/")
    results: List[str] = []

    def walk(folder: str):
        items = sb.storage.from_(SUPABASE_BUCKET).list(path=folder)
        for it in items:
            name = it.get("name")
            if not name:
                continue
            # folder items have "id" null sometimes; heuristic: if "metadata" missing and "name" has no dot, can still be file.
            # Supabase returns "metadata" for files.
            is_file = it.get("metadata") is not None
            full = f"{folder}/{name}" if folder else name
            if is_file:
                results.append(full)
            else:
                walk(full)

    walk(prefix)
    return results


# ----------------------------
# DB
# ----------------------------
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


class AudioAsset(Base):
    __tablename__ = "audio_assets"
    id = Column(String, primary_key=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    filename = Column(String, nullable=False)
    stored_path = Column(Text, nullable=False)
    size_bytes = Column(Integer, default=0)
    duration_sec = Column(Float, default=0.0)

    fingerprint_status = Column(String, default="pending")  # pending|done|error
    fingerprint_path = Column(Text, nullable=True)
    fingerprint_error = Column(Text, nullable=True)

    spectrogram_path = Column(Text, nullable=True)


class LibraryItem(Base):
    __tablename__ = "library_items"
    id = Column(String, primary_key=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    filename = Column(String, nullable=False)
    stored_path = Column(Text, nullable=False)
    fingerprint_path = Column(Text, nullable=True)


class ProofRecord(Base):
    __tablename__ = "proof_records"
    id = Column(String, primary_key=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    asset_id = Column(String, nullable=False)
    library_id = Column(String, nullable=False)
    sha256 = Column(String, nullable=False)
    tx_hash = Column(String, nullable=True)
    proof_hash = Column(String, nullable=True)
    email_sent = Column(String, default="false")
    email_message_id = Column(String, nullable=True)
    email_reason = Column(Text, nullable=True)


class MonitorIncident(Base):
    __tablename__ = "monitor_incidents"
    id = Column(String, primary_key=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    inbox_filename = Column(String, nullable=False)
    inbox_path = Column(Text, nullable=False)
    mode = Column(String, default="vr")  # vr|raw
    match_filename = Column(String, nullable=True)
    match_path = Column(Text, nullable=True)
    common_hashes = Column(Integer, default=0)
    rank = Column(Integer, default=-1)
    offset_sec = Column(String, default="0")
    email_sent = Column(String, default="false")
    email_reason = Column(Text, nullable=True)


Base.metadata.create_all(bind=engine)


# ----------------------------
# Utility
# ----------------------------
def db_session():
    return SessionLocal()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def sha256_file_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def safe_filename(name: str) -> str:
    return name.replace("\\", "_").replace("/", "_").strip()


def ffprobe_duration(path: Path) -> float:
    """
    Best-effort duration reading via ffprobe if available.
    """
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)]
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        js = json.loads(out)
        dur = float(js["format"]["duration"])
        return dur
    except Exception:
        return 0.0


def local_write_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def get_asset_dir(asset_id: str) -> Path:
    return UPLOADS_DIR / asset_id


def get_fingerprint_dir(asset_id: str) -> Path:
    return FINGERPRINTS_DIR / asset_id


def get_tmp_dir(asset_id: str) -> Path:
    return TEMP_DIR / asset_id


# ----------------------------
# Email (SendGrid)
# ----------------------------
def send_email(subject: str, text: str) -> Dict[str, Any]:
    if not SENDGRID_API_KEY or not EMAIL_FROM or not EMAIL_TO:
        return {"ok": False, "reason": "SendGrid env vars not set"}
    if not SendGridAPIClient or not Mail:
        return {"ok": False, "reason": "sendgrid package not installed"}

    try:
        message = Mail(
            from_email=EMAIL_FROM,
            to_emails=EMAIL_TO,
            subject=subject,
            plain_text_content=text,
        )
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        resp = sg.send(message)
        msg_id = resp.headers.get("X-Message-Id") if hasattr(resp, "headers") else None
        return {"ok": True, "message_id": msg_id}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


LEGAL_DISPUTE_TEXT = """
IMPORTANT NOTICE (Prototype / Demonstration)

This notification indicates that an uploaded audio file appears to match a beat from your registered library.
This system is a prototype and may generate false positives.

If you believe this notification is incorrect, you may dispute it by:
1) Gathering evidence of authorship and creation date (project files, session exports, stems).
2) Comparing the allegedly matching segments using independent audio analysis tools.
3) Contacting the uploader/platform with a formal dispute request and supporting evidence.
4) If applicable, consulting a qualified legal professional for DMCA/takedown or rights enforcement steps.

This message is not legal advice. It is provided as an example prototype notice only.
"""


# ----------------------------
# Blockchain proof (simple)
# ----------------------------
def record_proof_on_chain(sha256_hex: str) -> Dict[str, Any]:
    """
    Stores hash only (sha256) via contract call.
    Expects AMOY_RPC_URL + AMOY_PRIVATE_KEY + PROOF_REGISTRY_ADDRESS.
    """
    if not (AMOY_RPC_URL and AMOY_PRIVATE_KEY and PROOF_REGISTRY_ADDRESS):
        return {"ok": False, "reason": "Blockchain env vars not set"}

    if not Web3:
        return {"ok": False, "reason": "web3 not installed"}

    # Minimal ABI: eventless storeHash(bytes32)
    abi = [
        {
            "inputs": [{"internalType": "bytes32", "name": "h", "type": "bytes32"}],
            "name": "storeHash",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function",
        }
    ]

    try:
        w3 = Web3(Web3.HTTPProvider(AMOY_RPC_URL))
        acct = w3.eth.account.from_key(AMOY_PRIVATE_KEY)
        contract = w3.eth.contract(address=Web3.to_checksum_address(PROOF_REGISTRY_ADDRESS), abi=abi)

        h_bytes32 = bytes.fromhex(sha256_hex)
        if len(h_bytes32) != 32:
            return {"ok": False, "reason": "sha256 must be 32 bytes"}

        tx = contract.functions.storeHash(h_bytes32).build_transaction(
            {
                "from": acct.address,
                "nonce": w3.eth.get_transaction_count(acct.address),
                "chainId": w3.eth.chain_id,
            }
        )

        # Let node fill gas fields where possible; if it fails you can tune.
        signed = w3.eth.account.sign_transaction(tx, AMOY_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        return {"ok": True, "tx_hash": tx_hash.hex(), "from": acct.address}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


# ----------------------------
# MVP fingerprint / matching (exact equality)
# ----------------------------
def mvp_fingerprint_bytes(data: bytes) -> str:
    """
    MVP: fingerprint = sha256 over bytes.
    """
    return sha256_file_bytes(data)


# ----------------------------
# Audfprint runner
# ----------------------------
def run_audfprint(args: List[str], cwd: Optional[Path] = None) -> str:
    """
    Run: python audfprint.py <cmd> ... and return stdout+stderr (tail if huge).
    Uses sys.executable and vendored audfprint path for cross-platform.
    """
    if not Path(AUDPRINT_PY).exists():
        raise RuntimeError(f"Vendored audfprint not found at: {AUDPRINT_PY}")

    cmd = [sys.executable, AUDPRINT_PY] + args

    p = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    out = p.stdout or ""
    if p.returncode != 0:
        # raise tail for readability
        raise RuntimeError(out[-4000:])
    return out


def audfprint_index_supabase(db) -> Dict[str, Any]:
    """
    Build audfprint DB from Supabase folder: library_audio/
    Writes DB to /tmp then uploads to Supabase under audfprint/library.pklz + audfprint/library_files.txt
    """
    if STORAGE_MODE != "supabase":
        raise HTTPException(status_code=400, detail="STORAGE_MODE must be 'supabase' for this endpoint")

    # Collect library audio keys
    keys = sb_recursive_collect("library_audio")
    audio_keys = [k for k in keys if k.lower().endswith((".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"))]

    if not audio_keys:
        return {"ok": False, "reason": "No files found under supabase: library_audio/"}

    workdir = Path(tempfile.mkdtemp(prefix="audf_idx_"))
    try:
        local_audio_dir = workdir / "library_audio"
        local_audio_dir.mkdir(parents=True, exist_ok=True)

        # Download files locally for indexing
        downloaded = 0
        errors = 0
        for key in audio_keys:
            try:
                data = sb_download_bytes(key)
                fname = safe_filename(Path(key).name)
                local_write_bytes(local_audio_dir / fname, data)
                downloaded += 1
            except Exception:
                errors += 1

        files_txt = workdir / "library_files.txt"
        # Write absolute paths (audfprint handles)
        lines = [str(p.resolve()) for p in sorted(local_audio_dir.glob("*"))]
        files_txt.write_text("\n".join(lines), encoding="utf-8")

        out_pklz = workdir / "library.pklz"

        # Build database from list file
        raw = run_audfprint(["new", "--dbase", str(out_pklz), "-l", str(files_txt)], cwd=workdir)

        # Upload outputs back to Supabase
        sb_upload_bytes("audfprint/library.pklz", out_pklz.read_bytes(), content_type="application/octet-stream")
        sb_upload_bytes("audfprint/library_files.txt", files_txt.read_bytes(), content_type="text/plain")

        return {
            "ok": True,
            "downloaded": downloaded,
            "download_errors": errors,
            "db_uploaded_to": "audfprint/library.pklz",
            "files_uploaded_to": "audfprint/library_files.txt",
            "raw_output_tail": raw[-1200:],
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def audfprint_match_asset_supabase(asset_bytes: bytes, mode: str = "vr") -> Dict[str, Any]:
    """
    Match uploaded asset against Supabase audfprint DB.
    mode:
      - raw: convert to 11025 mono wav then match
      - vr: apply simple band-pass filter to reduce vocal energy then match (ffmpeg)
    """
    if STORAGE_MODE != "supabase":
        raise HTTPException(status_code=400, detail="STORAGE_MODE must be 'supabase' for this endpoint")

    # Download DB
    try:
        db_bytes = sb_download_bytes("audfprint/library.pklz")
    except Exception as e:
        return {"ok": False, "reason": f"Could not download audfprint DB from supabase: {e}"}

    workdir = Path(tempfile.mkdtemp(prefix="audf_match_"))
    try:
        db_path = workdir / "library.pklz"
        db_path.write_bytes(db_bytes)

        input_path = workdir / "query_in"
        input_path.write_bytes(asset_bytes)

        # Build wav query
        wav_query = workdir / "query.wav"
        if mode == "raw":
            cmd = ["ffmpeg", "-y", "-i", str(input_path), "-ac", "1", "-ar", "11025", str(wav_query)]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        else:
            # Vocal resistant: band-pass roughly 80-2000Hz (works OK for beat backbone)
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-ac",
                "1",
                "-ar",
                "11025",
                "-af",
                "highpass=f=80,lowpass=f=2000",
                str(wav_query),
            ]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        # Match
        raw = run_audfprint(["match", "--dbase", str(db_path), str(wav_query)], cwd=workdir)

        # Parse best line if present
        best: Dict[str, Any] = {}
        tail = raw.strip().splitlines()[-5:]
        for line in raw.splitlines():
            if line.startswith("Matched "):
                # Example line:
                # Matched <query> ... as <matchfile> at -0.0 s with 1047 of 2337 common hashes at rank 0
                best["line"] = line
                # crude extraction
                try:
                    parts = line.split(" as ", 1)[1]
                    match_path = parts.split(" at ", 1)[0].strip()
                    best["match_path"] = match_path
                    best["match_filename"] = Path(match_path).name
                except Exception:
                    pass

        return {
            "ok": True,
            "mode": mode,
            "best": best,
            "raw_output_tail": "\n".join(tail),
            "note": "audfprint is reliable matcher (constellation hashes + alignment).",
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ----------------------------
# API
# ----------------------------
@app.get("/health")
def health():
    return {"ok": True, "storage_mode": STORAGE_MODE, "time": now_utc().isoformat()}


@app.post("/upload")
async def upload_audio(file: UploadFile = File(...)):
    """
    Upload audio file as an "asset" (for matching / fingerprinting).
    In supabase mode: stores under uploads/<asset_id>/original.<ext>
    In local mode: stores under STORAGE_ROOT/uploads/<asset_id>/original.<ext>
    """
    asset_id = str(uuid.uuid4())
    filename = safe_filename(file.filename or "upload.bin")
    ext = Path(filename).suffix.lower() or ".bin"

    data = await file.read()
    size = len(data)

    stored_path = ""
    local_tmp = None

    if STORAGE_MODE == "supabase":
        key = f"uploads/{asset_id}/original{ext}"
        sb_upload_bytes(key, data, content_type=file.content_type or "application/octet-stream")
        stored_path = key
        duration = 0.0
    else:
        ensure_local_dirs()
        asset_dir = get_asset_dir(asset_id)
        asset_dir.mkdir(parents=True, exist_ok=True)
        local_path = asset_dir / f"original{ext}"
        local_write_bytes(local_path, data)
        stored_path = str(local_path)
        duration = ffprobe_duration(local_path)

    db = db_session()
    try:
        a = AudioAsset(
            id=asset_id,
            filename=filename,
            stored_path=stored_path,
            size_bytes=size,
            duration_sec=float(duration),
            fingerprint_status="pending",
        )
        db.add(a)
        db.commit()
        return {"id": asset_id, "filename": filename, "stored_path": stored_path, "size_bytes": size}
    finally:
        db.close()


@app.get("/assets")
def list_assets(limit: int = 50):
    db = db_session()
    try:
        assets = db.query(AudioAsset).order_by(AudioAsset.created_at.desc()).limit(limit).all()
        out = []
        for a in assets:
            out.append(
                {
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
                }
            )
        return out
    finally:
        db.close()


@app.post("/assets/clear_recent")
def clear_recent_assets(delete_files: bool = False):
    """
    Clears the "Recent Uploads" list by deleting asset rows from DB.
    DOES NOT touch library, proofs, incidents.
    By default does NOT delete storage files (safe for demo).
    """
    db = db_session()
    try:
        assets = db.query(AudioAsset).all()
        # optionally delete stored objects
        if delete_files:
            if STORAGE_MODE == "supabase":
                sb = supabase()
                if sb:
                    for a in assets:
                        try:
                            sb.storage.from_(SUPABASE_BUCKET).remove([a.stored_path])
                        except Exception:
                            pass
            else:
                for a in assets:
                    try:
                        p = Path(a.stored_path)
                        if p.exists():
                            p.unlink()
                    except Exception:
                        pass

        for a in assets:
            db.delete(a)
        db.commit()
        return {"ok": True, "cleared": len(assets), "deleted_files": delete_files}
    finally:
        db.close()


@app.post("/assets/{asset_id}/fingerprint")
def fingerprint_asset(asset_id: str):
    """
    MVP fingerprint: sha256 of original file bytes.
    Stores fingerprint.json under fingerprints/<id>/fingerprint.json (local or supabase)
    """
    db = db_session()
    try:
        a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
        if not a:
            raise HTTPException(status_code=404, detail="asset not found")

        # Load bytes
        if STORAGE_MODE == "supabase":
            original = sb_download_bytes(a.stored_path)
        else:
            original = Path(a.stored_path).read_bytes()

        fp = mvp_fingerprint_bytes(original)
        fp_obj = {
            "id": asset_id,
            "fingerprint": fp,
            "created_at": now_utc().isoformat(),
        }
        fp_json = json.dumps(fp_obj, indent=2).encode("utf-8")

        if STORAGE_MODE == "supabase":
            out_key = f"fingerprints/{asset_id}/fingerprint.json"
            sb_upload_bytes(out_key, fp_json, content_type="application/json")
            a.fingerprint_path = out_key
        else:
            ensure_local_dirs()
            out_path = get_fingerprint_dir(asset_id) / "fingerprint.json"
            local_write_bytes(out_path, fp_json)
            a.fingerprint_path = str(out_path)

        a.fingerprint_status = "done"
        a.fingerprint_error = None
        db.commit()

        return {
            "id": asset_id,
            "fingerprint_status": a.fingerprint_status,
            "fingerprint_path": a.fingerprint_path,
            "fingerprint_preview": fp[:64] + "...",
        }
    except Exception as e:
        a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
        if a:
            a.fingerprint_status = "error"
            a.fingerprint_error = str(e)
            db.commit()
        raise
    finally:
        db.close()


@app.post("/library/index")
def index_library():
    """
    Index library audio:
      - local: scans STORAGE_ROOT/library_audio
      - supabase: scans bucket folder library_audio/
    Creates LibraryItem rows
    """
    db = db_session()
    try:
        # Clear existing library rows
        db.query(LibraryItem).delete()
        db.commit()

        added = 0
        errors = 0

        if STORAGE_MODE == "supabase":
            keys = sb_recursive_collect("library_audio")
            audio_keys = [k for k in keys if k.lower().endswith((".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"))]
            for key in audio_keys:
                try:
                    lid = str(uuid.uuid4())
                    item = LibraryItem(
                        id=lid,
                        filename=Path(key).name,
                        stored_path=key,
                    )
                    db.add(item)
                    added += 1
                except Exception:
                    errors += 1
        else:
            ensure_local_dirs()
            for p in LIBRARY_AUDIO_DIR.glob("**/*"):
                if p.is_file() and p.suffix.lower() in [".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"]:
                    try:
                        lid = str(uuid.uuid4())
                        item = LibraryItem(id=lid, filename=p.name, stored_path=str(p))
                        db.add(item)
                        added += 1
                    except Exception:
                        errors += 1

        db.commit()
        return {"ok": True, "indexed": added, "errors": errors}
    finally:
        db.close()


@app.get("/library")
def list_library(limit: int = 200):
    db = db_session()
    try:
        items = db.query(LibraryItem).order_by(LibraryItem.created_at.desc()).limit(limit).all()
        return [
            {
                "id": it.id,
                "created_at": it.created_at.isoformat() if it.created_at else None,
                "filename": it.filename,
                "stored_path": it.stored_path,
                "fingerprint_path": it.fingerprint_path,
            }
            for it in items
        ]
    finally:
        db.close()


@app.post("/assets/{asset_id}/match")
def match_asset_mvp(asset_id: str):
    """
    MVP match: exact fingerprint equality vs library fingerprints (sha256).
    For prototype demo only.
    """
    db = db_session()
    try:
        a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
        if not a:
            raise HTTPException(status_code=404, detail="asset not found")

        # read bytes
        if STORAGE_MODE == "supabase":
            data = sb_download_bytes(a.stored_path)
        else:
            data = Path(a.stored_path).read_bytes()

        fp = mvp_fingerprint_bytes(data)

        # compare vs library items by hashing their stored bytes
        lib = db.query(LibraryItem).all()
        matches = []
        for it in lib:
            try:
                if STORAGE_MODE == "supabase":
                    b = sb_download_bytes(it.stored_path)
                else:
                    b = Path(it.stored_path).read_bytes()
                lfp = mvp_fingerprint_bytes(b)
                if lfp == fp:
                    matches.append({"library_id": it.id, "filename": it.filename, "stored_path": it.stored_path})
            except Exception:
                pass

        return {
            "asset_id": asset_id,
            "match_type": "exact_fingerprint",
            "match_count": len(matches),
            "matches": matches,
            "note": "MVP match is exact fingerprint equality (good proof-of-work).",
        }
    finally:
        db.close()


@app.post("/assets/{asset_id}/match_vr")
def match_asset_vr_mvp(asset_id: str, threshold: int = 70):
    """
    MVP 'vocal-resistant' matcher (legacy): returns top candidates with heuristic scores.
    Kept for continuity; use audfprint for reliable VR matching.
    """
    # Keep endpoint alive but advise audfprint
    return {
        "asset_id": asset_id,
        "mode": "vocal_resistant_multi",
        "threshold": threshold,
        "top_5": [],
        "matches": [],
        "match_count": 0,
        "note": "This legacy VR endpoint is kept for continuity. Use /assets/{id}/audfprint_match_vr for reliable matching.",
    }


@app.post("/assets/{asset_id}/spectrogram")
def make_spectrogram(asset_id: str):
    """
    Generates spectrogram PNG for asset.
    Requires numpy + matplotlib + scipy installed.
    """
    if not (np and plt and wavfile):
        raise HTTPException(status_code=500, detail="Spectrogram dependencies missing (numpy/matplotlib/scipy)")

    db = db_session()
    try:
        a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
        if not a:
            raise HTTPException(status_code=404, detail="asset not found")

        workdir = Path(tempfile.mkdtemp(prefix="spec_"))
        try:
            # Download asset to temp
            in_path = workdir / "input"
            if STORAGE_MODE == "supabase":
                in_path.write_bytes(sb_download_bytes(a.stored_path))
            else:
                in_path.write_bytes(Path(a.stored_path).read_bytes())

            wav_path = workdir / "input.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(in_path), "-ac", "1", "-ar", "22050", str(wav_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            sr, audio = wavfile.read(str(wav_path))
            if audio.ndim > 1:
                audio = audio[:, 0]

            fig = plt.figure(figsize=(10, 4))
            plt.specgram(audio, NFFT=2048, Fs=sr, noverlap=1024)
            plt.xlabel("Time")
            plt.ylabel("Frequency")
            plt.tight_layout()

            out_png = workdir / "spectrogram.png"
            fig.savefig(str(out_png))
            plt.close(fig)

            if STORAGE_MODE == "supabase":
                key = f"spectrograms/{asset_id}/spectrogram.png"
                sb_upload_bytes(key, out_png.read_bytes(), content_type="image/png")
                a.spectrogram_path = key
            else:
                ensure_local_dirs()
                out_local = TEMP_DIR / asset_id / "spectrogram.png"
                local_write_bytes(out_local, out_png.read_bytes())
                a.spectrogram_path = str(out_local)

            db.commit()
            return {"ok": True, "asset_id": asset_id, "spectrogram_path": a.spectrogram_path}
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    finally:
        db.close()


@app.get("/assets/{asset_id}/spectrogram.png")
def get_spectrogram(asset_id: str):
    db = db_session()
    try:
        a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
        if not a or not a.spectrogram_path:
            raise HTTPException(status_code=404, detail="spectrogram not found")

        if STORAGE_MODE == "supabase":
            png = sb_download_bytes(a.spectrogram_path)
            return Response(content=png, media_type="image/png")
        else:
            p = Path(a.spectrogram_path)
            if not p.exists():
                raise HTTPException(status_code=404, detail="spectrogram file missing")
            return FileResponse(str(p), media_type="image/png")
    finally:
        db.close()


@app.post("/audfprint/index")
def audfprint_index():
    """
    Build audfprint DB from library_audio (supabase only in hosted mode).
    """
    db = db_session()
    try:
        return audfprint_index_supabase(db)
    finally:
        db.close()


@app.post("/assets/{asset_id}/audfprint_match")
def audfprint_match(asset_id: str, mode: str = Query("raw", pattern="^(raw|vr)$")):
    db = db_session()
    try:
        a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
        if not a:
            raise HTTPException(status_code=404, detail="asset not found")

        if STORAGE_MODE == "supabase":
            b = sb_download_bytes(a.stored_path)
        else:
            # local fallback
            b = Path(a.stored_path).read_bytes()
        return audfprint_match_asset_supabase(b, mode=mode)
    finally:
        db.close()


@app.post("/proofs/record")
def record_proof(asset_id: str, library_id: str):
    """
    Records proof for (asset, library) as:
      - sha256 computed
      - optional on-chain record
      - SendGrid email
    """
    db = db_session()
    try:
        a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
        it = db.query(LibraryItem).filter(LibraryItem.id == library_id).first()
        if not a or not it:
            raise HTTPException(status_code=404, detail="asset or library item not found")

        # load bytes for hash (asset bytes)
        if STORAGE_MODE == "supabase":
            data = sb_download_bytes(a.stored_path)
        else:
            data = Path(a.stored_path).read_bytes()

        h = sha256_file_bytes(data)

        proof_id = str(uuid.uuid4())
        pr = ProofRecord(id=proof_id, asset_id=asset_id, library_id=library_id, sha256=h)

        # Chain record
        chain_res = record_proof_on_chain(h)
        if chain_res.get("ok"):
            pr.tx_hash = chain_res.get("tx_hash")
            pr.proof_hash = h
        else:
            pr.tx_hash = None
            pr.proof_hash = None

        # Email
        subject = "SampleDetect SAE: Potential infringement detected (prototype)"
        body = (
            f"Asset ID: {asset_id}\n"
            f"Library ID: {library_id}\n"
            f"SHA256: {h}\n"
            f"TX: {pr.tx_hash or 'N/A'}\n\n"
            f"{LEGAL_DISPUTE_TEXT.strip()}\n"
        )
        email_res = send_email(subject, body)
        pr.email_sent = "true" if email_res.get("ok") else "false"
        pr.email_message_id = email_res.get("message_id")
        pr.email_reason = email_res.get("reason")

        db.add(pr)
        db.commit()

        return {
            "ok": True,
            "proof_id": proof_id,
            "asset_id": asset_id,
            "library_id": library_id,
            "sha256": h,
            "tx_hash": pr.tx_hash,
            "email_sent": pr.email_sent,
            "email_message_id": pr.email_message_id,
            "email_reason": pr.email_reason,
        }
    finally:
        db.close()


@app.post("/monitor/scan")
def monitor_scan(mode: str = Query("vr", pattern="^(vr|raw)$")):
    """
    Scan monitor_inbox for files, match via audfprint, create incidents, send email.
    Supabase mode expects objects in: monitor_inbox/
    Local mode expects files in: STORAGE_ROOT/monitor_inbox
    """
    db = db_session()
    try:
        incidents_created = 0
        scanned = 0

        # collect inbox keys/paths
        inbox_items: List[Dict[str, str]] = []

        if STORAGE_MODE == "supabase":
            keys = sb_recursive_collect("monitor_inbox")
            audio_keys = [k for k in keys if k.lower().endswith((".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"))]
            for k in audio_keys:
                inbox_items.append({"filename": Path(k).name, "path": k})
        else:
            ensure_local_dirs()
            for p in MONITOR_INBOX_DIR.glob("**/*"):
                if p.is_file() and p.suffix.lower() in [".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"]:
                    inbox_items.append({"filename": p.name, "path": str(p)})

        for item in inbox_items:
            scanned += 1

            # avoid duplicates: if same inbox_path already logged, skip
            existing = db.query(MonitorIncident).filter(MonitorIncident.inbox_path == item["path"]).first()
            if existing:
                continue

            # read bytes
            if STORAGE_MODE == "supabase":
                b = sb_download_bytes(item["path"])
            else:
                b = Path(item["path"]).read_bytes()

            # match
            match_res = audfprint_match_asset_supabase(b, mode=mode)
            best = match_res.get("best", {})
            match_filename = best.get("match_filename")
            match_path = best.get("match_path")

            # common hashes / rank parse (best line)
            common_hashes = 0
            rank = -1
            offset_sec = "0"
            line = best.get("line", "")
            if line:
                # crude parse
                try:
                    if " with " in line and " common hashes" in line:
                        seg = line.split(" with ", 1)[1]
                        common_hashes = int(seg.split(" of ", 1)[0].strip())
                    if " rank " in line:
                        rank = int(line.split(" rank ", 1)[1].strip())
                    if " at " in line and " s with " in line:
                        offset_sec = line.split(" at ", 1)[1].split(" s with ", 1)[0].strip()
                except Exception:
                    pass

            inc = MonitorIncident(
                id=str(uuid.uuid4()),
                inbox_filename=item["filename"],
                inbox_path=item["path"],
                mode=mode,
                match_filename=match_filename,
                match_path=match_path,
                common_hashes=common_hashes,
                rank=rank,
                offset_sec=str(offset_sec),
            )

            # email notify
            subject = "SampleDetect SAE Monitor: Potential stolen beat detected (prototype)"
            body = (
                f"Inbox file: {inc.inbox_filename}\n"
                f"Inbox path: {inc.inbox_path}\n"
                f"Mode: {mode}\n\n"
                f"Best match: {inc.match_filename or 'None'}\n"
                f"Common hashes: {inc.common_hashes}\n"
                f"Rank: {inc.rank}\n"
                f"Offset sec: {inc.offset_sec}\n\n"
                f"{LEGAL_DISPUTE_TEXT.strip()}\n"
            )
            email_res = send_email(subject, body)
            inc.email_sent = "true" if email_res.get("ok") else "false"
            inc.email_reason = email_res.get("message_id") or email_res.get("reason")

            db.add(inc)
            db.commit()
            incidents_created += 1

        return {"ok": True, "scanned": scanned, "incidents_created": incidents_created, "mode": mode}
    finally:
        db.close()


@app.get("/monitor/incidents")
def monitor_incidents(limit: int = 200):
    db = db_session()
    try:
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
    finally:
        db.close()