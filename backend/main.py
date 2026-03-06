import os
import re
import json
import uuid
import hashlib
import shutil
import tempfile
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from dotenv import load_dotenv
load_dotenv()

# ---------- OPTIONAL imports (only used if available in env) ----------
try:
    from rapidfuzz import fuzz
except Exception:
    fuzz = None

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

# Supabase storage wrapper (created earlier)
# storage_supabase.py must exist
try:
    from storage_supabase import SupabaseStorage
except Exception:
    SupabaseStorage = None


# =========================
# Config / Settings
# =========================

def env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if v is not None and v != "" else default


STORAGE_MODE = env("STORAGE_MODE", "local").lower()  # local | supabase
STORAGE_ROOT = Path(env("STORAGE_ROOT", "./storage")).resolve()

DATABASE_URL = env("DATABASE_URL")  # required
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing in .env / environment variables")

# Supabase storage (only used if STORAGE_MODE=supabase)
SUPABASE_BUCKET = env("SUPABASE_BUCKET", "sampledetect")

# Audfprint paths (local)
AUDFPRINT_PKLZ_LOCAL = STORAGE_ROOT / "audfprint" / "library.pklz"
AUDFPRINT_FILES_TXT_LOCAL = STORAGE_ROOT / "audfprint" / "library_files.txt"

# Directory layout (local)
UPLOADS_DIR = STORAGE_ROOT / "uploads"
FINGERPRINTS_DIR = STORAGE_ROOT / "fingerprints"
SPECTROGRAMS_DIR = STORAGE_ROOT / "spectrograms"
TEMP_DIR = STORAGE_ROOT / "temp"
LOGS_DIR = STORAGE_ROOT / "logs"
EXPORTS_DIR = STORAGE_ROOT / "exports"
LIBRARY_AUDIO_DIR = STORAGE_ROOT / "library_audio"
LIBRARY_FINGERPRINTS_DIR = STORAGE_ROOT / "library_fingerprints"
MONITOR_INBOX_DIR = STORAGE_ROOT / "monitor_inbox"
MONITOR_PROCESSED_DIR = STORAGE_ROOT / "monitor_processed"
AUDFPRINT_DIR = STORAGE_ROOT / "audfprint"

# External tool paths
FFMPEG = env("FFMPEG_BIN", "ffmpeg")  # assumes ffmpeg is in PATH
PYTHON_EXE = env("PYTHON_EXE", None)  # optional override

# audfprint source path (local dev)
# You previously used:
# C:\Projects\sampledetect-mvp\backend\.venv\Scripts\python.exe C:\Projects\audfprint\audfprint\audfprint.py ...
AUDFPRINT_PY = env("AUDFPRINT_PY", r"C:\Projects\audfprint\audfprint\audfprint.py")


def ensure_local_dirs():
    for p in [
        UPLOADS_DIR,
        FINGERPRINTS_DIR,
        SPECTROGRAMS_DIR,
        TEMP_DIR,
        LOGS_DIR,
        EXPORTS_DIR,
        LIBRARY_AUDIO_DIR,
        LIBRARY_FINGERPRINTS_DIR,
        MONITOR_INBOX_DIR,
        MONITOR_PROCESSED_DIR,
        AUDFPRINT_DIR,
    ]:
        p.mkdir(parents=True, exist_ok=True)


def get_mode() -> str:
    return env("STORAGE_MODE", STORAGE_MODE).lower()


def get_sb() -> "SupabaseStorage":
    if SupabaseStorage is None:
        raise RuntimeError("SupabaseStorage not available. Ensure storage_supabase.py + requirements are installed.")
    return SupabaseStorage()


def sb_key(*parts: str) -> str:
    return "/".join([p.strip("/").replace("\\", "/") for p in parts if p is not None])


