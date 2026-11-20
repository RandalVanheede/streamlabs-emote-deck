# StreamLabs Emote Spawner

One Windows desktop app: emote image library + built-in HTTP server + OBS overlay.

## Setup
```bash
pip install -r requirements.txt
python -m emote_spawner
```

## Usage
1. Click **Add emotes** (or drop image files on the window). Images are copied into the library
   (`./emotes` from source; see below for the packaged app).
2. In Streamlabs add a **Browser Source** with the URL shown at the top of the app: `http://127.0.0.1:8765/overlay`.
   The light next to the title turns amber once the overlay is connected.
3. Click a pad to fire it. Its settings open on the right: sound (mp3/wav/ogg), volume, and animation
   (shake, bounce, spin, pulse, heartbeat, wobble, jelly, swing, flash, grow, shrink, zoom, float, …).
4. Chat bots / Streamer.bot / a hardware deck can fire too, with the links from **Settings → Spawn links for bots**:
   `GET http://127.0.0.1:8765/spawn/random?token=…` or `/spawn/{filename}?token=…`. The secret token stops other
   websites from firing emotes on your stream; **Make new links** replaces it.

Each spawn appears in the centre of the screen for 3 s with a quick (0.15 s) fade in and out. Emotes without an
animation also pop in with a bounce. Its sound plays at the pad's volume for at most 3 s, then fades out over 0.6 s;
shorter sounds play once (no loop). A new spawn replaces the one on screen, image and sound.

The app window is a web page (`emote_spawner/ui/deck.html`) shown with pywebview: Edge WebView2 on Windows
(built into Windows 10/11), WebKit on macOS. Its `/api/*` calls are refused unless they come from the app itself.

Other endpoints: `/images` (list), `/health`, `/static/{filename}`.

## Self-check
```bash
python -m tests.test_app
```

## Package the app
Build on the OS you target (PyInstaller does not cross-compile). `pip install pyinstaller` first.

Windows (single `EmoteDeck.exe`):
```bash
pyinstaller --onefile --windowed --name EmoteDeck --icon assets/icon.ico --paths . --add-data "emote_spawner/ui/deck.html:emote_spawner/ui" emote_spawner/__main__.py
```

macOS (`dist/EmoteDeck.app`; no `--onefile`, it does not suit .app bundles):
```bash
pyinstaller --windowed --name EmoteDeck --icon assets/icon.icns --paths . --add-data "emote_spawner/ui/deck.html:emote_spawner/ui" emote_spawner/__main__.py
```

Redraw the icon (`assets/icon.png`, `.icns`, `.ico`) with `python scripts/make_icon.py`.
macOS caches app icons per path; if a rebuilt app still shows the old icon, run `killall Dock Finder`.

The packaged app keeps its library in the OS app-data folder: `%APPDATA%\StreamLabsEmoteSpawner` (Windows),
`~/Library/Application Support/StreamLabsEmoteSpawner` (macOS).
If a packaged app opens no window, run it from a terminal to see the error, e.g.
`dist/EmoteDeck.app/Contents/MacOS/EmoteDeck`.
