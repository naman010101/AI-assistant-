"""Quick Ask: a global hotkey that opens a small ask box over any app, already
knowing what's on screen and what text you selected.

Windows only (the rest of the app still works elsewhere). Uses the Windows
API through ctypes, so the hotkey and capture need no extra packages. Reading
text from the screen uses Windows' built-in OCR through the optional `winrt`
packages; without them, screen questions go to the vision model instead.

    hk = HotkeyListener("ctrl+shift+space", on_press)   # background thread
    hk.start(); ...; hk.stop()
    info = foreground()          # {"hwnd", "title", "app", "rect"}
    img = capture(info)          # PIL image of that window
    text = ocr(img)              # str or None (+ reason in ocr.last_error)
"""
import ctypes
import logging
import os
import re
import sys
import threading
import time

log = logging.getLogger("hybridai")

IS_WINDOWS = sys.platform == "win32"

DEFAULT_HOTKEY = "ctrl+shift+space"
# Apps whose windows are never captured (process names, lower case).
DEFAULT_NEVER = ["keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe", "lastpass.exe",
                 "dashlane.exe", "enpass.exe", "proton pass.exe"]
SENSITIVE_TITLE = re.compile(r"(password|passwort|private browsing|inprivate|incognito|bank|net ?banking|upi|"
                             r"credit card|one-time password|\botp\b)", re.I)

ACTIONS = {
    "Explain": "Explain this clearly and simply.",
    "Summarise": "Summarise this in a few short bullet points.",
    "Translate": "Translate this into English (or into Hindi if it is already English). Give only the translation.",
    "Rewrite": "Rewrite this to read more clearly and professionally. Give only the rewritten text.",
    "Fix grammar": "Fix the grammar and spelling. Keep the meaning and tone. Give only the corrected text.",
}

MOD = {"alt": 0x0001, "ctrl": 0x0002, "control": 0x0002, "shift": 0x0004, "win": 0x0008}
MOD_NOREPEAT = 0x4000
VK = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "backspace": 0x08,
      **{f"f{i}": 0x6F + i for i in range(1, 13)}}


def parse_hotkey(text):
    """'ctrl+shift+space' -> (modifiers, virtual key). Raises ValueError."""
    parts = [p.strip().lower() for p in (text or "").replace("-", "+").split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    mods, key = 0, None
    for p in parts:
        if p in MOD:
            mods |= MOD[p]
        elif p in VK:
            key = VK[p]
        elif len(p) == 1 and p.isalnum():
            key = ord(p.upper())
        else:
            raise ValueError(f"unknown key '{p}'")
    if key is None:
        raise ValueError("no main key (e.g. space or a letter)")
    if not mods:
        raise ValueError("add at least one of ctrl, alt, shift or win")
    return mods, key


def never_capture(info, never_apps):
    """Reason not to capture this window, or ''."""
    app = (info.get("app") or "").lower()
    if app and app in {a.lower().strip() for a in never_apps}:
        return f"{info.get('app')} is on the never-capture list"
    if SENSITIVE_TITLE.search(info.get("title") or ""):
        return "the window title looks private (password, banking or private browsing)"
    return ""


def build_prompt(question, selection=None, screen_text=None, app=None, title=None, action=None):
    """The text sent to the model."""
    parts = []
    where = f"{app or 'an app'}" + (f" — “{title}”" if title else "")
    if selection:
        parts.append(f"The user selected this text in {where}:\n<selection>\n{selection.strip()[:12000]}\n</selection>")
    if screen_text:
        parts.append(f"Text read from the user's screen ({where}), in reading order, may contain OCR "
                     f"mistakes:\n<screen>\n{screen_text.strip()[:12000]}\n</screen>")
    if not selection and not screen_text and app:
        parts.append(f"The user is working in {where}.")
    ask = (question or "").strip()
    if action:
        target = "the selected text" if selection else "what's on the screen"
        ask = f"{ACTIONS[action]} (Apply it to {target}.)" + (f" {ask}" if ask else "")
    if not ask:
        ask = "Explain what this is about and anything I should know."
    parts.append(ask)
    return "\n\n".join(parts)


# =============================================================== Windows API
if IS_WINDOWS:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
    KEYEVENTF_KEYUP = 0x0002

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class _MSG(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT), ("wParam", wintypes.WPARAM),
                    ("lParam", wintypes.LPARAM), ("time", wintypes.DWORD), ("pt", _POINT)]

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetMessageW.argtypes = [ctypes.POINTER(_MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p]
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class HotkeyListener:
    """Registers a system-wide hotkey and calls on_press() (from a background
    thread) each time it's pressed."""

    def __init__(self, hotkey, on_press):
        self.mods, self.vk = parse_hotkey(hotkey)
        self.hotkey = hotkey
        self.on_press = on_press
        self.error = None
        self._thread = None
        self._tid = None
        self._ready = threading.Event()

    def start(self):
        if not IS_WINDOWS:
            self.error = "Quick Ask's hotkey works on Windows only"
            return False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(2)
        return self.error is None

    def _run(self):
        self._tid = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, self.mods | MOD_NOREPEAT, self.vk):
            self.error = (f"{self.hotkey} is already used by another program; pick a different "
                          f"Quick Ask hotkey in Settings")
            self._ready.set()
            return
        self._ready.set()
        msg = _MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    try:
                        self.on_press()
                    except Exception:
                        log.exception("Quick Ask hotkey handler failed")
        finally:
            user32.UnregisterHotKey(None, 1)

    def stop(self):
        if IS_WINDOWS and self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)