# =========================
# Database (SQLAlchemy)
# =========================
from sqlalchemy import create_engine, Column, String, DateTime, Integer, Text
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class AudioAsset(Base):
    __tablename__ = "audio_assets"

    id = Column(String, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    original_filename = Column(String, nullable=False)
    stored_path = Column(Text, nullable=False)  # local path or sb://bucket/key
    file_size_bytes = Column(Integer, nullable=False, default=0)

    fingerprint_status = Column(String, default="pending")  # pending|done|error
    fingerprint_path = Column(Text, nullable=True)
    fingerprint_error = Column(Text, nullable=True)

    spectrogram_path = Column(Text, nullable=True)

    # Last proof info (optional)
    proof_hash = Column(String, nullable=True)
    tx_hash = Column(String, nullable=True)
    explorer_url = Column(Text, nullable=True)

    # Last email info (optional)
    email_sent = Column(String, nullable=True)  # "true"/"false"
    email_reason = Column(Text, nullable=True)


class LibraryTrack(Base):
    __tablename__ = "library_tracks"

    id = Column(String, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    filename = Column(String, nullable=False)
    stored_path = Column(Text, nullable=False)  # local path or sb://bucket/key


class MonitorIncident(Base):
    __tablename__ = "monitor_incidents"

    id = Column(String, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    inbox_filename = Column(String, nullable=False)
    inbox_path = Column(Text, nullable=False)  # local path or sb://bucket/key
    mode = Column(String, default="vr")

    match_filename = Column(String, nullable=True)
    match_path = Column(Text, nullable=True)
    common_hashes = Column(Integer, nullable=True)
    rank = Column(Integer, nullable=True)
    offset_sec = Column(String, nullable=True)

    email_sent = Column(String, nullable=True)  # "true"/"false"
    email_reason = Column(Text, nullable=True)  # message id or reason


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =========================
# Helpers (Hashing / JSON)
# =========================
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def guess_content_type(filename: str) -> str:
    f = filename.lower()
    if f.endswith(".png"):
        return "image/png"
    if f.endswith(".jpg") or f.endswith(".jpeg"):
        return "image/jpeg"
    if f.endswith(".wav"):
        return "audio/wav"
    if f.endswith(".mp3"):
        return "audio/mpeg"
    if f.endswith(".flac"):
        return "audio/flac"
    return "application/octet-stream"


def is_audio_file(name: str) -> bool:
    ext = os.path.splitext(name)[1].lower()
    return ext in [".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"]


# =========================
# Helpers (Storage abstraction)
# =========================
def parse_sb_uri(path: str) -> Optional[Dict[str, str]]:
    # sb://bucket/key
    if not path.startswith("sb://"):
        return None
    rest = path[5:]
    if "/" not in rest:
        return None
    bucket, key = rest.split("/", 1)
    return {"bucket": bucket, "key": key}


def storage_put_bytes(key: str, data: bytes, content_type: str) -> str:
    """
    Returns stored_path string:
      local => absolute path
      supabase => sb://<bucket>/<key>
    """
    mode = get_mode()

    if mode == "supabase":
        sb = get_sb()
        sb.upload_bytes(key, data, content_type=content_type)
        return f"sb://{sb.bucket}/{key}"

    # local
    abs_path = STORAGE_ROOT / key.replace("/", os.sep)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(data)
    return str(abs_path)


def storage_put_file(key: str, file_path: str, content_type: str) -> str:
    mode = get_mode()

    if mode == "supabase":
        sb = get_sb()
        sb.upload_file(key, file_path, content_type=content_type)
        return f"sb://{sb.bucket}/{key}"

    abs_path = STORAGE_ROOT / key.replace("/", os.sep)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(file_path, str(abs_path))
    return str(abs_path)


def storage_get_to_temp(stored_path: str) -> str:
    """
    Given a stored_path (local absolute path OR sb://bucket/key),
    return a local temp filename containing the bytes.
    """
    mode = get_mode()

    # If it is a local absolute path:
    if mode == "local" and not stored_path.startswith("sb://"):
        if not os.path.exists(stored_path):
            raise FileNotFoundError(stored_path)
        return stored_path

    sb_info = parse_sb_uri(stored_path)
    if not sb_info:
        # might still be local path in supabase mode (shouldn't happen but handle)
        if os.path.exists(stored_path):
            return stored_path
        raise FileNotFoundError(stored_path)

    sb = get_sb()
    key = sb_info["key"]
    data = sb.download_bytes(key)

    suffix = os.path.splitext(key)[1] or ".bin"
    tmp_dir = tempfile.mkdtemp(prefix="sampledetect_")
    tmp_path = os.path.join(tmp_dir, f"dl{suffix}")
    with open(tmp_path, "wb") as f:
        f.write(data)
    return tmp_path


def storage_read_bytes(stored_path: str) -> bytes:
    mode = get_mode()

    if mode == "local" and not stored_path.startswith("sb://"):
        return Path(stored_path).read_bytes()

    sb_info = parse_sb_uri(stored_path)
    if not sb_info:
        return Path(stored_path).read_bytes()

    sb = get_sb()
    return sb.download_bytes(sb_info["key"])


def storage_delete_path(stored_path: str) -> None:
    mode = get_mode()

    if mode == "local" and not stored_path.startswith("sb://"):
        try:
            os.remove(stored_path)
        except Exception:
            pass
        return

    sb_info = parse_sb_uri(stored_path)
    if sb_info:
        sb = get_sb()
        try:
            sb.remove(sb_info["key"])
        except Exception:
            pass


# =========================
# Helpers (ffmpeg)
# =========================
def run_ffmpeg(args: List[str]) -> None:
    cmd = [FFMPEG] + args
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {p.stderr[:4000]}")


def make_vr_variant(input_path: str, out_path: str, variant: str) -> None:
    """
    VR preprocessing variant for audfprint matching.
    We always resample to 11025 mono s16 wav to match audfprint defaults.
    """
    if variant == "vr":
        # Basic vocal-resistant band-pass / mono / 11025
        # band 80-2000 is a good general choice for beat structure
        run_ffmpeg([
            "-y", "-i", input_path,
            "-vn",
            "-af", "highpass=f=80,lowpass=f=2000,acompressor=threshold=-18dB:ratio=2:attack=5:release=50",
            "-ac", "1",
            "-ar", "11025",
            out_path
        ])
        return

    # normal
    run_ffmpeg([
        "-y", "-i", input_path,
        "-vn",
        "-ac", "1",
        "-ar", "11025",
        out_path
    ])


def make_spectrogram_png(input_path: str, out_png: str) -> None:
    # Good looking spectrogram
    run_ffmpeg([
        "-y", "-i", input_path,
        "-lavfi", "showspectrumpic=s=1200x400:legend=disabled:mode=separate",
        out_png
    ])


# =========================
# Helpers (audfprint)
# =========================
MATCH_LINE_RE = re.compile(
    r"Matched\s+(?P<query>.+?)\s+.+?\s+as\s+(?P<match>.+?)\s+at\s+(?P<offset>-?\d+\.?\d*)\s+s\s+with\s+(?P<common>\d+)\s+of\s+(?P<total>\d+)\s+common\s+hashes\s+at\s+rank\s+(?P<rank>\d+)",
    re.IGNORECASE
)


def run_audfprint(cmd_args: List[str]) -> str:
    """
    Runs audfprint.py using your venv python if provided,
    else uses current python.
    """
    py = PYTHON_EXE or sys.executable  # type: ignore
    full = [py, AUDFPRINT_PY] + cmd_args
    p = subprocess.run(full, capture_output=True, text=True)
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    if p.returncode != 0:
        raise RuntimeError(out[-4000:])
    return out


def parse_audfprint_output(raw: str) -> Optional[Dict[str, Any]]:
    lines = raw.splitlines()
    # find best "Matched ..." line
    for line in reversed(lines):
        m = MATCH_LINE_RE.search(line)
        if m:
            return {
                "match_path": m.group("match").strip(),
                "match_filename": os.path.basename(m.group("match").strip()),
                "offset_sec": float(m.group("offset")),
                "common_hashes": int(m.group("common")),
                "total_common_candidates": int(m.group("total")),
                "rank": int(m.group("rank")),
                "raw_line": line.strip()
            }
    return None


def build_audfprint_file_list_local() -> int:
    ensure_local_dirs()
    files = []
    for p in LIBRARY_AUDIO_DIR.rglob("*"):
        if p.is_file() and is_audio_file(p.name):
            files.append(str(p))
    AUDFPRINT_DIR.mkdir(parents=True, exist_ok=True)
    # ASCII avoids BOM issues
    AUDFPRINT_FILES_TXT_LOCAL.write_text("\n".join(files), encoding="ascii", errors="ignore")
    return len(files)


def audfprint_index_local() -> Dict[str, Any]:
    files_listed = build_audfprint_file_list_local()
    if files_listed == 0:
        return {"ok": True, "files_listed": 0, "note": "No files in library_audio."}

    AUDFPRINT_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        "new",
        "--dbase", str(AUDFPRINT_PKLZ_LOCAL),
        "-l", str(AUDFPRINT_FILES_TXT_LOCAL)
    ]
    raw = run_audfprint(cmd)
    return {"ok": True, "files_listed": files_listed, "db_path": str(AUDFPRINT_PKLZ_LOCAL), "raw_tail": raw[-1200:]}


def audfprint_index_supabase(db) -> Dict[str, Any]:
    """
    Hosted minimal: uses DB library_tracks as the source of truth.
    Downloads each library track to /tmp and builds a pklz, then uploads to Supabase.
    """
    sb = get_sb()
    tmp_dir = tempfile.mkdtemp(prefix="audf_lib_")
    tmp_audio_dir = os.path.join(tmp_dir, "library_audio")
    os.makedirs(tmp_audio_dir, exist_ok=True)

    tracks = db.query(LibraryTrack).all()
    if not tracks:
        return {"ok": True, "files_listed": 0, "note": "No library tracks in DB. Use /library/index first."}

    file_list = []
    for t in tracks:
        local_path = storage_get_to_temp(t.stored_path)
        # move into tmp_audio_dir with safe filename
        safe_name = re.sub(r"[^a-zA-Z0-9_\-\. ]", "_", t.filename)
        dest = os.path.join(tmp_audio_dir, safe_name)
        shutil.copyfile(local_path, dest)
        file_list.append(dest)

    files_txt = os.path.join(tmp_dir, "library_files.txt")
    with open(files_txt, "w", encoding="ascii", errors="ignore") as f:
        f.write("\n".join(file_list))

    out_pklz = os.path.join(tmp_dir, "library.pklz")
    raw = run_audfprint(["new", "--dbase", out_pklz, "-l", files_txt])

    # upload pklz to supabase storage
    key = sb_key("audfprint", "library.pklz")
    storage_put_file(key, out_pklz, "application/octet-stream")

    return {"ok": True, "files_listed": len(tracks), "db_key": key, "raw_tail": raw[-1200:]}


def audfprint_match(asset_path_local: str, db_path_local: str) -> Dict[str, Any]:
    raw = run_audfprint(["match", "--dbase", db_path_local, asset_path_local])
    best = parse_audfprint_output(raw)
    return {"best": best, "raw_tail": raw[-2000:]}


# =========================
# Email helpers
# =========================
def send_email(subject: str, html: str) -> Dict[str, Any]:
    api_key = env("SENDGRID_API_KEY")
    email_from = env("EMAIL_FROM")
    email_to = env("EMAIL_TO")

    if not api_key or not email_from or not email_to:
        return {"sent": False, "reason": "Missing SENDGRID_API_KEY/EMAIL_FROM/EMAIL_TO", "status_code": None}

    if SendGridAPIClient is None or Mail is None:
        return {"sent": False, "reason": "SendGrid libs not installed", "status_code": None}

    try:
        message = Mail(
            from_email=email_from,
            to_emails=email_to,
            subject=subject,
            html_content=html,
        )
        sg = SendGridAPIClient(api_key)
        resp = sg.send(message)
        # SendGrid sometimes returns header "X-Message-Id"
        msg_id = None
        try:
            msg_id = resp.headers.get("X-Message-Id")
        except Exception:
            msg_id = None
        return {"sent": True, "status_code": resp.status_code, "message_id": msg_id}
    except Exception as e:
        return {"sent": False, "reason": str(e), "status_code": None}


def make_dispute_template_html(inbox_filename: str, match_filename: str, common_hashes: int, rank: int, offset_sec: float) -> str:
    # Template / example only
    utc_now = datetime.utcnow().isoformat() + "Z"
    return f"""
    <h2>Potential Unauthorized Upload Detected ✅</h2>
    <p><b>Inbox file:</b> {inbox_filename}</p>

    <hr/>

    <h3>Match Summary</h3>
    <p><b>Matched beat:</b> {match_filename}</p>
    <p><b>Common hashes:</b> {common_hashes}</p>
    <p><b>Rank:</b> {rank}</p>
    <p><b>Offset (sec):</b> {offset_sec}</p>

    <p style="color:#777; font-size:12px;">
    This alert was generated by SampleDetect SAE (prototype). The "monitor inbox" simulates external platform uploads.
    </p>

    <hr/>

    <h3>Template Dispute / Takedown Notice (Example Only — Not Legal Advice)</h3>
    <p style="color:#666; font-size:12px;">
    Use this as a starting point when contacting a platform or uploader. Modify to fit your situation and local laws.
    </p>

    <div style="border:1px solid #ddd; padding:12px; border-radius:10px; background:#fafafa;">
    <p><b>Subject:</b> Notice of Unauthorized Use of Copyrighted Sound Recording / Beat</p>

    <p>Hello,</p>

    <p>
    I am the copyright owner (or authorized representative) of the musical work / sound recording titled:
    <b>[YOUR BEAT TITLE]</b> by <b>[YOUR NAME / PRODUCER NAME]</b>.
    </p>

    <p>
    I have identified content that appears to use my copyrighted audio without authorization.
    Below is evidence generated by an audio-fingerprint detection tool:
    </p>

    <ul>
      <li><b>Detected upload file name:</b> {inbox_filename}</li>
      <li><b>Matched reference beat:</b> {match_filename}</li>
      <li><b>Detection confidence indicators:</b> common_hashes={common_hashes}, rank={rank}, offset={offset_sec}</li>
      <li><b>Detection timestamp (system time):</b> {utc_now}</li>
    </ul>

    <p>
    I request that you (a) remove/disable access to the infringing content, or (b) initiate your dispute/appeal workflow so ownership can be verified.
    I can provide additional verification upon request, including project/session files, stems, and proof-of-creation metadata.
    </p>

    <p>
    Please confirm receipt of this notice and advise next steps within <b>[7]</b> business days.
    </p>

    <p>
    Sincerely,<br/>
    <b>[YOUR NAME]</b><br/>
    <b>[YOUR EMAIL]</b><br/>
    <b>[YOUR WEBSITE / PORTFOLIO LINK]</b><br/>
    </p>
    </div>

    <hr/>

    <p style="color:#777; font-size:12px;">
    Important: This is a template for demonstration purposes and does not constitute legal advice.
    </p>
    """


# =========================
# Blockchain proof helper
# =========================
PROOF_ABI = [
    {
        "inputs": [{"internalType": "bytes32", "name": "proofHash", "type": "bytes32"}],
        "name": "recordProof",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

def record_proof_on_chain(proof_hash_hex: str) -> Dict[str, Any]:
    """
    Records a bytes32 hash to Polygon Amoy using ProofRegistry contract.
    Requires: AMOY_RPC_URL, AMOY_PRIVATE_KEY, PROOF_REGISTRY_ADDRESS
    """
    rpc = env("AMOY_RPC_URL")
    pk = env("AMOY_PRIVATE_KEY")
    addr = env("PROOF_REGISTRY_ADDRESS")

    if not rpc or not pk or not addr:
        return {"ok": False, "reason": "Missing AMOY_RPC_URL / AMOY_PRIVATE_KEY / PROOF_REGISTRY_ADDRESS"}

    if Web3 is None:
        return {"ok": False, "reason": "web3 not installed"}

    w3 = Web3(Web3.HTTPProvider(rpc))
    acct = w3.eth.account.from_key(pk)

    contract = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=PROOF_ABI)

    # bytes32 from hex string
    b = bytes.fromhex(proof_hash_hex)
    if len(b) != 32:
        # hash hex is 64 chars => 32 bytes. If not, compress using sha256 again:
        b = hashlib.sha256(b).digest()

    nonce = w3.eth.get_transaction_count(acct.address)
    tx = contract.functions.recordProof(b).build_transaction({
        "from": acct.address,
        "nonce": nonce,
        "chainId": w3.eth.chain_id,
        "gas": 200000,
    })

    # Let node suggest fees if possible; fallback
    try:
        tx["maxFeePerGas"] = w3.eth.gas_price
        tx["maxPriorityFeePerGas"] = w3.eth.gas_price
    except Exception:
        pass

    signed = acct.sign_transaction(tx)
    txh = w3.eth.send_raw_transaction(signed.rawTransaction)
    tx_hash = txh.hex()
    explorer = f"https://amoy.polygonscan.com/tx/{tx_hash}"
    return {"ok": True, "tx_hash": tx_hash, "explorer_url": explorer, "from": acct.address}


# =========================
# FastAPI app
# =========================
app = FastAPI(title="SampleDetect SAE API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ok for prototype
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True, "mode": get_mode(), "time": datetime.utcnow().isoformat() + "Z"}


# =========================
# Assets
# =========================
@app.post("/upload")
async def upload_audio(file: UploadFile = File(...), db=Depends(get_db)):
    """
    Upload audio asset.
    - local: writes to STORAGE_ROOT/uploads/<id>/original.<ext>
    - supabase: writes to bucket key uploads/<id>/original.<ext> (sb://...)
    """
    asset_id = str(uuid.uuid4())
    filename = file.filename or "upload.bin"
    ext = os.path.splitext(filename)[1] or ".bin"
    key = sb_key("uploads", asset_id, f"original{ext}")

    data = await file.read()
    stored_path = storage_put_bytes(key, data, content_type=file.content_type or guess_content_type(filename))

    a = AudioAsset(
        id=asset_id,
        original_filename=filename,
        stored_path=stored_path,
        file_size_bytes=len(data),
        fingerprint_status="pending",
    )
    db.add(a)
    db.commit()

    return {"ok": True, "id": asset_id, "stored_path": stored_path, "size": len(data)}


@app.get("/assets")
def list_assets(db=Depends(get_db)):
    assets = db.query(AudioAsset).order_by(AudioAsset.created_at.desc()).all()
    out = []
    for a in assets:
        out.append({
            "id": a.id,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "original_filename": a.original_filename,
            "stored_path": a.stored_path,
            "file_size_bytes": a.file_size_bytes,
            "fingerprint_status": a.fingerprint_status,
            "fingerprint_path": a.fingerprint_path,
            "fingerprint_error": a.fingerprint_error,
            "spectrogram_url": f"/assets/{a.id}/spectrogram.png" if a.spectrogram_path else None,
            "proof_hash": a.proof_hash,
            "tx_hash": a.tx_hash,
            "explorer_url": a.explorer_url,
            "email_sent": True if a.email_sent == "true" else (False if a.email_sent == "false" else None),
            "email_reason": a.email_reason,
        })
    return out


@app.post("/assets/clear")
def clear_assets(db=Depends(get_db)):
    """
    Deletes only uploaded assets (not library tracks).
    Also deletes related stored objects (uploads/fingerprints/spectrograms) for those assets.
    """
    assets = db.query(AudioAsset).all()
    deleted = 0

    for a in assets:
        # remove upload
        storage_delete_path(a.stored_path)
        # remove fingerprint
        if a.fingerprint_path:
            storage_delete_path(a.fingerprint_path)
        # remove spectrogram
        if a.spectrogram_path:
            storage_delete_path(a.spectrogram_path)

        db.delete(a)
        deleted += 1

    db.commit()
    return {"ok": True, "deleted_assets": deleted}


@app.post("/assets/{asset_id}/fingerprint")
def fingerprint_asset(asset_id: str, db=Depends(get_db)):
    """
    Fingerprints the uploaded file and stores fingerprint.json.
    This is the MVP fingerprint (sha256 + metadata), not audfprint.
    """
    a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Asset not found")

    try:
        b = storage_read_bytes(a.stored_path)
        fp = {
            "id": a.id,
            "filename": a.original_filename,
            "sha256": sha256_bytes(b),
            "size": len(b),
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        key = sb_key("fingerprints", a.id, "fingerprint.json")
        fp_path = storage_put_bytes(key, json.dumps(fp, indent=2).encode("utf-8"), content_type="application/json")

        a.fingerprint_status = "done"
        a.fingerprint_path = fp_path
        a.fingerprint_error = None
        db.commit()

        return {"ok": True, "id": a.id, "fingerprint_status": "done", "fingerprint_path": fp_path, "fingerprint_preview": fp["sha256"][:32]}
    except Exception as e:
        a.fingerprint_status = "error"
        a.fingerprint_error = str(e)
        db.commit()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/assets/{asset_id}/match")
def match_asset_mvp(asset_id: str, db=Depends(get_db)):
    """
    MVP matcher: compares against library filenames using RapidFuzz.
    Returns top_5 with pseudo scores.
    """
    if fuzz is None:
        raise HTTPException(status_code=500, detail="rapidfuzz not installed")

    tracks = db.query(LibraryTrack).all()
    if not tracks:
        raise HTTPException(status_code=400, detail="Library empty. Run /library/index first.")

    a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Asset not found")

    # Compare by name similarity (prototype)
    scores = []
    for t in tracks:
        s = fuzz.token_set_ratio(a.original_filename.lower(), t.filename.lower())
        scores.append({"score": float(s), "library_id": t.id, "filename": t.filename})
    scores.sort(key=lambda x: x["score"], reverse=True)
    return {"asset_id": a.id, "match_type": "mvp_filename_similarity", "top_5": scores[:5]}


# =========================
# Library
# =========================
@app.post("/library/index")
def library_index(db=Depends(get_db)):
    """
    Index library audio:
    - local: scans STORAGE_ROOT/library_audio and stores list in DB
    - supabase: scans supabase keys under library_audio/ and stores in DB
    """
    mode = get_mode()

    # clear existing library rows (re-index)
    db.query(LibraryTrack).delete()
    db.commit()

    added = 0
    errors = 0

    if mode == "local":
        ensure_local_dirs()
        for p in LIBRARY_AUDIO_DIR.rglob("*"):
            if p.is_file() and is_audio_file(p.name):
                t = LibraryTrack(
                    id=str(uuid.uuid4()),
                    filename=p.name,
                    stored_path=str(p),
                )
                db.add(t)
                added += 1
        db.commit()
        return {"ok": True, "mode": "local", "added": added, "errors": errors}

    # supabase mode
    sb = get_sb()
    # list is limited; but for demo we only store direct children of library_audio/
    # We'll list the "library_audio" folder and store all audio-looking keys.
    items = sb.sb.list(path="library_audio")
    for it in items:
        name = it.get("name") or ""
        if not name:
            continue
        if is_audio_file(name):
            key = sb_key("library_audio", name)
            t = LibraryTrack(
                id=str(uuid.uuid4()),
                filename=name,
                stored_path=f"sb://{sb.bucket}/{key}",
            )
            db.add(t)
            added += 1
    db.commit()
    return {"ok": True, "mode": "supabase", "added": added, "errors": errors, "note": "Upload demo library files to Supabase Storage folder: library_audio/"}


@app.get("/library")
def list_library(db=Depends(get_db)):
    tracks = db.query(LibraryTrack).order_by(LibraryTrack.created_at.desc()).all()
    return [
        {
            "id": t.id,
            "filename": t.filename,
            "stored_path": t.stored_path,
            "created_at": t.created_at.isoformat() if t.created_at else None
        }
        for t in tracks
    ]


# =========================
# audfprint
# =========================
@app.post("/audfprint/index")
def audfprint_index(db=Depends(get_db)):
    """
    Builds audfprint database:
    - local: writes to STORAGE_ROOT/audfprint/library.pklz
    - supabase: downloads library tracks to /tmp, builds pklz, uploads to audfprint/library.pklz
    """
    mode = get_mode()
    if mode == "local":
        return audfprint_index_local()
    return audfprint_index_supabase(db)


@app.post("/audfprint/match")
def audfprint_match_endpoint(
    asset_id: str = Query(...),
    mode: str = Query("vr"),
    db=Depends(get_db)
):
    """
    Reliable matching using audfprint.
    mode=normal|vr
    Returns best match with common hashes and rank.
    """
    a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Asset not found")

    # Get audfprint DB (local path)
    if get_mode() == "local":
        db_path_local = str(AUDFPRINT_PKLZ_LOCAL)
        if not os.path.exists(db_path_local):
            raise HTTPException(status_code=400, detail="audfprint DB not found. Run /audfprint/index first.")
    else:
        # Supabase: download audfprint/library.pklz to temp
        sb = get_sb()
        pkl_uri = f"sb://{sb.bucket}/audfprint/library.pklz"
        db_path_local = storage_get_to_temp(pkl_uri)
        if not os.path.exists(db_path_local):
            raise HTTPException(status_code=400, detail="audfprint DB not found in storage. Run /audfprint/index first.")

    # Download asset to temp and preprocess
    original_local = storage_get_to_temp(a.stored_path)

    tmp_dir = tempfile.mkdtemp(prefix=f"audf_query_{a.id}_")
    variant = "vr" if mode.lower() == "vr" else "normal"
    query_wav = os.path.join(tmp_dir, f"audf_query_{variant}_{a.id}.wav")
    make_vr_variant(original_local, query_wav, variant)

    result = audfprint_match(query_wav, db_path_local)
    best = result["best"]

    payload = {
        "asset_id": a.id,
        "mode": variant,
        "query_path": query_wav,
        "db_path": db_path_local,
        "best": best,
        "raw_output_tail": result["raw_tail"],
        "note": "audfprint is the reliable matcher (constellation hashes + alignment)."
    }
    return payload


# =========================
# Spectrogram
# =========================
@app.post("/assets/{asset_id}/spectrogram")
def make_asset_spectrogram(asset_id: str, db=Depends(get_db)):
    a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Asset not found")

    original_local = storage_get_to_temp(a.stored_path)

    tmp_dir = tempfile.mkdtemp(prefix=f"spec_{a.id}_")
    out_png = os.path.join(tmp_dir, f"{a.id}.png")
    make_spectrogram_png(original_local, out_png)

    key = sb_key("spectrograms", f"{a.id}.png")
    stored = storage_put_file(key, out_png, "image/png")

    a.spectrogram_path = stored
    db.commit()

    return {"ok": True, "asset_id": a.id, "spectrogram_url": f"/assets/{a.id}/spectrogram.png"}


@app.get("/assets/{asset_id}/spectrogram.png")
def get_asset_spectrogram(asset_id: str, db=Depends(get_db)):
    a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
    if not a or not a.spectrogram_path:
        raise HTTPException(status_code=404, detail="Spectrogram not found")

    local_path = storage_get_to_temp(a.spectrogram_path)
    return FileResponse(local_path, media_type="image/png")


# =========================
# Proofs (blockchain + email)
# =========================
@app.post("/proofs/record")
def proofs_record(asset_id: str = Query(...), library_id: str = Query(...), db=Depends(get_db)):
    """
    Record proof:
    - Compute proof_hash = sha256(asset_id + library_id + timestamp)
    - Optionally write to blockchain via ProofRegistry
    - Send email notification (SendGrid)
    """
    a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Asset not found")

    t = db.query(LibraryTrack).filter(LibraryTrack.id == library_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Library track not found")

    payload = f"{asset_id}|{library_id}|{datetime.utcnow().isoformat()}".encode("utf-8")
    proof_hash_hex = hashlib.sha256(payload).hexdigest()  # 64 hex chars => 32 bytes

    chain = record_proof_on_chain(proof_hash_hex)
    if chain.get("ok"):
        a.tx_hash = chain.get("tx_hash")
        a.explorer_url = chain.get("explorer_url")
    else:
        a.tx_hash = None
        a.explorer_url = None

    a.proof_hash = proof_hash_hex
    db.commit()

    # Email content
    subject = f"SampleDetect SAE Proof Recorded ✅ {a.original_filename}"
    html = f"""
    <h2>Proof Recorded ✅</h2>
    <p><b>Uploaded file:</b> {a.original_filename}</p>
    <p><b>Matched library beat:</b> {t.filename}</p>
    <p><b>Proof hash:</b> <code>{proof_hash_hex}</code></p>
    <p><b>Blockchain tx:</b> {a.tx_hash or "(not recorded / missing config)"}</p>
    <p><b>Explorer:</b> {a.explorer_url or "(n/a)"}</p>
    <hr/>
    <p style="color:#777;font-size:12px;">Prototype system email. Not legal advice.</p>
    """

    email = send_email(subject, html)
    a.email_sent = "true" if email.get("sent") else "false"
    a.email_reason = email.get("message_id") or email.get("reason")
    db.commit()

    return {
        "ok": True,
        "asset_id": a.id,
        "library_id": t.id,
        "proof_hash": proof_hash_hex,
        "tx_hash": a.tx_hash,
        "explorer_url": a.explorer_url,
        "email": email,
        "chain": chain,
    }


# =========================
# Monitor (Option A: hosted inbox upload)
# =========================
@app.post("/monitor/upload")
async def monitor_upload(file: UploadFile = File(...)):
    """
    Hosted-monitor simulation:
    Users upload a file here to simulate "someone uploaded to Spotify/YouTube".
    - local: saves into STORAGE_ROOT/monitor_inbox
    - supabase: uploads into storage key monitor_inbox/<filename>
    """
    filename = file.filename or f"monitor_{uuid.uuid4()}.bin"
    data = await file.read()
    key = sb_key("monitor_inbox", filename)

    if get_mode() == "supabase":
        stored = storage_put_bytes(key, data, content_type=file.content_type or guess_content_type(filename))
        return {"ok": True, "mode": "supabase", "monitor_key": key, "stored_path": stored}

    ensure_local_dirs()
    dest = MONITOR_INBOX_DIR / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return {"ok": True, "mode": "local", "monitor_path": str(dest)}


@app.post("/monitor/scan")
def monitor_scan(mode: str = Query("vr"), db=Depends(get_db)):
    """
    Scans monitor inbox:
    - local: scans STORAGE_ROOT/monitor_inbox
    - supabase: scans bucket folder monitor_inbox/
    For each audio file:
      - run audfprint match (vr/normal)
      - create MonitorIncident row
      - send email alert (with template dispute notice)
      - move to processed (monitor_processed/)
    """
    run_mode = "vr" if mode.lower() == "vr" else "normal"
    scanned = 0
    incidents = 0
    errors = 0

    # Ensure audfprint DB exists
    if get_mode() == "local":
        if not os.path.exists(str(AUDFPRINT_PKLZ_LOCAL)):
            raise HTTPException(status_code=400, detail="audfprint DB missing. Run /audfprint/index first.")
        db_path_local = str(AUDFPRINT_PKLZ_LOCAL)
    else:
        sb = get_sb()
        pkl_uri = f"sb://{sb.bucket}/audfprint/library.pklz"
        db_path_local = storage_get_to_temp(pkl_uri)
        if not os.path.exists(db_path_local):
            raise HTTPException(status_code=400, detail="audfprint DB missing in storage. Run /audfprint/index first.")

    def handle_one(inbox_name: str, inbox_path: str, local_audio_path: str):
        nonlocal incidents, errors

        try:
            # preprocess + match
            tmp_dir = tempfile.mkdtemp(prefix="monq_")
            query_wav = os.path.join(tmp_dir, f"mon_{run_mode}.wav")
            make_vr_variant(local_audio_path, query_wav, "vr" if run_mode == "vr" else "normal")

            res = audfprint_match(query_wav, db_path_local)
            best = res["best"]

            inc = MonitorIncident(
                id=str(uuid.uuid4()),
                inbox_filename=inbox_name,
                inbox_path=inbox_path,
                mode=run_mode,
            )

            if best:
                inc.match_filename = best["match_filename"]
                inc.match_path = best["match_path"]
                inc.common_hashes = best["common_hashes"]
                inc.rank = best["rank"]
                inc.offset_sec = str(best["offset_sec"])
            else:
                inc.match_filename = None
                inc.match_path = None
                inc.common_hashes = None
                inc.rank = None
                inc.offset_sec = None

            # Email if we got any best match
            if best:
                subject = f"SampleDetect SAE Alert ✅ Potential unauthorized use: {inbox_name}"
                html = make_dispute_template_html(
                    inbox_filename=inbox_name,
                    match_filename=best["match_filename"],
                    common_hashes=best["common_hashes"],
                    rank=best["rank"],
                    offset_sec=best["offset_sec"],
                )
                email = send_email(subject, html)
                inc.email_sent = "true" if email.get("sent") else "false"
                inc.email_reason = email.get("message_id") or email.get("reason")
            else:
                inc.email_sent = "false"
                inc.email_reason = "No match found"

            db.add(inc)
            db.commit()
            incidents += 1
        except Exception as e:
            errors += 1
            # still record an incident row for audit
            inc = MonitorIncident(
                id=str(uuid.uuid4()),
                inbox_filename=inbox_name,
                inbox_path=inbox_path,
                mode=run_mode,
                email_sent="false",
                email_reason=f"scan_error: {str(e)}",
            )
            db.add(inc)
            db.commit()

    # ---- local mode scanning ----
    if get_mode() == "local":
        ensure_local_dirs()
        for p in MONITOR_INBOX_DIR.rglob("*"):
            if not p.is_file():
                continue
            if not is_audio_file(p.name):
                continue
            scanned += 1

            # process file
            handle_one(p.name, str(p), str(p))

            # move to processed to prevent re-alert spam
            dest = MONITOR_PROCESSED_DIR / p.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(str(p), str(dest))
            except Exception:
                pass

        return {"ok": True, "mode": "local", "scanned": scanned, "incidents": incidents, "errors": errors}

    # ---- supabase mode scanning ----
    sb = get_sb()
    # list monitor_inbox folder
    items = sb.sb.list(path="monitor_inbox")
    for it in items:
        name = it.get("name") or ""
        if not name or not is_audio_file(name):
            continue

        scanned += 1
        inbox_key = sb_key("monitor_inbox", name)
        inbox_uri = f"sb://{sb.bucket}/{inbox_key}"
        local_audio = storage_get_to_temp(inbox_uri)

        handle_one(name, inbox_uri, local_audio)

        # move to processed (so we don't scan again)
        try:
            sb.sb.move(inbox_key, sb_key("monitor_processed", name))
        except Exception:
            pass

    return {"ok": True, "mode": "supabase", "scanned": scanned, "incidents": incidents, "errors": errors}


@app.get("/monitor/incidents")
def monitor_incidents(db=Depends(get_db)):
    rows = db.query(MonitorIncident).order_by(MonitorIncident.created_at.desc()).all()
    out = []
    for r in rows:
        out.append({
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
        })
    return out


# =========================
# Root convenience
# =========================
@app.get("/")
def root():
    return {"ok": True, "service": "SampleDetect SAE API", "docs": "/docs"}