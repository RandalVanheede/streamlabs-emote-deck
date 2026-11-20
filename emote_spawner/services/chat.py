"""Read-only Twitch chat client (anonymous IRC login, no account or token needed)."""

import asyncio
import random
import ssl
import time

import certifi

TWITCH_IRC = ("irc.chat.twitch.tv", 6697, True)
READ_TIMEOUT = 360  # Twitch PINGs about every 5 min; silence longer than this means a dead (half-open) connection


async def run_chat(channel: str, on_message, on_status, irc=TWITCH_IRC):
    """Join #channel and call on_message(text) for every chat message. Reconnects until cancelled."""
    host, port, tls = irc
    # certifi: python.org builds on macOS ship without system CA certificates
    context = ssl.create_default_context(cafile=certifi.where()) if tls else None
    delay = 2
    while True:
        on_status("connecting")
        writer, connected_at = None, None
        try:
            reader, writer = await asyncio.open_connection(host, port, ssl=context)
            writer.write(f"PASS SCHMOOPIIE\r\nNICK justinfan{random.randint(10000, 99999)}\r\nJOIN #{channel}\r\n".encode())
            await writer.drain()
            on_status("connected")
            connected_at = time.monotonic()
            marker = f" PRIVMSG #{channel} :"
            while line := await asyncio.wait_for(reader.readline(), READ_TIMEOUT):
                text = line.decode("utf-8", "replace").rstrip("\r\n")
                if text.startswith("PING"):
                    writer.write(("PONG" + text[4:] + "\r\n").encode())
                    await writer.drain()
                elif marker in text:
                    try:
                        on_message(text.split(marker, 1)[1])
                    except Exception:  # one bad message must not end the chat connection
                        pass
            on_status("disconnected, retrying")
        except asyncio.TimeoutError:
            on_status("no data from Twitch chat, reconnecting")
        except Exception as e:  # network errors, over-long lines, …; CancelledError still stops the loop
            on_status(f"can't reach Twitch chat ({e.__class__.__name__}), retrying")
        finally:
            if writer:
                writer.close()
        if connected_at and time.monotonic() - connected_at > 60:
            delay = 2  # only a connection that held up resets the backoff
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)
