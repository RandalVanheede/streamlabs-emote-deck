"""Entry point: starts the HTTP server and opens the deck in a native app window."""

import html

import webview

from emote_spawner.config import get_config
from emote_spawner.server import HTTPServerManager


def main():
    cfg = get_config()
    server = HTTPServerManager(cfg)
    window = dict(title="Emote Deck", width=1180, height=800, min_size=(640, 520), background_color="#1E1B2E")
    if server.start():
        webview.create_window(url=f"{server.base_url}/", **window)
    else:
        webview.create_window(html=(
            "<body style='background:#1E1B2E;color:#ECE8F7;font:16px system-ui;padding:32px'>"
            f"<h1>Emote Deck could not start</h1><p>Port {html.escape(str(cfg.port))} is already in use. "
            "Close the other program using it (or another Emote Deck window) and start the app again.</p></body>"
        ), **window)
    webview.start()
    server.stop()


if __name__ == "__main__":
    main()
