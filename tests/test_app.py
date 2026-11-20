"""Self-check: run with `python -m tests.test_app` (no extra deps)."""

import io
import json
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

from emote_spawner.server import HTTPServerManager
from emote_spawner.services.library import ANIMATIONS


def png_bytes(color) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (8, 8), color).save(buf, "PNG")
    return buf.getvalue()


def fake_twitch_chat():
    """One-connection IRC server: after JOIN it sends chat lines and a PING, then waits for PONG."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    received = []

    def run():
        conn, _ = srv.accept()
        f = conn.makefile("rwb")
        for line in f:
            received.append(line.decode().strip())
            if line.startswith(b"JOIN"):
                f.write(b":v!v@v.tmi.twitch.tv PRIVMSG #testchan :hello\r\n"
                        b":v!v@v.tmi.twitch.tv PRIVMSG #testchan :!\r\n"
                        b":v!v@v.tmi.twitch.tv PRIVMSG #testchan :!KAPPA please\r\n"
                        b"PING :tmi.twitch.tv\r\n")
                f.flush()
            if line.startswith(b"PONG"):
                return

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1], received


def main():
    lib = Path(tempfile.mkdtemp())
    irc_port, irc_received = fake_twitch_chat()

    class Cfg:
        library_dir, host, port = lib, "127.0.0.1", 8799
        irc = ("127.0.0.1", irc_port, False)

    server = HTTPServerManager(Cfg)
    assert server.start(), "server did not start"
    base = server.base_url

    def call(method, path, body=None, headers=None, deck=True):
        h = {"X-Emote-Deck": "1"} if deck else {}
        h.update(headers or {})
        req = urllib.request.Request(base + path, data=body, method=method, headers=h)
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read() or b"null")

    def status(method, path, **kw):
        try:
            call(method, path, **kw)
            return 200
        except urllib.error.HTTPError as e:
            return e.code

    # Deck page and overlay are served
    assert b"Emote Deck" in urllib.request.urlopen(base + "/").read()
    overlay = urllib.request.urlopen(base + "/overlay").read().decode()
    missing = [a for a in ANIMATIONS[1:] if f".fx-{a} " not in overlay]
    assert not missing, f"animations without overlay CSS: {missing}"

    # API refuses requests without the app's header (other websites) or with a foreign Host (DNS rebinding)
    assert status("GET", "/api/emotes", deck=False) == 403
    assert status("GET", "/api/emotes", headers={"Host": "evil.example:8799"}) in (400, 403)
    assert status("GET", "/overlay", deck=False, headers={"Host": "evil.example:8799"}) == 400  # whole app checks Host

    # Add emote by upload, attach sound, set animation + volume
    assert call("POST", "/api/emotes?filename=kappa.png", png_bytes("red"))["filename"] == "kappa.png"
    assert status("POST", "/api/emotes?filename=notes.txt", body=b"hello") == 400
    assert call("PUT", "/api/emotes/kappa.png/sound?sound=boom.mp3", b"ID3fake")["sound"] == "kappa.png.mp3"
    patch = lambda body: call("PATCH", "/api/emotes/kappa.png", json.dumps(body).encode(), {"Content-Type": "application/json"})
    assert patch({"animation": "shake", "volume": 40}) == {"animation": "shake", "volume": 40, "command": "kappa"}
    assert patch({"volume": 55})["animation"] == "shake"  # partial update keeps the rest
    assert status("PATCH", "/api/emotes/kappa.png", body=b'{"volume": 101}', headers={"Content-Type": "application/json"}) == 400
    assert status("PATCH", "/api/emotes/kappa.png", body=b'{"animation": "explode"}', headers={"Content-Type": "application/json"}) == 400
    [e] = call("GET", "/api/emotes")
    assert (e["sound"], e["animation"], e["volume"]) == ("kappa.png.mp3", "shake", 55), e

    # Overlay settings: validated, and sent with every spawn
    patch_settings = lambda body: call("PATCH", "/api/settings", json.dumps(body).encode(), {"Content-Type": "application/json"})
    assert patch_settings({"size": 320, "position": "random", "duration": 4.5})["duration"] == 4.5
    for bad in ({"size": 10}, {"position": "left"}, {"duration": 0}, {"duration": True}, {"bogus": 1}):
        assert status("PATCH", "/api/settings", body=json.dumps(bad).encode(), headers={"Content-Type": "application/json"}) == 400, bad

    # Overlay listener receives spawns with sound, animation and volume
    events, hello, ready = [], [], threading.Event()

    def listen():
        with urllib.request.urlopen(base + "/events", timeout=5) as r:
            ready.set()
            named = False
            for line in r:
                if line.startswith(b"event:"):
                    named = True
                elif line.startswith(b"data:") and named:
                    hello.append(line[5:].strip().decode())
                    named = False
                elif line.startswith(b"data:"):
                    events.append(json.loads(line[5:]))
                    if len(events) == 2:
                        return

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    ready.wait(5)
    time.sleep(0.3)  # let the server register the queue
    assert call("GET", "/api/info")["overlays"] == 1
    token = call("GET", "/api/settings")["token"]
    assert status("GET", "/spawn/kappa.png", deck=False) == 403  # spawn links need the secret token
    assert status("GET", "/spawn/random?token=wrong", deck=False) == 403
    assert call("GET", f"/spawn/kappa.png?token={token}", deck=False)["overlays"] == 1
    assert call("POST", "/api/spawn/kappa.png")["filename"] == "kappa.png"  # the app's own buttons
    t.join(5)
    assert hello == [server._boot_id], hello  # overlay reload signal
    assert [(e["filename"], e["sound"], e["animation"], e["volume"]) for e in events] == [("kappa.png", "kappa.png.mp3", "shake", 55)] * 2, events
    assert all(isinstance(e["sound_version"], int) for e in events), events
    assert all((e["size"], e["position"], e["duration"]) == (320, "random", 4.5) for e in events), events

    # Only images and sounds are served from the library folder: never settings.json (holds the token)
    assert status("GET", "/static/settings.json", deck=False) == 404
    assert status("GET", "/static/deck.json", deck=False) == 404

    # Library files must be revalidated (a replaced sound keeps its filename)
    assert urllib.request.urlopen(base + "/static/kappa.png").headers["Cache-Control"] == "no-cache"

    # Cooldowns block bot links (and chat), never the app's own presses
    patch_settings({"cooldown_global": 0, "cooldown_emote": 60})
    assert status("GET", f"/spawn/kappa.png?token={token}", deck=False) == 429  # kappa fired just now
    assert status("GET", f"/spawn/random?token={token}", deck=False) == 429  # the only emote is cooling down
    assert call("POST", "/api/spawn/kappa.png")["filename"] == "kappa.png"
    patch_settings({"cooldown_global": 60, "cooldown_emote": 0})
    assert status("GET", f"/spawn/kappa.png?token={token}", deck=False) == 429
    assert status("GET", f"/spawn/KAPPA.PNG?token={token}", deck=False) in (404, 429)  # other spelling: no bypass
    patch_settings({"cooldown_global": 0, "cooldown_emote": 0})
    assert call("GET", f"/spawn/kappa.png?token={token}", deck=False)["filename"] == "kappa.png"

    # Twitch chat: "!kappa" (any case) fires kappa, bare "!" is ignored, PING answered
    assert status("PATCH", "/api/emotes/kappa.png", body=b'{"command": "bad cmd"}', headers={"Content-Type": "application/json"}) == 400
    assert patch({"command": "!Kappa"})["command"] == "kappa"
    assert status("PATCH", "/api/settings", body=b'{"channel": "no spaces!"}', headers={"Content-Type": "application/json"}) == 400
    before = server._last_spawn["kappa.png"]
    patch_settings({"channel": "testchan"})
    for _ in range(50):
        if any(r.startswith("PONG") for r in irc_received):
            break
        time.sleep(0.1)
    assert "JOIN #testchan" in irc_received and "PONG :tmi.twitch.tv" in irc_received, irc_received
    assert server._last_spawn["kappa.png"] > before, "chat command did not fire the emote"
    patch_settings({"channel": ""})
    assert call("GET", "/api/info")["chat"] == "off"

    for bad in ("/spawn/nope.png", "/spawn/..%2Fsecret.png", "/spawn/kappa.png.mp3"):
        assert status("GET", f"{bad}?token={token}", deck=False) == 404, bad

    # New token: old links stop working; token can't be set directly
    new = call("POST", "/api/settings/token")["token"]
    assert new != token and status("GET", f"/spawn/kappa.png?token={token}", deck=False) == 403
    assert status("PATCH", "/api/settings", body=b'{"token": "mine"}', headers={"Content-Type": "application/json"}) == 400

    # Deck order and rename (sound, settings and position move along)
    assert call("POST", "/api/emotes?filename=zebra.png", png_bytes("blue"))["filename"] == "zebra.png"
    assert [e["filename"] for e in call("GET", "/api/emotes")] == ["kappa.png", "zebra.png"]
    call("PUT", "/api/order", json.dumps(["zebra.png", "kappa.png"]).encode(), {"Content-Type": "application/json"})
    assert [e["filename"] for e in call("GET", "/api/emotes")] == ["zebra.png", "kappa.png"]
    rename = lambda old, name: call("POST", f"/api/emotes/{old}/rename", json.dumps({"name": name}).encode(), {"Content-Type": "application/json"})
    assert rename("kappa.png", "Kappa Pride")["filename"] == "Kappa Pride.png"
    emotes = call("GET", "/api/emotes")
    assert [e["filename"] for e in emotes] == ["zebra.png", "Kappa Pride.png"], emotes
    assert (emotes[1]["sound"], emotes[1]["animation"]) == ("Kappa Pride.png.mp3", "shake"), emotes[1]
    for bad in ("ZEBRA", "../evil", ""):
        assert status("POST", "/api/emotes/Kappa%20Pride.png/rename", body=json.dumps({"name": bad}).encode(),
                      headers={"Content-Type": "application/json"}) == 400, bad
    rename("Kappa%20Pride.png", "kappa")
    call("DELETE", "/api/emotes/zebra.png")

    # Remove sound, then emote (cleans settings)
    call("DELETE", "/api/emotes/kappa.png/sound")
    assert not (lib / "kappa.png.mp3").exists()
    call("DELETE", "/api/emotes/kappa.png")
    assert call("GET", "/api/emotes") == [] and "kappa.png" not in server.library._meta()

    server.stop()
    assert not server._thread.is_alive(), "server did not shut down with an overlay connected"
    print("OK")


if __name__ == "__main__":
    main()
