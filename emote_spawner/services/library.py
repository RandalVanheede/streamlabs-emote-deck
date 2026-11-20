"""Image library service — copy, list, delete images."""

import json
import os
import re
import secrets
from pathlib import Path
from PIL import Image as PILImage
import shutil

ALLOWED_EXTENSIONS = {".png", ".gif", ".jpg", ".jpeg", ".webp"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg"}
ANIMATIONS = (
    "none", "shake", "bounce", "spin", "spin-slow", "pulse", "heartbeat", "wobble", "jelly",
    "swing", "flash", "grow", "shrink", "zoom", "float",
)  # must match .fx-* classes in the overlay
MAX_FILE_SIZE_MB = 10  # max single file size


# App-wide settings (settings.json): default value and validator per key. "token" is managed by the library itself.
def _number(lo, hi):
    return lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi


SETTINGS = {
    "size": (256, _number(64, 1024), "Size must be 64 to 1024 pixels"),
    "position": ("center", lambda v: v in ("center", "random"), "Position must be 'center' or 'random'"),
    "duration": (3, _number(1, 15), "Display time must be 1 to 15 seconds"),
    "cooldown_global": (2, _number(0, 600), "Cooldown must be 0 to 600 seconds"),
    "cooldown_emote": (5, _number(0, 600), "Cooldown must be 0 to 600 seconds"),
    "channel": ("", lambda v: isinstance(v, str) and (v == "" or re.fullmatch(r"\w{3,25}", v) is not None),
                "Twitch channel names are 3 to 25 letters, digits or underscores"),
    "order": ([], lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v), "Order must be a list of file names"),
}
COMMAND_RE = re.compile(r"[a-z0-9_]{1,25}")


