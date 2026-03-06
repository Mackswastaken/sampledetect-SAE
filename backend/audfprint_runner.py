import re
import subprocess
from pathlib import Path

MATCH_RE = re.compile(
    r"Matched\s+(?P<query>.+?)\s+.*?\s+as\s+(?P<match>.+?)\s+at\s+(?P<offset>-?\d+(\.\d+)?)\s*s\s+with\s+(?P<common>\d+)\s+of\s+(?P<total>\d+)\s+common hashes at rank\s+(?P<rank>\d+)",
    re.IGNORECASE,
)

def run_cmd(cmd: list[str]) -> str:
    res = subprocess.run(cmd, capture_output=True, text=True)
    out = (res.stdout or "") + "\n" + (res.stderr or "")
    if res.returncode != 0:
        raise RuntimeError(out.strip() or "Command failed")
    return out.strip()

def build_index(python_exe: Path, audfprint_py: Path, db_path: Path, list_file: Path) -> str:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(python_exe),
        str(audfprint_py),
        "new",
        "--dbase", str(db_path),
        "-l", str(list_file),
    ]
    return run_cmd(cmd)

def match_file(python_exe: Path, audfprint_py: Path, db_path: Path, query_file: Path) -> dict:
    cmd = [
        str(python_exe),
        str(audfprint_py),
        "match",
        "--dbase", str(db_path),
        str(query_file),
    ]
    raw = run_cmd(cmd)

    best = None
    for line in raw.splitlines():
        m = MATCH_RE.search(line)
        if m:
            best = {
                "match_path": m.group("match").strip(),
                "offset_sec": float(m.group("offset")),
                "common_hashes": int(m.group("common")),
                "total_common_candidates": int(m.group("total")),
                "rank": int(m.group("rank")),
            }
            best["match_filename"] = Path(best["match_path"]).name
            break

    return {"best": best, "raw": raw}