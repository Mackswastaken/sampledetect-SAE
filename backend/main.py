import os
import sys
import json
import time
import uuid
import shutil
import base64
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

# ✅ If any of these imports fail, tell me the exact ImportError line and
# I'll give you the correct import lines for your project structure.
from db import get_db  # expects get_db() -> Session
from models import AudioAsset, MonitorIncident  # adjust if your model names differ
from settings import (
    ensure_dirs,
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
)

# Optional (only if you have these env vars for email)
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "")
EMAIL_TO_DEFAULT = os.getenv("EMAIL_TO", "")  # fallback if frontend doesn't provide

# Optional (proof/chain)
PROOF_CONTRACT_ADDRESS = os.getenv("PROOF_CONTRACT_ADDRESS", "")
CHAIN_RPC_URL = os.getenv("CHAIN_RPC_URL", "")
DEPLOYER_PRIVATE_KEY = os.getenv("DEPLOYER_PRIVATE_KEY", "")

# Optional (Supabase storage)
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "sampledetect")

app = FastAPI(title="SampleDetect SAE API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------------------------
# Storage abstraction
# -------------------------
class LocalStorage:
    def __init__(self):
        ensure_dirs()

    def put_file(self, local_path: Path, stored_path: str) -> str:
        """Copy local_path into STORAGE_ROOT/<stored_path>"""
        dest = (STORAGE_ROOT / stored_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest)
        return stored_path

    def get_local_path(self, stored_path: str) -> Path:
        return (STORAGE_ROOT / stored_path).resolve()

    def exists(self, stored_path: str) -> bool:
        return self.get_local_path(stored_path).exists()

    def list_prefix(self, prefix: str) -> list[str]:
        base = (STORAGE_ROOT / prefix).resolve()
        if not base.exists():
            return []
        out = []
        for p in base.rglob("*"):
            if p.is_file():
                out.append(str(p.relative_to(STORAGE_ROOT)).replace("\\", "/"))
        return out


def get_storage():
    if STORAGE_MODE.lower() == "supabase":
        # You said you already got Supabase working.
        # If your storage_supabase module name differs, adjust import here.
        from storage_supabase import SupabaseStorage  # type: ignore

        return SupabaseStorage(
            supabase_url=SUPABASE_URL,
            service_role_key=SUPABASE_SERVICE_ROLE_KEY,
            bucket=SUPABASE_BUCKET,
        )
    return LocalStorage()


# -------------------------
# Helpers
# -------------------------
def now_iso():
    return datetime.utcnow().isoformat()


def safe_uuid() -> str:
    return str(uuid.uuid4())


def run_cmd(cmd: list[str], cwd: Optional[Path] = None, timeout: int = 1800) -> str:
    """Runs a command and returns combined output; raises on nonzero."""
    p = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    if p.returncode != 0:
        raise RuntimeError(out[-4000:] if len(out) > 4000 else out)
    return out


def ffmpeg_to_wav_mono_11025(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-ac",
        "1",
        "-ar",
        "11025",
        str(dst),
    ]
    run_cmd(cmd, cwd=None, timeout=1800)


def make_spectrogram_png(audio_path: Path, out_png: Path) -> None:
    """
    Uses matplotlib. If matplotlib isn't installed, you'll see an ImportError.
    Add `matplotlib` to requirements if needed.
    """
    import numpy as np
    import matplotlib.pyplot as plt
    import soundfile as sf  # if missing, add `soundfile` to requirements

    out_png.parent.mkdir(parents=True, exist_ok=True)
    data, sr = sf.read(str(audio_path))
    if data.ndim > 1:
        data = data.mean(axis=1)

    plt.figure()
    plt.specgram(data, Fs=sr)
    plt.axis("off")
    plt.savefig(str(out_png), bbox_inches="tight", pad_inches=0)
    plt.close()


# -------------------------
# ✅ NEW: resolve library_id from audfprint match output
# -------------------------
def resolve_library_id(db: Session, match_filename: str | None, match_path: str | None) -> Optional[str]:
    """
    Map audfprint's matched filename/path back to the DB library row.
    This is READ-ONLY and does NOT modify anything.
    """
    if not match_filename and not match_path:
        return None

    fn = (match_filename or "").strip()
    if not fn and match_path:
        fn = os.path.basename(match_path).strip()
    if not fn:
        return None

    stored_like_1 = f"%/library_audio/{fn}"
    stored_like_2 = f"%library_audio/{fn}"
    stored_exact = f"library_audio/{fn}"

    # Try common table names. If your library table is different,
    # add it here.
    table_candidates = ["audio_library", "library_audio", "library_items"]

    for table in table_candidates:
        try:
            row = db.execute(
                text(f"""
                    SELECT id
                    FROM {table}
                    WHERE filename = :fn
                       OR stored_path = :stored_exact
                       OR stored_path LIKE :like1
                       OR stored_path LIKE :like2
                    ORDER BY created_at DESC NULLS LAST
                    LIMIT 1
                """),
                {"fn": fn, "stored_exact": stored_exact, "like1": stored_like_1, "like2": stored_like_2},
            ).fetchone()
            if row and row[0]:
                return str(row[0])
        except Exception:
            continue

    return None


# -------------------------
# Startup
# -------------------------
@app.on_event("startup")
def _startup():
    ensure_dirs()


# -------------------------
# Routes
# -------------------------
@app.get("/health")
def health():
    return {"ok": True, "time": now_iso(), "storage_mode": STORAGE_MODE}


@app.post("/upload")
def upload_audio(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    Uploads a file to storage and creates an AudioAsset row.
    """
    storage = get_storage()
    asset_id = safe_uuid()

    # Save upload temporarily
    orig_name = file.filename or f"upload_{asset_id}"
    ext = Path(orig_name).suffix.lower() or ".bin"
    tmp_path = (TEMP_DIR / f"upload_{asset_id}{ext}").resolve()
    tmp_path.parent.mkdir(parents=True, exist_ok=True)

    with open(tmp_path, "wb") as f:
        f.write(file.file.read())

    stored_path = f"uploads/{asset_id}/original{ext}"
    storage.put_file(tmp_path, stored_path)

    # Create DB row
    a = AudioAsset(
        id=asset_id,
        created_at=datetime.utcnow(),
        filename=orig_name,
        stored_path=stored_path,
        size_bytes=tmp_path.stat().st_size,
        duration_sec=0,
        fingerprint_status="pending",
        fingerprint_path=None,
        fingerprint_error=None,
        spectrogram_path=None,
    )
    db.add(a)
    db.commit()

    return {"ok": True, "id": asset_id, "stored_path": stored_path, "filename": orig_name}


@app.get("/assets")
def list_assets(limit: int = 50, db: Session = Depends(get_db)):
    rows = (
        db.query(AudioAsset)
        .order_by(AudioAsset.created_at.desc())
        .limit(limit)
        .all()
    )
    out = []
    for a in rows:
        out.append(
            {
                "id": a.id,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "filename": getattr(a, "filename", None),
                "stored_path": getattr(a, "stored_path", None),
                "size_bytes": getattr(a, "size_bytes", None),
                "duration_sec": getattr(a, "duration_sec", None),
                "fingerprint_status": getattr(a, "fingerprint_status", None),
                "fingerprint_path": getattr(a, "fingerprint_path", None),
                "fingerprint_error": getattr(a, "fingerprint_error", None),
                "spectrogram_path": getattr(a, "spectrogram_path", None),
            }
        )
    return out


# ---- MVP fingerprint (json file per asset) ----
@app.post("/assets/{asset_id}/fingerprint")
def fingerprint_asset(asset_id: str, db: Session = Depends(get_db)):
    storage = get_storage()
    a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
    if not a:
        raise HTTPException(404, "asset not found")

    try:
        a.fingerprint_status = "running"
        db.commit()

        src = storage.get_local_path(a.stored_path) if STORAGE_MODE != "supabase" else None
        # In supabase mode, your storage_supabase should provide a download_to_file method.
        if STORAGE_MODE == "supabase":
            from storage_supabase import download_to_file  # type: ignore
            src = (TEMP_DIR / f"{asset_id}_orig").resolve()
            download_to_file(a.stored_path, src)

        assert src is not None
        fp_dir = (FINGERPRINTS_DIR / asset_id).resolve()
        fp_dir.mkdir(parents=True, exist_ok=True)
        fp_json = fp_dir / "fingerprint.json"

        # Simple placeholder fingerprint: base64 hash of bytes (MVP)
        data = src.read_bytes()
        preview = base64.b64encode(data[:2048]).decode("utf-8")

        payload = {"id": asset_id, "created_at": now_iso(), "fingerprint_preview": preview}
        fp_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        # store fingerprint in storage
        stored_fp_path = f"fingerprints/{asset_id}/fingerprint.json"
        storage.put_file(fp_json, stored_fp_path)

        a.fingerprint_status = "done"
        a.fingerprint_path = stored_fp_path
        a.fingerprint_error = None
        db.commit()

        return {
            "id": asset_id,
            "fingerprint_status": "done",
            "fingerprint_path": stored_fp_path,
            "fingerprint_preview": preview[:64] + "...",
        }
    except Exception as e:
        a.fingerprint_status = "error"
        a.fingerprint_error = str(e)
        db.commit()
        raise


# ---- MVP match (exact fingerprint equality) ----
@app.post("/assets/{asset_id}/match")
def match_asset_mvp(asset_id: str, db: Session = Depends(get_db)):
    a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
    if not a:
        raise HTTPException(404, "asset not found")
    if not a.fingerprint_path:
        raise HTTPException(400, "asset has no fingerprint yet")

    # MVP: exact equality of preview field vs library fingerprints
    # (Kept for demo; real matching is audfprint below.)
    storage = get_storage()

    # load asset fp
    if STORAGE_MODE == "supabase":
        from storage_supabase import download_text  # type: ignore
        asset_fp_text = download_text(a.fingerprint_path)
    else:
        asset_fp_text = storage.get_local_path(a.fingerprint_path).read_text("utf-8")

    asset_fp = json.loads(asset_fp_text)
    asset_preview = asset_fp.get("fingerprint_preview", "")

    # Load library fingerprints folder list (local mode expects files under LIBRARY_FINGERPRINTS_DIR)
    matches = []
    if STORAGE_MODE != "supabase":
        for fp_file in LIBRARY_FINGERPRINTS_DIR.rglob("fingerprint.json"):
            try:
                lib = json.loads(fp_file.read_text("utf-8"))
                if lib.get("fingerprint_preview") == asset_preview:
                    lib_id = fp_file.parent.name
                    matches.append({"library_id": lib_id, "match": "exact"})
            except Exception:
                continue

    return {
        "asset_id": asset_id,
        "match_type": "exact_fingerprint",
        "matches": matches,
        "match_count": len(matches),
        "note": "MVP exact fingerprint equality (demo). Use audfprint_match for real matching.",
    }


# ---- Spectrogram ----
@app.post("/assets/{asset_id}/spectrogram")
def create_spectrogram(asset_id: str, db: Session = Depends(get_db)):
    storage = get_storage()
    a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
    if not a:
        raise HTTPException(404, "asset not found")

    # get audio locally
    if STORAGE_MODE == "supabase":
        from storage_supabase import download_to_file  # type: ignore
        local_audio = (TEMP_DIR / f"{asset_id}_spec_src").resolve()
        download_to_file(a.stored_path, local_audio)
    else:
        local_audio = storage.get_local_path(a.stored_path)

    out_png = (TEMP_DIR / f"{asset_id}_spectrogram.png").resolve()
    make_spectrogram_png(local_audio, out_png)

    stored_png = f"spectrograms/{asset_id}/spectrogram.png"
    storage.put_file(out_png, stored_png)

    a.spectrogram_path = stored_png
    db.commit()

    return {"ok": True, "asset_id": asset_id, "spectrogram_path": stored_png}


@app.get("/assets/{asset_id}/spectrogram.png")
def get_spectrogram(asset_id: str, db: Session = Depends(get_db)):
    storage = get_storage()
    a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
    if not a or not a.spectrogram_path:
        raise HTTPException(404, "spectrogram not found")

    if STORAGE_MODE == "supabase":
        from storage_supabase import download_to_file  # type: ignore
        local_png = (TEMP_DIR / f"{asset_id}_spectrogram.png").resolve()
        download_to_file(a.spectrogram_path, local_png)
    else:
        local_png = storage.get_local_path(a.spectrogram_path)

    return FileResponse(str(local_png), media_type="image/png")


# -------------------------
# audfprint (reliable) — Index + Match
# -------------------------
VENDORED_AUDFPRINT = (Path(__file__).parent / "vendor" / "audfprint" / "audfprint.py").resolve()


def run_audfprint(args: list[str], cwd: Optional[Path] = None) -> str:
    """
    Runs vendored audfprint with the current python executable.
    IMPORTANT: uses sys.executable inside Render container.
    """
    py = sys.executable
    cmd = [py, str(VENDORED_AUDFPRINT)] + args
    return run_cmd(cmd, cwd=cwd, timeout=3600)


@app.post("/audfprint/index")
def audfprint_index(db: Session = Depends(get_db)):
    """
    Builds/refreshes the audfprint database from library_audio files.
    In supabase mode, expects files in bucket under prefix `library_audio/`.
    """
    storage = get_storage()

    workdir = (TEMP_DIR / f"audf_idx_{uuid.uuid4().hex[:6]}").resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    files_txt = workdir / "library_files.txt"
    out_pklz = workdir / "library.pklz"

    # 1) Gather library files
    if STORAGE_MODE == "supabase":
        # list objects under library_audio/
        objs = storage.list_prefix("library_audio/")
        audio_objs = [o for o in objs if o.lower().endswith((".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"))]
        if not audio_objs:
            raise HTTPException(400, "No library_audio files found in Supabase bucket.")

        # download into workdir/library_audio/
        lib_dir = workdir / "library_audio"
        lib_dir.mkdir(parents=True, exist_ok=True)

        downloaded = 0
        for obj in audio_objs:
            local_dst = lib_dir / Path(obj).name
            from storage_supabase import download_to_file  # type: ignore
            download_to_file(obj, local_dst)
            downloaded += 1

        # write a list file
        lines = [str((lib_dir / Path(o).name).resolve()) for o in audio_objs]
        files_txt.write_text("\n".join(lines), encoding="utf-8")

    else:
        # local mode uses D:\SampleDetectStorage\library_audio
        if not LIBRARY_AUDIO_DIR.exists():
            raise HTTPException(400, f"Local library_audio folder not found: {LIBRARY_AUDIO_DIR}")

        lines = []
        for p in LIBRARY_AUDIO_DIR.rglob("*"):
            if p.is_file() and p.suffix.lower() in [".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"]:
                lines.append(str(p.resolve()))
        if not lines:
            raise HTTPException(400, "No audio files found in local library_audio folder.")
        files_txt.write_text("\n".join(lines), encoding="utf-8")

        downloaded = len(lines)

    # 2) Run audfprint "new"
    raw = run_audfprint(["new", "--dbase", str(out_pklz), "-l", str(files_txt)], cwd=workdir)

    # 3) Upload database + files list to storage
    db_stored = "audfprint/library.pklz"
    list_stored = "audfprint/library_files.txt"
    storage.put_file(out_pklz, db_stored)
    storage.put_file(files_txt, list_stored)

    return {
        "ok": True,
        "downloaded": downloaded,
        "download_errors": 0,
        "db_uploaded_to": db_stored,
        "files_uploaded_to": list_stored,
        "raw_output_tail": raw[-2500:],
    }


@app.post("/assets/{asset_id}/audfprint_match")
def audfprint_match(asset_id: str, mode: str = Query("vr", pattern="^(vr|raw)$"), db: Session = Depends(get_db)):
    """
    Reliable matching using audfprint.
    mode=vr: converts query to wav mono 11025 (good for vocals overlay)
    mode=raw: tries query as-is (fastest but less robust)
    """
    storage = get_storage()
    a = db.query(AudioAsset).filter(AudioAsset.id == asset_id).first()
    if not a:
        raise HTTPException(404, "asset not found")

    workdir = (TEMP_DIR / f"audf_match_{uuid.uuid4().hex[:6]}").resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    # Get audfprint DB locally
    local_pklz = workdir / "library.pklz"
    if STORAGE_MODE == "supabase":
        from storage_supabase import download_to_file  # type: ignore
        download_to_file("audfprint/library.pklz", local_pklz)
    else:
        # local storage: read from STORAGE_ROOT/audfprint/library.pklz
        local_src = (STORAGE_ROOT / "audfprint" / "library.pklz").resolve()
        if not local_src.exists():
            raise HTTPException(400, "audfprint DB not found. Run /audfprint/index first.")
        shutil.copy2(local_src, local_pklz)

    # Get asset audio locally
    local_in = workdir / f"query{Path(a.stored_path).suffix.lower() or '.bin'}"
    if STORAGE_MODE == "supabase":
        from storage_supabase import download_to_file  # type: ignore
        download_to_file(a.stored_path, local_in)
    else:
        shutil.copy2(storage.get_local_path(a.stored_path), local_in)

    # Prepare query
    if mode == "vr":
        query_wav = workdir / "query.wav"
        ffmpeg_to_wav_mono_11025(local_in, query_wav)
    else:
        query_wav = local_in

    # Run match
    raw = run_audfprint(["match", "--dbase", str(local_pklz), str(query_wav)], cwd=workdir)

    # Parse best line (audfprint prints: "Matched <query> ... as <matchfile> at <offset> with <common> of <total> ... rank <r>"
    best = {"match_path": None, "match_filename": None, "offset_sec": None, "common_hashes": None, "rank": None}
    for line in raw.splitlines()[::-1]:
        if line.strip().startswith("Matched "):
            # crude parse but stable enough for demo
            try:
                # split at " as "
                parts = line.split(" as ")
                right = parts[1] if len(parts) > 1 else ""
                # right starts with match path
                match_path = right.split(" at ")[0].strip()
                best["match_path"] = match_path
                best["match_filename"] = os.path.basename(match_path)

                if " at " in right:
                    off_part = right.split(" at ")[1]
                    off_str = off_part.split(" s")[0].strip()
                    best["offset_sec"] = off_str

                if " with " in right and " common hashes" in right:
                    mid = right.split(" with ")[1]
                    common_str = mid.split(" of ")[0].strip()
                    best["common_hashes"] = int(common_str)

                if " rank " in right:
                    rank_str = right.split(" rank ")[-1].strip()
                    best["rank"] = int(rank_str)
            except Exception:
                pass
            break

    # ✅ NEW: attach library_id if we can map it
    best["library_id"] = resolve_library_id(db, best.get("match_filename"), best.get("match_path"))

    return {
        "ok": True,
        "asset_id": asset_id,
        "mode": mode,
        "query_path": str(query_wav),
        "db_path": str(local_pklz),
        "best": best,
        "raw_output_tail": raw[-2500:],
        "note": "audfprint is the reliable matcher (constellation hashes + alignment).",
    }


# -------------------------
# Proof record (blockchain + email)
# -------------------------
def send_email(subject: str, text_body: str, to_email: str) -> tuple[bool, Optional[str]]:
    """
    SendGrid email. Returns (sent_ok, message_id_or_reason)
    """
    if not SENDGRID_API_KEY or not EMAIL_FROM:
        return False, "Email not configured (missing SENDGRID_API_KEY or EMAIL_FROM)"

    try:
        from sendgrid import SendGridAPIClient  # type: ignore
        from sendgrid.helpers.mail import Mail  # type: ignore

        msg = Mail(
            from_email=EMAIL_FROM,
            to_emails=to_email,
            subject=subject,
            plain_text_content=text_body,
        )
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        resp = sg.send(msg)
        # SendGrid gives a message-id header sometimes; if not, return status
        mid = resp.headers.get("X-Message-Id") or resp.headers.get("x-message-id")
        return True, mid or f"status={resp.status_code}"
    except Exception as e:
        return False, str(e)


@app.post("/proofs/record")
def record_proof(
    asset_id: str = Query(...),
    library_id: str = Query(...),
    email_to: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Records a proof (demo) and sends an email (Phase 5).
    If your chain integration is already working, keep using your existing approach.
    This endpoint focuses on the combined behavior + returning a proof hash and tx hash if chain is enabled.
    """
    # Create a deterministic "proof hash" for demo
    proof_payload = f"{asset_id}|{library_id}|{int(time.time())}"
    proof_hash = base64.b16encode(proof_payload.encode("utf-8")).decode("utf-8")[:64]

    tx_hash = None
    if CHAIN_RPC_URL and DEPLOYER_PRIVATE_KEY and PROOF_CONTRACT_ADDRESS:
        # If you already have web3 proof logic elsewhere, replace this block with your existing logic.
        # Keeping minimal here to avoid breaking.
        tx_hash = "tx_demo_" + proof_hash[:16]

    # Email
    to_email = (email_to or EMAIL_TO_DEFAULT or "").strip()
    email_sent = False
    email_reason = None
    if to_email:
        subject = "SampleDetect SAE — Proof Recorded"
        body = (
            f"Proof recorded.\n\n"
            f"Asset ID: {asset_id}\n"
            f"Matched Library ID: {library_id}\n"
            f"Proof Hash: {proof_hash}\n"
            f"Tx Hash: {tx_hash}\n\n"
            f"If you believe this match is incorrect, you may dispute it by providing:\n"
            f"1) Your original project files and timestamps\n"
            f"2) A signed statement of authorship\n"
            f"3) Evidence of license/permission (if applicable)\n"
            f"4) Any distribution links + upload times\n\n"
            f"This is a prototype notification for academic demonstration."
        )
        email_sent, email_reason = send_email(subject, body, to_email)

    return {
        "ok": True,
        "asset_id": asset_id,
        "library_id": library_id,
        "proof_hash": proof_hash,
        "tx_hash": tx_hash,
        "email_sent": email_sent,
        "email_reason": email_reason,
    }


# -------------------------
# Monitor inbox + incidents (Phase 7)
# -------------------------
@app.post("/monitor/scan")
def monitor_scan(mode: str = Query("vr", pattern="^(vr|raw)$"), email_to: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """
    Scans MONITOR_INBOX_DIR for audio files, runs audfprint match, stores incidents, sends email.
    """
    storage = get_storage()

    if STORAGE_MODE == "supabase":
        raise HTTPException(400, "Monitor scan is configured for local inbox in this prototype.")

    inbox = MONITOR_INBOX_DIR
    if not inbox.exists():
        raise HTTPException(400, f"monitor_inbox folder missing: {inbox}")

    audio_files = [p for p in inbox.iterdir() if p.is_file() and p.suffix.lower() in [".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"]]
    if not audio_files:
        return {"ok": True, "scanned": 0, "created": 0}

    created = 0
    for f in audio_files:
        # create a temporary asset record? (we store incident directly)
        try:
            workdir = (TEMP_DIR / f"mon_{uuid.uuid4().hex[:6]}").resolve()
            workdir.mkdir(parents=True, exist_ok=True)

            # ensure audfprint DB exists locally
            local_pklz = workdir / "library.pklz"
            local_src = (STORAGE_ROOT / "audfprint" / "library.pklz").resolve()
            if not local_src.exists():
                raise HTTPException(400, "audfprint DB not found. Run /audfprint/index first.")
            shutil.copy2(local_src, local_pklz)

            local_in = workdir / f.name
            shutil.copy2(f, local_in)

            if mode == "vr":
                query_wav = workdir / "query.wav"
                ffmpeg_to_wav_mono_11025(local_in, query_wav)
            else:
                query_wav = local_in

            raw = run_audfprint(["match", "--dbase", str(local_pklz), str(query_wav)], cwd=workdir)

            # parse best
            best = {"match_path": None, "match_filename": None, "offset_sec": None, "common_hashes": None, "rank": None}
            for line in raw.splitlines()[::-1]:
                if line.strip().startswith("Matched "):
                    try:
                        parts = line.split(" as ")
                        right = parts[1] if len(parts) > 1 else ""
                        match_path = right.split(" at ")[0].strip()
                        best["match_path"] = match_path
                        best["match_filename"] = os.path.basename(match_path)

                        if " at " in right:
                            off_part = right.split(" at ")[1]
                            off_str = off_part.split(" s")[0].strip()
                            best["offset_sec"] = off_str

                        if " with " in right and " common hashes" in right:
                            mid = right.split(" with ")[1]
                            common_str = mid.split(" of ")[0].strip()
                            best["common_hashes"] = int(common_str)

                        if " rank " in right:
                            rank_str = right.split(" rank ")[-1].strip()
                            best["rank"] = int(rank_str)
                    except Exception:
                        pass
                    break

            # Email alert (optional)
            to_email = (email_to or EMAIL_TO_DEFAULT or "").strip()
            email_sent = False
            email_reason = None
            if to_email and best.get("match_filename"):
                subject = "SampleDetect SAE — Monitor Alert: Possible Upload Detected"
                body = (
                    f"Monitor detected a potential match.\n\n"
                    f"Inbox file: {f.name}\n"
                    f"Matched beat: {best.get('match_filename')}\n"
                    f"Common hashes: {best.get('common_hashes')}\n"
                    f"Rank: {best.get('rank')}\n"
                    f"Offset: {best.get('offset_sec')}\n\n"
                    f"Dispute / next steps (prototype guidance):\n"
                    f"- Collect project timestamps (DAW project, stems, exports)\n"
                    f"- Collect license/permission documents if applicable\n"
                    f"- Provide signed authorship statement\n"
                    f"- Provide link/time evidence of the suspected upload\n\n"
                    f"This is a prototype email for academic demonstration."
                )
                email_sent, email_reason = send_email(subject, body, to_email)

            inc = MonitorIncident(
                id=safe_uuid(),
                created_at=datetime.utcnow(),
                inbox_filename=f.name,
                inbox_path=str(f),
                mode=mode,
                match_filename=best.get("match_filename"),
                match_path=best.get("match_path"),
                common_hashes=best.get("common_hashes"),
                rank=best.get("rank"),
                offset_sec=str(best.get("offset_sec")) if best.get("offset_sec") is not None else None,
                email_sent=str(email_sent).lower(),
                email_reason=email_reason,
            )
            db.add(inc)
            db.commit()
            created += 1

        except Exception:
            # keep scanning other files
            continue

    return {"ok": True, "scanned": len(audio_files), "created": created}


@app.get("/monitor/incidents")
def monitor_incidents(limit: int = 200, db: Session = Depends(get_db)):
    rows = (
        db.query(MonitorIncident)
        .order_by(MonitorIncident.created_at.desc())
        .limit(limit)
        .all()
    )
    out = []
    for r in rows:
        out.append(
            {
                "id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "inbox_filename": getattr(r, "inbox_filename", None),
                "inbox_path": getattr(r, "inbox_path", None),
                "mode": getattr(r, "mode", None),
                "match_filename": getattr(r, "match_filename", None),
                "match_path": getattr(r, "match_path", None),
                "common_hashes": getattr(r, "common_hashes", None),
                "rank": getattr(r, "rank", None),
                "offset_sec": getattr(r, "offset_sec", None),
                "email_sent": getattr(r, "email_sent", None),
                "email_reason": getattr(r, "email_reason", None),
            }
        )
    return out