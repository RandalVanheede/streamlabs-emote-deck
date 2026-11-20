"""Global hotkeys: Ctrl+Shift+1…9, 0 fire pads 1–10 while another app (the game) has focus.

Ctrl+Shift, not Ctrl+Alt: on AZERTY keyboards Ctrl+Alt is AltGr, which types @ # { } with the digit keys.
"""

import sys
import threading

LABEL = "Ctrl+Shift+1 … Ctrl+Shift+0"


def start_hotkeys(on_pad) -> str:
    """Register the hotkeys; on_pad(index) is called from a background/main thread. Returns a status line."""
    try:
        if sys.platform == "win32":
            return _windows(on_pad)
        if sys.platform == "darwin":
            return _macos(on_pad)
    except Exception as e:  # never let hotkeys stop the app from starting
        return f"Hotkeys unavailable: {e}"
    return "Global hotkeys aren't supported on this system; they only work while the app window has focus."


def _windows(on_pad) -> str:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT, WM_HOTKEY = 0x2, 0x4, 0x4000, 0x0312
    keys = [0x31 + i for i in range(9)] + [0x30]  # VK_1 … VK_9, VK_0 (top-row digits, any layout)
    result = {}
    ready = threading.Event()

    def loop():
        # Hotkeys belong to the thread that registers them, so register and pump messages in the same thread
        failed = [i for i, vk in enumerate(keys) if not user32.RegisterHotKey(None, i + 1, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, vk)]
        result["failed"] = failed
        ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                on_pad(msg.wParam - 1)

    threading.Thread(target=loop, daemon=True, name="hotkeys").start()
    ready.wait(2)
    failed = result.get("failed", [])
    if failed:
        taken = ", ".join(f"Ctrl+Shift+{(i + 1) % 10}" for i in failed)
        return f"{LABEL} active, except {taken}: already used by another app."
    return f"{LABEL} active."


_monitor = None  # keep the macOS monitor alive


def _macos(on_pad) -> str:
    import ctypes
    from AppKit import NSEvent
    from PyObjCTools import AppHelper

    # Physical key codes (kVK_ANSI_1 … kVK_ANSI_0), so AZERTY works too
    keycodes = {18: 0, 19: 1, 20: 2, 21: 3, 23: 4, 22: 5, 26: 6, 28: 7, 25: 8, 29: 9}
    ctrl, shift, option, command = 1 << 18, 1 << 17, 1 << 19, 1 << 20
    keydown_mask = 1 << 10

    def handler(event):
        if event.modifierFlags() & (ctrl | shift | option | command) == ctrl | shift and event.keyCode() in keycodes:
            on_pad(keycodes[event.keyCode()])

    def install():
        global _monitor
        _monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(keydown_mask, handler)

    AppHelper.callAfter(install)  # AppKit wants this on the main thread
    trusted = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices").AXIsProcessTrusted()
    if not trusted:
        return ("Hotkeys need permission: System Settings → Privacy & Security → Accessibility → turn on EmoteDeck "
                "(or your terminal when running from source), then restart the app.")
    return f"{LABEL} active."