def foreground():
    """The window you were working in: hwnd, title, app (exe name), rect."""
    if not IS_WINDOWS:
        return {"hwnd": None, "title": "", "app": "", "rect": None}
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    app = ""
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        size = wintypes.DWORD(1024)
        pbuf = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, pbuf, ctypes.byref(size)):
            app = os.path.basename(pbuf.value)
        kernel32.CloseHandle(h)
    return {"hwnd": hwnd, "title": buf.value, "app": app, "rect": _window_rect(hwnd), "pid": pid.value}


def _window_rect(hwnd):
    rect = wintypes.RECT()
    try:  # the visible frame, without the invisible resize borders
        dwm = ctypes.WinDLL("dwmapi")
        if dwm.DwmGetWindowAttribute(wintypes.HWND(hwnd), 9, ctypes.byref(rect), ctypes.sizeof(rect)) == 0:
            return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        log.exception("couldn't read the window's visible frame")
    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return (rect.left, rect.top, rect.right, rect.bottom)
    return None


def cursor_pos():
    if not IS_WINDOWS:
        return (200, 200)
    p = _POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return (p.x, p.y)


def capture(info, whole_screen=False):
    """A screenshot of the window (or every screen). PIL image or None."""
    try:
        from PIL import ImageGrab
    except ImportError:
        return None
    bbox = None if whole_screen else info.get("rect")
    if bbox and (bbox[2] - bbox[0] < 20 or bbox[3] - bbox[1] < 20):
        bbox = None
    try:
        return ImageGrab.grab(bbox=bbox, all_screens=True)
    except Exception:
        return None


def _key(vk, up=False):
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP if up else 0, None)


def release_modifiers():
    """The hotkey's keys may still be held; let go of them so a simulated
    Ctrl+C isn't seen as Ctrl+Shift+C."""
    for vk in (0x10, 0xA0, 0xA1, 0x11, 0xA2, 0xA3, 0x12, 0x5B, 0x5C, 0x20):
        if user32.GetAsyncKeyState(vk) & 0x8000:
            _key(vk, up=True)


def send_copy():
    release_modifiers()
    _key(0x11)
    _key(ord("C"))
    _key(ord("C"), up=True)
    _key(0x11, up=True)


def send_paste():
    release_modifiers()
    _key(0x11)
    _key(ord("V"))
    _key(ord("V"), up=True)
    _key(0x11, up=True)


def clipboard_has_only_nontext():
    """True if the clipboard holds something that isn't text (an image, files).
    Copying the selection would overwrite it, and only text can be put back,
    so in that case the selection isn't read."""
    if not IS_WINDOWS:
        return False
    try:
        n = user32.CountClipboardFormats()
        has_text = user32.IsClipboardFormatAvailable(13) or user32.IsClipboardFormatAvailable(1)
        return bool(n) and not has_text
    except Exception:
        return False


def focus(hwnd):
    if IS_WINDOWS and hwnd and user32.IsWindow(hwnd):
        user32.SetForegroundWindow(hwnd)


# =============================================================== OCR
def ocr(img):
    """Text on an image with Windows' built-in OCR (lines in reading order),
    or None. ocr.last_error says why when it isn't available."""
    ocr.last_error = None
    if not IS_WINDOWS:
        ocr.last_error = "Windows OCR is only on Windows"
        return None
    try:
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter
    except ImportError:
        ocr.last_error = ("Windows OCR packages aren't installed. Run: venv\\Scripts\\python -m pip install "
                          "winrt-runtime winrt-Windows.Media.Ocr winrt-Windows.Graphics.Imaging "
                          "winrt-Windows.Storage.Streams winrt-Windows.Globalization winrt-Windows.Foundation "
                          "winrt-Windows.Foundation.Collections")
        return None
    import asyncio

    async def run():
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError("no OCR language installed in Windows (Settings → Time & language → Language)")
        im = img.convert("RGBA")
        side = int(getattr(OcrEngine, "max_image_dimension", 10000) or 10000)
        if max(im.size) > side:
            im.thumbnail((side, side))
        r, g, b, a = im.split()
        from PIL import Image
        data = Image.merge("RGBA", (b, g, r, a)).tobytes()  # BGRA
        writer = DataWriter()
        writer.write_bytes(data)
        bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8,
                                                        im.size[0], im.size[1])
        result = await engine.recognize_async(bitmap)
        return "\n".join(line.text for line in result.lines)

    try:
        return asyncio.run(run())
    except Exception as e:
        ocr.last_error = f"Windows OCR failed: {type(e).__name__}: {e}"
        return None


ocr.last_error = None
