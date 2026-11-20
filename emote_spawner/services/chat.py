"""Read-only Twitch chat client (anonymous IRC login, no account or token needed)."""

import asyncio
import random
import ssl

import certifi

TWITCH_IRC = ("irc.chat.twitch.tv", 6697, True)


async def run_chat(channel: str, on_message, on_status, irc=TWITCH_IRC):
    """Join #channel and call on_message(text) for every chat message. Reconnects until cancelled."""
    host, port, tls = irc
    # certifi: python.org builds on macOS ship without system CA certificates
    context = ssl.create_default_context(cafile=certifi.where()) if tls else None
    delay = 2
    while True:
        on_status("connecting")
        writer = None
        try:
            reader, writer = await asyncio.open_connection(host, port, ssl=context)
            writer.write(f"PASS SCHMOOPIIE\r\nNICK justinfan{random.randint(10000, 99999)}\r\nJOIN #{channel}\r\n".encode())
            await writer.drain()
            on_status("connected")
            delay = 2
            marker = f" PRIVMSG #{channel} :"
            while line := await reader.readline():
                text = line.decode("utf-8", "replace").rstrip("\r\n")
                if text.startswith("PING"):
                    writer.write(("PONG" + text[4:] + "\r\n").encode())
                    await writer.drain()
                elif marker in text:
                    on_message(text.split(marker, 1)[1])
            on_status("disconnected, retrying")
        except (OSError, asyncio.IncompleteReadError) as e:
            on_status(f"can't reach Twitch chat ({e.__class__.__name__}), retrying")
        finally:
            if writer:
                writer.close()
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)
