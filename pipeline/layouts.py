from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path


DEFAULT_LAYOUT = Path("data/store_layout.json")
STORE_LAYOUT_DIR = Path("data/store_layouts")
KNOWN_LAYOUTS = [STORE_LAYOUT_DIR / "store_1.json", STORE_LAYOUT_DIR / "store_2.json", DEFAULT_LAYOUT]


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
    for name in names:
        if name.endswith(".mp4"):
            layout_path = layout_path_for_camera_name(name, default_layout)
            if layout_path != Path(default_layout):
                return layout_path
    return Path(default_layout)


def layout_path_for_camera_name(name: str, default_layout: str | Path = DEFAULT_LAYOUT) -> Path:
    camera_key = camera_key_for_name(name)
    for layout_path in KNOWN_LAYOUTS:
        if not layout_path.exists():
            continue
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        if camera_key in layout.get("cameras", {}):
            return layout_path
    return Path(default_layout)
