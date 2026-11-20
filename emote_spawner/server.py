"""FastAPI HTTP server running in a background thread, plus the OBS overlay page."""

import asyncio
import json
import random
import secrets
import tempfile
import threading
import time
from pathlib import Path

from contextlib import asynccontextmanager

import uvicorn
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from emote_spawner.services.chat import TWITCH_IRC, run_chat
from emote_spawner.services.library import (
    ALLOWED_EXTENSIONS, ANIMATIONS, AUDIO_EXTENSIONS, MAX_FILE_SIZE_MB, Library, LibraryError,
)

DECK_HTML = Path(__file__).parent / "ui" / "deck.html"


class LibraryFiles(StaticFiles):
    """Serves only emote images and sounds (never settings.json, which holds the spawn token).
    Files change in place (a replaced sound keeps its name), so browsers must revalidate every time."""

    async def get_response(self, path, scope):
        if Path(path).suffix.lower() not in ALLOWED_EXTENSIONS | AUDIO_EXTENSIONS:
            raise HTTPException(status_code=404)
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response

# Browser Source page: each spawn pops into the centre and plays its sound for HOLD ms,
# then image and sound fade out over FADE ms. Short sounds play once (no loop).
OVERLAY_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>
html, body { margin: 0; height: 100%; overflow: hidden; background: transparent; }
#unmute { position: fixed; top: 12px; left: 50%; translate: -50% 0; display: none; cursor: pointer;
          font: 600 16px system-ui; color: #fff; background: #e5484d; padding: 8px 16px; border-radius: 8px; }
/* Every emote gets a quick fade in/out (opacity, done in JS so it follows the display time). With no effect it
   also pops in (scale); an effect replaces the pop and animates transform. --dur is the display time. */
img { position: absolute; left: 50%; top: 50%; width: 256px; translate: -50% -50%; --dur: 3s;
      animation: pop .9s cubic-bezier(.2, .9, .3, 1.3); }
.fx-shake  { animation: shake .09s linear infinite; }
.fx-bounce { animation: bounce .5s ease-in-out infinite; }
.fx-spin   { animation: spin 1s linear infinite; }
.fx-pulse  { animation: pulse .6s ease-in-out infinite; }
.fx-wobble { animation: wobble .8s ease-in-out infinite; }
.fx-grow      { animation: grow var(--dur) ease-in forwards; }
.fx-shrink    { animation: shrink var(--dur) ease-in forwards; }
.fx-float     { animation: float var(--dur) ease-in-out forwards; }
.fx-swing     { animation: swing 1s ease-in-out infinite; transform-origin: top center; }
.fx-flash     { animation: flash .4s steps(1) infinite; }
.fx-jelly     { animation: jelly .9s ease-in-out infinite; }
.fx-heartbeat { animation: heartbeat 1s ease-in-out infinite; }
.fx-spin-slow { animation: spin 4s linear infinite; }
.fx-zoom      { animation: zoom var(--dur) cubic-bezier(.5, 0, 1, .5) forwards; }
@keyframes shake  { 0%,100% { transform: translate(0,0) rotate(0); } 25% { transform: translate(-12px,6px) rotate(-6deg); }
                    50% { transform: translate(10px,-8px) rotate(5deg); } 75% { transform: translate(-8px,-6px) rotate(-4deg); } }
@keyframes bounce { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-60px); } }
@keyframes spin   { to { transform: rotate(360deg); } }
@keyframes pulse  { 0%,100% { transform: scale(1); } 50% { transform: scale(1.18); } }
@keyframes grow      { to { transform: scale(2.2); } }
@keyframes shrink    { to { transform: scale(.3); } }
@keyframes float     { to { transform: translateY(-35vh); } }
@keyframes swing     { 0%,100% { transform: rotate(18deg); } 50% { transform: rotate(-18deg); } }
@keyframes flash     { 50% { filter: brightness(2.5) saturate(0); } }
@keyframes jelly     { 0%,100% { transform: scale(1,1); } 30% { transform: scale(1.25,.75); }
                       50% { transform: scale(.8,1.2); } 70% { transform: scale(1.1,.9); } }
@keyframes heartbeat { 0%,40%,100% { transform: scale(1); } 15% { transform: scale(1.25); } 30% { transform: scale(1.1); } }
@keyframes zoom      { to { transform: scale(6); } }
@keyframes wobble { 0%,100% { transform: rotate(0) skewX(0); } 25% { transform: rotate(-12deg) skewX(8deg); }
                    75% { transform: rotate(12deg) skewX(-8deg); } }
