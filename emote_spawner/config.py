"""Application configuration."""

import os
import sys
from pathlib import Path


def get_config():
    app_dir = Path(__file__).resolve().parent.parent
    # Packaged app: the per-user app-data folder of the OS
    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            base = Path(os.environ["APPDATA"])
        elif sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share")
        data_dir = base / "StreamLabsEmoteSpawner"
    else:
        data_dir = app_dir / "emotes"
    data_dir.mkdir(parents=True, exist_ok=True)

    class Config:
        library_dir = data_dir
        host = "127.0.0.1"
        port = 8765  # fixed so the OBS Browser Source URL stays valid
        spawn_prefix = "spawn_"
        max_library_size_mb = 500

    return Config