def default_command(filename: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", Path(filename).stem.lower())[:25]


class LibraryError(Exception):
    pass


def _write_json(path: Path, data):
    """Write via a temp file so a crash mid-write can't leave a truncated file (and e.g. a lost spawn token)."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


class Library:
    def __init__(self, library_dir: Path):
        library_dir.mkdir(parents=True, exist_ok=True)
        self.library_dir = library_dir.resolve()  # resolved, so get_path's containment check survives symlinks/junctions

    def _validate_image(self, src: Path) -> tuple[str, int]:
        """Validate an image file. Returns (name, size_kb)."""
        if src.suffix.lower() not in ALLOWED_EXTENSIONS:
            raise LibraryError(
                f"Unsupported format '{src.suffix}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            )
        size = src.stat().st_size
        if size > MAX_FILE_SIZE_MB * 1024 * 1024:
            raise LibraryError(f"File too large: {size // 1024}KB (max {MAX_FILE_SIZE_MB}MB)")
        # Quick open test
        try:
            with PILImage.open(src) as img:
                img.verify()
        except Exception:
            raise LibraryError(f"Invalid image file: {src.name}")
        # Use a sanitized name to avoid conflicts
        base = src.stem
        ext = src.suffix
        dest = self.library_dir / f"{base}{ext}"
        if dest.exists():
            counter = 1
            while (self.library_dir / f"{base}_{counter}{ext}").exists():
                counter += 1
            dest = self.library_dir / f"{base}_{counter}{ext}"
            base = f"{base}_{counter}"
        return f"{base}{ext}", size // 1024

    def add_image(self, src: Path) -> str:
        """Copy an image into the library. Returns the new filename."""
        if not src.is_file():
            raise LibraryError(f"Source not found: {src}")
        name, _ = self._validate_image(src)
        dest = self.library_dir / name
        shutil.copy2(src, dest)
        return name

    def list_images(self) -> list[dict]:
        """Image info dicts in deck order (settings "order"; emotes not in it go last, by name)."""
        order = {name: i for i, name in enumerate(self.app_settings()["order"])}
        images = []
        for p in sorted(self.library_dir.iterdir(), key=lambda p: (order.get(p.name, len(order)), p.name)):
            if p.suffix.lower() in ALLOWED_EXTENSIONS:
                images.append({
                    "filename": p.name,
                    "size_kb": p.stat().st_size // 1024,
                    "sound": self.sound_for(p.name),
                    "sound_version": self.sound_version(p.name),
                    **self.settings(p.name),
                })
        return images

    def delete_image(self, filename: str) -> bool:
        """Remove an image from the library."""
        target = self.get_path(filename)  # traversal-safe
        if target is None:
            return False
        self.remove_sound(target.name)
        meta = self._meta()
        if meta.pop(target.name, None) is not None:
            self._save_meta(meta)
        target.unlink()
        return True

    def rename_image(self, filename: str, new_stem: str) -> str:
        """Rename an emote (keeps its extension); its sound, settings and deck position move along."""
        target = self.get_path(filename)
        if target is None:
            raise LibraryError(f"Emote not found: {filename}")
        new_stem = new_stem.strip()
        if not re.fullmatch(r"[\w][\w\- ]{0,39}", new_stem):
            raise LibraryError("Names are 1 to 40 letters, digits, spaces, - or _")
        if new_stem.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            raise LibraryError(f"'{new_stem}' is a reserved name on Windows")
        new = new_stem + target.suffix
        if new == target.name:
            return new
        sound = self.sound_for(target.name)
        new_sound = new + sound[len(target.name):] if sound else None
        taken = {p.name.casefold() for p in self.library_dir.iterdir()} - {target.name.casefold(), (sound or "").casefold()}
        if new.casefold() in taken or (new_sound and new_sound.casefold() in taken):
            raise LibraryError(f"There already is an emote called {new_stem}")
        target.rename(self.library_dir / new)
        if sound:
            (self.library_dir / sound).rename(self.library_dir / new_sound)
        meta = self._meta()
        if target.name in meta:
            meta[new] = meta.pop(target.name)
            self._save_meta(meta)
        order = self.app_settings()["order"]
        if target.name in order:
            self.update_app_settings({"order": [new if n == target.name else n for n in order]})
        return new

    # Per-emote settings live in deck.json: {"kappa.png": {"animation": "shake", "volume": 80}}
    def _meta(self) -> dict:
        try:
            return json.loads((self.library_dir / "deck.json").read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def _save_meta(self, meta: dict):
        _write_json(self.library_dir / "deck.json", meta)

    def settings(self, filename: str) -> dict:
        defaults = {"animation": "none", "volume": 100, "command": default_command(filename)}
        return {**defaults, **self._meta().get(Path(filename).name, {})}

    def update_settings(self, filename: str, animation: str | None = None, volume: int | None = None,
                        command: str | None = None):
        target = self.get_path(filename)
        if target is None:
            raise LibraryError(f"Emote not found: {filename}")
        if animation is not None and animation not in ANIMATIONS:
            raise LibraryError(f"Unknown animation '{animation}'")
        if volume is not None and not (isinstance(volume, int) and 0 <= volume <= 100):
            raise LibraryError("Volume must be a whole number from 0 to 100")
        if command is not None:
            command = command.lower().removeprefix("!")
            if not COMMAND_RE.fullmatch(command):
                raise LibraryError("Chat commands are 1 to 25 letters, digits or underscores")
        meta = self._meta()
        entry = meta.setdefault(target.name, {})
        if animation is not None:
            entry["animation"] = animation
        if volume is not None:
            entry["volume"] = volume
        if command is not None:
            entry["command"] = command
        self._save_meta(meta)

    # App-wide settings live in settings.json
    def app_settings(self) -> dict:
        path = self.library_dir / "settings.json"
        try:
            stored = json.loads(path.read_text())
        except (FileNotFoundError, ValueError):
            stored = {}
        if not stored.get("token"):
            stored["token"] = secrets.token_urlsafe(16)
            _write_json(path, stored)
        return {**{k: default for k, (default, _, _) in SETTINGS.items()}, **stored}

    def update_app_settings(self, changes: dict) -> dict:
        current = self.app_settings()
        for key, value in changes.items():
            if key not in SETTINGS:
                raise LibraryError(f"Unknown setting '{key}'")
            _, valid, message = SETTINGS[key]
            if not valid(value):
                raise LibraryError(message)
            current[key] = value
        _write_json(self.library_dir / "settings.json", current)
        return current

    def regenerate_token(self) -> str:
        current = self.app_settings()
        current["token"] = secrets.token_urlsafe(16)
        _write_json(self.library_dir / "settings.json", current)
        return current["token"]

    # A sound is stored next to its emote as "<emote filename><audio ext>", e.g. kappa.png.mp3
    def sound_for(self, filename: str) -> str | None:
        for ext in AUDIO_EXTENSIONS:
            if (self.library_dir / f"{Path(filename).name}{ext}").is_file():
                return f"{Path(filename).name}{ext}"
        return None

    def sound_version(self, filename: str) -> int | None:
        """Changes whenever the sound is replaced, so overlays know to reload their decoded copy."""
        sound = self.sound_for(filename)
        return (self.library_dir / sound).stat().st_mtime_ns if sound else None

    def set_sound(self, filename: str, src: Path) -> str:
        """Attach (or replace) the sound effect for an emote. Returns the stored sound filename."""
        target = self.get_path(filename)
        if target is None:
            raise LibraryError(f"Emote not found: {filename}")
        ext = src.suffix.lower()
        if ext not in AUDIO_EXTENSIONS:
            raise LibraryError(f"Unsupported sound '{src.suffix}'. Allowed: {', '.join(sorted(AUDIO_EXTENSIONS))}")
        if src.stat().st_size > MAX_FILE_SIZE_MB * 1024 * 1024:
            raise LibraryError(f"Sound too large (max {MAX_FILE_SIZE_MB}MB)")
        self.remove_sound(target.name)
        shutil.copy(src, self.library_dir / f"{target.name}{ext}")  # not copy2: mtime must be "now" for sound_version
        return f"{target.name}{ext}"

    def remove_sound(self, filename: str):
        sound = self.sound_for(filename)
        if sound:
            (self.library_dir / sound).unlink()

    def get_path(self, filename: str) -> Path | None:
        """Resolve a filename to an absolute path (use with caution)."""
        # Prevent directory traversal
        safe = Path(filename).name
        resolved = (self.library_dir / safe).resolve()
        if not resolved.is_file() or not resolved.is_relative_to(self.library_dir):
            return None
        return resolved