@keyframes pop { 0% { scale: 0; } 35% { scale: 1.25; } 60% { scale: .9; } 80%, 100% { scale: 1; } }
</style></head><body>
<div id="unmute">🔇 Sound blocked by the browser. Click here to enable</div><script>
const unmute = document.getElementById("unmute");
const SOUND_FADE = 0.6, IMAGE_FADE = 150;  // seconds, ms
let current = null;  // the emote on screen: { img, source }; a new spawn replaces it fully

// Sounds are decoded once and played from memory (Web Audio): no fetch/decode delay per spawn.
const ctx = new AudioContext();
const buffers = new Map();  // url (includes sound_version) -> Promise<AudioBuffer>
const soundUrl = (ev) => `/static/${encodeURIComponent(ev.sound)}?v=${ev.sound_version}`;
function loadSound(url) {
  if (!buffers.has(url)) {
    buffers.set(url, fetch(url).then((r) => r.arrayBuffer()).then((b) => ctx.decodeAudioData(b))
      .catch((err) => { buffers.delete(url); throw err; }));
  }
  return buffers.get(url);
}
fetch("/images").then((r) => r.json()).then((list) => list.forEach((e) => e.sound && loadSound(soundUrl(e))));
unmute.onclick = () => { ctx.resume(); unmute.style.display = "none"; };  // the click unlocks audio

function stopCurrent() {
  if (!current) return;
  try { current.source?.stop(); } catch {}
  current.img.remove();
  current = null;
}

async function playSound(url, volume, hold, me) {
  if (ctx.state !== "running") await ctx.resume().catch(() => {});
  if (ctx.state !== "running") unmute.style.display = "block";  // autoplay blocked (normal tab, not OBS)
  let buffer;
  try { buffer = await loadSound(url); } catch (err) { return console.warn("sound not loaded:", url, err); }
  if (current !== me) return;  // replaced while the sound was still loading
  const source = ctx.createBufferSource();
  const gain = ctx.createGain();
  source.buffer = buffer;
  source.connect(gain).connect(ctx.destination);
  const t = ctx.currentTime;
  gain.gain.setValueAtTime(volume, t);
  gain.gain.setValueAtTime(volume, t + hold);
  gain.gain.linearRampToValueAtTime(0, t + hold + SOUND_FADE);
  source.start(t);
  source.stop(t + hold + SOUND_FADE);  // short sounds just end; no loop
  me.source = source;
}

