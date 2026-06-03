from __future__ import annotations

import re
import zipfile
from pathlib import Path


DEFAULT_LAYOUT = Path("data/store_layout.json")
STORE_LAYOUT_DIR = Path("data/store_layouts")


def camera_key_for_name(name: str) -> str:
    stem = Path(name).stem.upper()
    return re.sub(r"[^A-Z0-9]+", "_", stem).strip("_")


def layout_path_for_zip(zip_path: str | Path, default_layout: str | Path = DEFAULT_LAYOUT) -> Path:
    path = Path(zip_path)
    try:
        with zipfile.ZipFile(path) as archive:
            names = [member.filename.lower().replace("\\", "/") for member in archive.infolist()]
    except zipfile.BadZipFile:
        return Path(default_layout)

    if any(name.startswith("store 1/") or "store 1" in name for name in names):
        return STORE_LAYOUT_DIR / "store_1.json"
    if any(name.startswith("store 2/") or "store 2" in name for name in names):
        return STORE_LAYOUT_DIR / "store_2.json"
    return Path(default_layout)
