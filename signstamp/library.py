"""등록된 서명 PNG 보관함 (%APPDATA%\\SignStamp\\signatures)."""
from __future__ import annotations

import os
import re
from pathlib import Path

from PIL import Image

DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "SignStamp"
SIG_DIR = DATA_DIR / "signatures"


def _ensure() -> Path:
    SIG_DIR.mkdir(parents=True, exist_ok=True)
    return SIG_DIR


def list_signatures() -> list[Path]:
    return sorted(_ensure().glob("*.png"), key=lambda p: p.stat().st_mtime)


def save_signature(name: str, img: Image.Image) -> Path:
    safe = re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "서명"
    path = _ensure() / f"{safe}.png"
    i = 2
    while path.exists():
        path = SIG_DIR / f"{safe} ({i}).png"
        i += 1
    img.save(path)
    return path


def delete_signature(path: Path) -> None:
    Path(path).unlink(missing_ok=True)