const events = new EventSource("/events");
let bootId = null;
events.addEventListener("hello", (e) => {
  // EventSource reconnects by itself after an app restart; reload so Streamlabs picks up overlay updates
  if (bootId && e.data !== bootId) location.reload();
  bootId = e.data;
});
events.onmessage = (e) => {
  const ev = JSON.parse(e.data);
  stopCurrent();
  const img = document.createElement("img");
  img.src = "/static/" + encodeURIComponent(ev.filename);
  img.className = "fx-" + ev.animation;
  img.style.width = ev.size + "px";
  img.style.setProperty("--dur", ev.duration + "s");
  if (ev.position === "random") {  // keep the whole emote on screen
    img.style.left = ev.size / 2 + Math.random() * Math.max(0, innerWidth - ev.size) + "px";
    img.style.top = ev.size / 2 + Math.random() * Math.max(0, innerHeight - ev.size) + "px";
  }
  const me = { img, source: null };
  document.body.appendChild(img);
  current = me;
  const total = ev.duration * 1000 + IMAGE_FADE;
  img.animate([{ opacity: 0 }, { opacity: 1, offset: IMAGE_FADE / total }, { opacity: 1, offset: 1 - IMAGE_FADE / total }, { opacity: 0 }],
              { duration: total, fill: "forwards" }).onfinish = () => { if (current === me) stopCurrent(); };
  if (ev.sound) playSound(soundUrl(ev), ev.volume / 100, ev.duration, me);
};
</script></body></html>"""


class HTTPServerManager:
    """Owns the library, the uvicorn server thread and the overlay listeners."""

    def __init__(self, config):
        self.config = config
        self.library = Library(config.library_dir)
        self.port = config.port
        self._clients: set[asyncio.Queue] = set()
        self._chat_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.hotkey_status = "Hotkeys only work while the app window has focus."
        self.chat_status = "off"
        self._last_spawn = {"*": 0.0}  # monotonic time of the last spawn, overall ("*") and per emote
        self._boot_id = str(time.time_ns())  # overlays reload when this changes, i.e. the app restarted
        self._server = uvicorn.Server(uvicorn.Config(
            self._create_app(),
            host=config.host,
            port=config.port,
            log_config=None,  # default log config crashes in a --windowed exe (sys.stdout is None)
            timeout_graceful_shutdown=1,  # open overlay streams would otherwise block shutdown
        ))
        self._thread: threading.Thread | None = None

    def _create_app(self) -> FastAPI:
        @asynccontextmanager
        async def lifespan(app):
            self._loop = asyncio.get_running_loop()
            self._restart_chat()
            yield
            if self._chat_task:
                self._chat_task.cancel()

        app = FastAPI(title="StreamLabs Emote Spawner", lifespan=lifespan)
        # DNS rebinding: a website re-pointing its own domain at 127.0.0.1 would otherwise read our pages as same-origin
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

        @app.get("/health")
        async def health():
            return {"status": "ok", "images": len(self.library.list_images())}

        @app.get("/images")
        async def images():
            return self.library.list_images()

        def spawn_token(token: str = ""):
            # Spawn links are plain GETs for chat bots, so any website could hit them; a secret token stops that.
            if not secrets.compare_digest(token.encode(), self.library.app_settings()["token"].encode()):
                raise HTTPException(status_code=403, detail="Missing or wrong token. Copy the spawn link from the app's settings.")

        @app.get("/spawn/random", dependencies=[Depends(spawn_token)])
        async def spawn_random():
            ready = [e["filename"] for e in self.library.list_images() if self._ready(e["filename"])]
            if not ready:
                raise HTTPException(status_code=429, detail="All emotes are on cooldown (or the library is empty)")
            return self._broadcast(random.choice(ready))

        @app.get("/spawn/{filename}", dependencies=[Depends(spawn_token)])
        async def spawn(filename: str):
            name = self._emote_path(filename).name
            if not self._ready(name):
                raise HTTPException(status_code=429, detail="On cooldown")
            return self._broadcast(name)

        @app.get("/overlay", response_class=HTMLResponse)
        async def overlay():
            return OVERLAY_HTML

        @app.get("/events")
        async def events(request: Request):
            queue: asyncio.Queue = asyncio.Queue()
            self._clients.add(queue)

            async def stream():
                yield f"event: hello\ndata: {self._boot_id}\n\n"
                try:
                    while not await request.is_disconnected():
                        try:
                            yield f"data: {json.dumps(await asyncio.wait_for(queue.get(), 15))}\n\n"
                        except asyncio.TimeoutError:
                            yield ": ping\n\n"  # keep-alive; also lets us notice disconnects
                finally:
                    self._clients.discard(queue)

            return StreamingResponse(stream(), media_type="text/event-stream")

        @app.get("/", include_in_schema=False)
        async def deck():
            return FileResponse(DECK_HTML)

        @app.exception_handler(LibraryError)
        async def library_error(request, exc):
            return JSONResponse({"detail": str(exc)}, status_code=400)

        app.include_router(self._api_router())
        app.mount("/static", LibraryFiles(directory=self.library.library_dir), name="static")
        return app

    def _api_router(self) -> APIRouter:
        """Deck management API, used by the app window only."""

        def guard(request: Request):
            # Any website open in the user's browser can send requests to 127.0.0.1. Requiring a custom header
            # forces a CORS preflight we never answer, and the Host check stops DNS-rebinding pages.
            hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
            if request.headers.get("x-emote-deck") != "1" or request.headers.get("host") not in hosts:
                raise HTTPException(status_code=403, detail="Forbidden")

        async def upload(request: Request, filename: str, tmp: str) -> Path:
            body = await request.body()
            if len(body) > MAX_FILE_SIZE_MB * 1024 * 1024:
                raise HTTPException(status_code=413, detail=f"File too large (max {MAX_FILE_SIZE_MB}MB)")
            path = Path(tmp) / Path(filename).name  # keep the original name; Library validates it
            path.write_bytes(body)
            return path

        emote = self._emote_path
        api = APIRouter(prefix="/api", dependencies=[Depends(guard)])

        @api.get("/info")
        async def info():
            return {"overlay_url": f"{self.base_url}/overlay", "overlays": len(self._clients), "animations": ANIMATIONS,
                    "chat": self.chat_status, "hotkeys": self.hotkey_status}

        @api.post("/spawn/{filename}")
        async def spawn(filename: str):
            return self._broadcast(emote(filename).name)

        @api.get("/settings")
        async def get_settings():
            return self.library.app_settings()

        @api.patch("/settings")
        async def update_settings(changes: dict = Body(...)):
            result = self.library.update_app_settings(changes)
            if "channel" in changes:
                self._restart_chat()
            return result

        @api.post("/settings/token")
        async def new_token():
            return {"token": self.library.regenerate_token()}

        @api.get("/emotes")
        async def list_emotes():
            return self.library.list_images()

        @api.post("/emotes")
        async def add_emote(request: Request, filename: str):
            with tempfile.TemporaryDirectory() as tmp:
                return {"filename": self.library.add_image(await upload(request, filename, tmp))}

        @api.patch("/emotes/{filename}")
        async def update_emote(filename: str, animation: str | None = Body(None), volume: int | None = Body(None),
                               command: str | None = Body(None)):
            self.library.update_settings(emote(filename).name, animation=animation, volume=volume, command=command)
            return self.library.settings(filename)

        @api.post("/emotes/{filename}/rename")
        async def rename_emote(filename: str, name: str = Body(..., embed=True)):
            return {"filename": self.library.rename_image(emote(filename).name, name)}

        @api.put("/order")
        async def set_order(order: list[str] = Body(...)):
            self.library.update_app_settings({"order": order})
            return {"ok": True}

        @api.delete("/emotes/{filename}")
        async def delete_emote(filename: str):
            self.library.delete_image(emote(filename).name)
            return {"ok": True}

        @api.put("/emotes/{filename}/sound")
        async def set_sound(request: Request, filename: str, sound: str):
            with tempfile.TemporaryDirectory() as tmp:
                return {"sound": self.library.set_sound(emote(filename).name, await upload(request, sound, tmp))}

        @api.delete("/emotes/{filename}/sound")
        async def remove_sound(filename: str):
            self.library.remove_sound(emote(filename).name)
            return {"ok": True}

        return api

    def _emote_path(self, filename: str) -> Path:
        # Use the stored spelling: on case-insensitive disks (macOS, Windows) "KAPPA.PNG" would otherwise slip past
        # the per-emote cooldown and miss the emote's settings
        wanted = Path(filename).name.casefold()
        real = next((e["filename"] for e in self.library.list_images() if e["filename"].casefold() == wanted), None)
        if real is None:  # unknown, or not an emote (e.g. its sound)
            raise HTTPException(status_code=404, detail=f"Emote not found: {filename}")
        return self.library.library_dir / real

    def fire_pad(self, index: int):
        """Fire the pad at this deck position. Thread-safe (called by the hotkey listener)."""
        def fire():
            emotes = self.library.list_images()
            if 0 <= index < len(emotes):
                self._broadcast(emotes[index]["filename"])
        if self._loop:
            try:
                self._loop.call_soon_threadsafe(fire)
            except RuntimeError:  # loop closed while the app shuts down
                pass

    def _restart_chat(self):
        """(Re)join the Twitch channel from the settings. Runs on the server loop."""
        if self._chat_task:
            self._chat_task.cancel()
            self._chat_task = None
        channel = self.library.app_settings()["channel"].lower()
        self.chat_status = "off"
        if channel:
            def set_status(status):
                self.chat_status = status
            irc = getattr(self.config, "irc", TWITCH_IRC)
            self._chat_task = asyncio.create_task(run_chat(channel, self._on_chat, set_status, irc))

    def _on_chat(self, text: str):
        """Fire the emote whose command a viewer typed, e.g. "!angry" or "!random", respecting cooldowns."""
        words = text[1:].split() if text.startswith("!") else []
        if not words:
            return
        command = words[0].lower()
        emotes = self.library.list_images()
        if command == "random":
            ready = [e["filename"] for e in emotes if self._ready(e["filename"])]
            if ready:
                self._broadcast(random.choice(ready))
            return
        match = next((e["filename"] for e in emotes if e["command"] == command), None)
        if match and self._ready(match):
            self._broadcast(match)

    def _ready(self, filename: str) -> bool:
        """Cooldown check for chat and bot triggers (the streamer's own presses are never blocked)."""
        cfg, now = self.library.app_settings(), time.monotonic()
        return (now - self._last_spawn["*"] >= cfg["cooldown_global"]
                and now - self._last_spawn.get(filename, 0.0) >= cfg["cooldown_emote"])

    def _broadcast(self, filename: str) -> dict:
        """Push a spawn to every open overlay. Must run on the server loop."""
        self._last_spawn["*"] = self._last_spawn[filename] = time.monotonic()
        event = {"filename": filename, "url": f"{self.base_url}/static/{filename}",
                 "sound": self.library.sound_for(filename), "sound_version": self.library.sound_version(filename),
                 **self.library.settings(filename),
                 **{k: v for k, v in self.library.app_settings().items() if k in ("size", "position", "duration")}}
        for queue in self._clients:
            queue.put_nowait(event)
        return {**event, "overlays": len(self._clients)}

    @property
    def base_url(self) -> str:
        return f"http://{self.config.host}:{self.port}"

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._server.started)

    def start(self) -> bool:
        """Start in a background thread; returns False if it failed (e.g. port in use)."""
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        for _ in range(50):  # 5 s
            if self._server.started or not self._thread.is_alive():
                break
            time.sleep(0.1)
        return self.running

    def stop(self):
        self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)
