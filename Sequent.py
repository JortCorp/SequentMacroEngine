import ctypes
import time
import copy
import json
import os
import re
import struct
import base64
import tempfile
import subprocess
import tkinter as tk
from tkinter import filedialog
from threading import Thread
from queue import Queue, Empty
import math
import colorsys
import random
import winsound

ACTIVE_SOUND_THEME = "Classic"
CUSTOM_SOUND_PATHS = {
    "start_rec": "",
    "stop_rec": "",
    "pause": "",
    "start": ""  
}

class MacroExecutionError(Exception):
    """Expected macro failure, such as an unmet verification condition."""


# --- Native Audio Feedback (Non-Blocking Kernel32 Beeps & WAVs) ---
def play_cue(cue_type):
    global ACTIVE_SOUND_THEME, CUSTOM_SOUND_PATHS
    def _beep():
        # 1. Play custom WAV or MP3 if set
        custom_path = CUSTOM_SOUND_PATHS.get(cue_type, "")
        if custom_path and os.path.exists(custom_path):
            ext = custom_path.lower()
            try:
                if ext.endswith(".wav"):
                    winsound.PlaySound(custom_path, winsound.SND_FILENAME | winsound.SND_NODEFAULT)
                    return
                elif ext.endswith(".mp3"):
                    # Use native Windows MCI (Media Control Interface) for MP3s
                    mci = ctypes.windll.winmm.mciSendStringW
                    alias = f"seq_audio_{cue_type}"
                    mci(f'close {alias}', None, 0, None)  # Ensure track isn't locked
                    
                    if mci(f'open "{custom_path}" alias {alias}', None, 0, None) == 0:
                        mci(f'play {alias} wait', None, 0, None)
                        mci(f'close {alias}', None, 0, None)
                        return
            except Exception:
                pass

        # 2. Fallback to Theme Beeps
        themes = {
            "Classic": {
                "start": [(520, 60), (700, 80)],
                "stop": [(700, 60), (520, 80)],
                "pause": [(440, 100)],
                "resume": [(880, 100)],
                "match": [(750, 60), (1050, 80)],
                "error": [(320, 200)]
            },
            "Subtle": {
                "start": [(300, 40), (400, 40)],
                "stop": [(400, 40), (300, 40)],
                "pause": [(250, 60)],
                "resume": [(500, 60)],
                "match": [(600, 40)],
                "error": [(200, 100)]
            },
            "Sci-Fi": {
                "start": [(1200, 40), (1600, 40), (2000, 60)],
                "stop": [(2000, 40), (1600, 40), (1200, 60)],
                "pause": [(1800, 80)],
                "resume": [(2400, 80)],
                "match": [(1500, 30), (2000, 30), (2500, 40)],
                "error": [(100, 150), (100, 150)]
            }
        }
        try:
            b = ctypes.windll.kernel32.Beep
            theme = themes.get(ACTIVE_SOUND_THEME, themes["Classic"])
            
            # Map specific cue_types to their corresponding theme arrays
            if cue_type == "start_rec": theme_cue = "start"
            elif cue_type == "stop_rec": theme_cue = "stop"
            else: theme_cue = cue_type
            
            for freq, dur in theme.get(theme_cue, []):
                b(freq, dur)
        except Exception:
            pass
    Thread(target=_beep, daemon=True).start()

# --- Window-Relative Coordinate Helpers ---
def get_active_window_info():
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd: return None, 0, 0, ""
    buf = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
    title = buf.value
    rect = (ctypes.c_long * 4)()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return hwnd, rect[0], rect[1], title

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

def screen_to_client_pos(hwnd, x, y):
    pt = POINT(int(x), int(y))
    ctypes.windll.user32.ScreenToClient(hwnd, ctypes.byref(pt))
    return pt.x, pt.y

def client_to_screen_pos(hwnd, rel_x, rel_y):
    pt = POINT(int(rel_x), int(rel_y))
    ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y

def find_window_by_title_substr(query):
    """Locate a visible desktop window with pointer-safe Win64 signatures."""
    if not query:
        return None
    user32 = ctypes.windll.user32
    WindowCallback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_ssize_t)
    user32.EnumWindows.argtypes = [WindowCallback, ctypes.c_ssize_t]
    user32.EnumWindows.restype = ctypes.c_int
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user32.IsWindowVisible.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    target_hwnd = [None]
    needle = str(query).casefold()
    def callback(hwnd, _extra):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(512)
            if user32.GetWindowTextW(hwnd, buf, len(buf)) and needle in buf.value.casefold():
                target_hwnd[0] = hwnd
                return False
        return True
    user32.EnumWindows(WindowCallback(callback), 0)
    return target_hwnd[0]

# --- Humanized Bézier Curve Calculator ---
def calculate_bezier_trajectory(start_pt, end_pt, steps=25):
    x0, y0 = start_pt
    x3, y3 = end_pt
    dx = x3 - x0
    dy = y3 - y0
    dist = math.hypot(dx, dy)
    if dist < 12:
        return [end_pt]

    offset = dist * 0.18
    ctrl_x1 = x0 + dx * 0.25 + random.uniform(-offset, offset)
    ctrl_y1 = y0 + dy * 0.25 + random.uniform(-offset, offset)
    ctrl_x2 = x0 + dx * 0.75 + random.uniform(-offset, offset)
    ctrl_y2 = y0 + dy * 0.75 + random.uniform(-offset, offset)

    points = []
    for i in range(1, steps + 1):
        t = i / steps
        u = 1 - t
        bx = (u**3)*x0 + 3*(u**2)*t*ctrl_x1 + 3*u*(t**2)*ctrl_x2 + (t**3)*x3
        by = (u**3)*y0 + 3*(u**2)*t*ctrl_y1 + 3*u*(t**2)*ctrl_y2 + (t**3)*y3
        points.append((int(bx), int(by)))
    return points

# --- Windows DPI Awareness ---

def get_dpi_scale():
    """Calculates the active display DPI scaling ratio (e.g., 1.0 for 100%, 1.25 for 125%, 1.5 for 150%)."""
    try:
        user32 = ctypes.windll.user32
        user32.GetDpiForSystem.restype = ctypes.c_uint
        dpi = user32.GetDpiForSystem()
        if dpi > 0:
            return dpi / 96.0
    except Exception:
        pass
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX = 88
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi > 0:
            return dpi / 96.0
    except Exception:
        pass
    return 1.0


# System-DPI awareness is the default for consistent Tk rendering.
# SEQUENT_DPI_MODE can optionally override it for environments with different
# display configurations (system, permonitor, unaware). Set before launch.
SEQUENT_DPI_MODE = os.environ.get("SEQUENT_DPI_MODE", "system").strip().lower()
if SEQUENT_DPI_MODE not in ("system", "permonitor", "unaware"):
    SEQUENT_DPI_MODE = "system"

if os.name == "nt":
    try:
        awareness = {"unaware": 0, "system": 1, "permonitor": 2}[SEQUENT_DPI_MODE]
        rc = ctypes.windll.shcore.SetProcessDpiAwareness(awareness)
        # Windows may return E_ACCESSDENIED if Python or a manifest has
        # already selected awareness. In that case Tk keeps the existing mode.
    except (AttributeError, OSError):
        pass

# --- Color targeting geometry (shared by preview, sequencer, and playback) ---
COLOR_ANCHORS = (
    "Top Left", "Top Center", "Top Right",
    "Middle Left", "Center", "Middle Right",
    "Bottom Left", "Bottom Center", "Bottom Right",
)


def color_anchor_position(box, anchor="Center"):
    """Return the selected anchor within an exclusive-right/bottom pixel bbox."""
    x1, y1, x2, y2 = box
    xs = (x1, (x1 + x2 - 1) // 2, x2 - 1)
    ys = (y1, (y1 + y2 - 1) // 2, y2 - 1)
    if anchor not in COLOR_ANCHORS:
        anchor = "Center"  # Compatibility with existing saved macros.
    idx = COLOR_ANCHORS.index(anchor)
    return xs[idx % 3], ys[idx // 3]


def color_click_target(action, box):
    """Unconstrained target: reticle may be anywhere, even beyond the bbox."""
    ax, ay = color_anchor_position(box, action.get("anchor", "Center"))
    return ax + int(action.get("ox", 0)), ay + int(action.get("oy", 0))


def find_ocr_word_match(words, target_text, match_any=False):
    """Find a text/phrase using WinRT OCR word boxes, not nonexistent line boxes.

    Return the tight union of matching word rectangles in *capture pixels*,
    or None. A phrase may span adjacent words on one OCR line. Comparing
    casefolded alphanumeric text tolerates OCR-inserted spaces/punctuation.
    """
    def compact(text):
        return "".join(ch for ch in str(text).casefold() if ch.isalnum())

    target = compact(target_text)
    if not target and not match_any:
        return None

    by_line = {}
    for word in words:
        if not isinstance(word, dict):
            continue
        try:
            rect = (float(word["X"]), float(word["Y"]),
                    float(word["Width"]), float(word["Height"]))
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        if not all(math.isfinite(v) for v in rect) or rect[2] <= 0 or rect[3] <= 0:
            continue
        content = compact(word.get("Text", ""))
        if content:
            by_line.setdefault(word.get("Line", 0), []).append((content, rect))

    for line_words in by_line.values():
        joined = ""
        segments = []
        for content, rect in line_words:
            start = len(joined)
            joined += content
            segments.append((start, len(joined), rect))
        start = 0 if match_any else joined.find(target)
        if start < 0:
            continue
        end = start + (1 if match_any else len(target))
        boxes = [rect for first, last, rect in segments
                 if first < end and last > start]
        if boxes:
            return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                    max(b[0] + b[2] for b in boxes),
                    max(b[1] + b[3] for b in boxes))
    return None


def largest_color_component(raw, width, height, stride, rgb, tolerance=8):
    """Return bounding box of largest 4-connected matching region, or None.

    raw is a top-down 24-bit BGR screenshot; the returned box is relative to
    that screenshot, with exclusive right and bottom edges. Isolated matches
    never merge merely because they share a color.
    """
    from collections import deque

    tolerance = max(0, min(255, int(tolerance)))
    tr, tg, tb = rgb
    mask = bytearray(width * height)
    for y in range(height):
        row = y * stride
        base = y * width
        for x in range(width):
            idx = row + 3 * x
            if (abs(raw[idx] - tb) <= tolerance and
                abs(raw[idx + 1] - tg) <= tolerance and
                abs(raw[idx + 2] - tr) <= tolerance):
                mask[base + x] = 1

    best_box = None
    best_count = 0
    for seed in range(len(mask)):
        if not mask[seed]:
            continue
        mask[seed] = 0
        pending = deque((seed,))
        pixels = 0
        x1, y1 = width, height
        x2 = y2 = -1
        while pending:
            i = pending.popleft()
            py, px = divmod(i, width)
            pixels += 1
            x1, y1 = min(x1, px), min(y1, py)
            x2, y2 = max(x2, px), max(y2, py)
            for neighbor in (i - width, i + width, i - 1, i + 1):
                if ((neighbor == i - 1 and px == 0) or
                    (neighbor == i + 1 and px == width - 1) or
                    neighbor < 0 or neighbor >= len(mask)):
                    continue
                if mask[neighbor]:
                    mask[neighbor] = 0
                    pending.append(neighbor)
        if pixels > best_count:
            best_count = pixels
            best_box = (x1, y1, x2 + 1, y2 + 1)
    return best_box



class SequentChromeRect(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


class SequentChromeMonitorInfo(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint32), ('rcMonitor', SequentChromeRect),
                ('rcWork', SequentChromeRect), ('dwFlags', ctypes.c_uint32)]


# --- Optional native borderless DLL (never uses Python WNDPROC callbacks) ---
_sequent_chrome_dll = None
_sequent_chrome_attempted = False


def get_sequent_native_chrome():
    """Load the optional x64 DLL from beside Sequent.py, once per process.

    SequentChrome.dll contains the Win32 subclass callback in compiled C.
    Do not emulate it using ctypes.WINFUNCTYPE: a Python WNDPROC may crash
    during Windows' nested native resize event loop (GIL released).
    """
    global _sequent_chrome_dll, _sequent_chrome_attempted
    if _sequent_chrome_attempted:
        return _sequent_chrome_dll
    _sequent_chrome_attempted = True
    if os.name != 'nt':
        return None
    dll_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'SequentChrome.dll')
    if not os.path.isfile(dll_path):
        print('[Sequent] Native borderless DLL not found; using reliable thin-frame resize mode.')
        print('[Sequent] To enable borderless mode, build native\\build_native.cmd with MSVC.')
        return None
    try:
        dll = ctypes.WinDLL(dll_path, use_last_error=True)
        dll.SequentChromeVersion.argtypes = []
        dll.SequentChromeVersion.restype = ctypes.c_int
        if dll.SequentChromeVersion() != 1:
            raise OSError('SequentChrome.dll has an incompatible ABI version')
        dll.SequentInstallChrome.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        dll.SequentInstallChrome.restype = ctypes.c_int
        dll.SequentRemoveChrome.argtypes = [ctypes.c_void_p]
        dll.SequentRemoveChrome.restype = ctypes.c_int
        _sequent_chrome_dll = dll   # Keep loaded until all Tk windows close.
    except (OSError, AttributeError, ValueError) as exc:
        print(f'[Sequent] Could not load native borderless DLL: {exc}')
    return _sequent_chrome_dll


# --- Sequent Windows-managed custom chrome (safe: no Python WNDPROC callbacks) ---
# Never install a Python ctypes WNDPROC/SUBCLASSPROC on a Tk window. Windows
# resize/move processing re-enters WNDPROC while ctypes may have released the
# GIL; handing that traffic through a Python callback can terminate Python
# with PyEval_RestoreThread. Let Tk's native C WNDPROC own all messages.
class SequentNativeChrome:
    """Use Windows-managed sizing with custom-painted Sequent window chrome.

    Preserve WS_THICKFRAME for native live resizing, but ask the Windows 11
    Desktop Window Manager not to paint its visible border. Never subclass
    Tk's native WNDPROC from Python (a resize can otherwise crash the GIL).
    """
    WS_CAPTION = 0x00C00000
    WS_THICKFRAME = 0x00040000
    WS_SYSMENU = 0x00080000
    WS_MINIMIZEBOX = 0x00020000
    WS_MAXIMIZEBOX = 0x00010000
    SWP_FRAMECHANGED = 0x0020
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010

    def __init__(self, window, resizable=True):
        self.window = window
        self.resizable = resizable
        self.hwnd = native_toplevel_hwnd(window)
        user32 = ctypes.windll.user32
        user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long
        user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                         ctypes.c_int, ctypes.c_uint]
        user32.SetWindowPos.restype = ctypes.c_int

        old_style = user32.GetWindowLongW(self.hwnd, -16)
        style = (old_style & ~self.WS_CAPTION) | self.WS_SYSMENU | self.WS_MINIMIZEBOX
        if resizable:
            style |= self.WS_THICKFRAME | self.WS_MAXIMIZEBOX
        else:
            style &= ~(self.WS_THICKFRAME | self.WS_MAXIMIZEBOX)
        user32.SetWindowLongW(self.hwnd, -16, style)
        if not user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0,
            self.SWP_FRAMECHANGED | self.SWP_NOMOVE | self.SWP_NOSIZE |
            self.SWP_NOZORDER | self.SWP_NOACTIVATE):
            # Restore the original style if Windows refuses the frame update.
            user32.SetWindowLongW(self.hwnd, -16, old_style)
            raise OSError('Could not update Sequent window frame')

        # Ask DWM for rounded corners; this does not intercept window messages.
        try:
            value = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(self.hwnd), 33, ctypes.byref(value), ctypes.sizeof(value))
        except (OSError, AttributeError):
            pass
        self.refresh_dwm_frame()
        self.true_frameless = False
        # Optional native, GIL-independent non-client-area handling. Retain
        # the existing safe, thin-frame implementation if the DLL is absent.
        if os.environ.get('SEQUENT_WINDOW_MODE', 'custom').lower() not in ('legacy', 'native'):
            native = get_sequent_native_chrome()
            if native is not None:
                try:
                    if native.SequentInstallChrome(ctypes.c_void_p(self.hwnd), 8 if resizable else 0):
                        if user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0,
                            self.SWP_FRAMECHANGED | self.SWP_NOMOVE | self.SWP_NOSIZE |
                            self.SWP_NOZORDER | self.SWP_NOACTIVATE):
                            self.true_frameless = True
                        else:
                            native.SequentRemoveChrome(ctypes.c_void_p(self.hwnd))
                except (OSError, AttributeError) as exc:
                    print(f'[Sequent] Native chrome unavailable: {exc}')

    def refresh_dwm_frame(self):
        """Hide the native white frame without losing Win32 live resizing.

        DWMWA_BORDER_COLOR=DWMWA_COLOR_NONE (34, 0xFFFFFFFE) prevents Windows 11
        from drawing its visible frame even with WS_THICKFRAME retained. The
        caption's fallback color matches Sequent's current custom header,
        so unsupported/partial DWM styling is less conspicuous. Unsupported
        Windows versions safely retain the regular native resize border.
        """
        self.border_hidden = False
        try:
            dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
            dwm.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                            ctypes.c_void_p, ctypes.c_uint32]
            dwm.restype = ctypes.c_long
            hwnd = ctypes.c_void_p(self.hwnd)
            none = ctypes.c_uint32(0xFFFFFFFE)  # DWMWA_COLOR_NONE
            hr = dwm(hwnd, 34, ctypes.byref(none), ctypes.sizeof(none))
            self.border_hidden = (hr == 0)

            # DWMWA_CAPTION_COLOR=35 (COLORREF is 0x00BBGGRR).
            # This is updated again when the app switches light/dark themes.
            header = globals().get('BG_HEADER', '#111318').lstrip('#')
            if len(header) == 6:
                r, g, b = (int(header[i:i+2], 16) for i in (0, 2, 4))
                colorref = ctypes.c_uint32((b << 16) | (g << 8) | r)
                dwm(hwnd, 35, ctypes.byref(colorref), ctypes.sizeof(colorref))
        except (AttributeError, OSError, ValueError):
            # Color attributes need Windows 11; avoid touching resizing/focus.
            pass


def install_sequent_chrome(window, *, resizable=True):
    """Install safe custom window styling; Tk keeps its native WNDPROC."""
    if os.name != 'nt' or os.environ.get('SEQUENT_WINDOW_MODE', 'custom') == 'native':
        return None
    window.update_idletasks()
    return SequentNativeChrome(window, resizable=resizable)


def begin_native_window_motion(window, hit_test=2):
    """Queue native move/size after Tk's ButtonPress handler returns.

    Posting avoids a nested synchronous SendMessage call into the Tk WNDPROC
    while Tk is still executing the originating Python mouse callback.
    """
    user32 = ctypes.windll.user32
    hwnd = native_toplevel_hwnd(window)
    # Reuse the module-level POINT structure. ctypes checks structure class
    # identity, not just compatible fields. Redefining POINT here would set
    # GetCursorPos.argtypes globally to the nested type and break recording's
    # get_mouse_pos() (and other screen-position callers).
    point = POINT()
    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.GetCursorPos.restype = ctypes.c_int
    if not user32.GetCursorPos(ctypes.byref(point)):
        return False
    # WM_NCLBUTTONDOWN expects signed virtual-desktop coordinates packed into LPARAM.
    lparam = ((int(point.y) & 0xffff) << 16) | (int(point.x) & 0xffff)
    user32.ReleaseCapture.argtypes = []
    user32.ReleaseCapture.restype = ctypes.c_int
    user32.ReleaseCapture()
    user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                    ctypes.c_size_t, ctypes.c_ssize_t]
    user32.PostMessageW.restype = ctypes.c_int
    return bool(user32.PostMessageW(hwnd, 0x00A1, int(hit_test), lparam))


def add_native_resize_grips(window, bg):
    """Provide edge/corner hit targets for live OS-managed window resizing."""
    grips = []
    edge_specs = (
        ('left_side', 10, dict(relx=0, rely=0, x=0, y=12, width=6, relheight=1, height=-24), 'size_we'),
        ('right_side', 11, dict(relx=1, rely=0, x=-6, y=12, width=6, relheight=1, height=-24), 'size_we'),
        ('top_side', 12, dict(relx=0, rely=0, x=12, y=0, relwidth=1, width=-24, height=6), 'size_ns'),
        ('bottom_side', 15, dict(relx=0, rely=1, x=12, y=-6, relwidth=1, width=-24, height=6), 'size_ns'),
        ('top_left', 13, dict(x=0, y=0, width=12, height=12), 'size_nw_se'),
        ('top_right', 14, dict(relx=1, x=-12, y=0, width=12, height=12), 'size_ne_sw'),
        ('bottom_left', 16, dict(x=0, rely=1, y=-12, width=12, height=12), 'size_ne_sw'),
        ('bottom_right', 17, dict(relx=1, rely=1, x=-12, y=-12, width=12, height=12), 'size_nw_se'),
    )
    for name, code, geometry, cursor in edge_specs:
        grip = tk.Frame(window, bg=bg, cursor=cursor, name='seq_grip_' + name)
        grip.place(**geometry)
        grip.bind('<Button-1>', lambda e, c=code: begin_native_window_motion(window, c))
        grips.append(grip)
    return grips


# --- Native Tk toplevel placement and hit testing ---
def native_toplevel_hwnd(window):
    """Get the *wrapper* HWND. Tk's winfo_id() is its child HWND on Windows.

    Moving or styling the child instead of the wrapper puts reticles/selection
    overlays at a different position from their reported screen coordinates.
    Explicit ctypes signatures avoid truncating 64-bit HWND values.
    """
    user32 = ctypes.windll.user32
    user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user32.GetAncestor.restype = ctypes.c_void_p
    return user32.GetAncestor(window.winfo_id(), 2) or window.winfo_id()  # GA_ROOT


def position_toplevel_on_screen(window, x, y, width, height, activate=False):
    """Place a Tk Toplevel in *absolute virtual-screen pixels*, including negatives."""
    x, y = int(x), int(y)
    width, height = max(1, int(width)), max(1, int(height))
    # Request size from Tk, then move its native outer wrapper (not the child).
    # Tk's negative geometry notation is right/bottom-relative, so never pass
    # negative screen coordinates through geometry() directly.
    window.geometry(f"{width}x{height}")
    window.update_idletasks()
    hwnd = native_toplevel_hwnd(window)
    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_uint]
    user32.SetWindowPos.restype = ctypes.c_int
    flags = 0x0040 | (0x0010 if not activate else 0)  # SHOWWINDOW | NOACTIVATE
    if not user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), x, y, width, height, flags):
        raise OSError("Could not position screen overlay")


def set_overlay_input_passthrough(window, enabled):
    """Use the actual top-level HWND for click-through; disable it for dragging."""
    hwnd = native_toplevel_hwnd(window)
    user32 = ctypes.windll.user32
    # LongPtr preserves Win64 styles and avoids accidental HWND truncation.
    get_style = getattr(user32, "GetWindowLongPtrW", None)
    set_style = getattr(user32, "SetWindowLongPtrW", None)
    if get_style is None or set_style is None:
        get_style = user32.GetWindowLongW
        set_style = user32.SetWindowLongW
        style_t = ctypes.c_long
    else:
        style_t = ctypes.c_ssize_t
    get_style.argtypes, get_style.restype = [ctypes.c_void_p, ctypes.c_int], style_t
    set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, style_t]
    set_style.restype = style_t
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_NOACTIVATE = 0x08000000
    before = get_style(hwnd, -20)
    after = (before | WS_EX_NOACTIVATE)
    after = (after | WS_EX_TRANSPARENT) if enabled else (after & ~WS_EX_TRANSPARENT)
    if after != before:
        set_style(hwnd, -20, after)
        # Refresh window manager state after changing extended styles.
        user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_uint]
        user32.SetWindowPos.restype = ctypes.c_int
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                            0x0001 | 0x0002 | 0x0010 | 0x0020)
        # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_FRAMECHANGED


# --- Fast live-overlay movement and Windows capture exclusion ---
def move_overlay_without_redraw(window, x, y):
    """Move the real HWND without modifying Tk geometry or repainting its canvas.

    Rebuilding a Toplevel on each arrow press makes the reticle flash. This
    changes only its position; the existing pixels and drag bindings survive.
    """
    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_uint]
    user32.SetWindowPos.restype = ctypes.c_int
    # SWP_NOSIZE | SWP_NOACTIVATE. The Toplevel is already visible/topmost.
    if not user32.SetWindowPos(native_toplevel_hwnd(window), None,
                               int(x), int(y), 0, 0, 0x0001 | 0x0010):
        raise OSError("Unable to move preview reticle")


def exclude_overlay_from_capture(window):
    """Exclude Sequent's own preview windows from Windows screen capture.

    On supported Windows 10/11 builds WDA_EXCLUDEFROMCAPTURE keeps the green
    outline out of BitBlt reads without hiding/redrawing anything. We do not
    fall back to WDA_MONITOR: its black capture rectangle can cover the object
    being searched and create false negatives.
    """
    try:
        affinity = ctypes.windll.user32.SetWindowDisplayAffinity
        affinity.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        affinity.restype = ctypes.c_int
        hwnd = native_toplevel_hwnd(window)
        return bool(affinity(hwnd, 0x00000011))  # WDA_EXCLUDEFROMCAPTURE
    except (AttributeError, OSError):
        return False


# --- Native Windows API Constants & Structures ---
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_WHEEL = 0x0800
WH_MOUSE_LL = 14
WM_MOUSEWHEEL = 0x020A


GWL_EXSTYLE = -20
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12       # ALT
VK_CAPITAL = 0x14    # Caps Lock
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21      # Page Up
VK_NEXT = 0x22       # Page Down
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25       # Left Arrow
VK_UP = 0x26         # Up Arrow
VK_RIGHT = 0x27      # Right Arrow
VK_DOWN = 0x28       # Down Arrow
VK_DELETE = 0x2E
VK_LWIN = 0x5B       # Left Windows / Super Key
VK_RWIN = 0x5C       # Right Windows / Super Key

VK_A = 0x41
VK_C = 0x43
VK_V = 0x56

EXTENDED_KEYS = {
    VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN,
    VK_PRIOR, VK_NEXT, VK_END, VK_HOME,
    0x2D, VK_DELETE, VK_LWIN, VK_RWIN
}

MODIFIER_VK_MAP = {
    VK_SHIFT: "Shift",
    VK_CONTROL: "Ctrl",
    VK_MENU: "Alt",
    VK_LWIN: "Win",
    VK_RWIN: "Win",
}

MODIFIER_KEYS = {
    0x10, 0x11, 0x12,
    0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5,
    VK_LWIN, VK_RWIN
}

SPECIAL_KEY_NAMES = {
    VK_LEFT: "Left",
    VK_UP: "Up",
    VK_RIGHT: "Right",
    VK_DOWN: "Down",
    VK_TAB: "Tab",
    VK_SPACE: "Space",
    VK_DELETE: "Delete",
    VK_HOME: "Home",
    VK_END: "End",
    VK_PRIOR: "PageUp",
    VK_NEXT: "PageDown",
}

class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t)
    ]

class MARGINS(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int)
    ]

def get_clipboard_text():
    """Safely extracts unicode text from the Windows clipboard using native Win32 APIs."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    CF_UNICODETEXT = 13
    
    opened = False
    for _ in range(5):
        if user32.OpenClipboard(None):
            opened = True
            break
        time.sleep(0.02)
    if not opened:
        return ""

    try:
        user32.GetClipboardData.restype = ctypes.c_void_p
        user32.GetClipboardData.argtypes = [ctypes.c_uint]
        h_mem = user32.GetClipboardData(CF_UNICODETEXT)
        if not h_mem:
            return ""

        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]

        ptr = kernel32.GlobalLock(h_mem)
        if not ptr:
            return ""
        try:
            val = ctypes.c_wchar_p(ptr).value
            return val if val else ""
        finally:
            kernel32.GlobalUnlock(h_mem)
    finally:
        user32.CloseClipboard()

# --- Native Windows API Functions ---
def get_mouse_pos():
    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y

def move_mouse(x, y):
    """Move the real cursor, reporting API failure instead of clicking at stale coordinates."""
    user32 = ctypes.windll.user32
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = ctypes.c_int
    if not user32.SetCursorPos(int(x), int(y)):
        raise MacroExecutionError(f"Windows could not move the cursor to ({x}, {y})")

def get_pixel_color(x, y):
    """Read a desktop pixel using correctly typed 64-bit Win32 GDI handles.

    GetDC and GetPixel must agree on the HDC type. Otherwise, after the color
    scanner sets GetDC.restype=c_void_p, an untyped GetPixel argument raises
    ctypes.ArgumentError/OverflowError on 64-bit Windows.
    """
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    HDC = ctypes.c_void_p
    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.GetDC.restype = HDC
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    gdi32.GetPixel.argtypes = [HDC, ctypes.c_int, ctypes.c_int]
    gdi32.GetPixel.restype = ctypes.c_uint32  # COLORREF / CLR_INVALID

    hdc = user32.GetDC(None)
    if not hdc:
        raise MacroExecutionError("Could not access desktop pixels (GetDC failed)")
    try:
        color = gdi32.GetPixel(hdc, int(x), int(y))
    finally:
        user32.ReleaseDC(None, hdc)
    if color == 0xFFFFFFFF:  # CLR_INVALID; don't silently interpret as black
        raise MacroExecutionError(f"Could not sample pixel at ({x}, {y})")
    r = color & 0xFF
    g = (color >> 8) & 0xFF
    b = (color >> 16) & 0xFF
    return f"#{r:02X}{g:02X}{b:02X}"


# Native INPUT / MOUSEINPUT layout, including 64-bit ULONG_PTR alignment.
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32),
                ("mouseData", ctypes.c_uint32), ("dwFlags", ctypes.c_uint32),
                ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("u", INPUT_UNION)]


def wait_for_cursor_settled(x, y, timeout_s=0.25, settle_s=0.05, tolerance=15):
    """Wait until the real cursor is at the target *continuously* for a short time.

    SetCursorPos returning success doesn't mean an application's event loop has
    processed the movement yet. Give the target a minimum hover/settling window,
    independent of macro playback speed. A timeout cancels the click safely.
    """
    x, y = int(x), int(y)
    deadline = time.monotonic() + timeout_s
    stable_since = None
    last_pos = get_mouse_pos()
    while True:
        last_pos = get_mouse_pos()
        if abs(last_pos[0] - x) <= tolerance and abs(last_pos[1] - y) <= tolerance:
            if stable_since is None:
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= settle_s:
                return
        else:
            stable_since = None
        if time.monotonic() >= deadline:
            raise MacroExecutionError(
                f"Cursor did not settle at ({x}, {y}); last position "
                f"{last_pos}. Click cancelled."
            )
        time.sleep(0.01)


def click(button="left", hold_s=0.055):
    """Inject a checked mouse down, hold briefly, then inject mouse up.

    A batched down+up can be processed in one application frame and appear to
    do nothing. Send separate events with a minimum hold duration that doesn't
    shrink when playback is accelerated. OS acceptance still doesn't guarantee
    that a particular application responds to synthetic input.
    """
    flags = {
        "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    }.get(button)
    if flags is None:
        raise MacroExecutionError(f"Unsupported mouse button: {button!r}")

    user32 = ctypes.windll.user32
    user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = ctypes.c_uint

    def send_flag(flag):
        event = INPUT()
        event.type = 0  # INPUT_MOUSE
        event.u.mi.dwFlags = flag
        return user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) == 1

    if not send_flag(flags[0]):
        raise MacroExecutionError(f"Windows rejected mouse-down for {button} click")
    try:
        time.sleep(max(0.03, float(hold_s)))
    finally:
        # Always try to release, even if the wait is interrupted.
        released = send_flag(flags[1])
    if not released:
        raise MacroExecutionError(f"Windows rejected mouse-up for {button} click")


def perform_color_target_action(action, found_box, playback_speed=1.0):
    """Shared Find Color & Click behavior for player, debugger, and chains.

    Only the visual detection coordinates influence the saved click target.
    The reticle may be freely offset outside the object's bounding rectangle.
    """
    tx, ty = color_click_target(action, found_box)
    mode = action.get("action", "Single Click")
    if mode not in ("Move Only", "Single Click", "Double Click"):
        raise MacroExecutionError(f"Unrecognized Find Color action: {mode!r}")
    move_mouse(tx, ty)
    # Not proportional to speed: the target UI still needs time to handle
    # mouse movement even when macro playback is accelerated.
    wait_for_cursor_settled(tx, ty)
    if mode in ("Single Click", "Double Click"):
        # Check again directly before sending mouse-down, as external input can
        # move the cursor after the initial settling interval.
        actual_x, actual_y = get_mouse_pos()
        if abs(actual_x - tx) > 2 or abs(actual_y - ty) > 2:
            raise MacroExecutionError(
                f"Cursor moved to ({actual_x}, {actual_y}) after settling; "
                f"expected ({tx}, {ty}). Click cancelled."
            )
        click("left")
    if mode == "Double Click":
        time.sleep(0.07)
        click("left")
    return (mode, tx, ty)

def send_unicode_char(char):
    """Dispatches raw Unicode characters that lack a standard Virtual Key code."""
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    char_code = ord(char)
    ctypes.windll.user32.keybd_event(0, char_code, KEYEVENTF_UNICODE, 0)
    ctypes.windll.user32.keybd_event(0, char_code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0)

def type_string(text, speed_multiplier=1.0):
    for char in text:
        res = ctypes.windll.user32.VkKeyScanW(ord(char))
        if res == -1 or res == 0xFFFF:
            send_unicode_char(char)
            time.sleep(max(0.001, 0.02 / speed_multiplier))
            continue

        vk = res & 0xFF
        shift = bool(res & 0x100)
        ctrl = bool(res & 0x200)
        alt = bool(res & 0x400)

        if shift: ctypes.windll.user32.keybd_event(VK_SHIFT, 0, 0, 0)
        if ctrl:  ctypes.windll.user32.keybd_event(VK_CONTROL, 0, 0, 0)
        if alt:   ctypes.windll.user32.keybd_event(VK_MENU, 0, 0, 0)

        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

        if alt:   ctypes.windll.user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        if ctrl:  ctypes.windll.user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        if shift: ctypes.windll.user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)

        time.sleep(max(0.001, 0.02 / speed_multiplier))


def get_char_from_vk(vk):
    shift_pressed = bool((ctypes.windll.user32.GetAsyncKeyState(VK_SHIFT) & 0x8000) or 
                         (ctypes.windll.user32.GetAsyncKeyState(0xA0) & 0x8000) or 
                         (ctypes.windll.user32.GetAsyncKeyState(0xA1) & 0x8000))
    caps_on = bool(ctypes.windll.user32.GetKeyState(VK_CAPITAL) & 0x0001)

    keyboard_state = (ctypes.c_byte * 256)()
    if shift_pressed: keyboard_state[VK_SHIFT] = 0x80
    if caps_on: keyboard_state[VK_CAPITAL] = 0x01

    buf = ctypes.create_unicode_buffer(2)
    scan_code = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    res = ctypes.windll.user32.ToUnicode(vk, scan_code, keyboard_state, buf, 2, 0)
    return buf.value if res > 0 else None

def press_virtual_key(vk):
    flags = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_KEYS else 0
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    ctypes.windll.user32.keybd_event(vk, scan, flags, 0)
    time.sleep(0.05)
    ctypes.windll.user32.keybd_event(vk, scan, flags | KEYEVENTF_KEYUP, 0)

def hold_virtual_key(vk):
    flags = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_KEYS else 0
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    ctypes.windll.user32.keybd_event(vk, scan, flags, 0)

def release_virtual_key(vk):
    flags = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_KEYS else 0
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    ctypes.windll.user32.keybd_event(vk, scan, flags | KEYEVENTF_KEYUP, 0)

def press_key_combo(mods, vk, hold_time=0.06):
    if isinstance(mods, int): mods = [mods]
    for m in mods:
        m_flags = KEYEVENTF_EXTENDEDKEY if m in EXTENDED_KEYS else 0
        m_scan = ctypes.windll.user32.MapVirtualKeyW(m, 0)
        ctypes.windll.user32.keybd_event(m, m_scan, m_flags, 0)

    time.sleep(0.05)
    vk_flags = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_KEYS else 0
    vk_scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    ctypes.windll.user32.keybd_event(vk, vk_scan, vk_flags, 0)
    time.sleep(hold_time)
    ctypes.windll.user32.keybd_event(vk, vk_scan, vk_flags | KEYEVENTF_KEYUP, 0)
    time.sleep(0.04)

    for m in reversed(mods):
        m_flags = KEYEVENTF_EXTENDEDKEY if m in EXTENDED_KEYS else 0
        m_scan = ctypes.windll.user32.MapVirtualKeyW(m, 0)
        ctypes.windll.user32.keybd_event(m, m_scan, m_flags | KEYEVENTF_KEYUP, 0)

# --- Sequent workspace theme tokens (all main views + action library) ---
BG_APP = "#17191e"          # Workspace canvas
BG_SIDEBAR = "#1c1f26"      # Navigation / action library
BG_HEADER = "#111318"       # Window chrome / dock headers
BG_PANEL = "#23262e"        # Dock interiors and cards
BG_INPUT = "#181b21"        # Edit fields and recessed controls
BG_GUTTER = "#15171c"
BG_SCROLL_TRACK = "#15171c"
BG_BORDER = "#353a43"
BG_HOVER = "#303642"
BG_SELECTED = "#293b54"
TRACK_INACTIVE = "#4a5260"
TRACK_HOVER = "#657185"
TRACK_ACTIVE = "#4089eb"

TEXT_PRIMARY = "#edf1f6"
TEXT_MUTED = "#a0aab8"
TEXT_HEADER = "#ffffff"

COLOR_ACCENT = "#347BD8"
COLOR_ACCENT_HOV = "#2667BE"
TEXT_ON_ACCENT = "#FFFFFF"
COLOR_DANGER = "#d54d54"
COLOR_DANGER_HOV = "#a73942"
COLOR_SUCCESS = "#35a879"
DISABLED = "#363b45"

SYNTAX_BLUE = "#79b8ff"
SYNTAX_ORANGE = "#f3ba65"
SYNTAX_PURPLE = "#c4a3ed"
SYNTAX_GREEN = "#65d4aa"


DEFAULT_ACCENT_COLOR = "#347BD8"
ACCENT_PRESETS = (
    ("Sequent Blue", "#347BD8"),
    ("Blurple", "#5865F2"),
    ("Violet", "#9256CC"),
    ("Mint", "#1BA883"),
    ("Coral", "#DE6752"),
    ("Rose", "#D14C91"),
    ("Amber", "#E9AC32"),
)


def normalize_accent_color(value):
    """Return a canonical #RRGGBB or reject malformed user/config values."""
    if not isinstance(value, str):
        raise ValueError("Accent color must be a hexadecimal string")
    value = value.strip().upper()
    if re.fullmatch(r"#[0-9A-F]{3}", value):
        value = "#" + "".join(ch * 2 for ch in value[1:])
    if not re.fullmatch(r"#[0-9A-F]{6}", value):
        raise ValueError("Enter a color such as #5865F2")
    return value


def blend_hex(foreground, background, foreground_weight):
    """Blend RGB colors without third-party dependencies."""
    ratio = max(0, min(1, float(foreground_weight)))
    fg = [int(foreground[i:i + 2], 16) for i in (1, 3, 5)]
    bg = [int(background[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(a * ratio + b * (1 - ratio)):02X}" for a, b in zip(fg, bg))


def accent_text_color(hex_color):
    """Prefer readable text on both bright and dark accent choices."""
    channels = [int(hex_color[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
    luminance = sum(a * b for a, b in zip(linear, (0.2126, 0.7152, 0.0722)))
    return "#111318" if luminance > 0.179 else "#FFFFFF"


def read_windows_app_theme():
    """Read Windows 'Default app mode'. Unavailable keys safely default to dark."""
    if os.name != "nt":
        return "dark"
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _type = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if int(value) else "dark"
    except (OSError, ValueError, TypeError, ImportError):
        return "dark"


def sequent_dock_header(parent, title, subtitle="", right_text=""):
    """Small consistent dock title bar. No changes to automation behavior."""
    shell = tk.Frame(parent, bg=BG_HEADER, height=39)
    shell.pack(fill=tk.X)
    shell.pack_propagate(False)
    tk.Frame(shell, bg=COLOR_ACCENT, width=3).pack(side=tk.LEFT, fill=tk.Y)
    tk.Label(shell, text=title.upper(), bg=BG_HEADER, fg=TEXT_PRIMARY,
             font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(12, 8))
    if subtitle:
        tk.Label(shell, text=subtitle, bg=BG_HEADER, fg=TEXT_MUTED,
                 font=("Segoe UI", 8)).pack(side=tk.LEFT)
    if right_text:
        tk.Label(shell, text=right_text, bg=BG_INPUT, fg=SYNTAX_BLUE,
                 font=("Consolas", 8), padx=7, pady=3).pack(side=tk.RIGHT, padx=10)
    tk.Frame(parent, bg=BG_BORDER, height=1).pack(fill=tk.X)
    return shell


def sequent_toolbar_button(parent, text, command, accent=False):
    """Compact Sequent toolbar button with clear hover affordance."""
    base = COLOR_ACCENT if accent else BG_INPUT
    hover = COLOR_ACCENT_HOV if accent else BG_HOVER
    label = tk.Label(parent, text=text, bg=base,
                     fg=TEXT_ON_ACCENT if accent else TEXT_PRIMARY,
                     font=("Segoe UI", 9, "bold"), padx=11, pady=6,
                     cursor="hand2", highlightbackground=BG_BORDER,
                     highlightthickness=0 if accent else 1)
    label.bind("<Button-1>", lambda e: command())
    label.bind("<Enter>", lambda e: label.config(bg=hover))
    label.bind("<Leave>", lambda e: label.config(bg=base))
    return label


# --- Sequent native-looking, dependency-free custom accent color dialog ---
class SequentAccentColorDialog:
    """HSV square + hue strip with HEX/RGB controls; saves only when applied.

    The desktop accent is NOT modified during preview. This prevents Sequent's
    full theme rebuild from destroying the still-open editor on every drag.
    """

    SV_WIDTH = 242
    SV_HEIGHT = 208
    HUE_WIDTH = 26

    def __init__(self, app):
        self.app = app
        self.original = app.accent_color
        self.selected = self.original
        self._closing = False
        self._updating_fields = False
        self._sv_image = None
        self._sv_redraw_job = None
        self.h, self.s, self.v = self.hex_to_hsv(self.original)

        self.window = tk.Toplevel(app.root)
        self.window.title("Sequent — Custom Accent Color")
        self.window.configure(bg=BG_BORDER)
        self.window.resizable(False, False)
        self.window.transient(app.root)
        self.window.attributes("-topmost", True)
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)
        self.window.bind("<Escape>", self.cancel)
        self._chrome = None

        # Place inside the current screen; preserve topmost behavior of Sequent's
        # borderless root without hiding the chooser behind the main workspace.
        self._build()
        self.window.update_idletasks()
        width = 596
        # Use the actual requested size; fixed short heights can hide the footer.
        height = min(max(620, self.window.winfo_reqheight()),
                     max(480, self.window.winfo_screenheight() - 64))
        app.root.update_idletasks()
        left = app.root.winfo_rootx() + max(0, (app.root.winfo_width() - width) // 2)
        top = app.root.winfo_rooty() + max(0, (app.root.winfo_height() - height) // 2)
        left = max(0, min(left, self.window.winfo_screenwidth() - width))
        top = max(0, min(top, self.window.winfo_screenheight() - height))
        self.window.geometry(f"{width}x{height}+{left}+{top}")
        if os.name == 'nt':
            self._chrome = install_sequent_chrome(self.window, resizable=False)
        self._render_sv_image()
        self._draw_cursors()
        self._update_preview()
        self.window.grab_set()
        self.window.lift()
        self.window.focus_force()

    @staticmethod
    def hex_to_rgb(color):
        color = normalize_accent_color(color)
        return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))

    @staticmethod
    def rgb_to_hex(rgb):
        return "#" + "".join(f"{max(0, min(255, round(c))):02X}" for c in rgb)

    @classmethod
    def hex_to_hsv(cls, color):
        r, g, b = cls.hex_to_rgb(color)
        return colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)

    @staticmethod
    def hsv_to_hex(h, s, v):
        return SequentAccentColorDialog.rgb_to_hex(
            tuple(c * 255 for c in colorsys.hsv_to_rgb(h % 1.0, s, v))
        )

    def _label(self, parent, text, *, size=9, color=None, bold=False, bg=None):
        return tk.Label(parent, text=text, bg=bg or BG_PANEL, fg=color or TEXT_PRIMARY,
                        font=("Segoe UI", size, "bold" if bold else "normal"),
                        anchor="w")

    def _build(self):
        card = tk.Frame(self.window, bg=BG_PANEL)
        card.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        head = tk.Frame(card, bg=BG_HEADER, height=67)
        head.pack(fill=tk.X)
        head.pack_propagate(False)
        if os.name == 'nt':
            head.bind('<Button-1>', lambda e: begin_native_window_motion(self.window))
        tk.Frame(head, bg=COLOR_ACCENT, width=4).pack(side=tk.LEFT, fill=tk.Y)
        title_box = tk.Frame(head, bg=BG_HEADER)
        title_box.pack(side=tk.LEFT, padx=18, pady=10)
        self._label(title_box, "CUSTOM ACCENT COLOR", size=12, bold=True, bg=BG_HEADER).pack(anchor="w")
        self._label(title_box, "Fine-tune Sequent's workspace highlight color", size=8,
                    color=TEXT_MUTED, bg=BG_HEADER).pack(anchor="w", pady=(3, 0))
        if os.name == 'nt':
            for widget in [title_box, *title_box.winfo_children()]:
                widget.bind('<Button-1>', lambda e: begin_native_window_motion(self.window))
        close = tk.Label(head, text="✕", bg=BG_HEADER, fg=TEXT_MUTED,
                         font=("Segoe UI", 13), padx=17, cursor="hand2")
        close.pack(side=tk.RIGHT, fill=tk.Y)
        close.bind("<Button-1>", lambda e: self.cancel())

        # Reserve footer space before growing the content frame, so buttons
        # are not packed offscreen when the desktop is small or DPI is high.
        footer = tk.Frame(card, bg=BG_HEADER, height=66)
        footer.pack(fill=tk.X, side=tk.BOTTOM)
        footer.pack_propagate(False)
        content = tk.Frame(card, bg=BG_PANEL, padx=20, pady=15)
        content.pack(fill=tk.BOTH, expand=True)
        self._label(content, "COLOR SPECTRUM", color=TEXT_MUTED, size=8, bold=True).pack(anchor="w", pady=(0, 8))
        top = tk.Frame(content, bg=BG_PANEL)
        top.pack(fill=tk.X)
        spectrum = tk.Frame(top, bg=BG_PANEL)
        spectrum.pack(side=tk.LEFT, anchor="n")

        self.sv_canvas = tk.Canvas(spectrum, width=self.SV_WIDTH, height=self.SV_HEIGHT,
                                   bg=BG_INPUT, highlightthickness=1,
                                   highlightbackground=BG_BORDER, cursor="crosshair", bd=0)
        self.sv_canvas.pack(side=tk.LEFT)
        self.sv_canvas.bind("<Button-1>", self._on_sv_drag)
        self.sv_canvas.bind("<B1-Motion>", self._on_sv_drag)
        self._sv_image = tk.PhotoImage(width=self.SV_WIDTH, height=self.SV_HEIGHT)
        self.sv_canvas.create_image(0, 0, image=self._sv_image, anchor="nw")

        self.hue_canvas = tk.Canvas(spectrum, width=self.HUE_WIDTH, height=self.SV_HEIGHT,
                                    bg=BG_INPUT, bd=0, highlightthickness=1,
                                    highlightbackground=BG_BORDER, cursor="sb_v_double_arrow")
        self.hue_canvas.pack(side=tk.LEFT, padx=(11, 0))
        self.hue_canvas.bind("<Button-1>", self._on_hue_drag)
        self.hue_canvas.bind("<B1-Motion>", self._on_hue_drag)
        for y in range(self.SV_HEIGHT):
            c = self.hsv_to_hex(y / max(1, self.SV_HEIGHT - 1), 1.0, 1.0)
            self.hue_canvas.create_line(0, y, self.HUE_WIDTH, y, fill=c)

        right = tk.Frame(top, bg=BG_PANEL)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(19, 0))
        self._label(right, "BEFORE", size=8, bold=True, color=TEXT_MUTED).pack(anchor="w")
        self.before_swatch = tk.Label(right, bg=self.original, height=2,
                                      highlightthickness=1, highlightbackground=BG_BORDER)
        self.before_swatch.pack(fill=tk.X, pady=(4, 7))
        self._label(right, self.original, size=9, color=TEXT_MUTED).pack(anchor="w")
        self._label(right, "AFTER", size=8, bold=True, color=TEXT_MUTED).pack(anchor="w", pady=(14, 0))
        self.after_swatch = tk.Label(right, bg=self.selected, height=2,
                                     highlightthickness=1, highlightbackground=BG_BORDER)
        self.after_swatch.pack(fill=tk.X, pady=(4, 7))
        self.after_code = self._label(right, self.selected, size=9, color=TEXT_MUTED)
        self.after_code.pack(anchor="w")
        self.brand_preview = tk.Label(right, text=" MACRO ENGINE", bg=BG_INPUT,
                                      fg=self.selected, font=("Segoe UI", 9, "bold"),
                                      pady=10, padx=5)
        self.brand_preview.pack(fill=tk.X, pady=(16, 0))

        self._label(content, "HEX AND RGB", color=TEXT_MUTED, size=8, bold=True).pack(anchor="w", pady=(17, 7))
        fields = tk.Frame(content, bg=BG_PANEL)
        fields.pack(fill=tk.X)
        self.hex_var = tk.StringVar(value=self.selected)
        self.rgb_vars = [tk.StringVar(value=str(c)) for c in self.hex_to_rgb(self.selected)]

        def field_box(title, var, width):
            part = tk.Frame(fields, bg=BG_PANEL)
            part.pack(side=tk.LEFT, padx=(0, 12))
            self._label(part, title, size=8, color=TEXT_MUTED).pack(anchor="w")
            entry = tk.Entry(part, textvariable=var, width=width, bg=BG_INPUT,
                             fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                             relief="flat", font=("Consolas", 10),
                             highlightbackground=BG_BORDER, highlightthickness=1)
            entry.pack(ipady=6, pady=(3, 0))
            return entry

        hex_entry = field_box("Hexadecimal", self.hex_var, 13)
        hex_entry.bind("<KeyRelease>", self._from_hex)
        hex_entry.bind("<Return>", self._from_hex)
        for name, var in zip(("Red", "Green", "Blue"), self.rgb_vars):
            entry = field_box(name, var, 5)
            entry.bind("<KeyRelease>", self._from_rgb)
            entry.bind("<Return>", self._from_rgb)
        self.validation_msg = self._label(content, "Drag the spectrum or enter a color value.",
                                           size=8, color=TEXT_MUTED)
        self.validation_msg.pack(anchor="w", pady=(6, 0))

        presets = tk.Frame(content, bg=BG_PANEL)
        presets.pack(fill=tk.X, pady=(11, 2))
        self._label(presets, "PRESETS", color=TEXT_MUTED, bold=True, size=8).pack(side=tk.LEFT, padx=(0, 13))
        for name, hex_value in ACCENT_PRESETS:
            button = tk.Button(presets, bg=hex_value, activebackground=hex_value,
                               width=3, height=1, relief="flat", bd=0,
                               cursor="hand2", highlightthickness=1,
                               highlightbackground=BG_BORDER,
                               command=lambda c=hex_value: self._set_color(c))
            button.pack(side=tk.LEFT, padx=(0, 9))
            ModernTooltip(button, name)

        buttons = tk.Frame(footer, bg=BG_HEADER)
        buttons.pack(side=tk.RIGHT, padx=20, pady=13)
        tk.Button(buttons, text="Cancel", command=self.cancel, bg=BG_INPUT, fg=TEXT_PRIMARY,
                  activebackground=BG_HOVER, activeforeground=TEXT_PRIMARY,
                  font=("Segoe UI", 9, "bold"), relief="flat", bd=0, cursor="hand2",
                  padx=20, pady=8).pack(side=tk.LEFT, padx=(0, 9))
        self.apply_button = tk.Button(buttons, text="Apply Accent", command=self.apply,
                                      bg=self.selected, fg=accent_text_color(self.selected),
                                      activebackground=self.selected,
                                      activeforeground=accent_text_color(self.selected),
                                      font=("Segoe UI", 9, "bold"), relief="flat", bd=0,
                                      cursor="hand2", padx=19, pady=8)
        self.apply_button.pack(side=tk.LEFT)

    def _render_sv_image(self):
        # PhotoImage is in the Python standard-library Tk distribution.
        # Updating rows is significantly smoother than thousands of Canvas items.
        hues = [colorsys.hsv_to_rgb(self.h, x / max(1, self.SV_WIDTH - 1), 1)
                for x in range(self.SV_WIDTH)]
        for y in range(self.SV_HEIGHT):
            value = 1 - y / max(1, self.SV_HEIGHT - 1)
            pixels = "{" + " ".join(self.rgb_to_hex((r * value * 255,
                                                       g * value * 255,
                                                       b * value * 255))
                                          for r, g, b in hues) + "}"
            self._sv_image.put(pixels, to=(0, y))

    def _schedule_sv_render(self):
        # Hue dragging can generate dozens of events per second. Coalesce
        # gradient rebuilds without delaying the selected-color preview.
        if self._sv_redraw_job is None:
            self._sv_redraw_job = self.window.after(35, self._flush_sv_render)

    def _flush_sv_render(self):
        self._sv_redraw_job = None
        if self.window.winfo_exists():
            self._render_sv_image()
            self._draw_cursors()

    def _draw_cursors(self):
        x = round(self.s * (self.SV_WIDTH - 1))
        y = round((1 - self.v) * (self.SV_HEIGHT - 1))
        self.sv_canvas.delete("selection")
        self.sv_canvas.create_oval(x - 6, y - 6, x + 6, y + 6,
                                   outline="#111318", width=3, tags="selection")
        self.sv_canvas.create_oval(x - 6, y - 6, x + 6, y + 6,
                                   outline="#FFFFFF", width=1, tags="selection")
        hy = round(self.h * (self.SV_HEIGHT - 1))
        self.hue_canvas.delete("selection")
        self.hue_canvas.create_line(0, hy, self.HUE_WIDTH, hy,
                                    fill="#111318", width=5, tags="selection")
        self.hue_canvas.create_line(0, hy, self.HUE_WIDTH, hy,
                                    fill="#FFFFFF", width=2, tags="selection")

    def _update_preview(self):
        self.after_swatch.configure(bg=self.selected)
        self.after_code.configure(text=self.selected)
        self.brand_preview.configure(fg=self.selected)
        text_fg = accent_text_color(self.selected)
        self.apply_button.configure(bg=self.selected, fg=text_fg,
                                    activebackground=self.selected, activeforeground=text_fg)

    def _set_color(self, value, *, from_hex=False, from_rgb=False):
        value = normalize_accent_color(value)
        old_hue = self.h
        new_h, self.s, self.v = self.hex_to_hsv(value)
        # Hue is undefined for grayscale; preserve the selected hue in that case.
        if self.s > 0:
            self.h = new_h
        self.selected = value
        self._updating_fields = True
        try:
            if not from_hex:
                self.hex_var.set(value)
            if not from_rgb:
                for var, channel in zip(self.rgb_vars, self.hex_to_rgb(value)):
                    var.set(str(channel))
        finally:
            self._updating_fields = False
        if abs(old_hue - self.h) > 0.000001:
            self._schedule_sv_render()
        self._draw_cursors()
        self._update_preview()
        self.validation_msg.configure(text="Ready to apply.", fg=TEXT_MUTED)

    def _on_sv_drag(self, event):
        self.s = max(0.0, min(1.0, event.x / max(1, self.SV_WIDTH - 1)))
        self.v = 1 - max(0.0, min(1.0, event.y / max(1, self.SV_HEIGHT - 1)))
        self._set_color(self.hsv_to_hex(self.h, self.s, self.v))

    def _on_hue_drag(self, event):
        self.h = max(0.0, min(1.0, event.y / max(1, self.SV_HEIGHT - 1)))
        self._schedule_sv_render()
        self._set_color(self.hsv_to_hex(self.h, self.s, self.v))

    def _from_hex(self, event=None):
        if self._updating_fields:
            return
        try:
            value = normalize_accent_color(self.hex_var.get())
        except ValueError:
            self.validation_msg.configure(text="Use #RGB or #RRGGBB hexadecimal format.", fg=COLOR_DANGER)
            return
        self._set_color(value, from_hex=True)

    def _from_rgb(self, event=None):
        if self._updating_fields:
            return
        try:
            values = [int(v.get()) for v in self.rgb_vars]
            if any(not 0 <= value <= 255 for value in values):
                raise ValueError()
        except ValueError:
            self.validation_msg.configure(text="RGB values must be integers from 0 to 255.", fg=COLOR_DANGER)
            return
        self._set_color(self.rgb_to_hex(values), from_rgb=True)

    def _close(self):
        if self._closing:
            return False
        self._closing = True
        if self._sv_redraw_job is not None:
            try:
                self.window.after_cancel(self._sv_redraw_job)
            except tk.TclError:
                pass
            self._sv_redraw_job = None
        try:
            self.window.grab_release()
        except tk.TclError:
            pass
        if self.window.winfo_exists():
            self.window.destroy()
        self.app._accent_color_dialog = None
        return True

    def cancel(self, event=None):
        self._close()

    def apply(self):
        # Refuse to apply an incomplete/invalid value still being edited.
        # A spectrum drag/preset selection always leaves valid fields.
        try:
            typed = normalize_accent_color(self.hex_var.get())
            rgb = [int(v.get()) for v in self.rgb_vars]
            if any(not 0 <= value <= 255 for value in rgb):
                raise ValueError("RGB must be 0–255")
            if typed != self.selected or self.rgb_to_hex(rgb) != self.selected:
                raise ValueError("Finish editing the color fields before applying")
        except ValueError:
            self.validation_msg.configure(text="Correct the HEX/RGB fields before applying.",
                                          fg=COLOR_DANGER)
            return
        chosen = self.selected
        if self._close():
            # Finish the modal event before Sequent rebuilds its themed widgets.
            self.app.root.after_idle(lambda: self.app.set_accent_color(chosen))


class ModernTooltip:
    """Renders a sleek Modern-themed floating micro-tooltip with hotkey badges."""
    def __init__(self, widget, text, hotkey=None, delay_ms=350):
        self.widget = widget
        # Makes pending tooltip timers discoverable during theme teardown.
        if not hasattr(widget, "_sequent_tooltips"):
            widget._sequent_tooltips = []
        widget._sequent_tooltips.append(self)
        self.text = text
        self.hotkey = hotkey
        self.delay_ms = delay_ms
        self.tip_win = None
        self._after_id = None

        self.widget.bind("<Enter>", self._on_enter, add="+")
        self.widget.bind("<Leave>", self._on_leave, add="+")
        self.widget.bind("<ButtonPress>", self._on_leave, add="+")

    def _on_enter(self, event=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _on_leave(self, event=None):
        self._cancel()
        self.hide()

    def _cancel(self):
        if self._after_id:
            try: self.widget.after_cancel(self._after_id)
            except Exception: pass
            self._after_id = None

    def hide(self):
        if self.tip_win and self.tip_win.winfo_exists():
            try: self.tip_win.destroy()
            except Exception: pass
        self.tip_win = None

    def _show(self):
        if not self.widget.winfo_exists(): return
        self.hide()

        x = self.widget.winfo_rootx() + (self.widget.winfo_width() // 2)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6

        self.tip_win = tk.Toplevel(self.widget)
        self.tip_win.overrideredirect(True)
        self.tip_win.attributes("-topmost", True)
        self.tip_win.configure(bg=BG_BORDER)

        card = tk.Frame(self.tip_win, bg=BG_HEADER, padx=8, pady=4)
        card.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        tk.Label(
            card, text=self.text, font=("Segoe UI", 8, "bold"),
            bg=BG_HEADER, fg=TEXT_PRIMARY
        ).pack(side=tk.LEFT)

        if self.hotkey:
            tk.Label(
                card, text=self.hotkey, font=("Consolas", 7, "bold"),
                bg=BG_INPUT, fg=SYNTAX_BLUE, padx=4, pady=1,
                highlightbackground=BG_BORDER, highlightthickness=1
            ).pack(side=tk.LEFT, padx=(6, 0))

        self.tip_win.update_idletasks()
        win_w = self.tip_win.winfo_width()
        final_x = max(10, x - (win_w // 2))
        self.tip_win.geometry(f"+{final_x}+{y}")

# --- Lightweight Bordered Container ---
class ModernCard(tk.Frame):
    def __init__(self, parent, bg_card=None, border_color=None, **kwargs):
        bg_card = bg_card or BG_PANEL
        border_color = border_color or BG_BORDER
        super().__init__(parent, bg=bg_card, highlightbackground=border_color, highlightthickness=1, **kwargs)

# --- Thick Custom Dark Mode Scrollbar ---
class ModernScrollBar(tk.Canvas):
    def __init__(self, parent, command=None, width=18, bg_trough=None,
                 thumb_color=None, hover_color=None, active_color=None, **kwargs):
        bg_trough = bg_trough or BG_SCROLL_TRACK
        thumb_color = thumb_color or TRACK_INACTIVE
        hover_color = hover_color or TRACK_HOVER
        active_color = active_color or TRACK_ACTIVE
        
        super().__init__(parent, width=width, bg=bg_trough, highlightthickness=0, bd=0, cursor="hand2", **kwargs)
        self.command = command
        self.w = width
        self.bg_trough = bg_trough
        self.thumb_color = thumb_color
        self.hover_color = hover_color
        self.active_color = active_color
        self.current_color = thumb_color

        self.first = 0.0
        self.last = 1.0
        self.is_dragging = False
        self.drag_start_y = 0
        self.drag_start_first = 0.0

        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _on_enter(self, event):
        if not self.is_dragging:
            self.current_color = self.hover_color
            self.draw()

    def _on_leave(self, event):
        if not self.is_dragging:
            self.current_color = self.thumb_color
            self.draw()

    def _on_release(self, event):
        self.is_dragging = False
        if 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
            self.current_color = self.hover_color
        else:
            self.current_color = self.thumb_color
        self.draw()

    def set(self, first, last):
        self.first = float(first)
        self.last = float(last)
        self.draw()

    def draw(self):
        if not self.winfo_exists(): return
        self.delete("all")
        h = self.winfo_height()
        w = self.winfo_width()
        if h <= 2 or w <= 2:
            return

        # Render scroll trough background
        self.create_rectangle(0, 0, w, h, fill=self.bg_trough, outline="")

        # Compute max scrollable span
        span = min(1.0, max(0.01, self.last - self.first))
        thumb_h = max(24, int(span * h))
        travel = max(1, h - thumb_h)
        max_first = max(0.0001, 1.0 - span)
        
        # Determine exact thumb location relative to travel to fix wheel scrolling
        first_clamped = min(self.first, max_first)
        y0 = int((first_clamped / max_first) * travel)
        y1 = y0 + thumb_h

        pad = 5
        cx = w / 2
        
        # Draw rounded modern thumb
        self.create_line(
            cx, y0 + pad, cx, y1 - pad, 
            fill=self.current_color, width=w - (pad * 2), capstyle="round"
        )

    def _on_click(self, event):
        h = self.winfo_height()
        if h <= 2: return

        span = max(0.01, self.last - self.first)
        thumb_h = max(24, int(span * h))
        travel = max(1, h - thumb_h)
        max_first = max(0.0001, 1.0 - span)
        y0 = int((self.first / max_first) * travel)
        y1 = y0 + thumb_h

        if y0 <= event.y <= y1:
            self.is_dragging = True
            self.current_color = self.active_color
            self.drag_start_y = event.y
            self.drag_start_first = self.first
            self.draw()
        else:
            # Jump thumb center to click location
            target_first = max(0.0, min(max_first, ((event.y - thumb_h / 2) / travel) * max_first))
            self.is_dragging = True
            self.current_color = self.active_color
            self.drag_start_y = event.y
            self.drag_start_first = target_first
            if self.command:
                self.command("moveto", f"{target_first:.6f}")


    def _on_drag(self, event):
        h = self.winfo_height()
        if h <= 2: return

        span = max(0.01, self.last - self.first)
        thumb_h = max(24, int(span * h))
        travel = max(1, h - thumb_h)
        max_first = max(0.0001, 1.0 - span)

        if not self.is_dragging:
            self.is_dragging = True
            self.current_color = self.active_color
            self.drag_start_y = event.y
            self.drag_start_first = self.first

        dy = event.y - self.drag_start_y
        delta_first = (dy / travel) * max_first
        new_first = max(0.0, min(max_first, self.drag_start_first + delta_first))

        if self.command:
            self.command("moveto", f"{new_first:.6f}")

            
# New actions are stored as plain dictionaries alongside legacy recorded actions.
# Editor field: (key, label, kind, initial[, choice options]).
DYNAMIC_ACTION_SPECS = {
 'Smart UI Element & Click':('ui_element',[
   ('window','Target window title contains (blank = desktop)','text',''),
   ('name','Accessible element name contains','text',''),
   ('automation_id','Automation ID (optional)','text',''),
   ('control_type','Control type e.g. ButtonControl (optional)','text',''),
   ('action','Mouse action','choice','Single Click',['Move Only','Single Click','Double Click']),
   ('ox','Horizontal offset (pixels)','int',0),('oy','Vertical offset (pixels)','int',0)]),
 'Smart Wait & Retry':('smart_wait',[
   ('kind','Watch for','choice','text',['text','window','color','image','ui_element','clipboard','file','variable']),
   ('match','Text / color / filename / window name / image path','text','Ready'),
   ('area','Screen area: x,y,width,height','area',[0,0,1200,800]),
   ('name','UI element name or variable identifier','text',''),
   ('window','UI target window (optional)','text',''),
   ('control_type','UI control type (optional)','text',''),
   ('automation_id','UI Automation ID (optional)','text',''),
   ('mode','Wait until','choice','appears',['appears','disappears']),
   ('timeout','Timeout seconds','float',15.0),
   ('interval','Polling interval seconds','float',0.5),
   ('on_error','When timed out','choice','stop',['stop','skip','goto']),
   ('fallback_label','Fallback label if goto','text','')]),
 'OCR → Variable':('ocr_extract',[
   ('area','OCR screen region: x,y,width,height','area',[0,0,1200,800]),
   ('var_name','Save output to variable','text','ocr_value'),
   ('mode','Extraction mode','choice','full',['full','regex']),
   ('pattern','Regex pattern (regex mode only)','text','Order\\s*#(\\d+)'),
   ('group','Regex capture group (0 = whole match)','int',1)]),
 'IF (Dynamic Condition)':('if_dynamic',[
   ('logic','Combine conditions','choice','ALL',['ALL','ANY']),
   ('conditions','Conditions JSON list','json',[{'kind':'variable','name':'status','op':'contains','value':'ready'}])]),
 'ELSE':('else_dynamic',[]),
 'END IF':('end_if',[]),
 'LOOP Start':('loop_start',[
   ('mode','Loop mode','choice','count',['count','while','until','for_each']),
   ('count','Repeat N times','int',3),
   ('max_iterations','Maximum iterations (safety)','int',1000),
   ('table','Table name (for_each)','text','rows'),
   ('logic','Combine conditions','choice','ALL',['ALL','ANY']),
   ('conditions','While/Until conditions','json',[{'kind':'variable','name':'status','op':'!=','value':'done'}])]),
 'LOOP End':('loop_end',[]),
 'Find Image & Click':('find_image',[
   ('path','Template image (PNG/JPG)','path',''),
   ('area','Screen area: x,y,width,height','area',[0,0,1200,800]),
   ('confidence','Match confidence (0–1)','float',0.85),
   ('action','Mouse action','choice','Single Click',['Move Only','Single Click','Double Click']),
   ('ox','Horizontal offset (pixels)','int',0),('oy','Vertical offset (pixels)','int',0)]),
 'Recovery Policy':('recovery',[
   ('on_error','When an action fails','choice','stop',['stop','skip','goto']),
   ('retry_count','Retries before failure (0–20)','int',2),
   ('retry_delay','Delay between retries (seconds)','float',0.5),
   ('fallback_label','Fallback label for goto','text','recovery'),
   ('screenshot','Save screenshot on final failure','bool',True)]),
 'Load CSV / Excel':('data_load',[
   ('path','Input CSV or XLSX file','path',''),
   ('table','Table identifier','text','rows'),
   ('sheet','Excel sheet name (blank = first)','text','')]),
 'Update Table Cell':('data_set',[
   ('table','Loaded table name','text','rows'),
   ('column','Column to update/create','text','result'),
   ('value','Value (supports ${row.Column} variables)','text','${_last_value}')]),
 'Export CSV / Excel':('data_save',[
   ('table','Loaded table name','text','rows'),
   ('path','Output CSV or XLSX file','path','results.csv')]),
 'Auto-start Trigger':('trigger_start',[
   ('kind','Trigger when a new event occurs','choice','file',['file','window','clipboard','webhook']),
   ('value','File path/glob, window title, clipboard text, or /webhook path','text',''),
   ('port','Local webhook port (webhook mode only)','int',8765),
   ('enabled','Enable background auto-start while idle','bool',True)]),
'Advanced Key':('key_action',[
   ('key_name','Virtual Key Name (e.g. Alt, Tab, Shift)','text','Alt'),
   ('action','Key Action','choice','Hold',['Press','Hold','Release'])])
}


# --- Sequent Dynamic Automation Engine (shared by playback, chains and debugger) ---
# Zero third-party packages: ctypes/Win32/GDI+, Windows UI Automation through
# Windows PowerShell/.NET built into Windows, and standard-library XML/ZIP/CSV.
import csv
import glob
import pathlib
import fnmatch
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- Dependency-free Windows UI Automation, screen capture and Excel helpers ---
# Python standard library + Windows components shipped with the OS only.
import ctypes.wintypes as _seq_wt
import posixpath as _seq_posixpath
import zipfile as _seq_zipfile
import xml.etree.ElementTree as _seq_et
import zlib as _seq_zlib


def sequent_native_uia(query):
    """Use Microsoft's inbox .NET UIAutomation client, via Windows PowerShell 5.1.

    This is NOT the third-party 'uiautomation' Python package. Sequent's
    existing OCR implementation already uses Windows PowerShell for WinRT.
    JSON is sent in an environment variable, not interpolated into PS source.
    """
    if os.name != 'nt':
        raise MacroExecutionError('UI Automation requires Windows')
    ps = r'''
$ErrorActionPreference = 'Stop'
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -AssemblyName WindowsBase
    $q = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:SEQUENT_UIA_QUERY)) | ConvertFrom-Json
    function Selector([System.Windows.Automation.AutomationElement]$element) {
        if ($null -eq $element) { return $null }
        try {
            $c = $element.Current
            $rect = $c.BoundingRectangle
            if ($rect.IsEmpty -or $rect.Width -le 0 -or $rect.Height -le 0) { return $null }
            return @{name=[string]$c.Name; automation_id=[string]$c.AutomationId;
                control_type=[string]$c.ControlType.ProgrammaticName;
                bounds=@([int][Math]::Round($rect.Left),[int][Math]::Round($rect.Top),
                    [int][Math]::Round($rect.Right),[int][Math]::Round($rect.Bottom))}
        } catch { return $null }
    }
    $found = $null
    if ($q.mode -eq 'point') {
        $point = New-Object System.Windows.Point ([double]$q.x),([double]$q.y)
        $found = Selector ([System.Windows.Automation.AutomationElement]::FromPoint($point))
    } else {
        if ([long]$q.hwnd -ne 0) {
            $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]([long]$q.hwnd))
        } else {
            $root = [System.Windows.Automation.AutomationElement]::RootElement
        }
        if ($null -ne $root) {
            $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
            $stack = New-Object 'System.Collections.Generic.Stack[object]'
            $stack.Push(@($root, 0))
            $visited = 0
            while ($stack.Count -gt 0 -and $visited -lt 3500) {
                $item = $stack.Pop()
                $element = [System.Windows.Automation.AutomationElement]$item[0]
                $depth = [int]$item[1]
                $visited++
                try {
                    $c = $element.Current
                    $n = [string]$c.Name
                    $aid = [string]$c.AutomationId
                    $type = ([string]$c.ControlType.ProgrammaticName).Replace('ControlType.','').Replace('Control','')
                    $wantedType = ([string]$q.control_type).Replace('ControlType.','').Replace('Control','')
                    if ((-not $q.name -or $n.IndexOf([string]$q.name,[StringComparison]::OrdinalIgnoreCase) -ge 0) -and
                        (-not $q.automation_id -or $aid.Equals([string]$q.automation_id,[StringComparison]::OrdinalIgnoreCase)) -and
                        (-not $wantedType -or $type.Equals($wantedType,[StringComparison]::OrdinalIgnoreCase))) {
                        $found = Selector $element
                        if ($null -ne $found) { break }
                    }
                    if ($depth -lt 14) {
                        # Reverse siblings for stable visual discovery order.
                        $children = New-Object 'System.Collections.Generic.List[object]'
                        $child = $walker.GetFirstChild($element)
                        while ($null -ne $child -and $children.Count -lt 1000) {
                            $children.Add($child)
                            $child = $walker.GetNextSibling($child)
                        }
                        for ($k=$children.Count-1; $k -ge 0; $k--) {
                            $stack.Push(@($children[$k], $depth+1))
                        }
                    }
                } catch { continue }
            }
        }
    }
    if ($null -eq $found) { Write-Output 'null' }
    else { ConvertTo-Json -InputObject $found -Depth 4 -Compress }
} catch {
    Write-Output (ConvertTo-Json -InputObject @{error=$_.Exception.Message} -Compress)
}
'''
    payload = dict(query)
    env = os.environ.copy()
    env['SEQUENT_UIA_QUERY'] = base64.b64encode(json.dumps(payload).encode('utf-8')).decode('ascii')
    script = base64.b64encode(ps.encode('utf-16le')).decode('ascii')
    try:
        completed = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', script],
            capture_output=True, text=True, timeout=14, env=env,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MacroExecutionError(f'Windows UI Automation failed: {exc}') from exc
    stdout = completed.stdout.strip()
    if completed.returncode or not stdout:
        raise MacroExecutionError('Windows UI Automation failed: ' +
                                  (completed.stderr.strip() or 'no response')[:350])
    try:
        response = json.loads(stdout)
    except ValueError as exc:
        raise MacroExecutionError(f'Unexpected Windows UI Automation response: {stdout[:200]}') from exc
    if response and response.get('error'):
        raise MacroExecutionError('Windows UI Automation: ' + response['error'])
    return response


def sequent_capture_bgra(x, y, width, height):
    """Capture a physical-desktop rectangle in top-down BGRA (Win32 GDI)."""
    if os.name != 'nt':
        raise MacroExecutionError('Native screen capture requires Windows')
    x, y, width, height = map(int, (x, y, width, height))
    if width <= 0 or height <= 0 or width*height > 16000000:
        raise MacroExecutionError('Invalid native capture rectangle')
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    H = ctypes.c_void_p
    user32.GetDC.argtypes = [H]; user32.GetDC.restype = H
    user32.ReleaseDC.argtypes = [H, H]; user32.ReleaseDC.restype = ctypes.c_int
    gdi32.CreateCompatibleDC.argtypes = [H]; gdi32.CreateCompatibleDC.restype = H
    gdi32.CreateCompatibleBitmap.argtypes = [H, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = H
    gdi32.SelectObject.argtypes = [H, H]; gdi32.SelectObject.restype = H
    gdi32.DeleteObject.argtypes = [H]; gdi32.DeleteObject.restype = ctypes.c_int
    gdi32.DeleteDC.argtypes = [H]; gdi32.DeleteDC.restype = ctypes.c_int
    gdi32.BitBlt.argtypes = [H, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, H, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    gdi32.BitBlt.restype = ctypes.c_int
    gdi32.GetDIBits.argtypes = [H,H,ctypes.c_uint,ctypes.c_uint,
                                H,H,ctypes.c_uint]
    gdi32.GetDIBits.restype = ctypes.c_int
    class BMI(ctypes.Structure):
        _fields_ = [('biSize',ctypes.c_uint32),('biWidth',ctypes.c_int32),
                    ('biHeight',ctypes.c_int32),('biPlanes',ctypes.c_uint16),
                    ('biBitCount',ctypes.c_uint16),('biCompression',ctypes.c_uint32),
                    ('biSizeImage',ctypes.c_uint32),('biXPelsPerMeter',ctypes.c_int32),
                    ('biYPelsPerMeter',ctypes.c_int32),('biClrUsed',ctypes.c_uint32),
                    ('biClrImportant',ctypes.c_uint32)]
    screen_dc = memory_dc = bitmap = old = None
    try:
        screen_dc = user32.GetDC(None)
        if not screen_dc: raise MacroExecutionError('GetDC failed during screenshot')
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        if not memory_dc: raise MacroExecutionError('CreateCompatibleDC failed')
        bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        if not bitmap: raise MacroExecutionError('CreateCompatibleBitmap failed')
        old = gdi32.SelectObject(memory_dc, bitmap)
        if not old: raise MacroExecutionError('SelectObject failed')
        if not gdi32.BitBlt(memory_dc, 0, 0, width, height,
                            screen_dc, x, y, 0x40CC0020):  # SRCCOPY | CAPTUREBLT
            raise MacroExecutionError('BitBlt could not capture screen')
        gdi32.SelectObject(memory_dc, old); old = None  # REQUIRED before GetDIBits
        header = BMI()
        header.biSize = ctypes.sizeof(header)
        header.biWidth, header.biHeight = width, -height
        header.biPlanes, header.biBitCount = 1, 32
        header.biSizeImage = width * height * 4
        buf = ctypes.create_string_buffer(width * height * 4)
        if gdi32.GetDIBits(memory_dc, bitmap, 0, height,
                           ctypes.cast(buf, H), ctypes.cast(ctypes.byref(header), H), 0) != height:
            raise MacroExecutionError('GetDIBits failed')
        return bytes(buf)
    finally:
        if old and memory_dc: gdi32.SelectObject(memory_dc, old)
        if bitmap: gdi32.DeleteObject(bitmap)
        if memory_dc: gdi32.DeleteDC(memory_dc)
        if screen_dc: user32.ReleaseDC(None, screen_dc)


def sequent_save_bmp(filename, pixels, width, height):
    """Write uncompressed 32-bit BMP with top-down scanlines, using struct only."""
    size = width*height*4
    if len(pixels) != size:
        raise ValueError('Expected width * height * 4 bytes of BGRA pixel data')
    with open(filename, 'wb') as f:
        f.write(struct.pack('<2sIHHI', b'BM', 54+size, 0, 0, 54))
        f.write(struct.pack('<IiiHHIIiiII', 40, width, -height, 1, 32,
                            0, size, 2835, 2835, 0, 0))
        f.write(pixels)


def sequent_load_image_bgra(filename):
    """Read PNG/JPEG/BMP using inbox Windows GDI+ without Pillow."""
    if os.name != 'nt':
        # BMP (32-bit top-down) is supported on non-Windows for offline tests.
        with open(filename,'rb') as f: data=f.read()
        if len(data)<54 or data[:2]!=b'BM':
            raise MacroExecutionError('Template decoding requires Windows GDI+ (PNG/JPG/BMP)')
        offset=struct.unpack_from('<I',data,10)[0]
        w,h,planes,bpp,compression=struct.unpack_from('<iiHHI',data,18)
        if w<=0 or not h or abs(h)*w>1000000 or planes!=1 or bpp!=32 or compression!=0:
            raise MacroExecutionError('Unsupported BMP format')
        stride=w*4
        raw=data[offset:offset+stride*abs(h)]
        if len(raw)!=stride*abs(h): raise MacroExecutionError('Truncated BMP')
        if h>0: raw=b''.join(raw[i:i+stride] for i in range((h-1)*stride,-1,-stride))
        return (w,abs(h),raw)
    gdip = ctypes.windll.gdiplus
    H = ctypes.c_void_p
    class GdiStartup(ctypes.Structure):
        _fields_=[('GdiplusVersion',ctypes.c_uint32),('DebugEventCallback',H),
                  ('SuppressBackgroundThread',ctypes.c_int32),('SuppressExternalCodecs',ctypes.c_int32)]
    class GdiRect(ctypes.Structure):
        _fields_=[('X',ctypes.c_int32),('Y',ctypes.c_int32),
                  ('Width',ctypes.c_int32),('Height',ctypes.c_int32)]
    class BitmapData(ctypes.Structure):
        _fields_=[('Width',ctypes.c_uint32),('Height',ctypes.c_uint32),
                  ('Stride',ctypes.c_int32),('PixelFormat',ctypes.c_int32),
                  ('Scan0',H),('Reserved',ctypes.c_size_t)]
    gdip.GdiplusStartup.argtypes=[ctypes.POINTER(ctypes.c_size_t),ctypes.POINTER(GdiStartup),H]
    gdip.GdiplusStartup.restype=ctypes.c_int
    gdip.GdiplusShutdown.argtypes=[ctypes.c_size_t]
    gdip.GdipCreateBitmapFromFile.argtypes=[ctypes.c_wchar_p,ctypes.POINTER(H)]
    gdip.GdipCreateBitmapFromFile.restype=ctypes.c_int
    gdip.GdipDisposeImage.argtypes=[H]
    gdip.GdipGetImageWidth.argtypes=[H,ctypes.POINTER(ctypes.c_uint32)]
    gdip.GdipGetImageHeight.argtypes=[H,ctypes.POINTER(ctypes.c_uint32)]
    gdip.GdipBitmapLockBits.argtypes=[H,ctypes.POINTER(GdiRect),ctypes.c_uint32,
                                      ctypes.c_int32,ctypes.POINTER(BitmapData)]
    gdip.GdipBitmapLockBits.restype=ctypes.c_int
    gdip.GdipBitmapUnlockBits.argtypes=[H,ctypes.POINTER(BitmapData)]
    gdip.GdipBitmapUnlockBits.restype=ctypes.c_int
    token=ctypes.c_size_t()
    options=GdiStartup(1,None,0,0)
    if gdip.GdiplusStartup(ctypes.byref(token),ctypes.byref(options),None):
        raise MacroExecutionError('GDI+ initialization failed')
    bitmap=H()
    locked=False
    try:
        if gdip.GdipCreateBitmapFromFile(os.path.abspath(filename),ctypes.byref(bitmap)):
            raise MacroExecutionError(f'Cannot open template image: {filename}')
        width,height=ctypes.c_uint32(),ctypes.c_uint32()
        if gdip.GdipGetImageWidth(bitmap,ctypes.byref(width)) or gdip.GdipGetImageHeight(bitmap,ctypes.byref(height)):
            raise MacroExecutionError('Could not get template image dimensions')
        w,h=width.value,height.value
        if not w or not h or w*h>1000000:
            raise MacroExecutionError('Template image must contain 1 to 1,000,000 pixels')
        data=BitmapData()
        rect=GdiRect(0,0,w,h)
        if gdip.GdipBitmapLockBits(bitmap,ctypes.byref(rect),1,0x26200A,ctypes.byref(data)):
            raise MacroExecutionError('Could not read template pixels')
        locked=True
        # GDI+ 32bpp ARGB is stored in memory as BGRA.
        rows=[ctypes.string_at(data.Scan0+row*data.Stride,w*4) for row in range(h)]
        return (w,h,b''.join(rows))
    finally:
        if locked: gdip.GdipBitmapUnlockBits(bitmap,ctypes.byref(data))
        if bitmap: gdip.GdipDisposeImage(bitmap)
        gdip.GdiplusShutdown(token)


def sequent_resize_bgra(pixels,width,height,scaled_width,scaled_height):
    """Nearest-neighbour scaling, including alpha, entirely in stdlib Python."""
    if (width,height)==(scaled_width,scaled_height): return pixels
    out=bytearray(scaled_width*scaled_height*4)
    for y in range(scaled_height):
        sy=(y*height//scaled_height)*width*4
        start=y*scaled_width*4
        for x in range(scaled_width):
            src=sy+(x*width//scaled_width)*4
            dst=start+x*4
            out[dst:dst+4]=pixels[src:src+4]
    return bytes(out)


def sequent_match_bgra(screen, sw, sh, template, tw, th, confidence=0.85):
    """RGB template match with a fast sampled pass and exact-position verification.

    All candidate pixel coordinates are checked. Using a coarse 3- or 5-pixel
    grid can miss small or detailed templates when their position is offset.
    Confidence = 1 - mean_absolute_rgb_difference/255, not OpenCV NCC.
    """
    import heapq
    if tw > sw or th > sh or tw < 2 or th < 2: return None
    if len(screen) != sw * sh * 4 or len(template) != tw * th * 4:
        raise ValueError('Malformed BGRA image buffer')
    pts=[]
    for gy in range(10):
        for gx in range(10):
            tx=min(tw-1,(2*gx+1)*tw//20)
            ty=min(th-1,(2*gy+1)*th//20)
            ti=(ty*tw+tx)*4
            if template[ti+3]>=100:
                pts.append((tx,ty,template[ti],template[ti+1],template[ti+2]))
    if not pts: return None
    avg=[sum(pt[i] for pt in pts)/len(pts) for i in (2,3,4)]
    distinctive=sorted(pts,key=lambda p:sum(abs(p[i]-avg[i-2]) for i in (2,3,4)),reverse=True)
    landmarks=distinctive[:10]
    if len(landmarks)<3: return None
    # First anchor may be atypical; permit a generous per-pixel difference.
    anchor_x,anchor_y,ab,ag,ar=landmarks[0]
    anchor_row_offset=anchor_y*sw*4+anchor_x*4
    offsets=[(p[1]*sw+p[0])*4 for p in landmarks[1:]]
    colors=[p[2:] for p in landmarks[1:]]
    difference_limit=(1.0-confidence)*255
    coarse_cutoff=max(42,difference_limit*1.65)
    anchor_cutoff=max(90,difference_limit*3)
    candidate_heap=[]  # keep the 32 best coarse matches (max-heap via -error)
    count=len(landmarks)
    for y in range(sh-th+1):
        row_offset=y*sw*4
        for x in range(sw-tw+1):
            base=row_offset+x*4
            anchor=base+anchor_row_offset
            rdiff=abs(screen[anchor]-ab)+abs(screen[anchor+1]-ag)+abs(screen[anchor+2]-ar)
            if rdiff>anchor_cutoff*3:continue
            total=rdiff
            for offset,(b,g,r) in zip(offsets,colors):
                i=base+offset
                total+=abs(screen[i]-b)+abs(screen[i+1]-g)+abs(screen[i+2]-r)
            score=total/(3*count)
            if score>=coarse_cutoff:continue
            if len(candidate_heap)<32:
                heapq.heappush(candidate_heap,(-score,x,y))
            elif score < -candidate_heap[0][0]:
                heapq.heapreplace(candidate_heap,(-score,x,y))
    if not candidate_heap:return None
    best=None
    for negative_score,x,y in sorted(candidate_heap,reverse=True):
        base=(y*sw+x)*4
        diff=0
        for tx,ty,b,g,r in pts:
            i=base+(ty*sw+tx)*4
            diff+=abs(screen[i]-b)+abs(screen[i+1]-g)+abs(screen[i+2]-r)
        score=diff/(3*len(pts))
        if best is None or score<best[0]:best=(score,x,y)
        if score==0:break
    if best is None or best[0]>difference_limit:return None
    return (1.0-best[0]/255.0,(best[1],best[2],best[1]+tw,best[2]+th))


_SEQ_NS_XLSX = {'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
                'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}


def sequent_xlsx_read(path,sheet_name=''):
    """Read cell values (including sparse and shared-string cells) from XLSX."""
    try:
        with _seq_zipfile.ZipFile(path,'r') as book:
            workbook=_seq_et.fromstring(book.read('xl/workbook.xml'))
            relroot=_seq_et.fromstring(book.read('xl/_rels/workbook.xml.rels'))
            rels={e.get('Id'):e.get('Target') for e in relroot}
            sheets=workbook.find('s:sheets',_SEQ_NS_XLSX)
            if sheets is None or not list(sheets): raise MacroExecutionError('Excel workbook has no worksheets')
            chosen=next((s for s in sheets if s.get('name')==sheet_name),None) if sheet_name else list(sheets)[0]
            if chosen is None:raise MacroExecutionError(f'Excel sheet {sheet_name!r} not found')
            rid=chosen.get('{'+_SEQ_NS_XLSX['r']+'}id')
            target=rels.get(rid)
            if not target:raise MacroExecutionError('Excel worksheet relationship is missing')
            location=_seq_posixpath.normpath(target.lstrip('/') if target.startswith('/') else _seq_posixpath.join('xl',target))
            if not location.startswith('xl/') or location not in book.namelist():
                raise MacroExecutionError('Excel worksheet target is invalid')
            shared=[]
            if 'xl/sharedStrings.xml' in book.namelist():
                strings=_seq_et.fromstring(book.read('xl/sharedStrings.xml'))
                for si in strings.findall('s:si',_SEQ_NS_XLSX):
                    shared.append(''.join((e.text or '') for e in si.iter('{'+_SEQ_NS_XLSX['s']+'}t')))
            sheet=_seq_et.fromstring(book.read(location))
            sheetdata=sheet.find('s:sheetData',_SEQ_NS_XLSX)
            if sheetdata is None:return [],[]
            values=[]
            for row in sheetdata.findall('s:row',_SEQ_NS_XLSX):
                if len(values)>100000:raise MacroExecutionError('Excel table exceeds 100,000 row limit')
                data={}
                next_col=0
                for cell in row.findall('s:c',_SEQ_NS_XLSX):
                    ref=cell.get('r','')
                    letters=re.match(r'^[A-Z]+',ref.upper())
                    if letters:
                        idx=0
                        for letter in letters.group():idx=idx*26+ord(letter)-64
                        next_col=idx-1
                    value=cell.find('s:v',_SEQ_NS_XLSX)
                    typ=cell.get('t','')
                    if typ=='inlineStr':
                        inline=cell.find('s:is',_SEQ_NS_XLSX)
                        v=''.join((e.text or '') for e in inline.iter('{'+_SEQ_NS_XLSX['s']+'}t')) if inline is not None else ''
                    elif value is None:v=''
                    elif typ=='s':
                        try:v=shared[int(value.text)]
                        except (ValueError,IndexError,TypeError):v=''
                    elif typ=='b':v='TRUE' if value.text=='1' else 'FALSE'
                    else:v=value.text or ''
                    data[next_col]=v
                    next_col+=1
                values.append(data)
            if not values:return [],[]
            headers=[str(values[0].get(i,'')) for i in range(max(values[0],default=-1)+1)]
            rows=[{name:row.get(i,'') for i,name in enumerate(headers)} for row in values[1:]]
            return headers,rows
    except (_seq_zipfile.BadZipFile,KeyError,_seq_et.ParseError) as exc:
        raise MacroExecutionError(f'Cannot read XLSX workbook: {exc}') from exc


def sequent_xlsx_write(path,columns,rows):
    """Write a minimal Office Open XML workbook with strings as inlineStr."""
    def tag_text(parent,text):
        child=_seq_et.SubElement(parent,'{'+_SEQ_NS_XLSX['s']+'}t')
        child.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
        child.text=str(text if text is not None else '')
    def col_id(index):
        result=''
        while index:
            index,rem=divmod(index-1,26)
            result=chr(65+rem)+result
        return result
    ns=_SEQ_NS_XLSX['s']
    _seq_et.register_namespace('',ns)
    ws=_seq_et.Element('{'+ns+'}worksheet')
    sheet_data=_seq_et.SubElement(ws,'{'+ns+'}sheetData')
    for row_index,values in enumerate([columns]+[[row.get(c,'') for c in columns] for row in rows],1):
        element=_seq_et.SubElement(sheet_data,'{'+ns+'}row',{'r':str(row_index)})
        for col_index,value in enumerate(values,1):
            cell=_seq_et.SubElement(element,'{'+ns+'}c',{'r':col_id(col_index)+str(row_index),'t':'inlineStr'})
            inline=_seq_et.SubElement(cell,'{'+ns+'}is')
            tag_text(inline,value)
    workbook=('<workbook xmlns="'+ns+'" xmlns:r="'+_SEQ_NS_XLSX['r']+'">'
              '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
    relationships=('<?xml version="1.0" encoding="UTF-8"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                   '</Relationships>')
    root_rels=('<?xml version="1.0" encoding="UTF-8"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
               '</Relationships>')
    types=('<?xml version="1.0" encoding="UTF-8"?>'
           '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
           '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
           '<Default Extension="xml" ContentType="application/xml"/>'
           '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
           '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
           '</Types>')
    with _seq_zipfile.ZipFile(path,'w',compression=_seq_zipfile.ZIP_DEFLATED) as book:
        book.writestr('[Content_Types].xml',types)
        book.writestr('_rels/.rels',root_rels)
        book.writestr('xl/workbook.xml',workbook)
        book.writestr('xl/_rels/workbook.xml.rels',relationships)
        book.writestr('xl/worksheets/sheet1.xml',_seq_et.tostring(ws,encoding='utf-8',xml_declaration=True))

DYNAMIC_ACTION_TYPES = frozenset({
    'ui_element', 'smart_wait', 'ocr_extract', 'if_dynamic', 'else_dynamic',
    'end_if', 'loop_start', 'loop_end', 'find_image', 'recovery',
    'data_load', 'data_save', 'data_set', 'trigger_start', 'key_action',
    'search_area', 'if_var', 'label', 'goto', 'clip_fallback'
})
DYNAMIC_FLOW_TYPES = frozenset({'if_dynamic','else_dynamic','end_if','loop_start','loop_end'})


def dynamic_area(value, default=(0, 0, 1200, 800)):
    if value is None or value == '':
        value = default
    if isinstance(value, str):
        value = [x.strip() for x in value.split(',')]
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise MacroExecutionError('Screen area must contain x,y,width,height')
    try:
        x, y, w, h = (int(float(v)) for v in value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise MacroExecutionError('Invalid screen coordinates') from exc
    if w <= 0 or h <= 0 or w * h > 16000000:
        raise MacroExecutionError('Area must be positive and under 16 million pixels')
    return (x, y, w, h)


def dynamic_resolve_path(path):
    """Explicit user-selected local path; relative paths resolve beside Sequent."""
    value = os.path.expandvars(os.path.expanduser(str(path or '')))
    if not value:
        raise MacroExecutionError('A file path is required')
    if not os.path.isabs(value):
        value = os.path.join(os.path.dirname(os.path.abspath(__file__)), value)
    return os.path.abspath(value)


def dynamic_compare(actual, op, expected):
    left, right = str(actual or '').casefold(), str(expected or '').casefold()
    if op in ('==', 'equals'): return left == right
    if op in ('!=', 'not equals'): return left != right
    if op == 'contains': return right in left
    if op == 'not contains': return right not in left
    if op in ('not empty', 'exists'): return bool(left.strip())
    if op in ('empty', 'missing'): return not bool(left.strip())
    if op == 'startswith': return left.startswith(right)
    if op == 'endswith': return left.endswith(right)
    if op in ('>', '>=', '<', '<='):
        try:
            a,b = float(actual),float(expected)
            return {'>':lambda:a>b,'>=':lambda:a>=b,'<':lambda:a<b,'<=':lambda:a<=b}[op]()
        except (TypeError, ValueError): return False
    if op == 'regex':
        try: return re.search(str(expected), str(actual or ''), re.I) is not None
        except re.error as exc: raise MacroExecutionError(f'Invalid regex: {exc}') from exc
    raise MacroExecutionError(f'Unsupported comparison operator: {op}')


def dynamic_matching_block(actions, start, opening, closing, middle=None):
    """Locate same-level ELSE/END even with nested blocks."""
    depth = 0
    branch = None
    for index in range(start + 1, len(actions)):
        typ = actions[index].get('type')
        if typ == opening: depth += 1
        elif typ == closing:
            if depth == 0: return branch, index
            depth -= 1
        elif middle and typ == middle and depth == 0:
            if branch is not None:
                raise MacroExecutionError(f'Multiple {middle} branches in one block')
            branch = index
    raise MacroExecutionError(f'Unclosed {opening} block at step {start + 1}')


class SequentDynamicEngine:
    """Structured action execution and conditions; no independent Tk GUI state."""
    def __init__(self, app):
        self.app = app
        self.tables = {}
        self.policy = {'on_error':'stop', 'retry_count':0, 'retry_delay':0.4,
                       'fallback_label':'', 'screenshot':False}
        self.last_result = {}

    def interpolate(self, value):
        if not isinstance(value, str): return value
        return re.sub(r'\$\{([A-Za-z_][\w.]*)\}',
                      lambda m: str(self.app.variables.get(m.group(1), '')), value)

    def _pause(self, seconds, idle=False):
        end = time.monotonic() + max(0, float(seconds))
        while time.monotonic() < end:
            if not idle and not self.app.playing:
                raise MacroExecutionError('Execution stopped')
            if self.app.is_paused and not idle:
                frozen_at = time.monotonic()
                while self.app.is_paused and self.app.playing:
                    time.sleep(0.04)
                end += time.monotonic() - frozen_at
            time.sleep(min(0.04, max(0, end - time.monotonic())))

    def find_ui_element(self, action):
        """Detect a Windows UI Automation control without Python packages."""
        window = self.interpolate(action.get('window','')).strip()
        name = self.interpolate(action.get('name','')).strip()
        automation_id = self.interpolate(action.get('automation_id','')).strip()
        control_type = self.interpolate(action.get('control_type','')).strip()
        if not (name or automation_id or control_type):
            raise MacroExecutionError('Smart UI Element needs a name, Automation ID or control type')
        hwnd = find_window_by_title_substr(window) if window else None
        if window and not hwnd: return None
        found = sequent_native_uia({'mode':'find','hwnd':int(hwnd or 0),
                                    'name':name,'automation_id':automation_id,
                                    'control_type':control_type})
        return tuple(found['bounds']) if found else None

    def find_image(self, action):
        """Native GDI+ template read + GDI screenshot + stdlib image matching."""
        path = dynamic_resolve_path(self.interpolate(action.get('path','')))
        try:
            tw,th,template = sequent_load_image_bgra(path)
        except (OSError,ValueError) as exc:
            raise MacroExecutionError(f'Cannot read image template: {exc}') from exc
        x,y,w,h = dynamic_area(action.get('area'))
        threshold = float(action.get('confidence',0.85))
        if not 0 <= threshold <= 1:
            raise MacroExecutionError('Image confidence must be between 0 and 1')
        screen = sequent_capture_bgra(x,y,w,h)
        best=None
        for scale in (1.0,0.9,1.1,0.75,1.25):
            sw,sh=max(1,round(tw*scale)),max(1,round(th*scale))
            if sw>w or sh>h or min(sw,sh)<2:continue
            scaled=sequent_resize_bgra(template,tw,th,sw,sh)
            match=sequent_match_bgra(screen,w,h,scaled,sw,sh,threshold)
            if match is not None and (best is None or match[0]>best[0]):
                best=match
                if match[0]>=0.999:break
        if best is None:return None
        box=best[1]
        return (x+box[0],y+box[1],x+box[2],y+box[3])

    def observe(self, condition):
        kind = condition.get('kind','variable')
        value = self.interpolate(condition.get('value',''))
        area = condition.get('area',[0,0,1200,800])
        box = None
        if kind == 'variable': return str(self.app.variables.get(condition.get('name',''),'')), None
        if kind == 'text': return self.app.ocr_screen_area(*dynamic_area(area)), None
        if kind == 'color':
            box = self.app.search_color_in_area(*dynamic_area(area), value,
                                                 tolerance=int(condition.get('tolerance',8)))
            return ('found' if box else ''),box
        if kind == 'image':
            box = self.find_image(dict(condition, path=value))
            return ('found' if box else ''),box
        if kind == 'ui_element':
            box = self.find_ui_element(condition)
            return ('found' if box else ''),box
        if kind == 'window': return ('found' if find_window_by_title_substr(value) else ''),None
        if kind == 'file': return ('found' if glob.glob(dynamic_resolve_path(value)) else ''),None
        if kind == 'clipboard': return get_clipboard_text(),None
        raise MacroExecutionError(f'Unknown condition source: {kind}')

    def evaluate(self, action):
        conditions = action.get('conditions') or []
        if isinstance(conditions, str):
            try: conditions = json.loads(conditions)
            except json.JSONDecodeError as exc:
                raise MacroExecutionError(f'Invalid conditions JSON: {exc}') from exc
        if not isinstance(conditions, list) or not conditions:
            raise MacroExecutionError('IF/Loop requires at least one condition')
        results = []
        for condition in conditions:
            observed, _ = self.observe(condition)
            if str(observed).startswith('ERR:'):
                raise MacroExecutionError(f'OCR condition failed: {observed}')
            expected = self.interpolate(condition.get('value',''))
            op = condition.get('op','contains')
            if condition.get('kind') in ('window','color','image','ui_element','file'):
                op = 'not empty' if op not in ('empty','missing') else 'empty'
            results.append(dynamic_compare(observed, op, expected))
            logic = action.get('logic','ALL').upper()
            if logic == 'ALL' and not results[-1]: return False
            if logic == 'ANY' and results[-1]: return True
        return all(results) if action.get('logic','ALL').upper() == 'ALL' else any(results)

    def _save_error_screenshot(self):
        """Save a full virtual-desktop BMP via GDI without Pillow."""
        try:
            u32=ctypes.windll.user32
            left=u32.GetSystemMetrics(SM_XVIRTUALSCREEN)
            top=u32.GetSystemMetrics(SM_YVIRTUALSCREEN)
            width=u32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
            height=u32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
            folder=os.path.join(os.path.dirname(os.path.abspath(__file__)),'sequent_logs')
            os.makedirs(folder,exist_ok=True)
            filename=os.path.join(folder,f'failure_{time.strftime("%Y%m%d_%H%M%S")}.bmp')
            sequent_save_bmp(filename,sequent_capture_bgra(left,top,width,height),width,height)
        except Exception:
            pass  # The action's original exception is the important error.

    def _data_load(self, action):
        path=dynamic_resolve_path(self.interpolate(action.get('path','')))
        name=action.get('table','rows').strip() or 'rows'
        if path.lower().endswith('.xlsx'):
            _columns,rows=sequent_xlsx_read(path,action.get('sheet',''))
        else:
            with open(path,'r',encoding='utf-8-sig',newline='') as f:
                rows=list(csv.DictReader(f))
        if len(rows)>100000:raise MacroExecutionError('Table exceeds the 100,000 row safety limit')
        self.tables[name]=rows
        self.app.variables[name+'_count']=str(len(rows))
        return {'count':len(rows),'table':name}

    def _data_save(self, action):
        name=action.get('table','rows').strip() or 'rows'
        if name not in self.tables:
            raise MacroExecutionError(f'Table {name!r} has not been loaded')
        path=dynamic_resolve_path(self.interpolate(action.get('path','')))
        rows=self.tables[name]
        columns=list(dict.fromkeys(k for row in rows for k in row))
        if not columns:raise MacroExecutionError('Table has no columns to write')
        if path.lower().endswith('.xlsx'):
            sequent_xlsx_write(path,columns,rows)
        else:
            with open(path,'w',encoding='utf-8-sig',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)
        return {'path':path,'count':len(rows)}

    def _click_box(self, action, box):
        if not box: raise MacroExecutionError('Target was not found')
        result = perform_color_target_action(action,box,self.app.playback_speed)
        if hasattr(self.app,'root'):
            try:self.app.root.after(0,lambda b=box:self.app.draw_highlight_box(*b,COLOR_SUCCESS,800))
            except (AttributeError,tk.TclError): pass
        if self.app.audio_cues: play_cue('match')
        return {'bounds':list(box),'mode':result[0],'x':result[1],'y':result[2]}

    def execute(self, action, *, idle=False):
        """Unified executor for all new queue actions."""
        typ = action.get('type')
        result = {}
        if typ == 'ui_element':
            result = self._click_box(action,self.find_ui_element(action))
        elif typ == 'find_image':
            result = self._click_box(action,self.find_image(action))
        elif typ == 'ocr_extract':
            raw = self.app.ocr_screen_area(*dynamic_area(action.get('area')))
            if raw.startswith('ERR:'): raise MacroExecutionError(raw)
            mode = action.get('mode','full')
            if mode == 'regex':
                try: match = re.search(self.interpolate(action.get('pattern','')),raw,re.I|re.M)
                except re.error as exc: raise MacroExecutionError(f'Invalid regex: {exc}') from exc
                if not match: raise MacroExecutionError('OCR extraction pattern not found')
                index = int(action.get('group',0))
                try: extracted = match.group(index)
                except IndexError as exc: raise MacroExecutionError('OCR regex group does not exist') from exc
            else: extracted = raw.strip()
            name = action.get('var_name','ocr_value')
            self.app.variables[name] = extracted
            result = {'value':extracted,'variable':name}
        elif typ == 'smart_wait':
            timeout = max(0,float(action.get('timeout',15)))
            interval = max(0.1,float(action.get('interval',0.5)))
            deadline = time.monotonic()+timeout
            desired = action.get('mode','appears')
            probe = {'kind':action.get('kind','text'), 'value':action.get('match',''),
                     'area':action.get('area',[0,0,1200,800]),
                     'name':action.get('name',''),'window':action.get('window',''),
                     'control_type':action.get('control_type',''),
                     'automation_id':action.get('automation_id','')}
            while True:
                seen,bounds = self.observe(probe)
                if str(seen).startswith('ERR:'): raise MacroExecutionError(seen)
                if probe['kind'] in ('text','clipboard','variable'):
                    present = self.interpolate(probe['value']).casefold() in str(seen).casefold()
                    if not probe['value']: present = bool(str(seen).strip())
                else: present = bool(seen)
                if (present and desired=='appears') or (not present and desired=='disappears'):
                    result = {'found':present,'bounds':bounds}
                    break
                if time.monotonic() >= deadline:
                    raise MacroExecutionError('Smart Wait timed out')
                self._pause(min(interval,max(0,deadline-time.monotonic())),idle=idle)
        elif typ == 'recovery':
            for key in ('on_error','retry_count','retry_delay','fallback_label','screenshot'):
                if key in action: self.policy[key] = action[key]
        elif typ == 'data_load': result = self._data_load(action)
        elif typ == 'data_save': result = self._data_save(action)
        elif typ == 'data_set':
            name = action.get('table','rows')
            index = int(self.app.variables.get(name+'_index','0'))
            table = self.tables.get(name)
            if table is None or not 0 <= index < len(table):
                raise MacroExecutionError(f'No active row for {name!r}')
            col = str(action.get('column','')).strip()
            if not col: raise MacroExecutionError('A column name is required')
            table[index][col] = self.interpolate(action.get('value',''))
            result = {'table':name,'row':index,'column':col}
        elif typ in ('trigger_start','if_dynamic','else_dynamic','end_if','loop_start','loop_end'):
            pass # Control-flow actions are interpreted by run_program.

        elif typ == 'key_action':
            key_str = self.interpolate(action.get('key_name', '')).strip().upper()
            sub_action = action.get('action', 'Hold')
            
            vk_map = {"ALT": VK_MENU, "TAB": VK_TAB, "CTRL": VK_CONTROL, 
                      "SHIFT": VK_SHIFT, "WIN": VK_LWIN}
            vk = vk_map.get(key_str)
            if not vk and len(key_str) == 1:
                vk = ctypes.windll.user32.VkKeyScanW(ord(key_str)) & 0xFF
                
            if not vk:
                raise MacroExecutionError(f"Unknown key mapping for: {key_str}")
                
            if sub_action == 'Hold':
                hold_virtual_key(vk)
            elif sub_action == 'Release':
                release_virtual_key(vk)
            else:
                press_virtual_key(vk)
            result = {'key': key_str, 'action': sub_action}
        
        else:
            raise MacroExecutionError(f'Unsupported dynamic action: {typ}')
    
        self.last_result = {'type':typ,'success':True,**result}
        self.app.variables['_last_action'] = typ
        self.app.variables['_last_success'] = 'true'
        if 'value' in result: self.app.variables['_last_value'] = str(result['value'])
        if 'bounds' in result and result['bounds']:
            self.app.variables['_last_bounds'] = json.dumps(result['bounds'])
        return self.last_result

    def run_action_with_recovery(self, action, *, idle=False):
        mode = action.get('on_error',self.policy['on_error'])
        retries = max(0,min(20,int(action.get('retry_count',self.policy['retry_count']))))
        retry_delay = float(action.get('retry_delay',self.policy['retry_delay']))
        for attempt in range(retries+1):
            try:
                if action.get('type') in DYNAMIC_ACTION_TYPES:
                    return self.execute(action,idle=idle)
                self.app.execute_single_action(action)
                return {'success':True}
            except Exception as exc:
                self.app.variables['_last_success'] = 'false'
                self.app.variables['_last_error'] = str(exc)
                if attempt < retries:
                    self._pause(retry_delay,idle=idle)
                    continue
                if action.get('screenshot',self.policy['screenshot']):
                    self._save_error_screenshot()
                if mode in ('skip','goto'):
                    return {'success':False,'error':str(exc),
                            'goto':action.get('fallback_label',self.policy['fallback_label']) if mode=='goto' else None}
                raise

    def run_program(self, actions, start=0, *, on_step=None, idle=False,
                    max_actions=None, frames=None):
        """Execute a complete workflow or exactly one debugger step with saved loop frames."""
        frames = [] if frames is None else frames
        index = start
        executed = 0
        max_steps = 50000
        while index < len(actions):
            if max_actions is not None and executed >= max_actions:
                return {'partial':True,'next_index':index,'frames':frames}
            if not idle and not self.app.playing: return {'cancelled':True}
            while not idle and self.app.is_paused and self.app.playing:
                time.sleep(0.04)
            action = actions[index]
            typ = action.get('type')
            executed += 1
            if executed > max_steps: raise MacroExecutionError('Workflow exceeded 50,000 steps')
            if on_step: on_step(index, action)
            if typ == 'if_dynamic':
                branch,end = dynamic_matching_block(actions,index,'if_dynamic','end_if','else_dynamic')
                if self.evaluate(action): index += 1
                else: index = branch+1 if branch is not None else end+1
                continue
            if typ == 'else_dynamic':
                depth = 0
                for target in range(index+1,len(actions)):
                    tag = actions[target].get('type')
                    if tag=='if_dynamic':depth+=1
                    elif tag=='end_if':
                        if depth==0: index=target+1;break
                        depth-=1
                else:raise MacroExecutionError('ELSE without END IF')
                continue
            if typ == 'end_if':index+=1;continue
            if typ == 'loop_start':
                _,end = dynamic_matching_block(actions,index,'loop_start','loop_end')
                frame = frames[-1] if frames and frames[-1]['start']==index else None
                if frame is None:
                    frame={'start':index,'end':end,'iteration':0}
                    frames.append(frame)
                mode=action.get('mode','count')
                max_iterations=max(0,min(100000,int(action.get('max_iterations',1000))))
                if mode=='count': run = frame['iteration']<min(int(action.get('count',1)),max_iterations)
                elif mode in ('while','until'):
                    match=self.evaluate(action)
                    run = (match if mode=='while' else not match) and frame['iteration']<max_iterations
                elif mode=='for_each':
                    table=action.get('table','rows')
                    rows=self.tables.get(table)
                    if rows is None:raise MacroExecutionError(f'Load table {table!r} before For Each')
                    run = frame['iteration']<min(len(rows),max_iterations)
                    if run:
                        pos=frame['iteration']
                        self.app.variables[table+'_index']=str(pos)
                        for col,value in rows[pos].items():
                            self.app.variables['row.'+str(col)]=str(value)
                else: raise MacroExecutionError(f'Unknown loop mode: {mode}')
                if not run:
                    frames.pop()
                    index=end+1
                else:
                    frame['iteration']+=1
                    index+=1
                continue
            if typ=='loop_end':
                if not frames or frames[-1]['end']!=index:
                    raise MacroExecutionError('Loop End without matching Loop Start')
                index=frames[-1]['start'];continue
            if typ=='label': index+=1;continue
            if typ=='goto':
                label = action.get('target','')
                target=next((i for i,a in enumerate(actions) if a.get('type')=='label' and a.get('name')==label),None)
                if target is None:raise MacroExecutionError(f'Unknown label: {label}')
                frames.clear();index=target+1;continue
            if typ=='if_var':
                actual=str(self.app.variables.get(action.get('var_name',''),''))
                if dynamic_compare(actual,action.get('op','=='),self.interpolate(action.get('val',''))):
                    target_type=action.get('target_type','label')
                    if target_type=='label':
                        target=next((i for i,a in enumerate(actions) if a.get('type')=='label' and a.get('name')==action.get('target')),None)
                        if target is None:raise MacroExecutionError('IF Variable label not found')
                        frames.clear();index=target+1;continue
                    return {'switch':target_type,'target':action.get('target','')}
                index+=1;continue
            if typ=='search_area':
                target=action.get('text','').strip()
                match_found = False
                
                # Check up to 5 times over 1 second to account for UI loading lag
                for _ in range(5):
                    raw=self.app.ocr_screen_area(*dynamic_area(action.get('area')))
                    if raw.startswith('ERR:'):raise MacroExecutionError(raw)
                    clean_target = "".join(ch for ch in target.casefold() if ch.isalnum())
                    clean_raw = "".join(ch for ch in raw.casefold() if ch.isalnum())
                    if ((action.get('match_any') and bool(raw.strip())) or
                        (clean_target and clean_target in clean_raw)):
                        match_found = True
                        break
                    self._pause(0.2, idle=idle)
                    
                if match_found:
                    # Use the native 'preset' router, which automatically handles the paths and .json extensions
                    return {'switch': 'preset', 'target': action.get('preset', '')}
                index+=1;continue
            
            if typ=='trigger_start':index+=1;continue
            
            if typ == 'clip_fallback':
                cb_text = get_clipboard_text().casefold()
                keywords = [k.strip().casefold() for k in action.get('keywords', []) if k.strip()]
                
                # Check if ANY of the keywords exist in the clipboard text
                match_found = any(kw in cb_text for kw in keywords)
                
                # If NONE of them are found, abort and run the preset
                if not match_found:
                    return {'switch': 'preset', 'target': action.get('preset', '')}
                
                index += 1; continue

            result=self.run_action_with_recovery(action,idle=idle)
            if result.get('goto'):
                target=next((i for i,a in enumerate(actions) if a.get('type')=='label' and a.get('name')==result['goto']),None)
                if target is None:raise MacroExecutionError('Recovery label not found')
                frames.clear();index=target+1;continue
            index+=1
            if not idle:
                # Bypass the standard global delay for raw mouse movements so they play fluidly
                if typ not in ('move', 'drag'):
                    self._pause(max(0.01, self.app.action_delay / max(0.1, self.app.playback_speed)), idle=False)
                else:
                    # Give a tiny 1ms breather to prevent locking the thread during long drags
                    self._pause(0.001, idle=False)
            
        if frames:raise MacroExecutionError('Unclosed loop in workflow')
        return {'completed':True,'next_index':len(actions),'steps':executed}

# --- VS Code-Style Playback Queue Viewer ---
class VSCodeQueueViewer(tk.Frame):
    # Categorized action catalog. Existing action type names are unchanged,
    # so recorded macros and all existing action editors remain compatible.
    ACTION_PICKER_GROUPS = (
        ("Timing & Waiting", (
            ("Delay", "Pause playback for a set duration.", "DL"),
            ("Wait Pixel", "Wait for a screen pixel to match a color.", "PX"),
            ("Wait for Text", "Wait for OCR text to appear or disappear.", "TX"),
        )),
        ("Screen & Recognition", (
            ("Search selected area", "Read an area and branch to a preset.", "OCR"),
            ("Find Color & Click", "Find a color in an area and click it.", "FC"),
            ("Find Text & Click", "Find text in an area and click it.", "FT"),
        )),
        ("Variables & Inputs", (
            ("Read Clipboard", "Capture and inspect clipboard contents.", "CB"),
            ("Clipboard Branch / Fallback", "If keywords are missing, run a preset.", "🔀"),
            ("Request Input", "Ask the user for a value at runtime.", "IN"),
            ("Type Variable", "Type the value of a saved variable.", "VAR"),
            ("Advanced Key", "Press, hold, or release a specific key.", "⌨️"),
        )),
        ("Logic & Flow", (
            ("If Variable Condition", "Branch based on a variable comparison.", "IF"),
            ("Label Marker", "Create a named location in the queue.", "LB"),
            ("Jump to Label", "Continue execution at a named label.", "GO"),
        )),
        ("Dynamic Screen & UI", (
            ("Smart UI Element & Click", "Click accessible buttons and controls.", "UI"),
            ("Smart Wait & Retry", "Wait on changes with timeout and fallback.", "SW"),
            ("OCR → Variable", "Extract screen text into named variables.", "OV"),
            ("Find Image & Click", "Find image templates by similarity.", "IM"),
        )),
        ("Logic & Recovery", (
            ("IF (Dynamic Condition)", "Combine multiple live checks (ALL/ANY).", "IF"),
            ("ELSE", "Alternative branch for the prior IF.", "EL"),
            ("END IF", "Close an IF / ELSE block.", "EI"),
            ("LOOP Start", "Repeat N times, while, until or each row.", "LP"),
            ("LOOP End", "Close the current loop.", "LE"),
            ("Recovery Policy", "Choose retry, skip, fallback and logging.", "ER"),
        )),
        ("Files & Event Automation", (
            ("Load CSV / Excel", "Read a file's rows as variables.", "DL"),
            ("Update Table Cell", "Store processed results back to a row.", "DS"),
            ("Export CSV / Excel", "Save the processed output table.", "EX"),
            ("Auto-start Trigger", "Run when a file/window/clipboard changes.", "TR"),
        )),
        ("User Interaction", (
            ("Disclaimer / Warning", "Display a message before continuing.", "!"),
        )),
    )

    def __init__(self, parent, app_instance, bg=None, **kwargs):
        bg = bg or BG_INPUT
        super().__init__(parent, bg=bg, highlightbackground=BG_BORDER, highlightthickness=1, **kwargs)
        self.app = app_instance
        self.line_count = 0
        self.hovered_line = None
        self.interactive_enabled = True
        self.sub_menu = None

        self._sub_drag_start_x = 0
        self._sub_drag_start_y = 0
        self._sub_drag_win_x = 0
        self._sub_drag_win_y = 0

        self.gutter_frame = tk.Frame(self, bg=BG_GUTTER)
        self.gutter_frame.pack(side=tk.LEFT, fill=tk.Y)

        self.gutter_margin = tk.Canvas(
            self.gutter_frame, width=22, bg=BG_GUTTER,
            highlightthickness=0, bd=0, cursor="hand2"
        )
        self.gutter_margin.pack(side=tk.LEFT, fill=tk.Y)

        self.gutter = tk.Text(
            self.gutter_frame, width=5, bg=BG_GUTTER, fg=TEXT_MUTED,
            font=("Consolas", 10), bd=0, padx=4, pady=8,
            highlightthickness=0, wrap="none", cursor="arrow",
            takefocus=0
        )
        self.gutter.tag_configure("right", justify="right")
        self.gutter.pack(side=tk.LEFT, fill=tk.Y)

        self.sep = tk.Frame(self, bg=BG_BORDER, width=1)
        self.sep.pack(side=tk.LEFT, fill=tk.Y)

        self.scrollbar = ModernScrollBar(
            self, command=self._on_scrollbar_scroll,
            width=18, bg_trough=BG_SCROLL_TRACK
        )
        self.scroll_sep = tk.Frame(self, bg=BG_BORDER, width=1)

        self.code_text = tk.Text(
            self, bg=bg, fg=TEXT_PRIMARY,
            width=1,
            font=("Consolas", 10), bd=0, padx=10, pady=8,
            highlightthickness=0, wrap="none", cursor="xterm",
            yscrollcommand=self._on_code_scroll,
            exportselection=False  # Stops external focus changes from forcing selection jumps
        )


        self.code_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.plus_btn = tk.Label(
            self.gutter_margin, text="+", font=("Segoe UI", 8, "bold"),
            bg=BG_GUTTER, fg=TEXT_MUTED, cursor="hand2", padx=0, pady=0,
            anchor="center"
        )
        self.plus_btn.bind("<Enter>", lambda e: self.plus_btn.config(fg=TEXT_ON_ACCENT, bg=COLOR_ACCENT))
        self.plus_btn.bind("<Leave>", lambda e: self.plus_btn.config(fg=TEXT_MUTED, bg=BG_GUTTER))
        self.plus_btn.bind("<Button-1>", self._on_plus_btn_click)

        self.gutter.bind("<Key>", lambda e: "break")
        self.code_text.bind("<KeyPress>", self._on_key_press)

        for w in (self.gutter_margin, self.gutter, self.code_text):
            w.bind("<Motion>", self._on_viewer_motion)
            w.bind("<Leave>", self._on_viewer_leave)
            w.bind("<MouseWheel>", self._on_mousewheel)
            w.bind("<Button-3>", self._on_right_click)

        self.gutter_margin.bind("<Button-1>", self._on_gutter_margin_click)
        self.gutter.bind("<Button-1>", self._on_gutter_click)
        self.code_text.bind("<Button-1>", self._on_code_click)
        self.bind("<MouseWheel>", self._on_mousewheel)

        self.gutter.bind("<Double-Button-1>", self._on_double_click)
        self.code_text.bind("<Double-Button-1>", self._on_double_click)

        self.code_text.bind("<<Selection>>", self._on_text_selection_changed)
        self.code_text.bind("<ButtonRelease-1>", self._on_text_selection_changed)

        self.code_text.tag_configure("active_line", background="#36394a")
        self.code_text.tag_lower("active_line")
        self.gutter.tag_configure("active_line", background=COLOR_ACCENT, foreground=TEXT_ON_ACCENT)
        self.gutter.tag_raise("active_line")


    def highlight_action(self, index):
        self.code_text.tag_remove("active_line", "1.0", "end")
        self.gutter.tag_remove("active_line", "1.0", "end")
        if index is None or index < 0 or not self.app.recorded_actions or index >= len(self.app.recorded_actions):
            return

        line_num = index + 2
        if line_num <= self.line_count:
            self.code_text.tag_add("active_line", f"{line_num}.0", f"{line_num}.end+1c")
            self.gutter.tag_add("active_line", f"{line_num}.0", f"{line_num}.end+1c")
            self.code_text.see(f"{line_num}.0")
            self.gutter.see(f"{line_num}.0")

    def redraw_breakpoints(self):
        self.gutter_margin.delete("bp_dot")
        for act_idx in self.app.breakpoints:
            line_num = act_idx + 2
            dinfo = self.gutter.dlineinfo(f"{line_num}.0")
            if dinfo:
                gx, gy, gw, gh, gbase = dinfo
                r = 4.5
                cx = 11
                cy = gy + gh // 2
                self.gutter_margin.create_oval(
                    cx - r, cy - r, cx + r, cy + r,
                    fill=COLOR_DANGER, outline="#ff7b7b", width=1, tags="bp_dot"
                )

    def _on_gutter_margin_click(self, event):
        if not self.interactive_enabled: return
        rel_y = event.y_root - self.gutter.winfo_rooty()
        idx = self.gutter.index(f"@0,{rel_y}")
        try:
            line_num = int(idx.split(".")[0])
            if line_num >= 2 and (line_num - 2) < len(self.app.recorded_actions):
                self.app.toggle_breakpoint(line_num - 2)
        except Exception:
            pass

    def _on_text_selection_changed(self, event=None):
        if getattr(self.app, "_reticle_edit_index", None) is not None:
            return  # Live preview owns the screen overlays during edit mode.
        if not self.interactive_enabled or not self.app.recorded_actions:
            return
        try:
            sel = self.code_text.tag_ranges("sel")
            if not sel:
                return
            start_line = int(self.code_text.index(sel[0]).split('.')[0])
            end_line = int(self.code_text.index(sel[1]).split('.')[0])
            end_col = int(self.code_text.index(sel[1]).split('.')[1])
            if end_col == 0 and end_line > start_line:
                end_line -= 1

            if start_line == end_line:
                return

            start_idx = max(0, start_line - 2)
            end_idx = min(len(self.app.recorded_actions) - 1, end_line - 2)
            if start_idx > end_idx:
                return

            coord_items = []
            for idx in range(start_idx, end_idx + 1):
                act = self.app.recorded_actions[idx]
                if act.get("type") in ("move", "click", "wait_pixel", "scroll", "find_color", "search_area", "wait_text"):
                    coord_items.append(act)

            if not coord_items:
                self.app.ghost_reticle.hide()
                return

            # Restrict trailing path to maximum 50 items
            coord_items = coord_items[-50:]

            n = len(coord_items)
            reticle_data = []
            area_rect = None
            color_rect = None

            for i, act in enumerate(coord_items):
                # Gradient alpha: lowest line count (oldest) = 0.22, highest line count (newest) = 0.92
                alpha = 0.85 if n == 1 else (0.22 + (0.70 * (i / (n - 1))))
                is_click = (act.get("type") in ("click", "scroll", "find_color"))
                
                if act.get("type") == "find_color":
                    area = act.get("area", [0, 0, 0, 0])
                    ox, oy = int(act.get("ox", 0)), int(act.get("oy", 0))
                    
                    found_box = self.app.search_color_in_area(*area, act.get("color", "#FFFFFF"),
                                                        tolerance=act.get("tolerance", 8))
                    
                    if found_box:
                        x1, y1, x2, y2 = found_box
                        rx, ry = color_click_target(act, found_box)
                        if i == n - 1:
                            area_rect = (area[0], area[1], area[2], area[3], alpha)
                            color_rect = (x1, y1, x2 - x1, y2 - y1, alpha)
                    else:
                        rx = area[0] + (area[2] // 2) + ox
                        ry = area[1] + (area[3] // 2) + oy
                        if i == n - 1:
                            area_rect = (area[0], area[1], area[2], area[3], alpha)
                elif act.get("type") in ("search_area", "wait_text"):
                    area = act.get("area", [0, 0, 0, 0])
                    if i == n - 1:
                        area_rect = (area[0], area[1], area[2], area[3], alpha)
                    continue 
                else:
                    rx = int(act.get("x", 0))
                    ry = int(act.get("y", 0))
                    
                reticle_data.append((rx, ry, is_click, alpha))

            self.app.ghost_reticle.show_multi(reticle_data, area_rect=area_rect, color_rect=color_rect)
        except Exception:
            pass


    def clear(self):
        self.close_sub_menu()
        self.plus_btn.place_forget()
        self.code_text.tag_remove("sel", "1.0", "end")  # Forcibly clear selections
        self.code_text.tag_remove("active_line", "1.0", "end")
        self.gutter.tag_remove("active_line", "1.0", "end")
        self.line_count = 0
        self.gutter.delete("1.0", "end")
        self.code_text.delete("1.0", "end")
        self.gutter.configure(width=5)

    def append_line(self, text, color=TEXT_PRIMARY, auto_scroll=True):
        self.line_count += 1
        needed_width = max(5, len(str(self.line_count)) + 2)
        if int(self.gutter.cget("width")) < needed_width:
            self.gutter.configure(width=needed_width)

        self.gutter.insert("end", f"{self.line_count}\n", "right")

        tag_name = f"col_{color.replace('#', '')}"
        if tag_name not in self.code_text.tag_names():
            self.code_text.tag_configure(tag_name, foreground=color)

        self.code_text.insert("end", f"{text}\n", tag_name)
        if auto_scroll:
            self.code_text.see("end")
            self.gutter.see("end")

    def set_interactive(self, enabled):
        self.interactive_enabled = enabled
        if not enabled:
            self.plus_btn.place_forget()
            self.close_sub_menu()

    def _on_key_press(self, event):
        # 1. Feature 5: In-Place Coordinate Nudge via Alt / Alt+Shift
        alt_pressed = bool(event.state & 0x20000) or bool(ctypes.windll.user32.GetAsyncKeyState(VK_MENU) & 0x8000)
        shift_pressed = bool(event.state & 0x0001) or bool(ctypes.windll.user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
        
        if alt_pressed and event.keysym in ("Up", "Down", "Left", "Right"):
            multiplier = 5 if shift_pressed else 1
            dx = (-1 if event.keysym == "Left" else (1 if event.keysym == "Right" else 0)) * multiplier
            dy = (-1 if event.keysym == "Up" else (1 if event.keysym == "Down" else 0)) * multiplier
            self.app.nudge_active_coordinate(dx, dy)
            return "break"

        # 2. Feature 2: Keyboard Scrubber Navigation Shortcuts
        if self.app._reticle_edit_index is not None and event.keysym in ("Left", "Right", "Up", "Down"):
            self.app._on_scrub_nav({"Left": "prev", "Right": "next", "Up": "up", "Down": "down"}[event.keysym])
            return "break"
        if event.keysym == "Left":
            self.app._on_scrub_nav("prev")
            return "break"
        if event.keysym == "Right":
            self.app._on_scrub_nav("next")
            return "break"
        if event.keysym == "Home":
            self.app._on_scrub_nav("home")
            return "break"
        if event.keysym == "End":
            self.app._on_scrub_nav("end")
            return "break"
        if event.keysym == "space":
            self.app._on_scrub_nav("run_step")
            return "break"

        # 3. Standard Text Shortcuts
        ctrl = bool(event.state & 0x0004 or event.state & 0x0080)
        if ctrl and event.keysym.lower() in ("c", "a"): 
            return None
            
        if event.keysym in ("Up", "Down", "Prior", "Next"): 
            return None

        # 4. Feature 7: Cleaned Line Option (Backspace/Delete Deletion)
        if event.keysym in ("Delete", "BackSpace"):
            self._handle_delete_key()
            return "break"

        return "break"
    
    def _handle_delete_key(self):
        if not self.interactive_enabled or not self.app.recorded_actions:
            return
        try:
            sel_start = self.code_text.index("sel.first")
            sel_end = self.code_text.index("sel.last")
            start_line = int(sel_start.split(".")[0])
            end_line = int(sel_end.split(".")[0])
            if sel_end.endswith(".0") and end_line > start_line:
                end_line -= 1
        except tk.TclError:
            insert_pos = self.code_text.index("insert")
            start_line = end_line = int(insert_pos.split(".")[0])

        if end_line < 2:
            return

        start_idx = max(0, start_line - 2)
        end_idx = min(len(self.app.recorded_actions) - 1, end_line - 2)
        if start_idx <= end_idx:
            self.app.delete_action_range(start_idx, end_idx)

    def _on_right_click(self, event):
        if not self.interactive_enabled or not self.app.recorded_actions:
            return

        rel_y = event.y_root - self.gutter.winfo_rooty()
        idx = self.gutter.index(f"@0,{rel_y}")
        try: line_num = int(idx.split(".")[0])
        except Exception: return

        if line_num < 1 or line_num > self.line_count:
            return

        self.code_text.mark_set("insert", f"{line_num}.0")
        menu = tk.Menu(self, tearoff=0, bg=BG_PANEL, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT, activeforeground=TEXT_PRIMARY, bd=1, relief="flat", font=("Segoe UI", 9))

        if line_num >= 2 and (line_num - 2) < len(self.app.recorded_actions):
            act_idx = line_num - 2
            act = self.app.recorded_actions[act_idx]
            act_type = act.get("type", "Action")
            is_bp = act_idx in self.app.breakpoints
            bp_label = f"Remove Breakpoint (Line {line_num})" if is_bp else f"Toggle Breakpoint (Line {line_num})"

            # Direct edit option
            menu.add_command(label=f"Edit Action '{act_type}' (Line {line_num})...", command=lambda: self.open_action_submenu(line_num, event, edit_idx=act_idx))
            menu.add_command(label=bp_label, command=lambda: self.app.toggle_breakpoint(act_idx))
            menu.add_command(label=f"Delete Action '{act_type}' (Line {line_num})", command=lambda: self.app.delete_action(act_idx))
            if act_type == "move":
                menu.add_command(label="Delete All Mouse Movements", command=self.app.remove_all_mouse_moves)
            menu.add_separator()

        menu.add_command(label=f"Insert Action at Line {line_num}...", command=lambda: self.open_action_submenu(line_num, event))
        if any(a.get("type") == "move" for a in self.app.recorded_actions):
            menu.add_command(label="Remove All Mouse Movements", command=self.app.remove_all_mouse_moves)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()


    
    def _on_scrollbar_scroll(self, *args):
        self.plus_btn.place_forget()
        self.gutter.yview(*args)
        self.code_text.yview(*args)
        self.redraw_breakpoints()

    def _on_code_scroll(self, first, last):
        self.plus_btn.place_forget()
        f, l = float(first), float(last)
        if f <= 0.001 and l >= 0.999:
            if self.scrollbar.winfo_ismapped():
                self.scrollbar.pack_forget()
                self.scroll_sep.pack_forget()
        else:
            if not self.scrollbar.winfo_ismapped():
                self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y, before=self.code_text)
                self.scroll_sep.pack(side=tk.RIGHT, fill=tk.Y, before=self.code_text)
        self.scrollbar.set(first, last)
        self.gutter.yview_moveto(first)
        self.redraw_breakpoints()

    def _on_mousewheel(self, event):
        self.plus_btn.place_forget()
        if self.scrollbar.winfo_ismapped():
            delta = int(-1 * (event.delta / 120))
            self.code_text.yview_scroll(delta, "units")
            self.gutter.yview_scroll(delta, "units")
            self.redraw_breakpoints()
        return "break"
    
    def _on_viewer_motion(self, event):
        if not self.interactive_enabled or self.line_count == 0:
            self.plus_btn.place_forget()
            return

        rel_y = event.y_root - self.gutter.winfo_rooty()
        idx = self.gutter.index(f"@0,{rel_y}")
        try: line_num = int(idx.split(".")[0])
        except Exception: return

        if line_num < 1 or line_num > self.line_count:
            self.plus_btn.place_forget()
            return

        dinfo = self.gutter.dlineinfo(f"{line_num}.0")
        if not dinfo:
            self.plus_btn.place_forget()
            return

        gx, gy, gw, gh, gbase = dinfo
        btn_size = 14
        btn_y = gy + (gh - btn_size) // 2
        btn_x = (22 - btn_size) // 2

        self.hovered_line = line_num
        self.plus_btn.place(x=btn_x, y=btn_y, width=btn_size, height=btn_size)

    def _on_viewer_leave(self, event):
        x, y = self.winfo_pointerxy()
        widget = self.winfo_containing(x, y)
        if widget != self.plus_btn and widget not in (self.gutter_margin, self.gutter, self.code_text):
            self.plus_btn.place_forget()

    def _on_gutter_click(self, event):
        if not self.interactive_enabled: return
        rel_y = event.y_root - self.gutter.winfo_rooty()
        idx = self.gutter.index(f"@0,{rel_y}")
        try:
            line_num = int(idx.split(".")[0])
            if 1 <= line_num <= self.line_count:
                self.open_action_submenu(line_num, event)
        except Exception: pass

    def _on_code_click(self, event):
        if self.sub_menu and self.sub_menu.winfo_exists():
            self.close_sub_menu()

        # Synchronize playhead & reticle to the clicked queue line
        try:
            clicked_pos = self.code_text.index(f"@{event.x},{event.y}")
            line_num = int(clicked_pos.split(".")[0])
            if line_num >= 2:
                act_idx = line_num - 2
                if 0 <= act_idx < len(self.app.recorded_actions):
                    self.app.set_active_action_index(act_idx)
        except Exception:
            pass
    
    def _on_double_click(self, event):
        if not self.interactive_enabled or not self.app.recorded_actions:
            return "break"
        try:
            if event.widget == self.gutter:
                rel_y = event.y_root - self.gutter.winfo_rooty()
                idx = self.gutter.index(f"@0,{rel_y}")
            else:
                idx = self.code_text.index(f"@{event.x},{event.y}")
            line_num = int(idx.split(".")[0])
            if line_num >= 2:
                act_idx = line_num - 2
                if 0 <= act_idx < len(self.app.recorded_actions):
                    self.open_action_submenu(line_num, event, edit_idx=act_idx)
                    return "break"
        except Exception:
            pass
        return "break"
    
    def _on_plus_btn_click(self, event):
        if self.hovered_line:
            self.open_action_submenu(self.hovered_line, event)

    def open_action_submenu(self, line_num, event, edit_idx=None):
        self.close_sub_menu()
        if not self.interactive_enabled:
            return

        type_to_label = {
            "delay": "Delay",
            "disclaimer": "Disclaimer / Warning",
            "if_var": "If Variable Condition",
            "read_clipboard": "Read Clipboard",
            "clip_fallback": "Clipboard Branch / Fallback",
            "prompt_input": "Request Input",
            "type_variable": "Type Variable",
            "search_area": "Search selected area",
            "wait_pixel": "Wait Pixel",
            "wait_text": "Wait for Text",
            "find_color": "Find Color & Click",
            "find_text": "Find Text & Click",
            "click": "Smart UI Element & Click",  # Edit any recorded click to make it adaptive.
            **{spec[0]: label for label,spec in DYNAMIC_ACTION_SPECS.items()},
            "label": "Label Marker",
            "goto": "Jump to Label",
            
        }
        edit_action = None
        if edit_idx is not None:
            if not 0 <= edit_idx < len(self.app.recorded_actions):
                return
            edit_action = self.app.recorded_actions[edit_idx]
            if edit_action.get("type") not in type_to_label:
                return
        initial_action = type_to_label.get(
            edit_action.get("type"), "Delay") if edit_action else "Delay"

        if (edit_action and edit_action.get("type") == "find_color" and
                getattr(self.app, "_reticle_edit_index", None) is not None):
            self.app.stop_reticle_edit()

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint32), ("rcMonitor", RECT),
                        ("rcWork", RECT), ("dwFlags", ctypes.c_uint32)]

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if hwnd:
            monitor = user32.MonitorFromWindow(hwnd, 2)
        else:
            cursor = POINT()
            user32.GetCursorPos(ctypes.byref(cursor))
            monitor = user32.MonitorFromPoint(cursor, 2)

        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(mi)):
            mon_x, mon_y = mi.rcWork.left, mi.rcWork.top
            mon_w = mi.rcWork.right - mi.rcWork.left
            mon_h = mi.rcWork.bottom - mi.rcWork.top
        else:
            mon_x, mon_y = 0, 0
            mon_w, mon_h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

        menu_w = min(920, max(440, mon_w - 32))
        menu_h = min(640, max(360, mon_h - 32))
        init_x = mon_x + max(0, (mon_w - menu_w) // 2)
        init_y = mon_y + max(0, (mon_h - menu_h) // 2)

        self.sub_menu = tk.Toplevel(self.app.root)
        if os.name != 'nt':
            self.sub_menu.overrideredirect(True)
        self.sub_menu.attributes("-topmost", True)
        self.sub_menu.geometry(f"{menu_w}x{menu_h}+{init_x}+{init_y}")
        self.sub_menu.configure(bg=BG_BORDER)
        if os.name == 'nt':
            self.sub_menu.minsize(min(560, menu_w), min(400, menu_h))
            self._action_editor_native_chrome = install_sequent_chrome(self.sub_menu, resizable=True)
        self.sub_menu.bind("<Escape>", lambda e: self.close_sub_menu())
        self.sub_menu.bind("<Control-f>", lambda e: self._focus_action_search())

        if getattr(self.app, "entertain_me", True):
            self.sub_menu.attributes("-alpha", 0.0)
            def fade_in(alpha=0.0):
                if not self.sub_menu or not self.sub_menu.winfo_exists():
                    return
                alpha = min(1.0, alpha + 0.18)
                self.sub_menu.attributes("-alpha", alpha)
                if alpha < 1.0:
                    self.sub_menu.after(12, lambda: fade_in(alpha))
            self.sub_menu.after(10, fade_in)

        card = tk.Frame(self.sub_menu, bg=BG_PANEL)
        card.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        def start_drag(e):
            if os.name == 'nt' and getattr(self, '_action_editor_native_chrome', None):
                begin_native_window_motion(self.sub_menu, 2)
                return
            self._sub_drag_start_x, self._sub_drag_start_y = e.x_root, e.y_root
            self._sub_drag_win_x = self.sub_menu.winfo_x()
            self._sub_drag_win_y = self.sub_menu.winfo_y()

        def do_drag(e):
            if os.name == 'nt' and getattr(self, '_action_editor_native_chrome', None):
                return
            dx = e.x_root - self._sub_drag_start_x
            dy = e.y_root - self._sub_drag_start_y
            self.sub_menu.geometry(
                f"+{self._sub_drag_win_x + dx}+{self._sub_drag_win_y + dy}")

        head = tk.Frame(card, bg=BG_HEADER, height=40, cursor="fleur")
        head.pack(fill=tk.X)
        head.pack_propagate(False)
        title_lbl = tk.Label(
            head, text="  ≡  " + ("EDIT ACTION" if edit_idx is not None else "ADD ACTION"),
            font=("Segoe UI", 10, "bold"), bg=BG_HEADER, fg=TEXT_PRIMARY,
            cursor="fleur")
        title_lbl.pack(side=tk.LEFT, padx=(9, 10))
        line_lbl = tk.Label(head, text=f"LINE {line_num}",
                            font=("Consolas", 9, "bold"), bg=BG_INPUT,
                            fg=SYNTAX_BLUE, padx=8, pady=3, cursor="fleur")
        line_lbl.pack(side=tk.LEFT)
        tk.Label(head, text="SEQUENT  /  ACTION LIBRARY",
                 font=("Segoe UI", 8), bg=BG_HEADER,
                 fg=TEXT_MUTED).pack(side=tk.LEFT, padx=20)
        close_btn = tk.Label(head, text="  ✕  ", font=("Segoe UI", 11),
                             bg=BG_HEADER, fg=TEXT_MUTED, cursor="hand2")
        close_btn.pack(side=tk.RIGHT, padx=8)
        close_btn.bind("<Button-1>", lambda e: self.close_sub_menu())
        close_btn.bind("<Enter>", lambda e: close_btn.config(fg=TEXT_PRIMARY, bg=COLOR_DANGER))
        close_btn.bind("<Leave>", lambda e: close_btn.config(fg=TEXT_MUTED, bg=BG_HEADER))
        for w in (head, title_lbl, line_lbl):
            w.bind("<Button-1>", start_drag)
            w.bind("<B1-Motion>", do_drag)

        body = tk.Frame(card, bg=BG_PANEL)
        body.pack(fill=tk.BOTH, expand=True)
        nav_width = min(285, max(175, menu_w // 3))
        nav = tk.Frame(body, bg=BG_SIDEBAR, width=nav_width)
        nav.pack(side=tk.LEFT, fill=tk.Y)
        nav.pack_propagate(False)
        tk.Frame(body, bg=BG_BORDER, width=1).pack(side=tk.LEFT, fill=tk.Y)

        tk.Label(nav, text="ACTION LIBRARY", font=("Segoe UI", 9, "bold"),
                 bg=BG_SIDEBAR, fg=TEXT_PRIMARY, anchor="w").pack(
                     fill=tk.X, padx=14, pady=(16, 3))
        tk.Label(nav, text="Choose a type, then configure it →",
                 font=("Segoe UI", 8), bg=BG_SIDEBAR,
                 fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, padx=14)

        search_shell = tk.Frame(nav, bg=BG_INPUT, highlightbackground=BG_BORDER,
                                highlightthickness=1)
        search_shell.pack(fill=tk.X, padx=12, pady=(12, 12))
        tk.Label(search_shell, text="⌕", bg=BG_INPUT, fg=TEXT_MUTED,
                 font=("Segoe UI", 13)).pack(side=tk.LEFT, padx=(8, 4))
        self._action_search_var = tk.StringVar()
        self._action_search_entry = tk.Entry(
            search_shell, textvariable=self._action_search_var,
            bg=BG_INPUT, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
            font=("Segoe UI", 9), bd=0, relief="flat")
        self._action_search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8), pady=7)
        self._action_search_entry.bind("<Return>", self._select_first_search_result)

        picker_area = tk.Frame(nav, bg=BG_SIDEBAR)
        picker_area.pack(fill=tk.BOTH, expand=True, padx=(4, 2), pady=(0, 8))
        self._action_picker_canvas = tk.Canvas(
            picker_area, bg=BG_SIDEBAR, bd=0, highlightthickness=0)
        self._action_picker_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        picker_scroll = ModernScrollBar(
            picker_area, command=self._action_picker_canvas.yview,
            width=12, bg_trough=BG_SIDEBAR)
        picker_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._action_picker_canvas.configure(yscrollcommand=picker_scroll.set)
        self._action_picker_inner = tk.Frame(self._action_picker_canvas, bg=BG_SIDEBAR)
        picker_window = self._action_picker_canvas.create_window(
            (0, 0), window=self._action_picker_inner, anchor="nw")
        self._action_picker_inner.bind(
            "<Configure>", lambda e: self._action_picker_canvas.configure(
                scrollregion=self._action_picker_canvas.bbox("all")))
        self._action_picker_canvas.bind(
            "<Configure>", lambda e: self._action_picker_canvas.itemconfigure(
                picker_window, width=e.width))

        detail = tk.Frame(body, bg=BG_PANEL)
        detail.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        detail_head = tk.Frame(detail, bg=BG_PANEL)
        detail_head.pack(fill=tk.X, padx=23, pady=(18, 12))
        self._selected_action_title = tk.Label(
            detail_head, text="", bg=BG_PANEL, fg=TEXT_PRIMARY,
            font=("Segoe UI", 15, "bold"), anchor="w")
        self._selected_action_title.pack(fill=tk.X)
        self._selected_action_desc = tk.Label(
            detail_head, text="", bg=BG_PANEL, fg=TEXT_MUTED,
            font=("Segoe UI", 9), anchor="w", justify="left",
            wraplength=max(200, menu_w - nav_width - 95))
        self._selected_action_desc.pack(fill=tk.X, pady=(5, 0))
        detail.bind('<Configure>', lambda e: self._selected_action_desc.config(
            wraplength=max(170, e.width - 65)), add='+')
        tk.Frame(detail, bg=BG_BORDER, height=1).pack(fill=tk.X, padx=23)

        editor_shell = tk.Frame(detail, bg=BG_PANEL)
        editor_shell.pack(fill=tk.BOTH, expand=True, padx=(13, 9), pady=(15, 6))
        self._action_editor_canvas = tk.Canvas(
            editor_shell, bg=BG_PANEL, bd=0, highlightthickness=0)
        self._action_editor_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        editor_scroll = ModernScrollBar(
            editor_shell, command=self._action_editor_canvas.yview,
            width=14, bg_trough=BG_PANEL)
        editor_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._action_editor_canvas.configure(yscrollcommand=editor_scroll.set)
        self.action_content_frame = tk.Frame(self._action_editor_canvas, bg=BG_PANEL,
                                             padx=10, pady=5)
        editor_win = self._action_editor_canvas.create_window(
            (0, 0), window=self.action_content_frame, anchor="nw")
        self.action_content_frame.bind(
            "<Configure>", lambda e: self._action_editor_canvas.configure(
                scrollregion=self._action_editor_canvas.bbox("all")))
        self._action_editor_canvas.bind(
            "<Configure>", lambda e: self._action_editor_canvas.itemconfigure(
                editor_win, width=e.width))

        tk.Frame(detail, bg=BG_BORDER, height=1).pack(fill=tk.X)
        footer = tk.Frame(detail, bg=BG_PANEL)
        footer.pack(fill=tk.X, padx=20, pady=10)
        tk.Label(footer, text="Changes apply only when you save the action.",
                 font=("Segoe UI", 8), bg=BG_PANEL,
                 fg=TEXT_MUTED).pack(side=tk.LEFT)
        cancel_btn = tk.Label(footer, text="  Cancel  ", font=("Segoe UI", 9, "bold"),
                              bg=BG_INPUT, fg=TEXT_PRIMARY, padx=5, pady=5,
                              highlightbackground=BG_BORDER, highlightthickness=1,
                              cursor="hand2")
        cancel_btn.pack(side=tk.RIGHT)
        cancel_btn.bind("<Button-1>", lambda e: self.close_sub_menu())

        self._selected_action_name = None
        self._action_search_var.trace_add("write", lambda *args: self._rebuild_action_picker())
        self.sub_menu.bind("<MouseWheel>", self._on_action_picker_mousewheel)
        self._switch_action_view(initial_action, line_num, edit_idx=edit_idx)
        if os.name == 'nt' and getattr(self, '_action_editor_native_chrome', None):
            self._action_editor_grips = add_native_resize_grips(self.sub_menu, BG_BORDER)
        self.sub_menu.after_idle(self.restore_action_editor_focus)


    def restore_action_editor_focus(self):
        """Bring the open action editor in front of Sequent after a picker returns.

        Screen selection temporarily hides the root and action editor. The
        main-window styling helper remaps the root asynchronously; raising
        the editor *before* that remap leaves it behind the main window.
        """
        editor = self.sub_menu
        if editor is None:
            return
        try:
            if not editor.winfo_exists():
                return
            editor.deiconify()
            editor.attributes("-topmost", True)
            editor.lift()
            editor.focus_force()
        except tk.TclError:
            # User may have closed the editor while the deferred callback ran.
            pass


    def _focus_action_search(self):
        if self.sub_menu and self.sub_menu.winfo_exists():
            self._action_search_entry.focus_set()
            self._action_search_entry.select_range(0, "end")
        return "break"

    def _select_first_search_result(self, event=None):
        matches = getattr(self, "_visible_action_names", [])
        if matches:
            self._switch_action_view(
                matches[0], self._action_menu_line_num, self._action_menu_edit_idx)
        return "break"

    def _on_action_picker_mousewheel(self, event):
        if not self.sub_menu or not self.sub_menu.winfo_exists():
            return
        # Tk sends descendant events through their toplevel bindtag. Only
        # scroll the column currently under the mouse pointer.
        if isinstance(event.widget, (tk.Text, tk.Listbox)):
            return
        if event.x_root < self._action_editor_canvas.winfo_rootx():
            canvas = self._action_picker_canvas
        else:
            canvas = self._action_editor_canvas
        if canvas.winfo_exists():
            amount = -int(event.delta / 120) if event.delta else 0
            if amount:
                canvas.yview_scroll(amount, "units")
                return "break"

    def _rebuild_action_picker(self):
        if not self.sub_menu or not self.sub_menu.winfo_exists():
            return
        search = self._action_search_var.get().strip().casefold()
        old_scroll = self._action_picker_canvas.yview()[0]
        for child in self._action_picker_inner.winfo_children():
            child.destroy()

        matches = []
        for category, actions in self.ACTION_PICKER_GROUPS:
            visible = [item for item in actions if not search or
                       search in category.casefold() or
                       search in item[0].casefold() or
                       search in item[1].casefold()]
            if not visible:
                continue
            tk.Label(self._action_picker_inner, text=category.upper(),
                     font=("Segoe UI", 8, "bold"), fg=TEXT_MUTED,
                     bg=BG_SIDEBAR, anchor="w").pack(
                         fill=tk.X, padx=14, pady=(14, 5))
            for name, description, sigil in visible:
                matches.append(name)
                selected = (name == self._selected_action_name)
                row_color = BG_SELECTED if selected else BG_SIDEBAR
                tile = tk.Frame(self._action_picker_inner, bg=row_color,
                                cursor="hand2", padx=9, pady=8)
                tile.pack(fill=tk.X, padx=(4, 8), pady=2)
                accent = tk.Frame(tile, width=3,
                                  bg=COLOR_ACCENT if selected else row_color)
                accent.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 9))
                accent.pack_propagate(False)
                tag = tk.Label(tile, text=sigil, width=3,
                               font=("Consolas", 8, "bold"),
                               bg=BG_INPUT, fg=SYNTAX_BLUE, pady=4)
                tag.pack(side=tk.LEFT, padx=(0, 8))
                names = tk.Frame(tile, bg=row_color)
                names.pack(side=tk.LEFT, fill=tk.X, expand=True)
                name_lbl = tk.Label(
                    names, text=name, font=("Segoe UI", 9, "bold"),
                    bg=row_color, fg=TEXT_PRIMARY, anchor="w")
                name_lbl.pack(fill=tk.X)
                summary = tk.Label(
                    names, text=description, font=("Segoe UI", 8),
                    bg=row_color, fg=TEXT_MUTED, anchor="w", justify="left",
                    wraplength=170)
                summary.pack(fill=tk.X, pady=(2, 0))
                for widget in (tile, accent, tag, names, name_lbl, summary):
                    widget.bind("<Button-1>",
                                lambda e, n=name: self._switch_action_view(
                                    n, self._action_menu_line_num,
                                    self._action_menu_edit_idx))
        self._visible_action_names = matches
        if not matches:
            tk.Label(self._action_picker_inner,
                     text="No matching actions.\nTry another search.",
                     font=("Segoe UI", 9), bg=BG_SIDEBAR,
                     fg=TEXT_MUTED, justify="center").pack(padx=8, pady=32)
        self._action_picker_inner.update_idletasks()
        self._action_picker_canvas.configure(
            scrollregion=self._action_picker_canvas.bbox("all"))
        self._action_picker_canvas.yview_moveto(old_scroll)

    def _switch_action_view(self, action_type, line_num, edit_idx=None):
        if not self.sub_menu or not self.sub_menu.winfo_exists():
            return
        self._action_menu_line_num = line_num
        self._action_menu_edit_idx = edit_idx
        self._selected_action_name = action_type
        descriptions = {
            name: description
            for _, actions in self.ACTION_PICKER_GROUPS
            for name, description, _ in actions
        }
        self._selected_action_title.config(text=action_type)
        self._selected_action_desc.config(text=descriptions.get(action_type, ""))
        self._rebuild_action_picker()

        for child in self.action_content_frame.winfo_children():
            child.destroy()

        renderers = {
            "Delay": self._render_delay_view,
            "Disclaimer / Warning": self._render_disclaimer_view,
            "If Variable Condition": self._render_if_var_view,
            "Read Clipboard": self._render_read_clipboard_view,
            "Clipboard Branch / Fallback": self._render_clipboard_fallback_view,
            "Request Input": self._render_request_input_view,
            "Type Variable": self._render_type_variable_view,
            "Search selected area": self._render_search_area_view,
            "Find Color & Click": self._render_find_color_view,
            "Find Text & Click": self._render_find_text_view,
            "Wait for Text": self._render_wait_text_view,
            "Wait Pixel": self._render_wait_pixel_view,
            "Label Marker": self._render_label_view,
            "Jump to Label": self._render_goto_view,
        }
        renderers.get(action_type, self._render_dynamic_action_view)(line_num, edit_idx=edit_idx)
        self._action_editor_canvas.yview_moveto(0)


    def _render_label_view(self, line_num, edit_idx=None):
        body = self.action_content_frame
        curr_act = self.app.recorded_actions[edit_idx] if (edit_idx is not None and 0 <= edit_idx < len(self.app.recorded_actions)) else None

        existing_labels = {
            a.get("name", "").strip().lower() 
            for i, a in enumerate(self.app.recorded_actions) 
            if a.get("type") == "label" and i != edit_idx
        }

        default_idx = 1
        while f"checkpoint_{default_idx}".lower() in existing_labels:
            default_idx += 1
        suggested_name = curr_act.get("name", "") if (curr_act and curr_act.get("type") == "label") else f"checkpoint_{default_idx}"

        tk.Label(body, text="Label Identifier (e.g. retry_loop):", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 4))
        
        name_entry = tk.Entry(
            body, bg=BG_INPUT, fg=SYNTAX_PURPLE,
            font=("Consolas", 9, "bold"), insertbackground=TEXT_PRIMARY,
            bd=0, highlightbackground=BG_BORDER, highlightthickness=1
        )
        name_entry.pack(fill=tk.X, ipady=3, pady=(0, 4))
        name_entry.insert(0, suggested_name)
        name_entry.focus_set()
        name_entry.select_range(0, "end")

        lbl_error = tk.Label(body, text="", font=("Segoe UI", 8), bg=BG_PANEL, fg=COLOR_DANGER, anchor="w")
        lbl_error.pack(fill=tk.X, pady=(0, 6))

        def clear_error(event=None):
            name_entry.config(highlightbackground=BG_BORDER)
            lbl_error.config(text="")

        name_entry.bind("<Key>", clear_error)

        def submit():
            name = name_entry.get().strip()
            if not name:
                name_entry.config(highlightbackground=COLOR_DANGER)
                lbl_error.config(text="Label name cannot be empty.")
                return

            current_existing = {
                a.get("name", "").strip().lower() 
                for i, a in enumerate(self.app.recorded_actions) 
                if a.get("type") == "label" and i != edit_idx
            }

            if name.lower() in current_existing:
                name_entry.config(highlightbackground=COLOR_DANGER)
                lbl_error.config(text=f"Label '{name}' already exists!")
                self.app.update_status(f"Duplicate label '{name}' rejected", COLOR_DANGER)
                if self.app.audio_cues:
                    play_cue("error")
                return

            if edit_idx is not None:
                self.app.recorded_actions[edit_idx] = {"type": "label", "name": name}
                self.app.display_actions_in_queue(self.app.recorded_actions)
                self.app.set_active_action_index(edit_idx)
                self.app.update_status(f"Updated label '{name}'", COLOR_SUCCESS)
            else:
                idx = max(0, min(line_num - 1, len(self.app.recorded_actions)))
                self.app.recorded_actions.insert(idx, {"type": "label", "name": name})
                self.app.display_actions_in_queue(self.app.recorded_actions)
                self.app.update_status(f"Added label '{name}'", COLOR_SUCCESS)
            self.close_sub_menu()

        name_entry.bind("<Return>", lambda e: submit())
        btn_txt = "Save Changes" if edit_idx is not None else "Add Label Marker"
        ModernButton(body, text=btn_txt, bg_color=COLOR_SUCCESS if edit_idx is not None else COLOR_ACCENT, hover_color="#1d8a4b" if edit_idx is not None else COLOR_ACCENT_HOV, command=submit).pack(fill=tk.X)


    def _render_goto_view(self, line_num, edit_idx=None):
        body = self.action_content_frame
        curr_act = self.app.recorded_actions[edit_idx] if (edit_idx is not None and 0 <= edit_idx < len(self.app.recorded_actions)) else None
        init_target = curr_act.get("target", "") if (curr_act and curr_act.get("type") == "goto") else ""

        available_labels = [
            a.get("name", "").strip() 
            for a in self.app.recorded_actions 
            if a.get("type") == "label" and a.get("name")
        ]

        tk.Label(body, text="Target Label to Jump To:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 4))
        btn_txt = "Save Changes" if edit_idx is not None else "Add Jump Instruction"
        btn_col = COLOR_SUCCESS if edit_idx is not None else COLOR_ACCENT
        btn_hov = "#1d8a4b" if edit_idx is not None else COLOR_ACCENT_HOV

        if available_labels:
            selected_var = tk.StringVar(value=init_target if init_target in available_labels else available_labels[0])
            lbl_menu = tk.OptionMenu(body, selected_var, *available_labels)
            lbl_menu.config(
                bg=BG_INPUT, fg=SYNTAX_PURPLE, activebackground=BG_HOVER,
                font=("Consolas", 9, "bold"), highlightthickness=1, highlightbackground=BG_BORDER,
                bd=0, relief="flat", cursor="hand2"
            )
            lbl_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT, bd=1, relief="flat")
            lbl_menu.pack(fill=tk.X, pady=(0, 10))

            def submit():
                target = selected_var.get().strip()
                if not target: return
                if edit_idx is not None:
                    self.app.recorded_actions[edit_idx] = {"type": "goto", "target": target}
                    self.app.display_actions_in_queue(self.app.recorded_actions)
                    self.app.set_active_action_index(edit_idx)
                    self.app.update_status(f"Updated Jump to '{target}'", COLOR_SUCCESS)
                else:
                    idx = max(0, min(line_num - 1, len(self.app.recorded_actions)))
                    self.app.recorded_actions.insert(idx, {"type": "goto", "target": target})
                    self.app.display_actions_in_queue(self.app.recorded_actions)
                self.close_sub_menu()

            ModernButton(body, text=btn_txt, bg_color=btn_col, hover_color=btn_hov, command=submit).pack(fill=tk.X)
        else:
            target_entry = tk.Entry(
                body, bg=BG_INPUT, fg=SYNTAX_PURPLE,
                font=("Consolas", 9, "bold"), insertbackground=TEXT_PRIMARY,
                bd=0, highlightbackground=BG_BORDER, highlightthickness=1
            )
            target_entry.pack(fill=tk.X, ipady=3, pady=(0, 4))
            target_entry.insert(0, init_target or "checkpoint_1")
            target_entry.focus_set()

            lbl_note = tk.Label(body, text="// Note: No labels created in queue yet", font=("Segoe UI", 8), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w")
            lbl_note.pack(fill=tk.X, pady=(0, 6))

            def submit():
                target = target_entry.get().strip()
                if not target: return
                if edit_idx is not None:
                    self.app.recorded_actions[edit_idx] = {"type": "goto", "target": target}
                    self.app.display_actions_in_queue(self.app.recorded_actions)
                    self.app.set_active_action_index(edit_idx)
                else:
                    idx = max(0, min(line_num - 1, len(self.app.recorded_actions)))
                    self.app.recorded_actions.insert(idx, {"type": "goto", "target": target})
                    self.app.display_actions_in_queue(self.app.recorded_actions)
                self.close_sub_menu()

            target_entry.bind("<Return>", lambda e: submit())
            ModernButton(body, text=btn_txt, bg_color=btn_col, hover_color=btn_hov, command=submit).pack(fill=tk.X)


    def _render_wait_pixel_view(self, line_num, edit_idx=None):
        body = self.action_content_frame
        curr_act = self.app.recorded_actions[edit_idx] if (edit_idx is not None and 0 <= edit_idx < len(self.app.recorded_actions)) else None

        coords_row = tk.Frame(body, bg=BG_PANEL)
        coords_row.pack(fill=tk.X, pady=(0, 6))

        tk.Label(coords_row, text="X:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED).pack(side=tk.LEFT)
        x_entry = tk.Entry(coords_row, width=6, bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Consolas", 9), bd=0, highlightbackground=BG_BORDER, highlightthickness=1)
        x_entry.insert(0, str(curr_act.get("x", 0)) if (curr_act and curr_act.get("type") == "wait_pixel") else "0")
        x_entry.pack(side=tk.LEFT, padx=(2, 6))

        tk.Label(coords_row, text="Y:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED).pack(side=tk.LEFT)
        y_entry = tk.Entry(coords_row, width=6, bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Consolas", 9), bd=0, highlightbackground=BG_BORDER, highlightthickness=1)
        y_entry.insert(0, str(curr_act.get("y", 0)) if (curr_act and curr_act.get("type") == "wait_pixel") else "0")
        y_entry.pack(side=tk.LEFT, padx=(2, 6))

        tk.Label(coords_row, text="Color:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED).pack(side=tk.LEFT)
        col_entry = tk.Entry(coords_row, width=9, bg=BG_INPUT, fg=SYNTAX_GREEN, font=("Consolas", 9, "bold"), bd=0, highlightbackground=BG_BORDER, highlightthickness=1)
        col_entry.insert(0, str(curr_act.get("color", "#FFFFFF")).upper() if (curr_act and curr_act.get("type") == "wait_pixel") else "#FFFFFF")
        col_entry.pack(side=tk.LEFT, padx=(2, 0))

        def on_pixel_sampled(sx, sy, hex_color):
            x_entry.delete(0, "end"); x_entry.insert(0, str(sx))
            y_entry.delete(0, "end"); y_entry.insert(0, str(sy))
            col_entry.delete(0, "end"); col_entry.insert(0, hex_color)

        def trigger_inspect():
            if self.sub_menu: self.sub_menu.withdraw()
            self.app.start_pixel_inspection(on_pixel_sampled)

        btn_inspect = ModernButton(body, text="⌖ Inspect Desktop Pixel", bg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOV, command=trigger_inspect)
        btn_inspect.pack(fill=tk.X, pady=(0, 8))

        if edit_idx is None:
            pos_var = tk.StringVar(value=f"Before Line {line_num}")
            pos_menu = tk.OptionMenu(body, pos_var, f"Before Line {line_num}", f"After Line {line_num}")
            pos_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 8), highlightthickness=1, highlightbackground=BG_BORDER, bd=0, relief="flat")
            pos_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT)
            pos_menu.pack(fill=tk.X, pady=(0, 8))

        def submit_wait_pixel():
            try:
                px = int(x_entry.get().strip())
                py = int(y_entry.get().strip())
                color = col_entry.get().strip().upper()
                if not re.match(r"^#[0-9A-F]{6}$", color): raise ValueError
            except ValueError:
                col_entry.config(highlightbackground=COLOR_DANGER)
                return

            if edit_idx is not None:
                self.app.recorded_actions[edit_idx].update({
                    "type": "wait_pixel", "x": px, "y": py, "color": color, "timeout": 15.0
                })
                self.app.display_actions_in_queue(self.app.recorded_actions)
                self.app.set_active_action_index(edit_idx)
                self.app.update_status("Updated Wait Pixel", COLOR_SUCCESS)
            else:
                is_after = "After" in pos_var.get()
                insert_idx = 0 if line_num <= 1 else (line_num - 1 if is_after else line_num - 2)
                insert_idx = max(0, min(insert_idx, len(self.app.recorded_actions)))

                self.app.recorded_actions.insert(insert_idx, {
                    "type": "wait_pixel", "x": px, "y": py, "color": color, "timeout": 15.0
                })
                self.app.display_actions_in_queue(self.app.recorded_actions)
                self.app.set_active_action_index(insert_idx)
            self.close_sub_menu()

        btn_txt = "Save Changes" if edit_idx is not None else "Add Wait Pixel"
        ModernButton(body, text=btn_txt, bg_color=COLOR_SUCCESS, hover_color="#1d8a4b", command=submit_wait_pixel).pack(fill=tk.X)


    def _render_read_clipboard_view(self, line_num, edit_idx=None):
        body = self.action_content_frame
        curr_act = self.app.recorded_actions[edit_idx] if (edit_idx is not None and 0 <= edit_idx < len(self.app.recorded_actions)) else None

        tk.Label(body, text="Keywords to find (comma separated):", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 2))
        kw_entry = tk.Entry(body, bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Consolas", 9), insertbackground=TEXT_PRIMARY, bd=0, highlightbackground=BG_BORDER, highlightthickness=1)
        kw_entry.pack(fill=tk.X, ipady=2, pady=(0, 6))
        init_kws = ", ".join(curr_act.get("keywords", [])) if (curr_act and curr_act.get("type") == "read_clipboard") else "Please Scrap, Please Return"
        kw_entry.insert(0, init_kws)

        var_row = tk.Frame(body, bg=BG_PANEL)
        var_row.pack(fill=tk.X, pady=(0, 6))

        v_left = tk.Frame(var_row, bg=BG_PANEL)
        v_left.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        tk.Label(v_left, text="Variable Name:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 2))
        var_entry = tk.Entry(v_left, bg=BG_INPUT, fg=SYNTAX_PURPLE, font=("Consolas", 9, "bold"), insertbackground=TEXT_PRIMARY, bd=0, highlightbackground=BG_BORDER, highlightthickness=1)
        var_entry.pack(fill=tk.X, ipady=2)
        var_entry.insert(0, curr_act.get("var_name", "status") if (curr_act and curr_act.get("type") == "read_clipboard") else "status")

        v_right = tk.Frame(var_row, bg=BG_PANEL)
        v_right.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(4, 0))
        tk.Label(v_right, text="Value if Matched:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 2))
        val_entry = tk.Entry(v_right, bg=BG_INPUT, fg=SYNTAX_GREEN, font=("Consolas", 9, "bold"), insertbackground=TEXT_PRIMARY, bd=0, highlightbackground=BG_BORDER, highlightthickness=1)
        val_entry.pack(fill=tk.X, ipady=2)
        val_entry.insert(0, curr_act.get("value", "RTS") if (curr_act and curr_act.get("type") == "read_clipboard") else "RTS")

        if edit_idx is None:
            pos_var = tk.StringVar(value=f"Before Line {line_num}")
            pos_menu = tk.OptionMenu(body, pos_var, f"Before Line {line_num}", f"After Line {line_num}")
            pos_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 8), highlightthickness=1, highlightbackground=BG_BORDER, bd=0, relief="flat")
            pos_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT)
            pos_menu.pack(fill=tk.X, pady=(4, 10))

        def submit():
            raw_kws = [k.strip() for k in kw_entry.get().split(",") if k.strip()]
            v_name = var_entry.get().strip()
            v_val = val_entry.get().strip()

            if not raw_kws or not v_name:
                return

            if edit_idx is not None:
                self.app.recorded_actions[edit_idx] = {
                    "type": "read_clipboard",
                    "keywords": raw_kws,
                    "var_name": v_name,
                    "value": v_val
                }
                self.app.display_actions_in_queue(self.app.recorded_actions)
                self.app.set_active_action_index(edit_idx)
                self.app.update_status(f"Updated Clipboard Check for '{v_name}'", COLOR_SUCCESS)
            else:
                is_after = "After" in pos_var.get()
                insert_idx = 0 if line_num <= 1 else (line_num - 1 if is_after else line_num - 2)
                insert_idx = max(0, min(insert_idx, len(self.app.recorded_actions)))

                self.app.recorded_actions.insert(insert_idx, {
                    "type": "read_clipboard",
                    "keywords": raw_kws,
                    "var_name": v_name,
                    "value": v_val
                })
                self.app.display_actions_in_queue(self.app.recorded_actions)
                self.app.update_status(f"Added Clipboard Check for '{v_name}'", COLOR_SUCCESS)
            self.close_sub_menu()

        btn_txt = "Save Changes" if edit_idx is not None else "Add Clipboard Search"
        ModernButton(body, text=btn_txt, bg_color=COLOR_SUCCESS if edit_idx is not None else COLOR_ACCENT, hover_color="#1d8a4b" if edit_idx is not None else COLOR_ACCENT_HOV, command=submit).pack(fill=tk.X)

    def _render_clipboard_fallback_view(self, line_num, edit_idx=None):
        body = self.action_content_frame
        curr_act = self.app.recorded_actions[edit_idx] if (edit_idx is not None and 0 <= edit_idx < len(self.app.recorded_actions)) else None

        tk.Label(body, text="Keywords to find (comma separated):", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 2))
        kw_entry = tk.Entry(body, bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Consolas", 9), insertbackground=TEXT_PRIMARY, bd=0, highlightbackground=BG_BORDER, highlightthickness=1)
        kw_entry.pack(fill=tk.X, ipady=3, pady=(0, 8))
        init_kws = ", ".join(curr_act.get("keywords", [])) if (curr_act and curr_act.get("type") == "clip_fallback") else "Success, OK, Done"
        kw_entry.insert(0, init_kws)

        tk.Label(body, text="If NONE are found, Run Preset:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 2))
        preset_names = self.app.get_preset_names()
        init_preset = curr_act.get("preset", "") if (curr_act and curr_act.get("type") == "clip_fallback") else ""
        chosen_preset = init_preset if init_preset in preset_names else (preset_names[0] if preset_names else "-- No Presets Found --")
        preset_var = tk.StringVar(value=chosen_preset)

        opt_preset = tk.OptionMenu(body, preset_var, *(preset_names if preset_names else ["-- No Presets Found --"]))
        opt_preset.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 8), highlightthickness=1, highlightbackground=BG_BORDER, bd=0, relief="flat")
        opt_preset["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT)
        opt_preset.pack(fill=tk.X, pady=(0, 8))

        if edit_idx is None:
            pos_var = tk.StringVar(value=f"Before Line {line_num}")
            pos_menu = tk.OptionMenu(body, pos_var, f"Before Line {line_num}", f"After Line {line_num}")
            pos_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 8), highlightthickness=1, highlightbackground=BG_BORDER, bd=0, relief="flat")
            pos_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT)
            pos_menu.pack(fill=tk.X, pady=(0, 10))

        def submit():
            raw_kws = [k.strip() for k in kw_entry.get().split(",") if k.strip()]
            target_preset = preset_var.get()
            if not raw_kws or target_preset == "-- No Presets Found --":
                return

            new_act = {
                "type": "clip_fallback",
                "keywords": raw_kws,
                "preset": target_preset
            }

            if edit_idx is not None:
                self.app.recorded_actions[edit_idx] = new_act
                self.app.display_actions_in_queue(self.app.recorded_actions)
                self.app.set_active_action_index(edit_idx)
                self.app.update_status("Updated Clipboard Fallback", COLOR_SUCCESS)
            else:
                is_after = "After" in pos_var.get()
                insert_idx = 0 if line_num <= 1 else (line_num - 1 if is_after else line_num - 2)
                insert_idx = max(0, min(insert_idx, len(self.app.recorded_actions)))
                self.app.recorded_actions.insert(insert_idx, new_act)
                self.app.display_actions_in_queue(self.app.recorded_actions)
                self.app.update_status("Added Clipboard Fallback", COLOR_SUCCESS)
            self.close_sub_menu()

        btn_txt = "Save Changes" if edit_idx is not None else "Add Clipboard Fallback"
        ModernButton(body, text=btn_txt, bg_color=COLOR_SUCCESS if edit_idx is not None else COLOR_ACCENT, hover_color="#1d8a4b" if edit_idx is not None else COLOR_ACCENT_HOV, command=submit).pack(fill=tk.X)

    def _render_type_variable_view(self, line_num, edit_idx=None):
        body = self.action_content_frame
        curr_act = self.app.recorded_actions[edit_idx] if (edit_idx is not None and 0 <= edit_idx < len(self.app.recorded_actions)) else None

        tk.Label(body, text="Variable Name to Type:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 2))
        var_entry = tk.Entry(body, bg=BG_INPUT, fg=SYNTAX_PURPLE, font=("Consolas", 10, "bold"), insertbackground=TEXT_PRIMARY, bd=0, highlightbackground=BG_BORDER, highlightthickness=1)
        var_entry.pack(fill=tk.X, ipady=3, pady=(0, 8))
        var_entry.insert(0, curr_act.get("var_name", "status") if (curr_act and curr_act.get("type") == "type_variable") else "status")
        var_entry.focus_set()

        if edit_idx is None:
            pos_var = tk.StringVar(value=f"Before Line {line_num}")
            pos_menu = tk.OptionMenu(body, pos_var, f"Before Line {line_num}", f"After Line {line_num}")
            pos_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 8), highlightthickness=1, highlightbackground=BG_BORDER, bd=0, relief="flat")
            pos_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT)
            pos_menu.pack(fill=tk.X, pady=(0, 10))

        def submit():
            v_name = var_entry.get().strip()
            if not v_name: return

            if edit_idx is not None:
                self.app.recorded_actions[edit_idx] = {
                    "type": "type_variable",
                    "var_name": v_name
                }
                self.app.display_actions_in_queue(self.app.recorded_actions)
                self.app.set_active_action_index(edit_idx)
                self.app.update_status(f"Updated Type Variable '{v_name}'", COLOR_SUCCESS)
            else:
                is_after = "After" in pos_var.get()
                insert_idx = 0 if line_num <= 1 else (line_num - 1 if is_after else line_num - 2)
                insert_idx = max(0, min(insert_idx, len(self.app.recorded_actions)))

                self.app.recorded_actions.insert(insert_idx, {
                    "type": "type_variable",
                    "var_name": v_name
                })
                self.app.display_actions_in_queue(self.app.recorded_actions)
                self.app.update_status(f"Added Type Variable '{v_name}'", COLOR_SUCCESS)
            self.close_sub_menu()

        var_entry.bind("<Return>", lambda e: submit())
        btn_txt = "Save Changes" if edit_idx is not None else "Add Type Variable"
        ModernButton(body, text=btn_txt, bg_color=COLOR_SUCCESS if edit_idx is not None else COLOR_ACCENT, hover_color="#1d8a4b" if edit_idx is not None else COLOR_ACCENT_HOV, command=submit).pack(fill=tk.X)


    def _render_if_var_view(self, line_num, edit_idx=None):
        body = self.action_content_frame
        curr_act = self.app.recorded_actions[edit_idx] if (edit_idx is not None and 0 <= edit_idx < len(self.app.recorded_actions)) else None

        cond_row = tk.Frame(body, bg=BG_PANEL)
        cond_row.pack(fill=tk.X, pady=(0, 4))

        v_left = tk.Frame(cond_row, bg=BG_PANEL)
        v_left.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        tk.Label(v_left, text="Variable:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 2))
        var_entry = tk.Entry(v_left, bg=BG_INPUT, fg=SYNTAX_PURPLE, font=("Consolas", 9, "bold"), insertbackground=TEXT_PRIMARY, bd=0, highlightbackground=BG_BORDER, highlightthickness=1)
        var_entry.pack(fill=tk.X, ipady=2)
        var_entry.insert(0, curr_act.get("var_name", "status") if (curr_act and curr_act.get("type") == "if_var") else "status")

        v_mid = tk.Frame(cond_row, bg=BG_PANEL)
        v_mid.pack(side=tk.LEFT, padx=2)
        tk.Label(v_mid, text="Condition:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 2))
        op_var = tk.StringVar(value=curr_act.get("op", "==") if (curr_act and curr_act.get("type") == "if_var") else "==")
        op_menu = tk.OptionMenu(v_mid, op_var, "==", "!=", "contains", "not empty")
        op_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Consolas", 8, "bold"), highlightthickness=1, highlightbackground=BG_BORDER, bd=0, relief="flat", cursor="hand2")
        op_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT, bd=1, relief="flat")
        op_menu.pack(fill=tk.X)

        v_right = tk.Frame(cond_row, bg=BG_PANEL)
        v_right.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        tk.Label(v_right, text="Match Value:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 2))
        val_entry = tk.Entry(v_right, bg=BG_INPUT, fg=SYNTAX_GREEN, font=("Consolas", 9, "bold"), insertbackground=TEXT_PRIMARY, bd=0, highlightbackground=BG_BORDER, highlightthickness=1)
        val_entry.pack(fill=tk.X, ipady=2)
        val_entry.insert(0, curr_act.get("val", "RTS") if (curr_act and curr_act.get("type") == "if_var") else "RTS")

        tk.Label(body, text="If Condition is Met, Perform Action:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(6, 2))

        init_tgt_type = curr_act.get("target_type", "preset") if (curr_act and curr_act.get("type") == "if_var") else "preset"
        init_mode = "Jump to Label" if init_tgt_type == "label" else ("Browse & Run File" if init_tgt_type == "file" else "Switch to Preset")

        action_mode_var = tk.StringVar(value=init_mode)
        mode_menu = tk.OptionMenu(body, action_mode_var, "Switch to Preset", "Browse & Run File", "Jump to Label", command=lambda m: render_target_input(m))
        mode_menu.config(bg=BG_INPUT, fg=SYNTAX_BLUE, font=("Segoe UI", 8, "bold"), highlightthickness=1, highlightbackground=BG_BORDER, bd=0, relief="flat", cursor="hand2")
        mode_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT, bd=1, relief="flat")
        mode_menu.pack(fill=tk.X, pady=(0, 6))

        target_container = tk.Frame(body, bg=BG_PANEL)
        target_container.pack(fill=tk.X, pady=(0, 6))

        target_state = {
            "type": init_tgt_type, 
            "target": curr_act.get("target", "") if (curr_act and curr_act.get("type") == "if_var") else ""
        }

        def render_target_input(mode):
            for child in target_container.winfo_children():
                child.destroy()

            if mode == "Switch to Preset":
                target_state["type"] = "preset"
                presets = self.app.get_preset_names()
                curr_target = target_state["target"] if target_state["target"] in presets else (presets[0] if presets else "-- No Presets Found --")
                p_var = tk.StringVar(value=curr_target)
                target_state["target"] = curr_target

                def on_p_select(val): target_state["target"] = val
                p_menu = tk.OptionMenu(target_container, p_var, *(presets if presets else ["-- No Presets Found --"]), command=on_p_select)
                p_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Consolas", 9, "bold"), highlightthickness=1, highlightbackground=BG_BORDER, bd=0, relief="flat", cursor="hand2")
                p_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT, bd=1, relief="flat")
                p_menu.pack(fill=tk.X)

            elif mode == "Browse & Run File":
                target_state["type"] = "file"
                f_row = tk.Frame(target_container, bg=BG_PANEL)
                f_row.pack(fill=tk.X)

                file_display = os.path.basename(target_state["target"]) if target_state["target"] else "Select macro file..."
                lbl_file = tk.Label(f_row, text=file_display, font=("Consolas", 8), bg=BG_INPUT, fg=SYNTAX_GREEN if target_state["target"] else TEXT_MUTED, anchor="w", padx=6, pady=3, highlightbackground=BG_BORDER, highlightthickness=1)
                lbl_file.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

                def browse_file():
                    self.app.root.attributes('-topmost', False)
                    try:
                        chosen = filedialog.askopenfilename(
                            parent=self.sub_menu, title="Select Macro to Execute", initialdir=self.app.presets_dir,
                            filetypes=[("JSON Macro", "*.json"), ("All Files", "*.*")]
                        )
                    finally:
                        self.app.root.attributes('-topmost', True)
                    if chosen:
                        target_state["target"] = os.path.abspath(chosen)
                        lbl_file.config(text=os.path.basename(chosen), fg=SYNTAX_GREEN)

                btn_b = tk.Label(f_row, text="Browse", font=("Segoe UI", 8, "bold"), bg=COLOR_ACCENT, fg=TEXT_ON_ACCENT, cursor="hand2", padx=8, pady=3)
                btn_b.pack(side=tk.RIGHT)
                btn_b.bind("<Button-1>", lambda e: browse_file())

            elif mode == "Jump to Label":
                target_state["type"] = "label"
                labels = [a.get("name", "").strip() for a in self.app.recorded_actions if a.get("type") == "label" and a.get("name")]
                if labels:
                    curr_lbl = target_state["target"] if target_state["target"] in labels else labels[0]
                    l_var = tk.StringVar(value=curr_lbl)
                    target_state["target"] = curr_lbl
                    def on_l_select(val): target_state["target"] = val
                    l_menu = tk.OptionMenu(target_container, l_var, *labels, command=on_l_select)
                    l_menu.config(bg=BG_INPUT, fg=SYNTAX_PURPLE, font=("Consolas", 9, "bold"), highlightthickness=1, highlightbackground=BG_BORDER, bd=0, relief="flat", cursor="hand2")
                    l_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT, bd=1, relief="flat")
                    l_menu.pack(fill=tk.X)
                else:
                    l_entry = tk.Entry(target_container, bg=BG_INPUT, fg=SYNTAX_PURPLE, font=("Consolas", 9, "bold"), insertbackground=TEXT_PRIMARY, bd=0, highlightbackground=BG_BORDER, highlightthickness=1)
                    l_entry.insert(0, target_state["target"] or "checkpoint_1")
                    l_entry.pack(fill=tk.X, ipady=3)
                    def update_tgt(e=None): target_state["target"] = l_entry.get().strip()
                    l_entry.bind("<KeyRelease>", update_tgt)
                    target_state["target"] = target_state["target"] or "checkpoint_1"

        render_target_input(init_mode)

        if edit_idx is None:
            pos_var = tk.StringVar(value=f"Before Line {line_num}")
            pos_menu = tk.OptionMenu(body, pos_var, f"Before Line {line_num}", f"After Line {line_num}")
            pos_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 8), highlightthickness=1, highlightbackground=BG_BORDER, bd=0, relief="flat")
            pos_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT)
            pos_menu.pack(fill=tk.X, pady=(2, 8))

        def submit():
            v_name = var_entry.get().strip()
            op = op_var.get()
            val = val_entry.get().strip()
            t_type = target_state["type"]
            t_val = target_state["target"]

            if not v_name or not t_val or t_val == "-- No Presets Found --":
                return

            if edit_idx is not None:
                self.app.recorded_actions[edit_idx] = {
                    "type": "if_var",
                    "var_name": v_name,
                    "op": op,
                    "val": val,
                    "target_type": t_type,
                    "target": t_val
                }
                self.app.display_actions_in_queue(self.app.recorded_actions)
                self.app.set_active_action_index(edit_idx)
                self.app.update_status(f"Updated Branch for '{v_name}'", COLOR_SUCCESS)
            else:
                is_after = "After" in pos_var.get()
                insert_idx = 0 if line_num <= 1 else (line_num - 1 if is_after else line_num - 2)
                insert_idx = max(0, min(insert_idx, len(self.app.recorded_actions)))

                self.app.recorded_actions.insert(insert_idx, {
                    "type": "if_var",
                    "var_name": v_name,
                    "op": op,
                    "val": val,
                    "target_type": t_type,
                    "target": t_val
                })
                self.app.display_actions_in_queue(self.app.recorded_actions)
                self.app.update_status(f"Added Conditional Branch for '{v_name}'", COLOR_SUCCESS)
            self.close_sub_menu()

        btn_txt = "Save Changes" if edit_idx is not None else "Add Conditional Branch"
        ModernButton(body, text=btn_txt, bg_color=COLOR_SUCCESS if edit_idx is not None else COLOR_ACCENT, hover_color="#1d8a4b" if edit_idx is not None else COLOR_ACCENT_HOV, command=submit).pack(fill=tk.X)


    def _render_request_input_view(self, line_num, edit_idx=None):
        body = self.action_content_frame
        curr_act = self.app.recorded_actions[edit_idx] if (edit_idx is not None and 0 <= edit_idx < len(self.app.recorded_actions)) else None

        tk.Label(body, text="Prompt Message for User:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 2))
        prompt_entry = tk.Entry(body, bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 9), insertbackground=TEXT_PRIMARY, bd=0, highlightbackground=BG_BORDER, highlightthickness=1)
        prompt_entry.pack(fill=tk.X, ipady=2, pady=(0, 6))
        prompt_entry.insert(0, curr_act.get("prompt", "Enter value:") if (curr_act and curr_act.get("type") == "prompt_input") else "Enter value:")
        prompt_entry.focus_set()

        var_row = tk.Frame(body, bg=BG_PANEL)
        var_row.pack(fill=tk.X, pady=(0, 6))

        v_left = tk.Frame(var_row, bg=BG_PANEL)
        v_left.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        tk.Label(v_left, text="Save to Variable:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 2))
        var_entry = tk.Entry(v_left, bg=BG_INPUT, fg=SYNTAX_PURPLE, font=("Consolas", 9, "bold"), insertbackground=TEXT_PRIMARY, bd=0, highlightbackground=BG_BORDER, highlightthickness=1)
        var_entry.pack(fill=tk.X, ipady=2)
        var_entry.insert(0, curr_act.get("var_name", "user_data") if (curr_act and curr_act.get("type") == "prompt_input") else "user_data")

        v_right = tk.Frame(var_row, bg=BG_PANEL)
        v_right.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(4, 0))
        tk.Label(v_right, text="Default (Optional):", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 2))
        def_entry = tk.Entry(v_right, bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Consolas", 9), insertbackground=TEXT_PRIMARY, bd=0, highlightbackground=BG_BORDER, highlightthickness=1)
        def_entry.pack(fill=tk.X, ipady=2)
        def_entry.insert(0, curr_act.get("default", "") if (curr_act and curr_act.get("type") == "prompt_input") else "")

        if edit_idx is None:
            pos_var = tk.StringVar(value=f"Before Line {line_num}")
            pos_menu = tk.OptionMenu(body, pos_var, f"Before Line {line_num}", f"After Line {line_num}")
            pos_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 8), highlightthickness=1, highlightbackground=BG_BORDER, bd=0, relief="flat")
            pos_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT)
            pos_menu.pack(fill=tk.X, pady=(4, 8))

        def submit():
            p_text = prompt_entry.get().strip() or "Enter value:"
            v_name = var_entry.get().strip() or "user_data"
            d_val = def_entry.get()

            if edit_idx is not None:
                self.app.recorded_actions[edit_idx] = {
                    "type": "prompt_input",
                    "prompt": p_text,
                    "var_name": v_name,
                    "default": d_val
                }
                self.app.display_actions_in_queue(self.app.recorded_actions)
                self.app.set_active_action_index(edit_idx)
                self.app.update_status(f"Updated Input for '{v_name}'", COLOR_SUCCESS)
            else:
                is_after = "After" in pos_var.get()
                insert_idx = 0 if line_num <= 1 else (line_num - 1 if is_after else line_num - 2)
                insert_idx = max(0, min(insert_idx, len(self.app.recorded_actions)))

                self.app.recorded_actions.insert(insert_idx, {
                    "type": "prompt_input",
                    "prompt": p_text,
                    "var_name": v_name,
                    "default": d_val
                })
                self.app.display_actions_in_queue(self.app.recorded_actions)
                self.app.update_status(f"Added Request Input for '{v_name}'", COLOR_SUCCESS)
            self.close_sub_menu()

        prompt_entry.bind("<Return>", lambda e: submit())
        var_entry.bind("<Return>", lambda e: submit())
        btn_txt = "Save Changes" if edit_idx is not None else "Add Request Input"
        ModernButton(body, text=btn_txt, bg_color=COLOR_SUCCESS if edit_idx is not None else COLOR_ACCENT, hover_color="#1d8a4b" if edit_idx is not None else COLOR_ACCENT_HOV, command=submit).pack(fill=tk.X)


    def _render_disclaimer_view(self, line_num, edit_idx=None):
        body = self.action_content_frame
        curr_act = self.app.recorded_actions[edit_idx] if (edit_idx is not None and 0 <= edit_idx < len(self.app.recorded_actions)) else None

        tk.Label(body, text="Warning Title:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 2))
        title_entry = tk.Entry(body, bg=BG_INPUT, fg=SYNTAX_ORANGE, font=("Segoe UI", 9, "bold"), insertbackground=TEXT_PRIMARY, bd=0, highlightbackground=BG_BORDER, highlightthickness=1)
        title_entry.pack(fill=tk.X, ipady=2, pady=(0, 6))
        title_entry.insert(0, curr_act.get("title", "Disclaimer Note") if (curr_act and curr_act.get("type") == "disclaimer") else "Disclaimer Note")
        title_entry.focus_set()

        tk.Label(body, text="Disclaimer / Warning Message:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 2))
        msg_text = tk.Text(body, height=4, bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 9), insertbackground=TEXT_PRIMARY, bd=0, highlightbackground=BG_BORDER, highlightthickness=1, wrap="word")
        msg_text.pack(fill=tk.X, pady=(0, 6))
        init_msg = curr_act.get("message", "Please make sure your target window is open, focused, and scaled to 100% before proceeding.") if (curr_act and curr_act.get("type") == "disclaimer") else "Please make sure your target window is open, focused, and scaled to 100% before proceeding."
        msg_text.insert("1.0", init_msg)

        if edit_idx is None:
            pos_var = tk.StringVar(value=f"Before Line {line_num}")
            pos_menu = tk.OptionMenu(body, pos_var, f"Before Line {line_num}", f"After Line {line_num}")
            pos_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 8), highlightthickness=1, highlightbackground=BG_BORDER, bd=0, relief="flat")
            pos_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT)
            pos_menu.pack(fill=tk.X, pady=(2, 8))

        def submit():
            t_title = title_entry.get().strip() or "Disclaimer Note"
            t_msg = msg_text.get("1.0", "end").strip() or "Proceed with execution?"

            if edit_idx is not None:
                self.app.recorded_actions[edit_idx] = {
                    "type": "disclaimer",
                    "title": t_title,
                    "message": t_msg
                }
                self.app.display_actions_in_queue(self.app.recorded_actions)
                self.app.set_active_action_index(edit_idx)
                self.app.update_status("Updated Disclaimer Warning", COLOR_SUCCESS)
            else:
                is_after = "After" in pos_var.get()
                insert_idx = 0 if line_num <= 1 else (line_num - 1 if is_after else line_num - 2)
                insert_idx = max(0, min(insert_idx, len(self.app.recorded_actions)))

                self.app.recorded_actions.insert(insert_idx, {
                    "type": "disclaimer",
                    "title": t_title,
                    "message": t_msg
                })
                self.app.display_actions_in_queue(self.app.recorded_actions)
                self.app.update_status("Added Disclaimer Warning", COLOR_SUCCESS)
            self.close_sub_menu()

        btn_txt = "Save Changes" if edit_idx is not None else "Add Disclaimer Note"
        ModernButton(body, text=btn_txt, bg_color=COLOR_SUCCESS if edit_idx is not None else COLOR_ACCENT, hover_color="#1d8a4b" if edit_idx is not None else COLOR_ACCENT_HOV, command=submit).pack(fill=tk.X)

    
    def _render_dynamic_action_view(self, line_num, edit_idx=None):
        """Consistent JSON-backed property editor for dynamic automation actions."""
        body = self.action_content_frame
        spec = DYNAMIC_ACTION_SPECS[self._selected_action_name]
        type_name, fields = spec
        old = (self.app.recorded_actions[edit_idx] if edit_idx is not None
               and 0 <= edit_idx < len(self.app.recorded_actions) else {})
        if old.get('type') != type_name: old = {}  # Clicking a recorded click opens conversion to UI Element.
        vars_ = {}

        def entry_field(key, label, kind, default, options=None):
            tk.Label(body, text=label, font=('Segoe UI',8,'bold'),
                     bg=BG_PANEL,fg=TEXT_MUTED,anchor='w').pack(fill=tk.X,pady=(7,2))
            if kind=='choice':
                val = tk.StringVar(value=str(old.get(key,default)))
                menu = tk.OptionMenu(body,val,*(options or []))
                menu.config(bg=BG_INPUT,fg=TEXT_PRIMARY,font=('Consolas',9),
                            highlightbackground=BG_BORDER, highlightthickness=1,bd=0)
                menu['menu'].config(bg=BG_INPUT,fg=TEXT_PRIMARY,activebackground=COLOR_ACCENT)
                menu.pack(fill=tk.X)
                vars_[key]=(kind,val)
            elif kind=='bool':
                val=tk.BooleanVar(value=bool(old.get(key,default)))
                tk.Checkbutton(body,variable=val,bg=BG_PANEL,fg=TEXT_PRIMARY,
                               selectcolor=BG_INPUT,activebackground=BG_PANEL).pack(anchor='w')
                vars_[key]=(kind,val)
            elif kind=='json':
                field=tk.Text(body,height=5,bg=BG_INPUT,fg=SYNTAX_GREEN,
                              font=('Consolas',9),insertbackground=TEXT_PRIMARY,
                              wrap='word',bd=0,highlightbackground=BG_BORDER,highlightthickness=1)
                raw=old.get(key,default)
                field.insert('1.0',json.dumps(raw,indent=2) if not isinstance(raw,str) else raw)
                field.pack(fill=tk.X)
                vars_[key]=(kind,field)
            else:
                widget=tk.Entry(body,bg=BG_INPUT,fg=TEXT_PRIMARY,
                                font=('Consolas',9),insertbackground=TEXT_PRIMARY,bd=0,
                                highlightbackground=BG_BORDER,highlightthickness=1)
                value=old.get(key,default)
                if kind=='area' and isinstance(value,(tuple,list)):
                    value=', '.join(str(v) for v in value)
                widget.insert(0,str(value))
                widget.pack(fill=tk.X,ipady=4)
                vars_[key]=(kind,widget)
                if kind=='path':
                    def browse_path(w=widget):
                        path=filedialog.askopenfilename(parent=self.sub_menu)
                        if path:w.delete(0,tk.END);w.insert(0,path)
                    ModernButton(body,text='Browse File...',bg_color=BG_BORDER,
                                 hover_color=BG_HOVER,command=browse_path).pack(fill=tk.X,pady=(3,0))

        def capture_last_click():
            if type_name!='ui_element':return
            selected=(self.app.recorded_actions[edit_idx] if edit_idx is not None else None)
            clicks=[a for a in self.app.recorded_actions if a.get('type')=='click']
            if selected and selected.get('type')=='click':old_click=selected
            elif clicks:old_click=clicks[-1]
            else:
                error.config(text='Record a mouse click first, then capture its element.')
                return
            try:
                info=sequent_native_uia({'mode':'point','x':int(old_click.get('x',0)),
                                          'y':int(old_click.get('y',0))})
                if not info:
                    raise MacroExecutionError('No accessible UI element at the recorded point')
                for key in ('name','automation_id','control_type'):
                    widget=vars_[key][1]
                    widget.delete(0,tk.END)
                    value=info.get(key,'')
                    if key=='control_type':
                        value=value.replace('ControlType.','')+'Control'
                    widget.insert(0,str(value))
                error.config(text='Captured native Windows accessibility selector.',fg=COLOR_SUCCESS)
            except Exception as exc:
                error.config(text=f'UI element capture failed: {exc}')

        if type_name in ('if_dynamic','loop_start'):
            tk.Label(body,text='Conditions: JSON list of checks. Use ALL or ANY logic.\n'
                         'Sources: variable, text, color, image, ui_element, window, file, clipboard.\n'
                         'Example: [{"kind":"variable","name":"status","op":"contains","value":"ready"}]',
                     bg=BG_PANEL,fg=TEXT_MUTED,font=('Segoe UI',8),wraplength=470,
                     justify='left').pack(fill=tk.X,pady=4)
        if type_name in ('else_dynamic','end_if','loop_end'):
            tk.Label(body,text='Block marker: it pairs with the nearest preceding open block.\n'
                         'Use the queue to order your IF / ELSE / END IF and LOOP / END LOOP actions.',
                     bg=BG_PANEL,fg=TEXT_MUTED,font=('Segoe UI',9),wraplength=450,
                     justify='left').pack(fill=tk.X,pady=(4,8))
        if type_name=='trigger_start':
            tk.Label(body,text='Place this as the FIRST action in the sequence to arm auto-start. '
                         'Sequent checks for a new window, changed clipboard, or a new/modified file '
                         'while idle. F9 also runs the macro manually.',
                     bg=BG_PANEL,fg=TEXT_MUTED,wraplength=460,justify='left',
                     font=('Segoe UI',8)).pack(fill=tk.X,pady=4)
        if type_name in ('smart_wait','ui_element','find_image'):
            tk.Label(body,text='Search actions can have an error policy: stop, skip, or jump to a label. '
                         'Set a preceding Recovery Policy action to configure retries for the workflow.',
                     bg=BG_PANEL,fg=TEXT_MUTED,wraplength=460,justify='left',
                     font=('Segoe UI',8)).pack(fill=tk.X,pady=4)
        for field in fields:
            entry_field(*field)
        if type_name=='ui_element':
            ModernButton(body,text='⌖ Capture Selector from Recorded Click',
                         bg_color=COLOR_ACCENT,hover_color=COLOR_ACCENT_HOV,
                         command=capture_last_click).pack(fill=tk.X,pady=(10,2))
        error=tk.Label(body,text='',bg=BG_PANEL,fg=COLOR_DANGER,
                       font=('Segoe UI',8),wraplength=470,justify='left')
        error.pack(fill=tk.X,pady=6)
        if edit_idx is None:
            pos_var=tk.StringVar(value=f'Before Line {line_num}')
            menu=tk.OptionMenu(body,pos_var,f'Before Line {line_num}',f'After Line {line_num}')
            menu.config(bg=BG_INPUT,fg=TEXT_PRIMARY,highlightthickness=1,
                        highlightbackground=BG_BORDER,bd=0)
            menu['menu'].config(bg=BG_INPUT,fg=TEXT_PRIMARY,activebackground=COLOR_ACCENT)
            menu.pack(fill=tk.X,pady=5)
        def submit():
            try:
                action=dict(old,type=type_name)
                for key,(kind,var) in vars_.items():
                    val=var.get('1.0','end').strip() if kind=='json' else var.get()
                    if kind=='int':val=int(val)
                    elif kind=='float':val=float(val)
                    elif kind=='area':val=list(dynamic_area(val))
                    elif kind=='json':
                        val=json.loads(val)
                        if not isinstance(val,list):raise ValueError(f'{key}: expected a JSON list')
                    action[key]=val
                if type_name=='if_dynamic' and not action.get('conditions'):
                    raise ValueError('IF requires at least one condition')
                if type_name=='loop_start' and action.get('mode') in ('while','until') and not action.get('conditions'):
                    raise ValueError('While/Until loops need conditions')
                if type_name=='trigger_start' and edit_idx is None:
                    # Automatic triggers should be simple to discover and reason about.
                    first_insert=0
                    self.app.recorded_actions.insert(first_insert,action)
                    idx=first_insert
                elif edit_idx is not None:
                    idx=edit_idx
                    self.app.recorded_actions[idx]=action
                else:
                    idx=line_num-1 if 'After' in pos_var.get() else line_num-2
                    idx=max(0,min(idx,len(self.app.recorded_actions)))
                    self.app.recorded_actions.insert(idx,action)
                self.close_sub_menu()
                self.app.display_actions_in_queue(self.app.recorded_actions)
                self.app.set_active_action_index(idx)
                self.app.update_status(f'Configured {self._selected_action_name}',COLOR_SUCCESS)
            except (ValueError,TypeError,json.JSONDecodeError,MacroExecutionError) as exc:
                error.config(text=str(exc),fg=COLOR_DANGER)
        ModernButton(body,text='Save Changes' if edit_idx is not None else 'Add Action',
                     bg_color=COLOR_SUCCESS if edit_idx is not None else COLOR_ACCENT,
                     hover_color=COLOR_ACCENT_HOV,command=submit).pack(fill=tk.X,pady=(6,12))


    def _render_delay_view(self, line_num, edit_idx=None):
        body = self.action_content_frame
        curr_act = self.app.recorded_actions[edit_idx] if (edit_idx is not None and 0 <= edit_idx < len(self.app.recorded_actions)) else None

        tk.Label(body, text="Delay Duration (seconds):", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 4))

        input_frame = tk.Frame(body, bg=BG_PANEL)
        input_frame.pack(fill=tk.X, pady=(0, 6))

        delay_entry = tk.Entry(input_frame, bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Consolas", 10, "bold"), insertbackground=TEXT_PRIMARY, bd=0, highlightbackground=BG_BORDER, highlightthickness=1, justify="center")
        init_sec = f"{float(curr_act.get('seconds', 1.0)):.2f}" if (curr_act and curr_act.get("type") == "delay") else "1.00"
        delay_entry.insert(0, init_sec)
        delay_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)
        delay_entry.focus_set()
        delay_entry.select_range(0, "end")

        chips_frame = tk.Frame(body, bg=BG_PANEL)
        chips_frame.pack(fill=tk.X, pady=(0, 8))

        def set_chip(val):
            delay_entry.delete(0, "end")
            delay_entry.insert(0, f"{val:.2f}")

        for sec in (0.25, 0.50, 1.00, 2.00):
            lbl = tk.Label(chips_frame, text=f"{sec}s", font=("Segoe UI", 7, "bold"), bg=BG_INPUT, fg=TEXT_MUTED, cursor="hand2", padx=6, pady=2, highlightbackground=BG_BORDER, highlightthickness=1)
            lbl.pack(side=tk.LEFT, padx=(0, 4))
            lbl.bind("<Button-1>", lambda e, s=sec: set_chip(s))

        if edit_idx is None:
            pos_frame = tk.Frame(body, bg=BG_PANEL)
            pos_frame.pack(fill=tk.X, pady=(0, 10))
            pos_var = tk.StringVar(value=f"Before Line {line_num}")
            pos_menu = tk.OptionMenu(pos_frame, pos_var, f"Before Line {line_num}", f"After Line {line_num}")
            pos_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 8), highlightthickness=1, highlightbackground=BG_BORDER, bd=0, relief="flat")
            pos_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT)
            pos_menu.pack(fill=tk.X)

        def submit_delay(event=None):
            try:
                sec_val = float(delay_entry.get().strip())
                if sec_val < 0.001: raise ValueError
            except ValueError:
                delay_entry.config(highlightbackground=COLOR_DANGER)
                return

            if edit_idx is not None:
                self.app.recorded_actions[edit_idx] = {"type": "delay", "seconds": round(sec_val, 2)}
                self.app.display_actions_in_queue(self.app.recorded_actions)
                self.app.set_active_action_index(edit_idx)
                self.app.update_status(f"Updated Delay #{edit_idx + 1}", COLOR_SUCCESS)
            else:
                is_after = "After" in pos_var.get()
                insert_idx = 0 if line_num <= 1 else (line_num - 1 if is_after else line_num - 2)
                insert_idx = max(0, min(insert_idx, len(self.app.recorded_actions)))
                self.app.insert_delay_action(insert_idx, round(sec_val, 2))
            self.close_sub_menu()

        delay_entry.bind("<Return>", submit_delay)
        btn_txt = "Save Changes" if edit_idx is not None else "Add Delay"
        ModernButton(body, text=btn_txt, bg_color=COLOR_SUCCESS if edit_idx is not None else COLOR_ACCENT, hover_color="#1d8a4b" if edit_idx is not None else COLOR_ACCENT_HOV, command=submit_delay).pack(fill=tk.X)


    def _render_wait_text_view(self, line_num, edit_idx=None):
        """Editor for a screen-OCR checkpoint that waits until text changes."""
        body = self.action_content_frame
        curr = (self.app.recorded_actions[edit_idx]
                if edit_idx is not None else {})

        tk.Label(body, text="Text to detect:", font=("Segoe UI", 8, "bold"),
                 bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 2))
        text_entry = tk.Entry(body, bg=BG_INPUT, fg=TEXT_PRIMARY,
                              font=("Segoe UI", 9), insertbackground=TEXT_PRIMARY,
                              highlightbackground=BG_BORDER, highlightthickness=1, bd=0)
        text_entry.insert(0, curr.get("text", ""))
        text_entry.pack(fill=tk.X, ipady=3, pady=(0, 9))
        text_entry.focus_set()

        tk.Label(body, text="Wait until text:", font=("Segoe UI", 8, "bold"),
                 bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 2))
        mode_var = tk.StringVar(value=curr.get("mode", "appears"))
        mode_menu = tk.OptionMenu(body, mode_var, "appears", "disappears")
        mode_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 8),
                         highlightthickness=1, highlightbackground=BG_BORDER, bd=0)
        mode_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY,
                                 activebackground=COLOR_ACCENT)
        mode_menu.pack(fill=tk.X, pady=(0, 9))

        tk.Label(body, text="Timeout (seconds):", font=("Segoe UI", 8, "bold"),
                 bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 2))
        timeout_entry = tk.Entry(body, bg=BG_INPUT, fg=TEXT_PRIMARY,
                                 font=("Consolas", 9), insertbackground=TEXT_PRIMARY,
                                 highlightbackground=BG_BORDER, highlightthickness=1, bd=0)
        timeout_entry.insert(0, str(curr.get("timeout", 15.0)))
        timeout_entry.pack(fill=tk.X, ipady=3, pady=(0, 9))

        chosen = {"area": curr.get("area")}
        area_row = tk.Frame(body, bg=BG_PANEL)
        area_row.pack(fill=tk.X, pady=(0, 9))
        area = chosen["area"]
        area_text = (f"[{area[0]},{area[1]} {area[2]}x{area[3]}]"
                     if isinstance(area, (list, tuple)) and len(area) == 4 else "Area: Not selected")
        area_label = tk.Label(area_row, text=area_text, font=("Consolas", 8),
                              bg=BG_INPUT, fg=SYNTAX_BLUE if area else TEXT_MUTED,
                              padx=6, pady=3, highlightbackground=BG_BORDER,
                              highlightthickness=1, anchor="w")
        area_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        def snip_done(area):
            chosen["area"] = area
            x, y, w, h = area
            area_label.config(text=f"[{x},{y} {w}x{h}]", fg=SYNTAX_BLUE,
                              highlightbackground=BG_BORDER)

        def choose_area():
            if self.sub_menu and self.sub_menu.winfo_exists():
                self.sub_menu.withdraw()
            # The existing selector restores the application and submenu on completion/cancel.
            self.app.start_screen_selection(snip_done)

        snip = tk.Label(area_row, text="⛶ Snip", font=("Segoe UI", 8, "bold"),
                        bg=COLOR_ACCENT, fg=TEXT_ON_ACCENT, cursor="hand2", padx=8, pady=3)
        snip.pack(side=tk.RIGHT)
        snip.bind("<Button-1>", lambda event: choose_area())

        if edit_idx is None:
            pos_var = tk.StringVar(value=f"Before Line {line_num}")
            pos_menu = tk.OptionMenu(body, pos_var, f"Before Line {line_num}", f"After Line {line_num}")
            pos_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 8),
                            highlightthickness=1, highlightbackground=BG_BORDER, bd=0)
            pos_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY,
                                    activebackground=COLOR_ACCENT)
            pos_menu.pack(fill=tk.X, pady=(0, 9))

        def save():
            text = text_entry.get().strip()
            area = chosen["area"]
            try:
                timeout = float(timeout_entry.get().strip())
                if not math.isfinite(timeout) or timeout < 0:
                    raise ValueError
            except ValueError:
                timeout_entry.config(highlightbackground=COLOR_DANGER)
                return
            if not text:
                text_entry.config(highlightbackground=COLOR_DANGER)
                return
            if not isinstance(area, (list, tuple)) or len(area) != 4 or int(area[2]) <= 0 or int(area[3]) <= 0:
                area_label.config(text="Select a screen area!", highlightbackground=COLOR_DANGER)
                return

            action = {"type": "wait_text", "text": text, "mode": mode_var.get(),
                      "timeout": timeout, "area": [int(n) for n in area]}
            if edit_idx is not None:
                self.app.recorded_actions[edit_idx] = action
                new_index = edit_idx
            else:
                after = "After" in pos_var.get()
                new_index = 0 if line_num <= 1 else (line_num - 1 if after else line_num - 2)
                new_index = max(0, min(new_index, len(self.app.recorded_actions)))
                self.app.recorded_actions.insert(new_index, action)

            self.app.current_action_idx = new_index
            self.app.display_actions_in_queue(self.app.recorded_actions)
            self.app.update_status("Wait for Text saved", COLOR_SUCCESS)
            self.close_sub_menu()

        text_entry.bind("<Return>", lambda event: save())
        label = "Save Changes" if edit_idx is not None else "Add Wait for Text"
        ModernButton(body, text=label, bg_color=COLOR_SUCCESS,
                     hover_color="#1d8a4b", command=save).pack(fill=tk.X)


    def _render_search_area_view(self, line_num, edit_idx=None):
        body = self.action_content_frame
        curr_act = self.app.recorded_actions[edit_idx] if (edit_idx is not None and 0 <= edit_idx < len(self.app.recorded_actions)) else None

        init_match_any = bool(curr_act.get("match_any", False)) if (curr_act and curr_act.get("type") == "search_area") else False
        match_any_state = {"val": init_match_any}

        any_row = tk.Frame(body, bg=BG_PANEL)
        any_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(any_row, text="Match Any Detected Text:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED).pack(side=tk.LEFT)

        def toggle_match_any(state):
            match_any_state["val"] = state
            if state:
                text_entry.delete(0, "end")
                text_entry.insert(0, "<ANY TEXT DETECTED>")
                text_entry.config(state="disabled", disabledforeground=SYNTAX_PURPLE)
            else:
                text_entry.config(state="normal")
                text_entry.delete(0, "end")

        any_toggle = ModernPillToggle(any_row, initial_state=init_match_any, command=toggle_match_any)
        any_toggle.pack(side=tk.RIGHT)

        tk.Label(body, text="Target Text:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 2))
        text_entry = tk.Entry(body, bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 9), insertbackground=TEXT_PRIMARY, bd=0, highlightbackground=BG_BORDER, highlightthickness=1)
        text_entry.pack(fill=tk.X, ipady=3, pady=(0, 8))

        init_text = curr_act.get("text", "") if (curr_act and curr_act.get("type") == "search_area") else ""
        if init_match_any:
            text_entry.insert(0, "<ANY TEXT DETECTED>")
            text_entry.config(state="disabled", disabledforeground=SYNTAX_PURPLE)
        else:
            text_entry.insert(0, init_text)
            text_entry.focus_set()

        init_area = curr_act.get("area") if (curr_act and curr_act.get("type") == "search_area") else None
        selected_box = {"area": init_area}
        area_row = tk.Frame(body, bg=BG_PANEL)
        area_row.pack(fill=tk.X, pady=(0, 8))

        area_lbl_text = f"[{init_area[0]},{init_area[1]} {init_area[2]}x{init_area[3]}]" if init_area else "Area: None"
        lbl_area_status = tk.Label(area_row, text=area_lbl_text, font=("Consolas", 8), bg=BG_INPUT, fg=SYNTAX_BLUE if init_area else TEXT_MUTED, padx=6, pady=3, highlightbackground=BG_BORDER, highlightthickness=1, anchor="w")
        lbl_area_status.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        def on_area_snip_completed(area):
            selected_box["area"] = area
            x, y, w, h = area
            lbl_area_status.config(text=f"[{x},{y} {w}x{h}]", fg=SYNTAX_BLUE)

        def trigger_snip():
            if self.sub_menu: self.sub_menu.withdraw()
            self.app.start_screen_selection(on_area_snip_completed)

        btn_snip = tk.Label(area_row, text="⛶ Snip", font=("Segoe UI", 8, "bold"), bg=COLOR_ACCENT, fg=TEXT_ON_ACCENT, cursor="hand2", padx=8, pady=3)
        btn_snip.pack(side=tk.RIGHT)
        btn_snip.bind("<Button-1>", lambda e: trigger_snip())

        tk.Label(body, text="If Found, Stop & Run Preset:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, pady=(0, 2))
        preset_names = self.app.get_preset_names()
        init_preset = curr_act.get("preset", "") if (curr_act and curr_act.get("type") == "search_area") else ""
        chosen_preset = init_preset if init_preset in preset_names else (preset_names[0] if preset_names else "-- No Presets Found --")
        preset_var = tk.StringVar(value=chosen_preset)

        opt_preset = tk.OptionMenu(body, preset_var, *(preset_names if preset_names else ["-- No Presets Found --"]))
        opt_preset.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 8), highlightthickness=1, highlightbackground=BG_BORDER, bd=0, relief="flat")
        opt_preset["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT)
        opt_preset.pack(fill=tk.X, pady=(0, 8))

        if edit_idx is None:
            pos_var = tk.StringVar(value=f"Before Line {line_num}")
            pos_menu = tk.OptionMenu(body, pos_var, f"Before Line {line_num}", f"After Line {line_num}")
            pos_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 8), highlightthickness=1, highlightbackground=BG_BORDER, bd=0, relief="flat")
            pos_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT)
            pos_menu.pack(fill=tk.X, pady=(0, 10))

        def submit_search_area(event=None):
            is_match_any = match_any_state["val"]
            search_str = text_entry.get().strip()
            if not is_match_any and not search_str:
                text_entry.config(highlightbackground=COLOR_DANGER)
                return
            if not selected_box["area"]:
                lbl_area_status.config(highlightbackground=COLOR_DANGER, text="Area Required!")
                return
            target_preset = preset_var.get()
            if target_preset == "-- No Presets Found --": return

            if edit_idx is not None:
                self.app.recorded_actions[edit_idx] = {
                    "type": "search_area",
                    "text": "*" if is_match_any else search_str,
                    "area": [int(v) for v in selected_box["area"]],
                    "preset": str(target_preset),
                    "match_any": bool(is_match_any)
                }
                self.app.display_actions_in_queue(self.app.recorded_actions)
                self.app.set_active_action_index(edit_idx)
                desc = "Any Text" if is_match_any else f"'{search_str}'"
                self.app.update_status(f"Updated Area Search: {desc}", COLOR_SUCCESS)
            else:
                is_after = "After" in pos_var.get()
                insert_idx = 0 if line_num <= 1 else (line_num - 1 if is_after else line_num - 2)
                insert_idx = max(0, min(insert_idx, len(self.app.recorded_actions)))

                self.app.insert_search_area_action(insert_idx, "*" if is_match_any else search_str, selected_box["area"], target_preset, match_any=is_match_any)
            self.close_sub_menu()

        text_entry.bind("<Return>", submit_search_area)
        btn_txt = "Save Changes" if edit_idx is not None else "Add Search Action"
        ModernButton(body, text=btn_txt, bg_color=COLOR_SUCCESS if edit_idx is not None else COLOR_ACCENT, hover_color="#1d8a4b" if edit_idx is not None else COLOR_ACCENT_HOV, command=submit_search_area).pack(fill=tk.X)

    def _render_find_color_view(self, line_num, edit_idx=None):
        """Full Find Color editor, including reselectable and numeric search bounds.

        Double-clicking an existing action must not force the user to delete
        and rebuild it. All controls initialize from the saved action; Save
        preserves unrelated keys for forward compatibility.
        """
        body = self.action_content_frame
        curr = (self.app.recorded_actions[edit_idx]
                if edit_idx is not None and 0 <= edit_idx < len(self.app.recorded_actions)
                and self.app.recorded_actions[edit_idx].get("type") == "find_color"
                else {})

        def text_label(parent, label):
            return tk.Label(parent, text=label, font=("Segoe UI", 8, "bold"),
                            bg=BG_PANEL, fg=TEXT_MUTED)

        def entry(parent, value, width=7, fg=TEXT_PRIMARY):
            widget = tk.Entry(parent, width=width, bg=BG_INPUT, fg=fg,
                              insertbackground=TEXT_PRIMARY, font=("Consolas", 9),
                              bd=0, highlightbackground=BG_BORDER,
                              highlightthickness=1)
            widget.insert(0, str(value))
            return widget

        def set_value(widget, value):
            widget.delete(0, tk.END)
            widget.insert(0, str(value))
            widget.config(highlightbackground=BG_BORDER)

        title = "EDIT COLOR DETECTION" if edit_idx is not None else "NEW COLOR DETECTION"
        text_label(body, title).pack(anchor="w", pady=(0, 5))
        tk.Label(body, text="Define where Sequent searches. The Ghost Reticle can click anywhere outside this box.",
                 font=("Segoe UI", 8), bg=BG_PANEL, fg=TEXT_MUTED,
                 anchor="w", justify="left", wraplength=460).pack(fill=tk.X, pady=(0, 8))

        # Explicit, editable screen search region: users can type exact screen
        # coordinates or click Reselect to draw a fresh box on any monitor.
        initial_area = curr.get("area", [0, 0, 0, 0])
        if not isinstance(initial_area, (list, tuple)) or len(initial_area) != 4:
            initial_area = [0, 0, 100, 100]
        area_row = tk.Frame(body, bg=BG_PANEL)
        area_row.pack(fill=tk.X, pady=(0, 4))
        area_entries = []
        for label, value in zip(("X", "Y", "Width", "Height"), initial_area):
            field = tk.Frame(area_row, bg=BG_PANEL)
            field.pack(side=tk.LEFT, padx=(0, 9))
            text_label(field, label).pack(anchor="w")
            control = entry(field, value, width=7)
            control.pack(pady=(3, 0))
            area_entries.append(control)

        area_message = tk.Label(body, text="Search area is in absolute screen pixels (negative X/Y supported).",
                                font=("Segoe UI", 8), bg=BG_PANEL, fg=TEXT_MUTED,
                                anchor="w", wraplength=460)
        area_message.pack(fill=tk.X, pady=(0, 5))

        def resample_area_done(area):
            if not body.winfo_exists():
                return
            for control, value in zip(area_entries, area):
                set_value(control, value)
            area_message.config(text=f"Selected search box: {area[2]}×{area[3]} at ({area[0]}, {area[1]})",
                                fg=SYNTAX_GREEN)

        def pick_new_area():
            if self.sub_menu and self.sub_menu.winfo_exists():
                self.sub_menu.withdraw()
            try:
                self.app.start_screen_selection(resample_area_done)
            except Exception as exc:
                self.app.restore_after_desktop_picker()
                error_lbl.config(text=f"Could not open screen selector: {exc}")

        search_box_label = "⛶ Redefine Search Box on Desktop" if curr else "Define Search Box"
        ModernButton(body, text=search_box_label,
                     bg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOV,
                     command=pick_new_area).pack(fill=tk.X, pady=(0, 12))

        # Sampled hex color is editable, too; using the inspector should never
        # discard typed region coordinates or existing offsets.
        col_row = tk.Frame(body, bg=BG_PANEL)
        col_row.pack(fill=tk.X, pady=(0, 8))
        text_label(col_row, "Target RGB:").pack(side=tk.LEFT)
        col_entry = entry(col_row, curr.get("color", "#FFFFFF"), width=9, fg=SYNTAX_GREEN)
        col_entry.pack(side=tk.LEFT, padx=(8, 10))

        def color_picked(x, y, hex_color):
            if body.winfo_exists():
                set_value(col_entry, hex_color)
                area_message.config(text=f"Sampled {hex_color} at ({x}, {y})", fg=SYNTAX_GREEN)

        def inspect_color():
            if self.sub_menu and self.sub_menu.winfo_exists():
                self.sub_menu.withdraw()
            try:
                self.app.start_pixel_inspection(color_picked)
            except Exception as exc:
                self.app.restore_after_desktop_picker()
                error_lbl.config(text=f"Could not open color inspector: {exc}")

        ModernButton(col_row, text="⌖ Sample Color", bg_color=COLOR_ACCENT,
                     hover_color=COLOR_ACCENT_HOV, command=inspect_color).pack(side=tk.LEFT)

        # Target movement is anchored to detected component, not constrained
        # to the box's x/y or w/h.
        off_row = tk.Frame(body, bg=BG_PANEL)
        off_row.pack(fill=tk.X, pady=(0, 7))
        text_label(off_row, "Offset X:").pack(side=tk.LEFT)
        ox_entry = entry(off_row, curr.get("ox", 0))
        ox_entry.pack(side=tk.LEFT, padx=(4, 14))
        text_label(off_row, "Offset Y:").pack(side=tk.LEFT)
        oy_entry = entry(off_row, curr.get("oy", 0))
        oy_entry.pack(side=tk.LEFT, padx=(4, 0))

        anchor_row = tk.Frame(body, bg=BG_PANEL)
        anchor_row.pack(fill=tk.X, pady=(0, 8))
        text_label(anchor_row, "Anchor:").pack(side=tk.LEFT)
        anchor_var = tk.StringVar(value=curr.get("anchor", "Center"))
        anchor_menu = tk.OptionMenu(anchor_row, anchor_var, *COLOR_ANCHORS)
        anchor_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 8),
                           highlightthickness=1, highlightbackground=BG_BORDER,
                           bd=0, relief="flat", cursor="hand2")
        anchor_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT)
        anchor_menu.pack(side=tk.LEFT, padx=(6, 12))
        text_label(anchor_row, "RGB ±:").pack(side=tk.LEFT)
        tolerance_entry = entry(anchor_row, curr.get("tolerance", 8), width=5)
        tolerance_entry.pack(side=tk.LEFT, padx=(4, 0))

        act_row = tk.Frame(body, bg=BG_PANEL)
        act_row.pack(fill=tk.X, pady=(0, 8))
        text_label(act_row, "When found:").pack(side=tk.LEFT)
        act_var = tk.StringVar(value=curr.get("action", "Single Click"))
        act_menu = tk.OptionMenu(act_row, act_var, "Move Only", "Single Click", "Double Click")
        act_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 8),
                        highlightthickness=1, highlightbackground=BG_BORDER,
                        bd=0, relief="flat", cursor="hand2")
        act_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT)
        act_menu.pack(side=tk.LEFT, padx=(6, 0))

        if edit_idx is None:
            pos_var = tk.StringVar(value=f"Before Line {line_num}")
            pos_menu = tk.OptionMenu(body, pos_var, f"Before Line {line_num}", f"After Line {line_num}")
            pos_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 8),
                            highlightthickness=1, highlightbackground=BG_BORDER, bd=0)
            pos_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT)
            pos_menu.pack(fill=tk.X, pady=(0, 7))

        error_lbl = tk.Label(body, text="", font=("Segoe UI", 8),
                             bg=BG_PANEL, fg=COLOR_DANGER, anchor="w", wraplength=450)
        error_lbl.pack(fill=tk.X, pady=(0, 5))

        def save(start_reticle=False):
            try:
                area = [int(field.get().strip()) for field in area_entries]
                if area[2] <= 0 or area[3] <= 0:
                    raise ValueError("Search Width and Height must be positive.")
                if area[2] * area[3] > 16_000_000:
                    raise ValueError("Search region is over 16 million pixels. Select a smaller area.")
                ox, oy = int(ox_entry.get().strip()), int(oy_entry.get().strip())
                tolerance = int(tolerance_entry.get().strip())
                if not 0 <= tolerance <= 255:
                    raise ValueError("RGB tolerance must be between 0 and 255.")
                color = col_entry.get().strip().upper()
                if not re.fullmatch(r"#[0-9A-F]{6}", color):
                    raise ValueError("Target color must be #RRGGBB, such as #35A879.")
            except ValueError as exc:
                error_lbl.config(text=str(exc) or "Enter valid integer coordinates and offsets.")
                return

            # Preserve any future action options that we don't expose yet.
            updated_action = dict(curr)
            updated_action.update({"type": "find_color", "area": area,
                                   "color": color, "anchor": anchor_var.get(),
                                   "tolerance": tolerance, "ox": ox, "oy": oy,
                                   "action": act_var.get()})
            if edit_idx is not None:
                saved_idx = edit_idx
                self.app.recorded_actions[edit_idx] = updated_action
            else:
                saved_idx = line_num - 1 if "After" in pos_var.get() else line_num - 2
                saved_idx = max(0, min(saved_idx, len(self.app.recorded_actions)))
                self.app.recorded_actions.insert(saved_idx, updated_action)

            self.close_sub_menu()
            self.app.display_actions_in_queue(self.app.recorded_actions)
            self.app.set_active_action_index(saved_idx)
            self.app.update_status("Find Color settings saved; search box and target offsets updated", COLOR_SUCCESS)
            if start_reticle:
                # Start on the next idle turn, after the editor has closed and
                # the action queue has committed the new selection.
                self.app.root.after(90, self.app.toggle_reticle_edit)

        ModernButton(body, text="Save Changes" if edit_idx is not None else "Add Find Color Action",
                     bg_color=COLOR_SUCCESS, hover_color="#1d8a4b",
                     command=save).pack(fill=tk.X, pady=(0, 5))
        ModernButton(body, text="Save & Open Live Reticle Editor",
                     bg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOV,
                     command=lambda: save(start_reticle=True)).pack(fill=tk.X)

    def _render_find_text_view(self, line_num, edit_idx=None):
        body = self.action_content_frame
        curr = (self.app.recorded_actions[edit_idx]
                if edit_idx is not None and 0 <= edit_idx < len(self.app.recorded_actions)
                and self.app.recorded_actions[edit_idx].get("type") == "find_text"
                else {})

        def text_label(parent, label):
            return tk.Label(parent, text=label, font=("Segoe UI", 8, "bold"),
                            bg=BG_PANEL, fg=TEXT_MUTED)

        def entry(parent, value, width=7, fg=TEXT_PRIMARY):
            widget = tk.Entry(parent, width=width, bg=BG_INPUT, fg=fg,
                              insertbackground=TEXT_PRIMARY, font=("Consolas", 9),
                              bd=0, highlightbackground=BG_BORDER,
                              highlightthickness=1)
            widget.insert(0, str(value))
            return widget

        def set_value(widget, value):
            widget.delete(0, tk.END)
            widget.insert(0, str(value))
            widget.config(highlightbackground=BG_BORDER)

        title = "EDIT TEXT DETECTION" if edit_idx is not None else "NEW TEXT DETECTION"
        text_label(body, title).pack(anchor="w", pady=(0, 5))
        tk.Label(body, text="Define where Sequent searches. The Ghost Reticle can click anywhere outside this box.",
                 font=("Segoe UI", 8), bg=BG_PANEL, fg=TEXT_MUTED,
                 anchor="w", justify="left", wraplength=460).pack(fill=tk.X, pady=(0, 8))

        initial_area = curr.get("area", [0, 0, 0, 0])
        if not isinstance(initial_area, (list, tuple)) or len(initial_area) != 4:
            initial_area = [0, 0, 100, 100]
        area_row = tk.Frame(body, bg=BG_PANEL)
        area_row.pack(fill=tk.X, pady=(0, 4))
        area_entries = []
        for label, value in zip(("X", "Y", "Width", "Height"), initial_area):
            field = tk.Frame(area_row, bg=BG_PANEL)
            field.pack(side=tk.LEFT, padx=(0, 9))
            text_label(field, label).pack(anchor="w")
            control = entry(field, value, width=7)
            control.pack(pady=(3, 0))
            area_entries.append(control)

        area_message = tk.Label(body, text="Search area is in absolute screen pixels (negative X/Y supported).",
                                font=("Segoe UI", 8), bg=BG_PANEL, fg=TEXT_MUTED,
                                anchor="w", wraplength=460)
        area_message.pack(fill=tk.X, pady=(0, 5))

        def resample_area_done(area):
            if not body.winfo_exists():
                return
            for control, value in zip(area_entries, area):
                set_value(control, value)
            area_message.config(text=f"Selected search box: {area[2]}×{area[3]} at ({area[0]}, {area[1]})",
                                fg=SYNTAX_GREEN)

        def pick_new_area():
            if self.sub_menu and self.sub_menu.winfo_exists():
                self.sub_menu.withdraw()
            try:
                self.app.start_screen_selection(resample_area_done)
            except Exception as exc:
                self.app.restore_after_desktop_picker()
                error_lbl.config(text=f"Could not open screen selector: {exc}")

        search_box_label = "⛶ Redefine Search Box on Desktop" if curr else "Define Search Box"
        ModernButton(body, text=search_box_label,
                     bg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOV,
                     command=pick_new_area).pack(fill=tk.X, pady=(0, 12))

        txt_row = tk.Frame(body, bg=BG_PANEL)
        txt_row.pack(fill=tk.X, pady=(0, 8))
        text_label(txt_row, "Target Text:").pack(side=tk.LEFT)
        txt_entry = tk.Entry(txt_row, bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 9), insertbackground=TEXT_PRIMARY, bd=0, highlightbackground=BG_BORDER, highlightthickness=1)
        txt_entry.insert(0, curr.get("text", ""))
        txt_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0), ipady=3)

        off_row = tk.Frame(body, bg=BG_PANEL)
        off_row.pack(fill=tk.X, pady=(0, 7))
        text_label(off_row, "Offset X:").pack(side=tk.LEFT)
        ox_entry = entry(off_row, curr.get("ox", 0))
        ox_entry.pack(side=tk.LEFT, padx=(4, 14))
        text_label(off_row, "Offset Y:").pack(side=tk.LEFT)
        oy_entry = entry(off_row, curr.get("oy", 0))
        oy_entry.pack(side=tk.LEFT, padx=(4, 0))

        anchor_row = tk.Frame(body, bg=BG_PANEL)
        anchor_row.pack(fill=tk.X, pady=(0, 8))
        text_label(anchor_row, "Anchor:").pack(side=tk.LEFT)
        anchor_var = tk.StringVar(value=curr.get("anchor", "Center"))
        anchor_menu = tk.OptionMenu(anchor_row, anchor_var, *COLOR_ANCHORS)
        anchor_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 8),
                           highlightthickness=1, highlightbackground=BG_BORDER,
                           bd=0, relief="flat", cursor="hand2")
        anchor_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT)
        anchor_menu.pack(side=tk.LEFT, padx=(6, 12))

        act_row = tk.Frame(body, bg=BG_PANEL)
        act_row.pack(fill=tk.X, pady=(0, 8))
        text_label(act_row, "When found:").pack(side=tk.LEFT)
        act_var = tk.StringVar(value=curr.get("action", "Single Click"))
        act_menu = tk.OptionMenu(act_row, act_var, "Move Only", "Single Click", "Double Click")
        act_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 8),
                        highlightthickness=1, highlightbackground=BG_BORDER,
                        bd=0, relief="flat", cursor="hand2")
        act_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT)
        act_menu.pack(side=tk.LEFT, padx=(6, 0))

        if edit_idx is None:
            pos_var = tk.StringVar(value=f"Before Line {line_num}")
            pos_menu = tk.OptionMenu(body, pos_var, f"Before Line {line_num}", f"After Line {line_num}")
            pos_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 8),
                            highlightthickness=1, highlightbackground=BG_BORDER, bd=0)
            pos_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT)
            pos_menu.pack(fill=tk.X, pady=(0, 7))

        error_lbl = tk.Label(body, text="", font=("Segoe UI", 8),
                             bg=BG_PANEL, fg=COLOR_DANGER, anchor="w", wraplength=450)
        error_lbl.pack(fill=tk.X, pady=(0, 5))

        def save(start_reticle=False):
            try:
                area = [int(field.get().strip()) for field in area_entries]
                if area[2] <= 0 or area[3] <= 0:
                    raise ValueError("Search Width and Height must be positive.")
                if area[2] * area[3] > 16_000_000:
                    raise ValueError("Search region is over 16 million pixels. Select a smaller area.")
                ox, oy = int(ox_entry.get().strip()), int(oy_entry.get().strip())
                target_text = txt_entry.get().strip()
                if not target_text:
                    raise ValueError("Target text cannot be empty.")
            except ValueError as exc:
                error_lbl.config(text=str(exc) or "Enter valid integer coordinates and offsets.")
                return

            updated_action = dict(curr)
            updated_action.update({"type": "find_text", "area": area,
                                   "text": target_text, "anchor": anchor_var.get(),
                                   "ox": ox, "oy": oy, "action": act_var.get()})
            if edit_idx is not None:
                saved_idx = edit_idx
                self.app.recorded_actions[edit_idx] = updated_action
            else:
                saved_idx = line_num - 1 if "After" in pos_var.get() else line_num - 2
                saved_idx = max(0, min(saved_idx, len(self.app.recorded_actions)))
                self.app.recorded_actions.insert(saved_idx, updated_action)

            self.close_sub_menu()
            self.app.display_actions_in_queue(self.app.recorded_actions)
            self.app.set_active_action_index(saved_idx)
            self.app.update_status("Find Text settings saved; search box and target offsets updated", COLOR_SUCCESS)
            if start_reticle:
                self.app.root.after(90, self.app.toggle_reticle_edit)

        ModernButton(body, text="Save Changes" if edit_idx is not None else "Add Find Text Action",
                     bg_color=COLOR_SUCCESS, hover_color="#1d8a4b",
                     command=save).pack(fill=tk.X, pady=(0, 5))
        ModernButton(body, text="Save & Open Live Reticle Editor",
                     bg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOV,
                     command=lambda: save(start_reticle=True)).pack(fill=tk.X)

    def close_sub_menu(self):
        if self.sub_menu:
            try: self.sub_menu.destroy()
            except Exception: pass
            self.sub_menu = None
            
# --- Modern-Styled Scrollable Frame ---
class ModernScrollableFrame(tk.Frame):
    def __init__(self, parent, bg=None, **kwargs):
        bg = bg or BG_APP
        super().__init__(parent, bg=bg, **kwargs)

        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.scrollbar = ModernScrollBar(self, command=self.canvas.yview, width=18, bg_trough=BG_SCROLL_TRACK)
        self.scrollable_frame = tk.Frame(self.canvas, bg=bg)

        self.scrollable_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

        self.canvas_frame = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.bind('<Configure>', self._on_canvas_configure)
        self.canvas.configure(yscrollcommand=self._on_canvas_scroll)

        self.canvas.pack(side="left", fill="both", expand=True)

        # One active Settings scroll handler at a time. Using add="+" here
        # leaked callbacks from destroyed Settings pages on every theme change.
        self.bind_all("<MouseWheel>", self._on_mousewheel)


    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self.canvas_frame, width=event.width)

    def _on_canvas_scroll(self, first, last):
        f, l = float(first), float(last)
        if f <= 0.001 and l >= 0.999:
            if self.scrollbar.winfo_ismapped():
                self.scrollbar.pack_forget()
        else:
            if not self.scrollbar.winfo_ismapped():
                # Add 2px right padding to ensure it stays exactly centered against the new 2px resize grip
                self.scrollbar.pack(side="right", fill="y", padx=(0, 2), before=self.canvas)
        self.scrollbar.set(first, last)

    def _bind_mousewheel(self, event):
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, event):
        self.canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event):
        if not self.winfo_exists(): return
        try:
            # Check what widget the cursor is physically hovering over
            x, y = self.winfo_pointerxy()
            widget = self.winfo_containing(x, y)
            
            if widget:
                self_str = str(self)
                wid_str = str(widget)
                
                # Only scroll if the hovered widget is this frame or a child of this frame
                if wid_str == self_str or wid_str.startswith(self_str + "."):
                    if self.scrollbar.winfo_ismapped():
                        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                        return "break"
        except Exception:
            pass

        
# --- Custom Modern UI Components ---
class ModernButton(tk.Frame):
    def __init__(self, parent, text, bg_color, hover_color, command, **kwargs):
        super().__init__(parent, bg=bg_color, cursor="hand2", **kwargs)
        self.bg_color = bg_color
        self.hover_color = hover_color
        self.current_color = bg_color
        self.command = command
        self.disabled = False
        self._fade_job = None

        fg_color = self._get_contrast_fg(bg_color)
        self.label = tk.Label(self, text=text, bg=bg_color, fg=fg_color, font=("Segoe UI", 10, "bold"), cursor="hand2")
        self.label.pack(expand=True, fill=tk.BOTH, padx=16, pady=9)

        for widget in (self, self.label):
            widget.bind("<Enter>", self.on_hover)
            widget.bind("<Leave>", self.on_leave)
            widget.bind("<Button-1>", self.on_click)


    def _get_contrast_fg(self, hex_color):
        """Calculates perceptual luminance and returns high-contrast text color."""
        if self.disabled:
            return TEXT_MUTED
        try:
            h = hex_color.lstrip("#")
            r, g, b = tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
            # Relative luminance formula (YIQ)
            lum = (0.299 * r + 0.587 * g + 0.114 * b)
            return "#111214" if lum >= 155 else "#ffffff"
        except Exception:
            return "#ffffff"
            
    def on_hover(self, event):
        if not self.disabled:
            if getattr(BuiltInMacroApp, 'entertain_mode', True):
                self._fade_to(self.hover_color)
            else:
                self.configure(bg=self.hover_color)
                self.label.configure(bg=self.hover_color, fg=self._get_contrast_fg(self.hover_color))
                self.current_color = self.hover_color
    
    def on_leave(self, event):
        if not self.disabled:
            if getattr(BuiltInMacroApp, 'entertain_mode', True):
                self._fade_to(self.bg_color)
            else:
                self.configure(bg=self.bg_color)
                self.label.configure(bg=self.bg_color, fg=self._get_contrast_fg(self.bg_color))
                self.current_color = self.bg_color
    
    def _fade_to(self, target_hex, step=1, total_steps=5):
        if not self.winfo_exists(): return
        if self._fade_job:
            try:
                self.after_cancel(self._fade_job)
            except Exception:
                pass
            self._fade_job = None

        if self.disabled:
            return

        def _hex_to_rgb(h):
            h = h.lstrip("#")
            return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

        try:
            r1, g1, b1 = _hex_to_rgb(self.current_color)
            r2, g2, b2 = _hex_to_rgb(target_hex)
            t = step / total_steps
            rn = int(r1 + (r2 - r1) * t)
            gn = int(g1 + (g2 - g1) * t)
            bn = int(b1 + (b2 - b1) * t)
            interpolated = f"#{rn:02x}{gn:02x}{bn:02x}"
        except Exception:
            interpolated = target_hex

        self.configure(bg=interpolated)
        self.label.configure(bg=interpolated, fg=self._get_contrast_fg(interpolated))
        self.current_color = interpolated

        if step < total_steps:
            self._fade_job = self.after(14, lambda: self._fade_to(target_hex, step + 1, total_steps))
        else:
            self.current_color = target_hex
            self.configure(bg=target_hex)
            self.label.configure(bg=target_hex, fg=self._get_contrast_fg(target_hex))


    def on_click(self, event):
        if not self.disabled and self.command:
            self.command()

    def set_state(self, state, text=None, bg=None, hover=None):
        self.disabled = (state == "disabled")
        if text: self.label.config(text=text)
        self.bg_color = bg if bg else self.bg_color
        self.hover_color = hover if hover else self.hover_color
        active_bg = DISABLED if self.disabled else self.bg_color
        self.configure(bg=active_bg)
        self.label.configure(bg=active_bg)
        cursor = "arrow" if self.disabled else "hand2"
        self.configure(cursor=cursor)
        self.label.configure(cursor=cursor)
        self.label.config(fg=self._get_contrast_fg(active_bg))


class ModernPillToggle(tk.Canvas):
    def __init__(self, parent, initial_state=True, command=None, width=44, height=22, **kwargs):
        super().__init__(parent, width=width, height=height, bg=BG_PANEL, highlightthickness=0, cursor="hand2", **kwargs)
        self.state = initial_state
        self.anim_pos = 1.0 if initial_state else 0.0
        self._anim_id = None
        self.command = command
        self.w = width
        self.h = height

        self.bind("<Button-1>", self.toggle)
        self.draw()
    

    def draw(self):
        if not self.winfo_exists(): return
        self.delete("all")

        def _hex_to_rgb(h):
            h = h.lstrip("#")
            return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

        try:
            r1, g1, b1 = _hex_to_rgb(TRACK_INACTIVE)
            r2, g2, b2 = _hex_to_rgb(COLOR_ACCENT)
            r = int(r1 + (r2 - r1) * self.anim_pos)
            g = int(g1 + (g2 - g1) * self.anim_pos)
            b = int(b1 + (b2 - b1) * self.anim_pos)
            bg_color = f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            bg_color = COLOR_ACCENT if self.state else TRACK_INACTIVE

        radius = self.h / 2
        self.create_arc(0, 0, self.h, self.h, start=90, extent=180, fill=bg_color, outline=bg_color)
        self.create_arc(self.w - self.h, 0, self.w, self.h, start=-90, extent=180, fill=bg_color, outline=bg_color)
        self.create_rectangle(radius, 0, self.w - radius, self.h, fill=bg_color, outline=bg_color)

        pad = 3
        knob_size = self.h - (pad * 2)
        x_min = pad
        x_max = self.w - pad - knob_size
        x0 = x_min + ((x_max - x_min) * self.anim_pos)
        y0 = pad
        x1 = x0 + knob_size
        y1 = y0 + knob_size

        self.create_oval(x0, y0, x1, y1, fill="#ffffff", outline="#d1d4d8" if self.anim_pos < 0.5 else "#ffffff")


    def toggle(self, event=None):
        self.state = not self.state
        self._animate(trigger_command=True)

    def _animate(self, trigger_command=False):
        if not self.winfo_exists(): return
        if self._anim_id:
            try:
                self.after_cancel(self._anim_id)
            except Exception:
                pass
            self._anim_id = None

        target = 1.0 if self.state else 0.0
        diff = target - self.anim_pos

        if abs(diff) <= 0.05:
            self.anim_pos = target
            self.draw()
            if trigger_command and self.command:
                self.command(self.state)
            return

        self.anim_pos += diff * 0.40
        self.draw()
        self._anim_id = self.after(14, lambda: self._animate(trigger_command=trigger_command))


class ModernSlider(tk.Canvas):
    def __init__(self, parent, min_val=1.0, max_val=10.0, step=0.5, initial_val=1.0, command=None, width=180, height=24, bg=None, **kwargs):
        bg = bg or BG_PANEL
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0, cursor="hand2", **kwargs)
        self.min_val = min_val
        self.max_val = max_val
        self.step = step
        self.val = initial_val
        self.command = command
        self.w = width
        self.h = height
        self.bg_trough = BG_SCROLL_TRACK
        self.current_color = COLOR_ACCENT
        self.track_h = 6
        self.knob_r = 8
        self.padding = 12

        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", self.on_click)
        self.bind("<B1-Motion>", self.on_drag)
        self.draw()

    def draw(self):
        if not self.winfo_exists(): return
        self.delete("all")
        
        w = self.winfo_width()
        h = self.winfo_height()
        
        if w <= 2 or h <= 2:
            return

        x_start = self.padding
        x_end = w - self.padding
        usable_w = x_end - x_start
        
        span = self.max_val - self.min_val
        if span == 0: span = 1
        
        ratio = (self.val - self.min_val) / span
        ratio = max(0.0, min(1.0, ratio))
        
        knob_cx = x_start + (ratio * usable_w)
        cy = h / 2
        
        # Trough (Rounded Capstyle)
        self.create_line(x_start, cy, x_end, cy, fill=self.bg_trough, width=self.track_h, capstyle="round")
        
        # Active filled track (Rounded Capstyle)
        if knob_cx > x_start:
            self.create_line(x_start, cy, knob_cx, cy, fill=self.current_color, width=self.track_h, capstyle="round")
            
        # Knob
        self.create_oval(
            knob_cx - self.knob_r, cy - self.knob_r,
            knob_cx + self.knob_r, cy + self.knob_r,
            fill="#ffffff", outline=self.current_color
        )


    def set_val_from_x(self, x):
        w = self.winfo_width()
        if w <= 2: return
        
        x_start = self.padding
        x_end = w - self.padding
        
        x_clamped = max(x_start, min(x, x_end))
        ratio = (x_clamped - x_start) / max(1, x_end - x_start)

        raw_val = self.min_val + ratio * (self.max_val - self.min_val)
        steps = round((raw_val - self.min_val) / self.step)
        
        decimals = 2 if ("." in str(self.step) and len(str(self.step).split(".")[1]) >= 2) else 1
        stepped_val = round(self.min_val + steps * self.step, decimals)
        stepped_val = max(self.min_val, min(stepped_val, self.max_val))

        if stepped_val != self.val:
            self.val = stepped_val
            self.draw()
            if self.command:
                self.command(self.val)

    def on_click(self, event): self.set_val_from_x(event.x)
    def on_drag(self, event): self.set_val_from_x(event.x)
    def set(self, val):
        self.val = max(self.min_val, min(val, self.max_val))
        self.draw()

class ModernScrubberTrack(tk.Canvas):
    def __init__(self, parent, command=None, height=22, bg=None, **kwargs):
        bg = bg or BG_INPUT
        super().__init__(parent, height=height, bg=bg, highlightthickness=0, cursor="hand2", **kwargs)
        self.command = command
        self.height = height
        self.bg = bg
        self.current_idx = 0
        self.total_count = 0
        self.is_dragging = False

        self.track_h = 6
        self.knob_r = 6
        self.pad = 10

        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", self._on_click)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)


    def set_position(self, current_idx, total_count):
        self.total_count = max(0, total_count)
        self.current_idx = max(0, min(current_idx, max(0, self.total_count - 1)))
        self.draw()

    def draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= (self.pad * 2): return

        cy = h / 2
        x0 = self.pad
        x1 = w - self.pad
        usable_w = x1 - x0

        self.create_line(x0, cy, x1, cy, fill=TRACK_INACTIVE, width=self.track_h, capstyle="round")

        if self.total_count <= 1:
            ratio = 0.0
        else:
            ratio = self.current_idx / (self.total_count - 1)

        knob_x = x0 + (ratio * usable_w)

        if knob_x > x0:
            self.create_line(x0, cy, knob_x, cy, fill=COLOR_ACCENT, width=self.track_h, capstyle="round")

        knob_color = "#ffffff" if self.total_count > 0 else TEXT_MUTED
        self.create_oval(
            knob_x - self.knob_r, cy - self.knob_r,
            knob_x + self.knob_r, cy + self.knob_r,
            fill=knob_color, outline=knob_color
        )

    def _get_idx_from_x(self, x):
        if self.total_count <= 1: return 0
        w = self.winfo_width()
        usable_w = max(1, w - (self.pad * 2))
        clamped_x = max(self.pad, min(x, w - self.pad))
        ratio = (clamped_x - self.pad) / usable_w
        idx = int(round(ratio * (self.total_count - 1)))
        return max(0, min(idx, self.total_count - 1))

    def _on_click(self, event):
        if self.total_count <= 0: return
        self.is_dragging = True
        idx = self._get_idx_from_x(event.x)
        self.current_idx = idx
        self.draw()
        if self.command:
            self.command(idx)

    def _on_drag(self, event):
        if self.total_count <= 0: return
        idx = self._get_idx_from_x(event.x)
        if idx != self.current_idx:
            self.current_idx = idx
            self.draw()
            if self.command:
                self.command(idx)

    def _on_release(self, event):
        self.is_dragging = False

class SequentHotkeyField(tk.Frame):
    def __init__(self, parent, action_key, initial_name, app_instance):
        super().__init__(parent, bg=BG_INPUT, highlightbackground=BG_BORDER, highlightthickness=1, cursor="xterm")
        self.action_key = action_key
        self.app = app_instance
        
        self.lbl = tk.Label(self, text=initial_name, bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Consolas", 10, "bold"), cursor="xterm", anchor="w")
        self.lbl.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=6)
        
        self.clear_btn = tk.Label(self, text="\u2716", bg=BG_INPUT, fg=TEXT_MUTED, font=("Segoe UI", 10), cursor="hand2")
        self.clear_btn.pack(side=tk.RIGHT, padx=10)
        
        self.bind("<Button-1>", self.focus_in)
        self.lbl.bind("<Button-1>", self.focus_in)
        
        self.clear_btn.bind("<Button-1>", self.clear_hotkey)
        self.clear_btn.bind("<Enter>", lambda e: self.clear_btn.config(fg=COLOR_DANGER))
        self.clear_btn.bind("<Leave>", lambda e: self.clear_btn.config(fg=TEXT_MUTED))
        
        self.bind("<FocusOut>", self.focus_out)
        self.bind("<KeyPress>", self.on_key_press)
        self.config(takefocus=1)

    def focus_in(self, event=None):
        if self.app.recording or self.app.playing: return
        self.focus_set()
        self.config(highlightbackground=COLOR_ACCENT)
        self.app.is_binding = True
        self.app.active_hotkey_field = self

    def focus_out(self, event=None):
        self.config(highlightbackground=BG_BORDER)
        self.app.is_binding = False
        if getattr(self.app, 'active_hotkey_field', None) is self:
            self.app.active_hotkey_field = None

    def clear_hotkey(self, event=None):
        if self.app.recording or self.app.playing: return
        self.lbl.config(text="None")
        self.app.on_hotkey_change(self.action_key, None, [], "None")
        self.focus_out()
        self.app.root.focus_set()

    def on_key_press(self, event):
        if event.keysym.upper() == "TAB": return "break"
        vk = event.keycode
        keysym = event.keysym.upper()
        if keysym == "RETURN": keysym = "ENTER"
        elif keysym == "PRIOR": keysym = "PAGE UP"
        elif keysym == "NEXT": keysym = "PAGE DOWN"
        
        ctrl = bool(ctypes.windll.user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
        shift = bool(ctypes.windll.user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
        alt = bool(ctypes.windll.user32.GetAsyncKeyState(VK_MENU) & 0x8000)
        win = bool((ctypes.windll.user32.GetAsyncKeyState(VK_LWIN) & 0x8000) or 
                   (ctypes.windll.user32.GetAsyncKeyState(VK_RWIN) & 0x8000))
        
        is_mod_key = vk in MODIFIER_KEYS
        mod_vks, mods_str = [], []
        if win: mod_vks.append(VK_LWIN); mods_str.append("WIN")
        if ctrl: mod_vks.append(VK_CONTROL); mods_str.append("CTRL")
        if shift: mod_vks.append(VK_SHIFT); mods_str.append("SHIFT")
        if alt: mod_vks.append(VK_MENU); mods_str.append("ALT")
            
        if is_mod_key:
            final_vk = vk
            final_str = " + ".join(mods_str) if mods_str else keysym
        else:
            final_vk = vk
            key_name = SPECIAL_KEY_NAMES.get(vk, keysym)
            final_str = " + ".join(mods_str + [key_name]) if mods_str else key_name

        self.lbl.config(text=final_str)
        self.app.on_hotkey_change(self.action_key, final_vk, mod_vks, final_str)
        if not is_mod_key: self.app.root.focus_set()
        return "break"

class SidebarButton(tk.Frame):
    def __init__(self, parent, text, command, **kwargs):
        super().__init__(parent, bg=BG_SIDEBAR, cursor="hand2", **kwargs)
        self.command = command
        self.active = False
        
        self.indicator = tk.Frame(self, bg=BG_SIDEBAR, width=4)
        self.indicator.pack(side=tk.LEFT, fill=tk.Y)
        
        nav_text = f"  {text.title()}"
        self.label = tk.Label(self, text=nav_text, bg=BG_SIDEBAR, fg=TEXT_MUTED, font=("Segoe UI", 10, "bold"), cursor="hand2", anchor="w")
        self.label.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=12, pady=8)

        for widget in (self, self.label, self.indicator):
            widget.bind("<Enter>", self.on_hover)
            widget.bind("<Leave>", self.on_leave)
            widget.bind("<Button-1>", self.on_click)
    
    def on_hover(self, event):
        if not self.active:
            self.configure(bg=BG_HOVER)
            self.label.configure(bg=BG_HOVER, fg=TEXT_PRIMARY)

    def on_leave(self, event):
        if not self.active:
            self.configure(bg=BG_SIDEBAR)
            self.label.configure(bg=BG_SIDEBAR, fg=TEXT_MUTED)

    def on_click(self, event):
        if self.command: self.command()

    def set_active(self, is_active):
        self.active = is_active
        color = BG_SELECTED if is_active else BG_SIDEBAR
        fg_color = TEXT_PRIMARY if is_active else TEXT_MUTED
        ind_color = COLOR_ACCENT if is_active else color
        self.configure(bg=color)
        self.label.configure(bg=color, fg=fg_color)
        self.indicator.configure(bg=ind_color)

class GhostReticle:
    def __init__(self, root):
        self.root = root
        self.pool = []
        self.hide_timer = None
        self.area_win = None
        self.area_canvas = None
        self.color_win = None
        self.color_canvas = None
        self.edit_active = False
        self.edit_callback = None
        self.last_found_box = None
        self.last_target = None
        self._drag_xy = None
        # Live scan workers never call Tk. A queue transfers results back to
        # the UI loop; each editing session gets a generation token.
        self._live_generation = 0
        self._live_app = None
        self._live_index = None
        self._live_queue = Queue()
        self._live_timer = None
        self._live_worker = None
        self._live_next_scan = 0.0
        self._live_interval = 0.35
        self._live_is_found = False
        self._area_rendered = None
        self._color_rendered = None

    def _set_drag_access(self, enabled):
        if not self.pool:
            return
        win, canvas = self.pool[0]
        set_overlay_input_passthrough(win, not enabled)
        canvas.configure(cursor="fleur" if enabled else "arrow")

    def enable_drag(self, callback):
        """Enable mouse editing on only the current reticle, not its bounds."""
        if self.last_target is None or not self.pool:
            return False
        self.edit_active = True
        self.edit_callback = callback
        self._set_drag_access(True)
        canvas = self.pool[0][1]
        canvas.bind("<ButtonPress-1>", self._drag_start)
        canvas.bind("<B1-Motion>", self._drag_move)
        canvas.bind("<ButtonRelease-1>", self._drag_end)
        if self.hide_timer:
            self.root.after_cancel(self.hide_timer)
            self.hide_timer = None
        return True

    def disable_drag(self):
        if self.pool:
            canvas = self.pool[0][1]
            for binding in ("<ButtonPress-1>", "<B1-Motion>", "<ButtonRelease-1>"):
                canvas.unbind(binding)
            self._set_drag_access(False)
        self.edit_active = False
        self.edit_callback = None
        self._drag_xy = None

    def _drag_start(self, event):
        self._drag_xy = (event.x_root, event.y_root)

    def _drag_move(self, event):
        if not self.edit_active or self._drag_xy is None:
            return
        x, y = event.x_root, event.y_root
        dx, dy = x - self._drag_xy[0], y - self._drag_xy[1]
        self._drag_xy = (x, y)
        if (dx or dy) and self.edit_callback:
            self.edit_callback(dx, dy)

    def _drag_end(self, event):
        self._drag_move(event)
        self._drag_xy = None

    def _create_reticle_window(self):
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg="#da373c")

        win.update_idletasks()
        set_overlay_input_passthrough(win, True)
        exclude_overlay_from_capture(win)

        canvas = tk.Canvas(win, width=28, height=28, bg="#1e1f22", highlightthickness=1, highlightbackground="#da373c")
        canvas.pack()
        return win, canvas

    def _create_area_window(self):
        if self.area_win and self.area_win.winfo_exists():
            return
        self.area_win = tk.Toplevel(self.root)
        self.area_win.overrideredirect(True)
        self.area_win.attributes("-topmost", True, "-transparentcolor", "black")
        self.area_win.config(bg="black")
        self.area_canvas = tk.Canvas(self.area_win, bg="black", highlightthickness=0)
        self.area_canvas.pack(fill=tk.BOTH, expand=True)
        
        self.area_win.update_idletasks()
        set_overlay_input_passthrough(self.area_win, True)
        exclude_overlay_from_capture(self.area_win)

    def _create_color_window(self):
        if self.color_win and self.color_win.winfo_exists():
            return
        self.color_win = tk.Toplevel(self.root)
        self.color_win.overrideredirect(True)
        self.color_win.attributes("-topmost", True, "-transparentcolor", "black")
        self.color_win.config(bg="black")
        self.color_canvas = tk.Canvas(self.color_win, bg="black", highlightthickness=0)
        self.color_canvas.pack(fill=tk.BOTH, expand=True)
        
        self.color_win.update_idletasks()
        set_overlay_input_passthrough(self.color_win, True)
        exclude_overlay_from_capture(self.color_win)

    def show_multi(self, items, area_rect=None, color_rect=None):
        if self.hide_timer:
            try: self.root.after_cancel(self.hide_timer)
            except Exception: pass
            self.hide_timer = None

        if not items and not area_rect and not color_rect:
            self.hide()
            return

        items = items[-50:]

        while len(self.pool) < len(items):
            self.pool.append(self._create_reticle_window())

        size = 28
        for i, (x, y, is_click, alpha) in enumerate(items):
            win, canvas = self.pool[i]
            color = COLOR_DANGER if is_click else SYNTAX_BLUE

            canvas.delete("all")
            canvas.config(highlightbackground=color)
            canvas.create_oval(5, 5, 23, 23, outline=color, width=2)
            canvas.create_line(14, 0, 14, 28, fill=color, width=1)
            canvas.create_line(0, 14, 28, 14, fill=color, width=1)
            canvas.create_oval(12, 12, 16, 16, fill="#ffffff", outline=color)

            win.attributes("-alpha", max(0.15, min(1.0, alpha)))
            wx, wy = int(x - size // 2), int(y - size // 2)
            if not win.winfo_viewable():
                win.deiconify()
            position_toplevel_on_screen(win, wx, wy, size, size)

        for i in range(len(items), len(self.pool)):
            self.pool[i][0].withdraw()

        if area_rect:
            self._create_area_window()
            ax, ay, aw, ah, a_alpha = area_rect
            pad = 2
            ww, wh = aw + pad*2, ah + pad*2
            wx, wy = ax - pad, ay - pad
            self.area_win.deiconify()
            position_toplevel_on_screen(self.area_win, wx, wy, ww, wh)
            self.area_canvas.delete("all")
            self.area_canvas.create_rectangle(pad, pad, aw + pad, ah + pad, outline=SYNTAX_PURPLE, width=2, dash=(4, 4))
            self.area_win.attributes("-alpha", max(0.15, min(1.0, a_alpha)))
            self.area_win.deiconify()
        else:
            if self.area_win and self.area_win.winfo_exists():
                self.area_win.withdraw()

        if color_rect:
            self._create_color_window()
            cx, cy, cw, ch, c_alpha = color_rect
            pad = 2
            ww, wh = cw + pad*2, ch + pad*2
            wx, wy = cx - pad, cy - pad
            self.color_win.deiconify()
            position_toplevel_on_screen(self.color_win, wx, wy, ww, wh)
            self.color_canvas.delete("all")
            self.color_canvas.create_rectangle(pad, pad, cw + pad, ch + pad, outline="#35a879", width=2)
            self.color_win.attributes("-alpha", max(0.15, min(1.0, c_alpha)))
            self.color_win.deiconify()
        else:
            if self.color_win and self.color_win.winfo_exists():
                self.color_win.withdraw()

        if self.edit_active and self.pool:
            # Keep the hit-testable reticle ahead of the transparent outlines.
            self.pool[0][0].lift()
        else:
            self.hide_timer = self.root.after(3500, self.hide)

    def _move_target_only(self, action):
        """Apply the offset to the existing reticle without touching the boxes.

        This is the sole path for 1px nudges/dragging while editing. No screenshot,
        no canvas.delete(), no withdraw()/deiconify(), and no alpha animation.
        """
        box = self.last_found_box
        if box is not None:
            tx, ty = color_click_target(action, box)
        else:
            area = action.get("area", [0, 0, 0, 0])
            tx = int(area[0]) + int(area[2]) // 2 + int(action.get("ox", 0))
            ty = int(area[1]) + int(area[3]) // 2 + int(action.get("oy", 0))
        if self.last_target != (tx, ty) and self.pool:
            win, _ = self.pool[0]
            move_overlay_without_redraw(win, tx - 14, ty - 14)
        self.last_target = (tx, ty)
        return self.last_target

    def _refresh_live_bounds(self, action, box):
        """Reposition detection outlines only when the live matched box changes."""
        area = action.get("area", [0, 0, 0, 0])
        search_rect = tuple(int(v) for v in area)
        if self._area_rendered != search_rect:
            self._create_area_window()
            ax, ay, aw, ah = search_rect
            pad = 2
            self.area_canvas.configure(width=aw + 4, height=ah + 4)
            self.area_canvas.delete("all")
            self.area_canvas.create_rectangle(pad, pad, aw + pad, ah + pad,
                                              outline=SYNTAX_PURPLE, width=2,
                                              dash=(4, 4))
            position_toplevel_on_screen(self.area_win, ax - pad, ay - pad,
                                        aw + 4, ah + 4)
            self.area_win.attributes("-alpha", 0.85)
            self.area_win.deiconify()
            self._area_rendered = search_rect

        # Do not fabricate a bounding box if there is no current match.
        if box is None:
            if self.color_win is not None and self.color_win.winfo_exists():
                if self.color_win.winfo_viewable():
                    self.color_win.withdraw()
            self._color_rendered = None
        elif box != self._color_rendered:
            self._create_color_window()
            x1, y1, x2, y2 = box
            w, h = max(1, x2 - x1), max(1, y2 - y1)
            self.color_canvas.configure(width=w + 4, height=h + 4)
            self.color_canvas.delete("all")
            self.color_canvas.create_rectangle(2, 2, w + 2, h + 2,
                                               outline=COLOR_SUCCESS, width=2)
            position_toplevel_on_screen(self.color_win, x1 - 2, y1 - 2,
                                        w + 4, h + 4)
            self.color_win.attributes("-alpha", 0.90)
            self.color_win.deiconify()
            self._color_rendered = box

    def start_live_preview(self, app, index):
        """Continuously identify the color while keeping the overlays visible.

        Scanning is done on a worker thread so huge screen regions do not block
        Tk pointer motion. Only the UI thread moves/paints the overlay windows.
        """
        self.stop_live_preview()
        self._live_app = app
        self._live_index = index
        self._live_next_scan = 0.0
        self._live_is_found = self.last_found_box is not None
        self._live_queue = Queue()
        self._live_tick(self._live_generation)

    def stop_live_preview(self):
        self._live_generation += 1
        self._live_app = None
        self._live_index = None
        self._live_next_scan = 0.0
        if self._live_timer is not None:
            try: self.root.after_cancel(self._live_timer)
            except (tk.TclError, ValueError): pass
            self._live_timer = None
        # Any in-flight worker is allowed to finish; its result is discarded
        # because the editing session's generation has changed.
        self._live_worker = None
        self._area_rendered = None
        self._color_rendered = None

    def _live_tick(self, generation):
        if generation != self._live_generation or self._live_app is None:
            return
        app = self._live_app
        idx = self._live_index
        if (idx is None or idx >= len(app.recorded_actions) or
                getattr(app, "_reticle_edit_index", None) != idx or
                app.recorded_actions[idx].get("type") not in ("find_color", "find_text") or
                app.playing or app.recording or app.chain_running):
            app.stop_reticle_edit()
            return

        try:
            while True:
                result_generation, box, error = self._live_queue.get_nowait()
                if result_generation != generation:
                    continue
                action = app.recorded_actions[idx]
                self._live_is_found = box is not None
                if box is not None:
                    self.last_found_box = box
                self._refresh_live_bounds(action, box)
                tx, ty = self._move_target_only(action)
                if box is not None:
                    x1, y1, x2, y2 = box
                    app._set_color_live_label(
                        f"● LIVE MATCH ({x1}, {y1}) {x2-x1}×{y2-y1}px"
                        f"  → Reticle ({tx}, {ty})", COLOR_SUCCESS)
                elif error:
                    app._set_color_live_label(f"● Scan error: {error}", COLOR_DANGER)
                else:
                    app._set_color_live_label("○ LIVE • No matching target (last anchor held)",
                                              SYNTAX_ORANGE)
        except Empty:
            pass

        now = time.monotonic()
        if (now >= self._live_next_scan and
                (self._live_worker is None or not self._live_worker.is_alive())):
            act = app.recorded_actions[idx]
            region = tuple(act.get("area", [0, 0, 0, 0]))
            
            self._live_next_scan = now + self._live_interval
            results = self._live_queue

            def worker():
                try:
                    if act.get("type") == "find_color":
                        color = act.get("color", "#FFFFFF")
                        tolerance = act.get("tolerance", 8)
                        box = app.search_color_in_area(*region, color, tolerance=tolerance)
                    else:
                        text = act.get("text", "")
                        box = app.find_text_rect(*region, text)
                    results.put((generation, box, None))
                except Exception as exc:
                    results.put((generation, None, str(exc)))

            self._live_worker = Thread(target=worker, daemon=True)
            self._live_worker.start()
        self._live_timer = self.root.after(60, lambda: self._live_tick(generation))

    def update(self, actions, index, app=None):
        if not actions or index < 0 or index >= len(actions):
            self.hide()
            return

        act = actions[index]
        area_rect = None
        color_rect = None
        self.last_target = None

        if act.get("type") in ("move", "click", "wait_pixel", "scroll", "find_color", "search_area", "wait_text", "find_text", "ui_element", "find_image"):
            if act.get("type") in ("find_color", "find_text", "ui_element", "find_image"):
                area = act.get("area", [0, 0, 0, 0])
                if act.get('type') != 'ui_element':
                    area_rect = (area[0], area[1], area[2], area[3], 0.85)
                if self.edit_active:
                    found_box = self.last_found_box
                elif app is not None:
                    for overlay in (self.area_win, self.color_win):
                        if overlay is not None and overlay.winfo_exists():
                            overlay.withdraw()
                    for win, _ in self.pool:
                        if win.winfo_exists():
                            win.withdraw()
                    self.root.update_idletasks()
                    
                    try:
                        if act.get("type") == "find_color":
                            found_box = app.search_color_in_area(*area, act.get("color", "#FFFFFF"), tolerance=act.get("tolerance", 8))
                        elif act.get('type') == 'find_text':
                            found_box = app.find_text_rect(*area, act.get("text", ""))
                        elif act.get('type') == 'find_image':
                            found_box = app.dynamic_engine.find_image(act)
                        else:
                            found_box = app.dynamic_engine.find_ui_element(act)
                    except (MacroExecutionError, ValueError, OSError) as exc:
                        found_box = None
                        app.update_status(f"Dynamic target preview: {exc}", COLOR_DANGER)
                else:
                    found_box = None

                self.last_found_box = found_box
                if found_box:
                    x1, y1, x2, y2 = found_box
                    rx, ry = color_click_target(act, found_box)
                    color_rect = (x1, y1, x2 - x1, y2 - y1, 0.85)
                else:
                    if act.get('type') == 'ui_element':
                        self.show_multi([])
                        return
                    ax = area[0] + area[2] // 2
                    ay = area[1] + area[3] // 2
                    rx, ry = ax + int(act.get("ox", 0)), ay + int(act.get("oy", 0))
                self.last_target = (rx, ry)
                is_click = True
            elif act.get("type") in ("search_area", "wait_text"):
                area = act.get("area", [0, 0, 0, 0])
                self.show_multi([], area_rect=(area[0], area[1], area[2], area[3], 0.85))
                return
            else:
                self.last_found_box = None
                rx, ry = int(act.get("x", 0)), int(act.get("y", 0))
                is_click = act.get("type") in ("click", "scroll")

            self.show_multi([(rx, ry, is_click, 0.85)],
                            area_rect=area_rect, color_rect=color_rect)
        else:
            self.hide()

    def hide(self):
        self.stop_live_preview()
        self.disable_drag()
        self.last_found_box = None
        self.last_target = None
        if self.hide_timer:
            try: self.root.after_cancel(self.hide_timer)
            except Exception: pass
            self.hide_timer = None
        for win, _ in self.pool:
            if win.winfo_exists():
                win.withdraw()
        if self.area_win and self.area_win.winfo_exists():
            self.area_win.withdraw()
        if self.color_win and self.color_win.winfo_exists():
            self.color_win.withdraw()


# --- Interactive, non-destructive guided tour ---
class SequentWalkthrough:
    """Highlight existing controls without touching macro state or relaying input.

    Four thin borderless windows draw a spotlight around the real widget.
    Only the floating explanation card receives tour navigation input. Unlike
    a full-window overlay this doesn't resize or re-parent the application UI.
    """
    STEPS = (
        ("playback", "Welcome to Sequent",
         "This tour points out the main tools without recording, running, or editing anything. "
         "Use Next and Back to explore. Press Esc to leave whenever you want.", "hamburger"),
        ("playback", "Your Action Queue",
         "Recorded mouse and keyboard actions appear here in order. "
         "Click a line to inspect it, or double-click an action to edit its options.", "queue_viewport"),
        ("playback", "Insert and configure actions",
         "Right-click in the Action Queue or use its line gutter to add actions. "
         "The Action Library lets you choose waits, text recognition, conditions and other steps.", "queue_viewer"),
        ("playback", "Import an existing macro",
         "Choose Import above the Action Queue to open a saved JSON macro file. "
         "Its actions will appear in the queue for you to review or edit. "
         "Importing does not run the macro, but it replaces the current queue, so export any unsaved work first.",
         "import_macro_button"),
        ("playback", "Load a saved preset",
         "The Preset selector in the top toolbar lists JSON macros saved in Sequent's presets folder. "
         "Pick one to load it into the queue, or use Refresh to update the list. "
         "Selecting a preset does not execute it.", "preset_card"),
        ("playback", "Mini HUD",
         "Mini HUD hides the main workspace and opens a compact floating bar. "
         "The bar shows execution status and has Pause, Stop and Expand controls; "
         "drag its grip to move it, and choose Expand to return to the full workspace. "
         "The tour will not activate it.", "mini_hud_button"),
        ("playback", "Record what you do",
         "Start Recording captures input events. Stop it with the configured hotkey, "
         "then review the recorded steps before replaying them. This tour won't start recording.", "record_btn"),
        ("playback", "Run your macro",
         "Run Macro replays the queued actions. Confirm the target application and "
         "test with harmless actions first, especially before automating business transactions.", "play_btn"),
        ("playback", "Inspect and debug",
         "Use the playhead, Back/Next, Step, and breakpoints in the queue "
         "to inspect or execute individual actions while troubleshooting.", "scrub_card"),
        ("sequencer", "Build a macro chain",
         "The Sequencer combines saved macro presets into a sequence of stages. "
         "Import, export, and review the stage order in the Macro Chain panel.", "seq_timeline_viewport"),
        ("sequencer", "Configure a stage",
         "Choose a saved preset, set its loop count and delay, then select Add Stage. "
         "The Stage Builder also controls running the entire chain.", "stage_builder_dock"),
        ("settings", "Personalize Sequent",
         "Preferences contains appearance, timing, recording, audio, and hotkeys. "
         "Entertain Me controls the interface animations. You can relaunch this tour from Help anytime.", "frame_settings"),
    )

    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.index = 0
        self._closed = False
        self._position_job = None
        self._navigate = False
        self.original_tab = app.current_tab_name or "playback"
        self.original_sidebar_open = app.sidebar_open
        self.edges = []
        for _ in range(4):
            edge = tk.Toplevel(self.root)
            edge.overrideredirect(True)
            edge.configure(bg=COLOR_ACCENT)
            edge.attributes("-topmost", True)
            edge.withdraw()
            self.edges.append(edge)

        self.card = tk.Toplevel(self.root)
        self.card.overrideredirect(True)
        self.card.attributes("-topmost", True)
        self.card.configure(bg=COLOR_ACCENT)
        self.card.withdraw()
        shell = tk.Frame(self.card, bg=BG_HEADER, padx=17, pady=14)
        shell.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self.kicker = tk.Label(shell, text="SEQUENT  /  WALKTHROUGH", bg=BG_HEADER,
                               fg=SYNTAX_BLUE, font=("Segoe UI", 8, "bold"), anchor="w")
        self.kicker.pack(fill=tk.X)
        self.title = tk.Label(shell, bg=BG_HEADER, fg=TEXT_PRIMARY,
                              font=("Segoe UI", 15, "bold"), anchor="w")
        self.title.pack(fill=tk.X, pady=(8, 5))
        self.description = tk.Label(shell, bg=BG_HEADER, fg=TEXT_MUTED,
                                    font=("Segoe UI", 10), wraplength=320,
                                    justify="left", anchor="nw")
        self.description.pack(fill=tk.X, pady=(0, 14))
        self.progress = tk.Label(shell, bg=BG_HEADER, fg=TEXT_MUTED,
                                 font=("Consolas", 9), anchor="w")
        self.progress.pack(fill=tk.X, pady=(0, 7))
        btn_row = tk.Frame(shell, bg=BG_HEADER)
        btn_row.pack(fill=tk.X)
        self.btn_exit = tk.Button(btn_row, text="Exit tour", command=self.close,
                                   bg=BG_PANEL, fg=TEXT_PRIMARY, activebackground=BG_HOVER,
                                   activeforeground=TEXT_PRIMARY, relief="flat", bd=0,
                                   padx=10, pady=7, cursor="hand2")
        self.btn_exit.pack(side=tk.LEFT)
        self.btn_next = tk.Button(btn_row, text="Next  →", command=self.next,
                                   bg=COLOR_ACCENT, fg=TEXT_ON_ACCENT, activebackground=COLOR_ACCENT_HOV,
                                   activeforeground="#ffffff", relief="flat", bd=0,
                                   padx=12, pady=7, cursor="hand2")
        self.btn_next.pack(side=tk.RIGHT)
        self.btn_back = tk.Button(btn_row, text="←  Back", command=self.back,
                                   bg=BG_PANEL, fg=TEXT_PRIMARY, activebackground=BG_HOVER,
                                   activeforeground=TEXT_PRIMARY, relief="flat", bd=0,
                                   padx=10, pady=7, cursor="hand2")
        self.btn_back.pack(side=tk.RIGHT, padx=(0, 7))
        self.card.bind("<Escape>", lambda e: self.close())
        self.card.bind("<Right>", lambda e: self.next())
        self.card.bind("<Left>", lambda e: self.back())
        self._root_escape_id = self.root.bind("<Escape>", lambda e: self.close(), add="+")
        self._root_configure_id = self.root.bind("<Configure>", self._queue_position, add="+")
        self._workspace_configure_id = self.app.workspace.bind(
            "<Configure>", self._queue_position, add="+"
        )
        self.show_step(0)

    def _queue_position(self, event=None):
        if self._closed or self._position_job is not None:
            return
        self._position_job = self.root.after(70, self._refresh_position)

    def _refresh_position(self):
        self._position_job = None
        if not self._closed:
            self._position()

    def show_step(self, number):
        if self._closed:
            return
        self.index = max(0, min(len(self.STEPS) - 1, number))
        tab, heading, info, _target = self.STEPS[self.index]
        for edge in self.edges:
            edge.withdraw()
        self.card.withdraw()
        self._navigate = True
        try:
            if self.app.current_tab_name != tab:
                # Finish any pending transition. The tour NEVER animates or
                # re-parents the real control hierarchy while highlighting it.
                self.app.show_tab(tab, animate=False)
            else:
                self.app._settle_tab_transition()
        finally:
            self._navigate = False
        self.title.configure(text=heading)
        self.description.configure(text=info)
        self.progress.configure(text=f"STEP {self.index + 1} OF {len(self.STEPS)}")
        self.btn_back.configure(state="normal" if self.index else "disabled")
        self.btn_next.configure(text="Finish  ✓" if self.index == len(self.STEPS) - 1 else "Next  →")
        self._queue_position()

    def _position(self):
        if self._closed:
            return
        tab, _, _, attr = self.STEPS[self.index]
        target = getattr(self.app, attr, None)
        if target is None or not target.winfo_exists() or not target.winfo_ismapped():
            # A newly selected dock needs an idle layout pass before geometry
            # becomes available. Don't spin indefinitely if a widget is absent.
            return
        self.root.update_idletasks()
        x, y = target.winfo_rootx(), target.winfo_rooty()
        w, h = target.winfo_width(), target.winfo_height()
        if w < 8 or h < 8:
            return
        pad, thick = 4, 3
        x0, y0 = x - pad, y - pad
        x1, y1 = x + w + pad, y + h + pad
        regions = (
            (x0, y0, x1-x0, thick),
            (x0, y1-thick, x1-x0, thick),
            (x0, y0, thick, y1-y0),
            (x1-thick, y0, thick, y1-y0),
        )
        for edge, (rx, ry, rw, rh) in zip(self.edges, regions):
            edge.geometry(f"{max(1,rw)}x{max(1,rh)}+{rx}+{ry}")
            edge.deiconify()

        # Place the explanation near the highlight, but within the desktop.
        self.card.update_idletasks()
        cw, ch = self.card.winfo_reqwidth(), self.card.winfo_reqheight()
        desktop_left = self.root.winfo_rootx()
        desktop_top = self.root.winfo_rooty()
        desktop_right = desktop_left + self.root.winfo_width()
        desktop_bottom = desktop_top + self.root.winfo_height()
        gap = 14
        if x1 + gap + cw <= desktop_right:
            cx, cy = x1 + gap, y
        elif x0 - gap - cw >= desktop_left:
            cx, cy = x0 - gap - cw, y
        elif y1 + gap + ch <= desktop_bottom:
            cx, cy = x, y1 + gap
        elif y0 - gap - ch >= desktop_top:
            cx, cy = x, y0 - gap - ch
        else:
            cx, cy = desktop_right - cw - 12, desktop_top + 72
        cx = max(desktop_left + 8, min(cx, desktop_right - cw - 8))
        cy = max(desktop_top + 8, min(cy, desktop_bottom - ch - 8))
        self.card.geometry(f"{cw}x{ch}+{int(cx)}+{int(cy)}")
        self.card.deiconify()
        self.card.lift()

    def next(self):
        if self.index == len(self.STEPS) - 1:
            self.close()
        else:
            self.show_step(self.index + 1)

    def back(self):
        if self.index > 0:
            self.show_step(self.index - 1)

    def close(self, restore=True):
        if self._closed:
            return
        self._closed = True
        if self._position_job is not None:
            try:
                self.root.after_cancel(self._position_job)
            except tk.TclError:
                pass
        for seq, bind_id in (("<Escape>", self._root_escape_id),
                             ("<Configure>", self._root_configure_id)):
            if bind_id:
                try:
                    self.root.unbind(seq, bind_id)
                except tk.TclError:
                    pass
        if self._workspace_configure_id:
            try:
                self.app.workspace.unbind("<Configure>", self._workspace_configure_id)
            except tk.TclError:
                pass
        for window in [self.card, *self.edges]:
            try:
                window.destroy()
            except tk.TclError:
                pass
        if getattr(self.app, "_walkthrough", None) is self:
            self.app._walkthrough = None
        if restore and self.root.winfo_exists() and self.original_tab != self.app.current_tab_name:
            self.app.show_tab(self.original_tab, animate=False)

# --- Main Application ---
class BuiltInMacroApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Sequent")

        # 1. Tell Windows this is a distinct app so it gets its own taskbar grouping
        if os.name == 'nt':
            try:
                myappid = 'sequent.macro.engine.1' # Arbitrary unique ID
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
            except Exception:
                pass

        # 2. Apply the icon to the window and taskbar
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sequentTempLogo.ico")
        if os.path.exists(icon_path):
            try:
                self.root.iconbitmap(icon_path)
            except Exception:
                pass

        # Dynamic DPI Scaling & Centering
        self.dpi_scale = get_dpi_scale()
        win_w = int(1150 * self.dpi_scale)
        win_h = int(750 * self.dpi_scale)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        pos_x = max(0, (sw - win_w) // 2)
        pos_y = max(0, (sh - win_h) // 2)

        self.root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
        # Also protect programmatic size changes (not only edge-drag handles).
        self.root.minsize(int(940 * self.dpi_scale), int(700 * self.dpi_scale))
        self.root.configure(bg=BG_APP)
        self.root.attributes('-topmost', False)

        # Use a *managed* Windows window with a custom-painted client area.
        # The former override-redirect + geometry() resize path remains only
        # for non-Windows hosts. SEQUENT_WINDOW_MODE=native is a safe fallback.
        self.native_window_resize = (
            os.environ.get('SEQUENT_WINDOW_MODE', 'custom').strip().lower()
            == 'native'
        )
        self.native_chrome = None
        self.root.resizable(True, True)
        if os.name == 'nt':
            if not self.native_window_resize:
                self.native_chrome = install_sequent_chrome(self.root)
                self.set_app_window()
        else:
            self.root.overrideredirect(True)

        self.drag_x = 0
        self.drag_y = 0

        self.recording = False
        self.recording_paused = False
        self.playing = False
        self.is_binding = False
        self.active_hotkey_field = None
        self.recorded_actions = []
        self.active_keys = []
        self.variables = {}
        self.breakpoints = set()
        self.current_action_idx = 0
        self.playback_speed = 1.0
        self.action_delay = 0.20
        self.fluid_mouse = True
        self.current_source_label = "Recorded Sequence"
        self.switching_preset = False
        self._walkthrough = None
        
        # Playback Thread Synchronization IDs
        self.playback_run_id = 0
        self._playback_ui_pending = False

        # Theme & Entertainment Mode State
        self.theme_mode = "Dark"
        self.light_mode = False  # Kept for backward-compatible settings files.
        self.accent_color = DEFAULT_ACCENT_COLOR
        self._effective_theme = "dark"
        self._system_theme_job = None
        self.entertain_me = True
        BuiltInMacroApp.entertain_mode = True
        self._sparkle_particles = []
        self._sparkle_anim_id = None

        # Track mouse movements by default
        self.clicks_and_keys_only = False
        self.natural_timing = False
        self.human_bezier = False
        self.delay_jitter = False
        self.audio_cues = True
        self.window_relative = False

        # Loop & Pause State
        self.loop_mode = "1x"
        self.loop_options = ["1x", "2x", "5x", "10x", "25x", "∞ Loop"]
        self.is_paused = False

        # Mini HUD State
        self.mini_hud = None

        # Mouse Wheel Hook State
        self._wheel_hook = None
        self._wheel_hook_proc = None
        self.last_event_time = time.perf_counter()

        # Sequencer Pipeline State
        self.chain_steps = []
        self.current_chain_stage = -1
        self.chain_running = False

        self.presets_dir = os.path.abspath(os.path.join(os.getcwd(), "presets"))
        if not os.path.exists(self.presets_dir):
            try: os.makedirs(self.presets_dir, exist_ok=True)
            except Exception: pass

        self.chains_dir = os.path.abspath(os.path.join(os.getcwd(), "chains"))
        if not os.path.exists(self.chains_dir):
            try: os.makedirs(self.chains_dir, exist_ok=True)
            except Exception: pass

        # Sidebar transitions preserve the queue's right edge beside Controls.
        # Only the Action Queue dock follows the changing available width;
        # the workspace and Controls never reflow with sidebar animation.
        self.sidebar_open = True
        self.sidebar_width = int(184 * self.dpi_scale)
        self._sidebar_visible_width = self.sidebar_width
        self._sidebar_anim_id = None
        self._theme_rebuilding = False
        self._ui_generation = 0

        self.hotkeys = {
            "start_rec": {"vk": 0x76, "mod_vks": [], "name": "F7"},
            "stop_rec": {"vk": 0x77, "mod_vks": [], "name": "F8"},
            "start_play": {"vk": 0x78, "mod_vks": [], "name": "F9"},
            "stop_play": {"vk": 0x79, "mod_vks": [], "name": "F10"}
        }

        self.load_settings()
        self.dynamic_engine = SequentDynamicEngine(self)
        self._auto_trigger_key = None
        self._auto_trigger_state = None
        self._webhook_server = None
        self._webhook_config = None
        self._webhook_events = Queue()
        # Apply persisted colors BEFORE constructing Tk widgets (no launch flash).
        self._effective_theme = self.resolve_theme_mode()
        self._update_theme_tokens(self._effective_theme)
        self.root.configure(bg=BG_APP)

        self.setup_ui()
        self.ghost_reticle = GhostReticle(self.root)
        self._reticle_edit_index = None
        self.refresh_preset_menu()
        self.setup_resizing_handles()
        Thread(target=self.global_hotkey_listener, daemon=True).start()
        self._schedule_system_theme_poll()
        self.root.after(1200, self._watch_dynamic_trigger)

    
    def set_app_window(self):
        # This helper is also invoked after the Mini HUD and screen selection.
        # Never switch a natively managed window back to borderless mode.
        if getattr(self, 'native_window_resize', False) or getattr(self, 'native_chrome', None):
            return  # Never withdraw/deiconify a live managed native-chrome HWND.
        hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)

        # Tk paints the full client area itself. Extending a DWM glass frame
        # into that area can leave stale paint/compositing artifacts on Windows,
        # especially while a borderless window is repeatedly resized.
        # Leave glass-frame extension disabled by default.
        if os.environ.get("SEQUENT_DWM_FRAME") == "1":
            try:
                margins = MARGINS(1, 1, 1, 1)
                ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
                    hwnd, ctypes.byref(margins))
            except (AttributeError, OSError):
                pass

        self.root.wm_withdraw()
        self.root.after(10, lambda: self.root.wm_deiconify())

    def restore_after_desktop_picker(self):
        """Restore Sequent and refocus the editor after the root has remapped.

        Restore the editor only *after* set_app_window's delayed deiconify,
        otherwise the main window can cover the editor on Windows.
        """
        self.root.deiconify()
        self.set_app_window()
        viewer = getattr(self, "queue_viewer", None)
        if viewer is not None:
            self.root.after(75, viewer.restore_action_editor_focus)

    def start_drag(self, event):
        if self.native_window_resize:
            return
        if self.native_chrome is not None:
            begin_native_window_motion(self.root, 2)  # HTCAPTION = native snap/move
            return
        self.drag_x = event.x
        self.drag_y = event.y

    def drag_window(self, event):
        if self.native_window_resize or self.native_chrome is not None:
            return
        x = self.root.winfo_x() + (event.x - self.drag_x)
        y = self.root.winfo_y() + (event.y - self.drag_y)
        self.root.geometry(f"+{x}+{y}")

    def toggle_maximize_window(self):
        """Native maximize/restore retains the Windows taskbar work-area bounds."""
        if self.root.state() == "zoomed":
            self.root.state("normal")
        else:
            self.root.state("zoomed")

    def minimize_window(self):
        if self.native_window_resize or self.native_chrome is not None:
            self.root.iconify()
            return
        self.root.overrideredirect(False)
        self.root.iconify()
        self.root.bind("<FocusIn>", self.on_restore)

    def on_restore(self, event=None):
        self.root.unbind("<FocusIn>")
        if not self.native_window_resize and self.native_chrome is None:
            self.root.overrideredirect(True)

    def setup_resizing_handles(self):
        # Native Windows sizing handles resizing and snapping; do not install
        # Tk edge grips and geometry() drag handlers in this mode.
        if self.native_window_resize:
            return
        if self.native_chrome is not None:
            # Retain the tiny native resize targets as a compatibility fallback
            # for Tk builds that hit-test the child client HWND first.
            self._native_grips = add_native_resize_grips(self.root, BG_APP)
            return
        # Fallback: Tk geometry-based grips are for non-Windows hosts only.
        self.grip_e = tk.Frame(self.root, bg=BG_APP, cursor="size_we")
        self.grip_e.place(relx=1.0, rely=0, x=-2, y=35, relheight=1.0, width=2)

        self.grip_s = tk.Frame(self.root, bg=BG_APP, cursor="size_ns")
        self.grip_s.place(relx=0, rely=1.0, x=0, y=-2, relwidth=1.0, height=2)

        self.grip_w = tk.Frame(self.root, bg=BG_APP, cursor="size_we")
        self.grip_w.place(relx=0, rely=0, x=0, y=35, relheight=1.0, width=2)

        # Replace the text-based triangle with an invisible 6x6 square in the corner
        self.grip_se = tk.Frame(self.root, bg=BG_APP, cursor="size_nw_se")
        self.grip_se.place(relx=1.0, rely=1.0, x=-6, y=-6, width=6, height=6)

        handles = [
            (self.grip_e, "e"),
            (self.grip_s, "s"),
            (self.grip_w, "w"),
            (self.grip_se, "se")
        ]

        for widget, edge in handles:
            widget.bind("<Button-1>", lambda e, edge=edge: self._start_resize(e, edge))
            widget.bind("<B1-Motion>", self._do_resize)
            widget.bind("<ButtonRelease-1>", self._finish_resize)

    def toggle_mini_hud(self):
        """Collapses the main 740x600 window into a 260x44 floating HUD bar or restores it."""
        if self.mini_hud and self.mini_hud.winfo_exists():
            try: self.mini_hud.destroy()
            except Exception: pass
            self.mini_hud = None
            self.root.deiconify()
            self.set_app_window()
            return

        self.root.withdraw()
        sw = self.root.winfo_screenwidth()
        hud_w, hud_h = 265, 42
        hud_x = sw - hud_w - 25
        hud_y = 35

        self.mini_hud = tk.Toplevel()
        self.mini_hud.geometry(f"{hud_w}x{hud_h}+{hud_x}+{hud_y}")
        self.mini_hud.overrideredirect(True)
        self.mini_hud.attributes("-topmost", True)
        self.mini_hud.configure(bg=BG_HEADER)

        card = tk.Frame(self.mini_hud, bg=BG_HEADER, highlightbackground=COLOR_ACCENT, highlightthickness=1)
        card.pack(fill=tk.BOTH, expand=True)

        lbl_drag = tk.Label(card, text="⠇", font=("Segoe UI", 11), bg=BG_HEADER, fg=TEXT_MUTED, cursor="fleur")
        lbl_drag.pack(side=tk.LEFT, padx=(6, 2))

        def _drag(e):
            self.mini_hud.geometry(f"+{e.x_root - 15}+{e.y_root - 20}")
        lbl_drag.bind("<B1-Motion>", _drag)

        self.hud_status_lbl = tk.Label(card, text="● Sequent", font=("Segoe UI", 8, "bold"), bg=BG_HEADER, fg=TEXT_PRIMARY)
        self.hud_status_lbl.pack(side=tk.LEFT, padx=(2, 8))

        btn_pause = tk.Label(card, text="❚❚", font=("Segoe UI", 8, "bold"), bg=BG_INPUT, fg=TEXT_PRIMARY, cursor="hand2", padx=6, pady=2)
        btn_pause.pack(side=tk.LEFT, padx=(0, 4))
        btn_pause.bind("<Button-1>", lambda e: self.toggle_pause())

        btn_stop = tk.Label(card, text="⏹", font=("Segoe UI", 8, "bold"), bg=BG_INPUT, fg=COLOR_DANGER, cursor="hand2", padx=6, pady=2)
        btn_stop.pack(side=tk.LEFT, padx=(0, 6))
        btn_stop.bind("<Button-1>", lambda e: self.stop_macro())

        btn_expand = tk.Label(card, text="⤢", font=("Segoe UI", 9, "bold"), bg=COLOR_ACCENT, fg=TEXT_ON_ACCENT, cursor="hand2", padx=7, pady=2)
        btn_expand.pack(side=tk.RIGHT, padx=6)
        btn_expand.bind("<Button-1>", lambda e: self.toggle_mini_hud())

    def update_status(self, text, fg_color=TEXT_MUTED):
        def _apply():
            if hasattr(self, 'status_text') and self.status_text.winfo_exists():
                self.status_text.config(text=f"● {text}", fg=fg_color)
            if hasattr(self, "footer_status") and self.footer_status.winfo_exists():
                self.footer_status.config(text=str(text), fg=fg_color)
            if self.mini_hud and self.mini_hud.winfo_exists() and hasattr(self, 'hud_status_lbl'):
                trunc = (text[:14] + "..") if len(text) > 16 else text
                self.hud_status_lbl.config(text=f"● {trunc}", fg=fg_color)
        self.root.after(0, _apply)


    def _start_resize(self, event, edge):
        """Start a resize gesture without forcing synchronous redraws."""
        self._resize_edge = edge
        self._resize_start_x = event.x_root
        self._resize_start_y = event.y_root
        self._resize_start_w = self.root.winfo_width()
        self._resize_start_h = self.root.winfo_height()
        self._resize_start_win_x = self.root.winfo_x()
        self._resize_start_win_y = self.root.winfo_y()
        self._resize_pending_geometry = None
        self._resize_active = True
        self._resize_last_geometry = None
        if getattr(self, '_resize_job', None) is not None:
            self.root.after_cancel(self._resize_job)
            self._resize_job = None

    def _do_resize(self, event):
        """Coalesce rapid mouse events into at most one resize every ~16 ms."""
        if not getattr(self, '_resize_active', False):
            return

        dx = event.x_root - self._resize_start_x
        dy = event.y_root - self._resize_start_y
        w, h = self._resize_start_w, self._resize_start_h
        x, y = self._resize_start_win_x, self._resize_start_win_y

        scale = getattr(self, 'dpi_scale', 1.0)
        min_w = int(940 * scale)
        min_h = int(700 * scale)

        if "e" in self._resize_edge:
            w = max(min_w, self._resize_start_w + dx)
        if "s" in self._resize_edge:
            h = max(min_h, self._resize_start_h + dy)
        if "w" in self._resize_edge:
            possible_w = max(min_w, self._resize_start_w - dx)
            w = possible_w
            x = self._resize_start_win_x + self._resize_start_w - w

        self._resize_pending_geometry = f"{int(w)}x{int(h)}+{int(x)}+{int(y)}"
        if getattr(self, '_resize_job', None) is None:
            self._resize_job = self.root.after(16, self._flush_resize)

    def _flush_resize(self):
        """Apply the newest requested geometry; discard intermediate sizes."""
        self._resize_job = None
        geom = getattr(self, '_resize_pending_geometry', None)
        self._resize_pending_geometry = None
        if geom is not None and self.root.winfo_exists():
            if geom != getattr(self, '_resize_last_geometry', None):
                self.root.geometry(geom)
                self._resize_last_geometry = geom

    def _finish_resize(self, event=None):
        """Apply the final size and repaint after the user releases the mouse."""
        self._resize_active = False
        if getattr(self, '_resize_job', None) is not None:
            self.root.after_cancel(self._resize_job)
            self._resize_job = None
        self._flush_resize()

        # Only repaint after the drag. Forcing redraw on every mouse event
        # causes much more flickering and makes stale child areas more likely.
        if getattr(self, '_resize_repaint_job', None) is not None:
            self.root.after_cancel(self._resize_repaint_job)
        self._resize_repaint_job = self.root.after(60, self._repaint_after_resize)

    def _repaint_after_resize(self):
        self._resize_repaint_job = None
        if not self.root.winfo_exists():
            return
        self.root.update_idletasks()
        # Windows: invalidate the root and all child controls. On other
        # platforms update_idletasks is sufficient, so do nothing further.
        if os.name == 'nt':
            try:
                user32 = ctypes.windll.user32
                hwnd = self.root.winfo_id()
                RDW_INVALIDATE = 0x0001
                RDW_ERASE = 0x0004
                RDW_ALLCHILDREN = 0x0080
                user32.RedrawWindow(
                    hwnd, None, None,
                    RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN
                )
            except (AttributeError, OSError):
                pass


    def get_btn_text(self, action_type):
        hk = self.hotkeys[action_type]["name"]
        suffix = f" ({hk})" if hk and hk != "None" else ""
        if action_type == "start_rec": return f"Start Recording{suffix}"
        elif action_type == "stop_rec": return f"Stop Recording{suffix}"
        elif action_type == "start_play": return f"Run Macro{suffix}"
        elif action_type == "stop_play": return f"Stop Macro{suffix}"

    def shorten_path(self, path, max_len=30):
        if len(path) <= max_len: return path
        return "..." + path[-(max_len - 3):]

    def get_preset_names(self):
        if not os.path.exists(self.presets_dir): return []
        try:
            return sorted([os.path.splitext(f)[0] for f in os.listdir(self.presets_dir) if f.lower().endswith(".json")])
        except Exception: return []

    def setup_ui(self, initial_tab="playback", animate=True):
        """Sequent workspace shell: workspace rail, top toolbar, docked views, and footer."""
        self.main_container = tk.Frame(self.root, bg=BG_APP)
        self.main_container.pack(fill=tk.BOTH, expand=True)

        self.root.bind("<Left>", lambda e: self._on_scrub_nav("prev"))
        self.root.bind("<Right>", lambda e: self._on_scrub_nav("next"))
        self.root.bind("<Up>", lambda e: self._on_scrub_nav("up"))
        self.root.bind("<Down>", lambda e: self._on_scrub_nav("down"))
        self.root.bind("<Home>", lambda e: self._on_scrub_nav("home"))
        self.root.bind("<End>", lambda e: self._on_scrub_nav("end"))
        self.root.bind("<space>", lambda e: self._on_scrub_nav("run_step"))
        for seq, dx, dy in [
            ("<Alt-Left>", -1, 0), ("<Alt-Right>", 1, 0),
            ("<Alt-Up>", 0, -1), ("<Alt-Down>", 0, 1),
            ("<Alt-Shift-Left>", -5, 0), ("<Alt-Shift-Right>", 5, 0),
            ("<Alt-Shift-Up>", 0, -5), ("<Alt-Shift-Down>", 0, 5),
        ]:
            self.root.bind(seq, lambda e, x=dx, y=dy: self.nudge_active_coordinate(x, y))

        top_bar = tk.Frame(self.main_container, bg=BG_HEADER, height=49)
        top_bar.pack(fill=tk.X)
        top_bar.pack_propagate(False)
        top_bar.bind("<Button-1>", self.start_drag)
        top_bar.bind("<B1-Motion>", self.drag_window)

        self.hamburger = tk.Label(top_bar, text="☰", bg=BG_HEADER,
                                  fg=TEXT_MUTED, font=("Segoe UI", 14),
                                  padx=12, cursor="hand2")
        self.hamburger.pack(side=tk.LEFT, fill=tk.Y)
        self.hamburger.bind("<Button-1>", lambda e: self.toggle_sidebar())
        self.hamburger.bind("<Enter>", lambda e: self.hamburger.config(bg=BG_HOVER))
        self.hamburger.bind("<Leave>", lambda e: self.hamburger.config(bg=BG_HEADER))
        ModernTooltip(self.hamburger, "Toggle Workspace Navigation")

        brand = tk.Frame(top_bar, bg=BG_HEADER)
        brand.pack(side=tk.LEFT, padx=(6, 20))
        title = tk.Label(brand, text="SEQUENT", bg=BG_HEADER, fg=TEXT_PRIMARY,
                         font=("Segoe UI", 12, "bold"))
        title.pack(anchor="w")
        tk.Label(brand, text=" MACRO ENGINE", bg=BG_HEADER, fg=COLOR_ACCENT,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w")
        for widget in (brand, title):
            widget.bind("<Button-1>", self.start_drag)
            widget.bind("<B1-Motion>", self.drag_window)

        # Align the Mini HUD once with the expanded sidebar boundary.
        # Its position is independent of sidebar opening/closing animations.
        self._topbar_brand = brand
        self.mini_hud_button = sequent_toolbar_button(top_bar, "▣  Mini HUD", self.toggle_mini_hud)
        self.mini_hud_button.place(x=self.sidebar_width + 12, rely=0.5, anchor=tk.W)
        top_bar.bind("<Configure>", self._position_mini_hud_button, add="+")

        win_controls = tk.Frame(top_bar, bg=BG_HEADER)
        win_controls.pack(side=tk.RIGHT, fill=tk.Y)
        btn_close = tk.Label(win_controls, text="✕", bg=BG_HEADER,
                             fg=TEXT_MUTED, font=("Segoe UI", 10),
                             cursor="hand2", padx=17)
        btn_close.pack(side=tk.RIGHT, fill=tk.Y)
        btn_close.bind("<Button-1>", lambda e: self.root.destroy())
        btn_close.bind("<Enter>", lambda e: btn_close.config(bg=COLOR_DANGER, fg="#fff"))
        btn_close.bind("<Leave>", lambda e: btn_close.config(bg=BG_HEADER, fg=TEXT_MUTED))
        btn_min = tk.Label(win_controls, text="—", bg=BG_HEADER,
                           fg=TEXT_MUTED, font=("Segoe UI", 10),
                           cursor="hand2", padx=16)
        btn_min.pack(side=tk.RIGHT, fill=tk.Y)
        btn_min.bind("<Button-1>", lambda e: self.minimize_window())
        btn_min.bind("<Enter>", lambda e: btn_min.config(bg=BG_HOVER))
        btn_min.bind("<Leave>", lambda e: btn_min.config(bg=BG_HEADER))
        # The native title bar already supplies Windows minimize/close controls.
        if self.native_window_resize:
            win_controls.pack_forget()
        elif self.native_chrome is not None:
            btn_max = tk.Label(win_controls, text="□", bg=BG_HEADER,
                    fg=TEXT_MUTED, font=("Segoe UI", 12), cursor="hand2", padx=15)
            btn_max.pack(side=tk.RIGHT, fill=tk.Y, before=btn_min)
            btn_max.bind("<Button-1>", lambda e: self.toggle_maximize_window())
            btn_max.bind("<Enter>", lambda e: btn_max.config(bg=BG_HOVER))
            btn_max.bind("<Leave>", lambda e: btn_max.config(bg=BG_HEADER))
            # Windows handles double-click-to-maximize on the custom caption.
            top_bar.bind("<Double-Button-1>", lambda e: self.toggle_maximize_window())

        self.status_text = tk.Label(top_bar, text="● Ready", bg=BG_HEADER,
                                    fg=COLOR_SUCCESS, font=("Segoe UI", 9, "bold"))
        self.status_text.pack(side=tk.RIGHT, padx=(4, 16))

        preset_card = ModernCard(top_bar, bg_card=BG_INPUT)
        self.preset_card = preset_card  # Walkthrough highlights the complete preset selector.
        preset_card.pack(side=tk.RIGHT, padx=(0, 12), pady=7)
        tk.Label(preset_card, text="PRESET", bg=BG_INPUT, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT, padx=(10, 6))
        self.preset_var = tk.StringVar(value="-- Select Preset --")
        self.preset_menu = tk.OptionMenu(preset_card, self.preset_var, "-- Select Preset --")
        self.preset_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY,
                                activebackground=BG_HOVER, activeforeground=TEXT_PRIMARY,
                                font=("Segoe UI", 9), highlightthickness=0,
                                bd=0, relief="flat", cursor="hand2")
        self.preset_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY,
                                        activebackground=COLOR_ACCENT,
                                        activeforeground=TEXT_ON_ACCENT, bd=0)
        self.preset_menu.pack(side=tk.LEFT, pady=2)
        self.preset_menu.configure(width=17)
        btn_refresh = sequent_toolbar_button(preset_card, "↻", self.refresh_preset_menu)
        btn_refresh.pack(side=tk.LEFT, padx=(0, 4), pady=2)
        ModernTooltip(btn_refresh, "Refresh Saved Presets")

        tk.Frame(self.main_container, bg=BG_BORDER, height=1).pack(fill=tk.X)
        # Status dock is permanent and visible while switching pages.
        self.footer = tk.Frame(self.main_container, bg=BG_HEADER, height=29)
        self.footer.pack(side=tk.BOTTOM, fill=tk.X)
        self.footer.pack_propagate(False)
        tk.Label(self.footer, text="●", bg=BG_HEADER, fg=COLOR_SUCCESS,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(12, 4))
        self.footer_status = tk.Label(self.footer, text="Ready for input", bg=BG_HEADER,
                                      fg=TEXT_MUTED, font=("Segoe UI", 8), anchor="w")
        self.footer_status.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(self.footer, text="SEQUENT  •  WORKSPACE", bg=BG_HEADER,
                 fg=TEXT_MUTED, font=("Consolas", 8)).pack(side=tk.RIGHT, padx=12)
        tk.Frame(self.main_container, bg=BG_BORDER, height=1).pack(side=tk.BOTTOM, fill=tk.X)

        self.workspace = tk.Frame(self.main_container, bg=BG_APP)
        self.workspace.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        # Docked navigation: the sidebar and content occupy separate regions.
        # Keep the content's width stable while animating its x position, then
        # resize the content once at the end (avoids per-frame widget reflow).
        self.sidebar_width = int(184 * self.dpi_scale)
        self.sidebar_host = tk.Frame(self.workspace, bg=BG_SIDEBAR,
                                     width=self.sidebar_width + 1)
        self.sidebar_host.pack_propagate(False)
        self.sidebar_frame = tk.Frame(self.sidebar_host, bg=BG_SIDEBAR)
        self.sidebar_frame.pack_propagate(False)
        self.sidebar_divider = tk.Frame(self.sidebar_host, bg=BG_BORDER)
        self._sidebar_visible_width = self.sidebar_width if self.sidebar_open else 0
        self._sidebar_committed_width = self._sidebar_visible_width
        self.build_sidebar()
        # Playback and Sequencer have constrained viewports. Both the visible
        # left edge and its own content width animate together, keeping the
        # header, toolbar and editor aligned while Controls stays stationary.
        self.queue_viewport = tk.Frame(
            self.workspace, bg=BG_BORDER,
            highlightthickness=1, highlightbackground=BG_BORDER
        )
        self._queue_animation_active = False
        self._queue_resize_job = None
        # The Macro Chain timeline has its own independent anchored viewport.
        self.seq_timeline_viewport = tk.Frame(
            self.workspace, bg=BG_BORDER,
            highlightthickness=1, highlightbackground=BG_BORDER
        )
        self._seq_timeline_content_width = None
        self.workspace.bind("<Configure>", self._on_workspace_configure, add="+")
        self.workspace_main = tk.Frame(self.workspace, bg=BG_APP)
        # Negative absolute width is an offset against relwidth=1: when docked,
        # the workspace is exactly parent_width - sidebar_width wide.
        self.workspace_main.place(
            x=self._sidebar_visible_width, y=0, relwidth=1,
            width=-self._sidebar_committed_width, relheight=1
        )
        self.sidebar_frame.place(x=0, y=0, width=self.sidebar_width, relheight=1)
        self.sidebar_divider.place(x=self.sidebar_width, y=0, width=1, relheight=1)
        self._set_sidebar_visible_width(self._sidebar_visible_width)
        subhead = tk.Frame(self.workspace_main, bg=BG_APP, height=62)
        subhead.pack(fill=tk.X)
        subhead.pack_propagate(False)
        self.workspace_heading = tk.Label(subhead, text="Playback", bg=BG_APP,
                                           fg=TEXT_PRIMARY, font=("Segoe UI", 16, "bold"))
        self.workspace_heading.pack(side=tk.LEFT, padx=(18, 8), pady=13)
        self.workspace_subtitle = tk.Label(subhead, text="Record, edit and execute actions",
                                            bg=BG_APP, fg=TEXT_MUTED,
                                            font=("Segoe UI", 9))
        self.workspace_subtitle.pack(side=tk.LEFT, pady=16)
        tk.Frame(self.workspace_main, bg=BG_BORDER, height=1).pack(fill=tk.X)

        self.content_frame = tk.Frame(self.workspace_main, bg=BG_APP)
        self.content_frame.pack(fill=tk.BOTH, expand=True)
        self.current_tab_name = None
        self._tab_anim_id = None
        self.frame_playback = tk.Frame(self.content_frame, bg=BG_APP)
        self.frame_sequencer = tk.Frame(self.content_frame, bg=BG_APP)
        # Settings is a peer of the independent Playback/Sequencer docks.
        # If it stays nested in content_frame, the dock windows always paint
        # above it during page transitions, obscuring the Settings controls.
        self.frame_settings = tk.Frame(self.workspace, bg=BG_APP)
        # Settings has a large native-widget form. During a page change we
        # move ONE temporary image of that form, not its live child windows.
        # This makes the whole preferences panel bounce without Tk redraw trails.
        self._settings_motion_view = tk.Canvas(
            self.workspace, bg=BG_APP, highlightthickness=0, bd=0
        )
        self._settings_motion_image = None
        self._settings_motion_active = False
        # Reuse the last fully painted Settings panel when animating back in.
        # Screen-grabbing a freshly mapped form mid-transition can flash it.
        self._settings_cached_image = None
        self.build_playback_view()
        self.build_sequencer_view()
        self.build_settings_view()
        # Separate transition canvas (for page effects, not sidebar movement).
        self.overlay = tk.Canvas(self.root, bg=BG_APP, highlightthickness=0)
        self.show_tab(initial_tab, animate=animate)

    def _on_scrub_nav(self, action):
        focus = self.root.focus_get()
        is_busy = self.playing or (self.recording and not self.recording_paused)
        if isinstance(focus, tk.Entry) or is_busy:
            return
        if self._reticle_edit_index is not None and action in ("prev", "next", "up", "down"):
            shift = bool(ctypes.windll.user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
            amt = 5 if shift else 1
            delta = {"prev": (-amt, 0), "next": (amt, 0),
                     "up": (0, -amt), "down": (0, amt)}[action]
            return self.nudge_active_coordinate(*delta)
        if action == "prev": self.step_backward()
        elif action == "next": self.step_forward()
        elif action == "home": self.jump_to_start()
        elif action == "end":
            if self.recorded_actions:
                self.current_action_idx = len(self.recorded_actions) - 1
                self.update_scrubber_ui()
        elif action == "run_step":
            self.run_single_step()
        return "break"

    def nudge_active_coordinate(self, dx, dy):
        if (self.recording and not self.recording_paused) or (self.playing and not self.is_paused) or not self.recorded_actions:
            return "break"

        if not (0 <= self.current_action_idx < len(self.recorded_actions)):
            return "break"

        act = self.recorded_actions[self.current_action_idx]
        if act.get("type") not in ("move", "click", "wait_pixel", "scroll", "find_color", "find_text"):
            return "break"
            
        if act.get("type") in ("find_color", "find_text"):
            act["ox"] = int(act.get("ox", 0)) + dx
            act["oy"] = int(act.get("oy", 0)) + dy
        else:
            act["x"] = max(0, int(act.get("x", 0)) + dx)
            act["y"] = max(0, int(act.get("y", 0)) + dy)

        line_num = self.current_action_idx + 2
        new_text = self.get_action_summary(act)
        color = self.get_action_color(act)
        tag_name = f"col_{color.replace('#', '')}"

        self.queue_viewer.code_text.delete(f"{line_num}.0", f"{line_num}.end")
        self.queue_viewer.code_text.insert(f"{line_num}.0", new_text, tag_name)
        self.queue_viewer.highlight_action(self.current_action_idx)
        
        self.lbl_active_action.config(
            text=f"Action #{self.current_action_idx + 1}: {new_text}",
            fg=SYNTAX_GREEN
        )

        if act.get("type") in ("find_color", "find_text") and self._reticle_edit_index is not None:
            tx, ty = self.ghost_reticle._move_target_only(act)
            self._set_color_live_label(
                f"● LIVE {'MATCH' if self.ghost_reticle._live_is_found else 'SEARCH'}"
                f"  Reticle ({tx}, {ty})  Offset ({act['ox']:+d}, {act['oy']:+d})",
                COLOR_SUCCESS if self.ghost_reticle._live_is_found else SYNTAX_ORANGE)
        else:
            self.ghost_reticle.update(self.recorded_actions, self.current_action_idx, app=self)
        return "break"

    def _prepare_mouse_execution(self):
        """Remove interactive/transparent preview windows before sending clicks.

        In particular, the drag-enabled Ghost Reticle is a real topmost window.
        Leaving it under the pointer can redirect a synthetic click into the
        reticle instead of the underlying application.
        """
        if self._reticle_edit_index is not None:
            self.stop_reticle_edit()
        elif hasattr(self, "ghost_reticle"):
            self.ghost_reticle.hide()
        self.root.update_idletasks()  # Let Tk/Windows process withdraw requests.

    def _report_color_execution(self, mode, x, y):
        verb = "moved only (NO CLICK)" if mode == "Move Only" else f"sent {mode.lower()}"
        self._set_color_live_label(f"Last color action: {verb} at ({x}, {y})", COLOR_SUCCESS)
        self.update_status(f"Find Color: {verb} at ({x}, {y})", COLOR_SUCCESS)

    def _report_text_execution(self, mode, x, y):
        verb = "moved only (NO CLICK)" if mode == "Move Only" else f"sent {mode.lower()}"
        self._set_color_live_label(f"Last text action: {verb} at ({x}, {y})", COLOR_SUCCESS)
        self.update_status(f"Find Text: {verb} at ({x}, {y})", COLOR_SUCCESS)

    def toggle_reticle_edit(self):
        """Let users drag the real on-screen ghost, including beyond the box."""
        if self._reticle_edit_index is not None:
            self.stop_reticle_edit()
            return
        if self.recording or self.playing or self.chain_running:
            self.update_status("Stop playback/recording before editing the reticle", COLOR_DANGER)
            return
        idx = self.current_action_idx
        if not 0 <= idx < len(self.recorded_actions) or self.recorded_actions[idx].get("type") not in ("find_color", "find_text"):
            self.update_status("Select a Find Color or Find Text action first", SYNTAX_ORANGE)
            return
        try:
            self.ghost_reticle.update(self.recorded_actions, idx, app=self)
        except Exception as exc:
            self.update_status(f"Could not inspect screen: {exc}", COLOR_DANGER)
            return
        if not self.ghost_reticle.enable_drag(self._on_reticle_drag):
            self.update_status("Couldn't display a reticle for this action", COLOR_DANGER)
            return
        self._reticle_edit_index = idx
        self.btn_edit_reticle.config(text="✓ Finish Reticle Editing")
        self._set_color_live_label("◌ LIVE PREVIEW • Searching for matching region…", SYNTAX_BLUE)
        self.ghost_reticle.start_live_preview(self, idx)
        if self.ghost_reticle.last_found_box is None:
            self.update_status("Target isn't visible: preview uses search-area center. Show the target for precise alignment.", SYNTAX_ORANGE)
        else:
            self.update_status("Drag reticle anywhere, or use Arrows (Shift: 5px); Alt+Arrows also work", COLOR_SUCCESS)

    def _on_reticle_drag(self, dx, dy):
        if (self._reticle_edit_index is not None and
                self._reticle_edit_index == self.current_action_idx):
            self.nudge_active_coordinate(dx, dy)

    def _set_color_live_label(self, message, color=TEXT_MUTED):
        label = getattr(self, "lbl_color_live_preview", None)
        if label is not None and label.winfo_exists():
            label.config(text=message, fg=color)

    def stop_reticle_edit(self):
        if hasattr(self, "ghost_reticle"):
            self.ghost_reticle.hide()  # Removes the live bounds when editing ends.
        self._reticle_edit_index = None
        if hasattr(self, "btn_edit_reticle"):
            self.btn_edit_reticle.config(text="⌖ Adjust Ghost Reticle")
        self._set_color_live_label("Preview off • Adjust Ghost Reticle to inspect", TEXT_MUTED)

    def build_sidebar(self):
        nav_head = tk.Frame(self.sidebar_frame, bg=BG_SIDEBAR)
        nav_head.pack(fill=tk.X, padx=14, pady=(20, 18))
        tk.Label(nav_head, text="WORKSPACE", bg=BG_SIDEBAR, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 10, "bold"), anchor="w").pack(fill=tk.X)
        tk.Label(nav_head, text="AUTOMATION STUDIO", bg=BG_SIDEBAR,
                 fg=TEXT_MUTED, font=("Segoe UI", 7, "bold"),
                 anchor="w").pack(fill=tk.X, pady=(3, 0))
        tk.Frame(self.sidebar_frame, bg=BG_BORDER, height=1).pack(fill=tk.X, padx=12, pady=(0, 12))
        self.btn_tab_playback = SidebarButton(
            self.sidebar_frame, text="▣  PLAYBACK",
            command=lambda: self.show_tab("playback"))
        self.btn_tab_playback.pack(fill=tk.X, padx=7, pady=2)
        self.btn_tab_sequencer = SidebarButton(
            self.sidebar_frame, text="⇄  SEQUENCER",
            command=lambda: self.show_tab("sequencer"))
        self.btn_tab_sequencer.pack(fill=tk.X, padx=7, pady=2)
        self.btn_tab_settings = SidebarButton(
            self.sidebar_frame, text="⚙  SETTINGS",
            command=lambda: self.show_tab("settings"))
        self.btn_tab_settings.pack(fill=tk.X, padx=7, pady=2)
        tk.Frame(self.sidebar_frame, bg=BG_BORDER, height=1).pack(fill=tk.X, padx=12, pady=(20, 10))
        self.btn_tab_guide = SidebarButton(
            self.sidebar_frame, text="?  GUIDED TOUR", command=self.start_walkthrough)
        self.btn_tab_guide.pack(fill=tk.X, padx=7, pady=(0, 12))
        tk.Label(self.sidebar_frame, text="Ctrl+F in Action Library\nF7 Record   •   F9 Run",
                 bg=BG_SIDEBAR, fg=TEXT_MUTED, justify="left",
                 font=("Segoe UI", 8)).pack(anchor="w", padx=18)

    def _place_controls_dock(self):
        """Pin Playback's Controls dock to the right side of the window.

        Controls deliberately lives outside workspace_main, whose x-position
        animates with navigation. Its position changes only when the actual
        application window is resized, never during a sidebar transition.
        """
        if not hasattr(self, "control_dock") or not self.control_dock.winfo_exists():
            return
        # workspace_main contains: 62px subheading, 1px separator, then a
        # content frame with 13px margins on all four sides.
        self.control_dock.place(
            relx=1.0, x=-13, y=76, anchor="ne",
            width=289, relheight=1.0, height=-89
        )
        self.control_dock.lift()

    def _queue_available_width(self, sidebar_width):
        """Space between the sidebar gutter and the pinned Controls dock."""
        return max(1, self.workspace.winfo_width() - int(sidebar_width) - 324)

    def _place_action_queue(self, sidebar_width=None, resize_content=True):
        """Animate the queue's left edge and contents, with its right edge fixed.

        Only the Action Queue's internal width changes. The Controls dock and
        parent workspace remain stationary, avoiding whole-workspace reflow.
        """
        if not hasattr(self, "queue_dock") or not self.queue_dock.winfo_exists():
            return
        if sidebar_width is None:
            sidebar_width = self._sidebar_visible_width
        left = int(sidebar_width) + 13
        # Right boundary is 9px left of Controls. Controls has width 289 and
        # a 13px right margin, leaving a 311px total right inset.
        right_inset = 311
        self.queue_viewport.place(
            x=left, y=76, relwidth=1.0, width=-(left + right_inset),
            relheight=1.0, height=-89
        )
        if resize_content:
            target_width = self._queue_available_width(sidebar_width)
            # Avoid redundant geometry requests when an animation tick rounds
            # to the same pixel width as the previous tick.
            if getattr(self, "_queue_content_width", None) != target_width:
                self._queue_content_width = target_width
                self.queue_dock.place_configure(width=target_width)
        self.queue_viewport.lift()
        if hasattr(self, "control_dock") and self.control_dock.winfo_exists():
            self.control_dock.lift()

    def _place_stage_builder_dock(self):
        """Keep Stage Builder pinned to the application's right edge."""
        dock = getattr(self, "stage_builder_dock", None)
        if dock is None or not dock.winfo_exists():
            return
        dock.place(
            relx=1.0, x=-13, y=76, anchor="ne",
            width=289, relheight=1.0, height=-89
        )
        dock.lift()

    def _place_sequencer_timeline(self, sidebar_width=None, resize_content=True):
        """Resize the stage timeline from its left edge; pin its right edge.

        Stage Builder never moves with the sidebar. As the sidebar slides,
        both the timeline's frame and its actual contents resize together.
        """
        dock = getattr(self, "seq_timeline_dock", None)
        if dock is None or not dock.winfo_exists():
            return
        if sidebar_width is None:
            sidebar_width = self._sidebar_visible_width
        left = int(sidebar_width) + 13
        self.seq_timeline_viewport.place(
            x=left, y=76, relwidth=1.0, width=-(left + 311),
            relheight=1.0, height=-89
        )
        if resize_content:
            target_width = self._queue_available_width(sidebar_width)
            if self._seq_timeline_content_width != target_width:
                self._seq_timeline_content_width = target_width
                dock.place_configure(width=target_width)
        self.seq_timeline_viewport.lift()
        if hasattr(self, "stage_builder_dock") and self.stage_builder_dock.winfo_exists():
            self.stage_builder_dock.lift()

    def _prepare_queue_for_transition(self):
        """Mark the animation active without jumping the Queue's width."""
        tab = getattr(self, "current_tab_name", None)
        if tab == "playback":
            if not hasattr(self, "queue_dock") or not self.queue_dock.winfo_exists():
                return
            self._queue_animation_active = True
            self._place_action_queue(self._sidebar_visible_width, resize_content=True)
        elif tab == "sequencer":
            if not hasattr(self, "seq_timeline_dock") or not self.seq_timeline_dock.winfo_exists():
                return
            self._queue_animation_active = True
            self._place_sequencer_timeline(self._sidebar_visible_width, resize_content=True)
        self.sidebar_host.lift()

    def _on_workspace_configure(self, event):
        """Debounce only resize events belonging to the current workspace."""
        if (getattr(self, "_theme_rebuilding", False)
                or event.widget is not getattr(self, "workspace", None)):
            return
        if self._queue_animation_active:
            return
        # One frame at a time while Windows continuously sends WM_SIZE.
        # Cancel-and-reschedule (old 90ms debounce) delayed dock reflow until
        # the user released the mouse, which looked like a frozen screenshot.
        if getattr(self, "_queue_resize_job", None) is not None:
            return
        # Capture the workspace and generation. Old jobs must never be able
        # to manipulate freshly rebuilt controls (or destroyed sidebar paths).
        generation = self._ui_generation
        workspace = self.workspace
        self._queue_resize_job = self.root.after(
            16, lambda: self._settle_queue_resize(workspace, generation)
        )

    def _settle_queue_resize(self, workspace=None, generation=None):
        if (generation is not None and generation != self._ui_generation):
            return
        if (workspace is not None and workspace is not getattr(self, "workspace", None)):
            return
        self._queue_resize_job = None
        if (getattr(self, "_theme_rebuilding", False)
                or getattr(self, "_queue_animation_active", False)):
            return
        sidebar = getattr(self, "sidebar_host", None)
        current_workspace = getattr(self, "workspace", None)
        if (sidebar is None or current_workspace is None
                or not sidebar.winfo_exists() or not current_workspace.winfo_exists()):
            return
        if (getattr(self, "current_tab_name", None) == "playback"
                and hasattr(self, "queue_dock") and self.queue_dock.winfo_exists()):
            self._place_action_queue(self._sidebar_visible_width, resize_content=True)
        elif (getattr(self, "current_tab_name", None) == "sequencer"
                and hasattr(self, "seq_timeline_dock") and self.seq_timeline_dock.winfo_exists()):
            self._place_sequencer_timeline(self._sidebar_visible_width, resize_content=True)
        # The original callback raised "bad window path name" here after
        # theme changes destroyed and replaced sidebar_host.
        sidebar.lift()

    def _sync_controls_dock_visibility(self):
        """Show only the active page's anchored docks, independent of page frames."""
        tab = self.current_tab_name
        if tab == "playback":
            if hasattr(self, "stage_builder_dock"):
                self.stage_builder_dock.place_forget()
            if hasattr(self, "seq_timeline_viewport"):
                self.seq_timeline_viewport.place_forget()
            self._place_action_queue(
                self._sidebar_visible_width,
                resize_content=not self._queue_animation_active
            )
            self._place_controls_dock()
        elif tab == "sequencer":
            if hasattr(self, "control_dock"):
                self.control_dock.place_forget()
            if hasattr(self, "queue_viewport"):
                self.queue_viewport.place_forget()
            self._place_sequencer_timeline(
                self._sidebar_visible_width,
                resize_content=not self._queue_animation_active
            )
            self._place_stage_builder_dock()
        else:
            if hasattr(self, "control_dock"):
                self.control_dock.place_forget()
            if hasattr(self, "queue_viewport"):
                self.queue_viewport.place_forget()
            if hasattr(self, "stage_builder_dock"):
                self.stage_builder_dock.place_forget()
            if hasattr(self, "seq_timeline_viewport"):
                self.seq_timeline_viewport.place_forget()
        if hasattr(self, "sidebar_host") and self.sidebar_host.winfo_exists():
            self.sidebar_host.lift()

    def build_playback_view(self):
        """Sequent layout: action queue as a primary dock, controls as an inspector dock."""
        # Both Playback docks are independent of workspace_main: animation
        # cannot move their boundary beside Controls by shifting the page.
        # Other pages continue to use the normal workspace layout.
        self.control_dock = ModernCard(self.workspace, bg_card=BG_PANEL)
        self.control_dock.pack_propagate(False)
        self._place_controls_dock()
        sequent_dock_header(self.control_dock, "Controls", "REC / PLAY")

        controls = tk.Frame(self.control_dock, bg=BG_PANEL, padx=13, pady=14)
        controls.pack(fill=tk.BOTH, expand=True)
        tk.Label(controls, text="TRANSPORT", bg=BG_PANEL, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(fill=tk.X, anchor="w")
        self.scrub_card = tk.Frame(controls, bg=BG_PANEL)
        self.scrub_card.pack(fill=tk.X, pady=(9, 9))
        step_controls = tk.Frame(self.scrub_card, bg=BG_PANEL)
        step_controls.pack(fill=tk.X, pady=(0, 12))
        for title, callback, hint in [
            ("⏮", self.jump_to_start, "Home"),
            ("◀", self.step_backward, "Previous"),
            ("▶", self.step_forward, "Next"),
            ("⚡ Step", self.run_single_step, "Execute selected action"),
        ]:
            btn = sequent_toolbar_button(step_controls, title, callback)
            btn.pack(side=tk.LEFT, padx=(0, 5))
            ModernTooltip(btn, hint)
        tk.Label(self.scrub_card, text="PLAYHEAD", bg=BG_PANEL, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        scrub_row = tk.Frame(self.scrub_card, bg=BG_PANEL)
        scrub_row.pack(fill=tk.X, pady=(5, 9))
        self.scrub_badge = tk.Label(scrub_row, text="[ 0 / 0 ]",
                                    bg=BG_INPUT, fg=SYNTAX_BLUE,
                                    font=("Consolas", 8, "bold"), padx=7, pady=4)
        self.scrub_badge.pack(side=tk.RIGHT)
        self.scrubber = ModernScrubberTrack(scrub_row, command=self.on_scrub)
        self.scrubber.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 7))
        options = tk.Frame(self.scrub_card, bg=BG_PANEL)
        options.pack(fill=tk.X, pady=(2, 4))
        tk.Label(options, text="Repeat", bg=BG_PANEL, fg=TEXT_MUTED,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.loop_var = tk.StringVar(value=self.loop_mode)
        self.loop_menu = tk.OptionMenu(options, self.loop_var, *self.loop_options,
                                       command=lambda val: setattr(self, 'loop_mode', val))
        self.loop_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=BG_HOVER,
                              activeforeground=TEXT_PRIMARY, font=("Segoe UI", 9),
                              highlightthickness=1, highlightbackground=BG_BORDER,
                              bd=0, relief="flat", cursor="hand2")
        self.loop_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY,
                                      activebackground=COLOR_ACCENT, bd=0)
        self.loop_menu.pack(side=tk.RIGHT)
        tk.Frame(controls, bg=BG_BORDER, height=1).pack(fill=tk.X, pady=(12, 14))
        tk.Label(controls, text="CURRENT ACTION", bg=BG_PANEL, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.lbl_active_action = tk.Label(controls, text="// Queue idle", bg=BG_INPUT,
                                          fg=SYNTAX_BLUE, font=("Consolas", 9),
                                          anchor="w", padx=10, pady=10,
                                          wraplength=245, justify="left")
        self.lbl_active_action.pack(fill=tk.X, pady=(6, 0))
        self.btn_edit_reticle = sequent_toolbar_button(
            controls, "⌖ Adjust Ghost Reticle", self.toggle_reticle_edit)
        self.btn_edit_reticle.pack(fill=tk.X, pady=(8, 0))
        ModernTooltip(self.btn_edit_reticle,
                      "Select Find Color & Click; drag the ghost or use Alt+Arrows to nudge")
        self.lbl_color_live_preview = tk.Label(
            controls, text="Preview off • Adjust Ghost Reticle to inspect",
            font=("Consolas", 8), bg=BG_PANEL, fg=TEXT_MUTED,
            justify="left", anchor="w", wraplength=235)
        self.lbl_color_live_preview.pack(fill=tk.X, pady=(6, 0))
        tk.Frame(controls, bg=BG_BORDER, height=1).pack(fill=tk.X, pady=14)
        tk.Label(controls, text="EXECUTION", bg=BG_PANEL, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.record_btn = ModernButton(controls, text=self.get_btn_text("start_rec"),
                                       bg_color=COLOR_DANGER, hover_color=COLOR_DANGER_HOV,
                                       command=self.toggle_recording)
        self.record_btn.pack(fill=tk.X, pady=(9, 7))
        self.play_btn = ModernButton(controls, text=self.get_btn_text("start_play"),
                                     bg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOV,
                                     command=self.toggle_playback)
        self.play_btn.pack(fill=tk.X, pady=(0, 7))
        self.pause_btn = ModernButton(controls, text="Pause (F9)",
                                      bg_color=BG_BORDER, hover_color=BG_HOVER,
                                      command=self.toggle_pause)
        self.pause_btn.pack(fill=tk.X)
        self.pause_btn.set_state("disabled")
        ModernTooltip(self.record_btn, "Toggle Recording", self.hotkeys["start_rec"]["name"])
        ModernTooltip(self.play_btn, "Run Active Macro", self.hotkeys["start_play"]["name"])
        ModernTooltip(self.pause_btn, "Pause or Resume", "F9")

        self.queue_dock = ModernCard(self.queue_viewport, bg_card=BG_PANEL)
        queue_dock = self.queue_dock
        # Anchor the actual Queue to the viewport's right edge so it doesn't
        # shift with the animated boundary on the left.
        initial_queue_width = self._queue_available_width(self._sidebar_visible_width)
        queue_dock.place(
            relx=1.0, x=0, y=-1, anchor="ne",
            width=initial_queue_width,
            relheight=1.0, height=2
        )
        self._queue_content_width = initial_queue_width
        self._place_action_queue()
        qhead = sequent_dock_header(queue_dock, "Action Queue", "RECORD / EDIT / DEBUG")
        self.action_badge = tk.Label(qhead, text="0 Actions", bg=BG_INPUT,
                                     fg=TEXT_MUTED, font=("Segoe UI", 8, "bold"),
                                     padx=8, pady=3)
        self.action_badge.pack(side=tk.RIGHT, padx=(3, 9))
        filebar = tk.Frame(queue_dock, bg=BG_PANEL, padx=11, pady=9)
        filebar.pack(fill=tk.X)
        self.import_macro_button = sequent_toolbar_button(filebar, "↥  Import", self.load_macro_from_file)
        self.import_macro_button.pack(side=tk.LEFT, padx=(0, 5))
        sequent_toolbar_button(filebar, "↧  Export", self.save_macro_to_file).pack(side=tk.LEFT)
        # The lengthy instruction consumed more width than the queue could
        # supply at its default size. Keep the help accessible via a tooltip.
        help_label = tk.Label(filebar, text="?", bg=BG_PANEL, fg=TEXT_MUTED,
                              font=("Segoe UI", 9, "bold"), padx=7)
        help_label.pack(side=tk.RIGHT)
        ModernTooltip(help_label, "Double-click to edit • Right-click for options")
        self.queue_viewer = VSCodeQueueViewer(queue_dock, app_instance=self)
        self.queue_viewer.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.queue_viewer.append_line("// Awaiting input...", SYNTAX_GREEN)
        for seq, dx, dy in [
            ("<Alt-Left>", -1, 0), ("<Alt-Right>", 1, 0),
            ("<Alt-Up>", 0, -1), ("<Alt-Down>", 0, 1),
            ("<Alt-Shift-Left>", -5, 0), ("<Alt-Shift-Right>", 5, 0),
            ("<Alt-Shift-Up>", 0, -5), ("<Alt-Shift-Down>", 0, 5),
        ]:
            self.queue_viewer.code_text.bind(
                seq, lambda e, x=dx, y=dy: self.nudge_active_coordinate(x, y))

    def build_sequencer_view(self):
        """Independent sequencer docks: animated timeline and pinned Stage Builder."""
        # Match Playback's dock geometry. Both docks belong to the stationary
        # workspace, not workspace_main (which moves during sidebar animation).
        self.stage_builder_dock = ModernCard(self.workspace, bg_card=BG_PANEL)
        right = self.stage_builder_dock
        right.pack_propagate(False)
        self._place_stage_builder_dock()
        sequent_dock_header(right, "Stage Builder", "CONFIGURE")
        config = tk.Frame(right, bg=BG_PANEL, padx=14, pady=14)
        config.pack(fill=tk.BOTH, expand=True)

        def field_title(text):
            tk.Label(config, text=text.upper(), bg=BG_PANEL, fg=TEXT_MUTED,
                     font=("Segoe UI", 8, "bold"), anchor="w").pack(
                         fill=tk.X, pady=(10, 5))
        field_title("Macro preset")
        self.seq_preset_var = tk.StringVar(value="-- Select Preset --")
        self.seq_preset_menu = tk.OptionMenu(config, self.seq_preset_var,
                                             "-- Select Preset --")
        self.seq_preset_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY,
                                    activebackground=BG_HOVER, font=("Segoe UI", 9),
                                    highlightthickness=1, highlightbackground=BG_BORDER,
                                    bd=0, relief="flat", cursor="hand2")
        self.seq_preset_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY,
                                            activebackground=COLOR_ACCENT, bd=0)
        self.seq_preset_menu.pack(fill=tk.X, ipady=3)
        field_title("Loop count")
        self.seq_loops_entry = tk.Entry(config, bg=BG_INPUT, fg=SYNTAX_PURPLE,
                                        insertbackground=TEXT_PRIMARY,
                                        font=("Consolas", 10), bd=0,
                                        highlightbackground=BG_BORDER,
                                        highlightthickness=1)
        self.seq_loops_entry.insert(0, "1")
        self.seq_loops_entry.pack(fill=tk.X, ipady=6)
        field_title("Post-stage delay (seconds)")
        self.seq_delay_entry = tk.Entry(config, bg=BG_INPUT, fg=SYNTAX_BLUE,
                                        insertbackground=TEXT_PRIMARY,
                                        font=("Consolas", 10), bd=0,
                                        highlightbackground=BG_BORDER,
                                        highlightthickness=1)
        self.seq_delay_entry.insert(0, "1.0")
        self.seq_delay_entry.pack(fill=tk.X, ipady=6)
        btn_add_stage = ModernButton(config, text="+ Add Stage",
                                     bg_color=COLOR_ACCENT,
                                     hover_color=COLOR_ACCENT_HOV,
                                     command=self.add_chain_stage)
        btn_add_stage.pack(fill=tk.X, pady=(18, 0))
        tk.Frame(config, bg=BG_BORDER, height=1).pack(fill=tk.X, pady=18)
        tk.Label(config, text="CHAIN TRANSPORT", bg=BG_PANEL, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.btn_run_chain = ModernButton(config, text="▶ Run Macro Chain (F9)",
                                          bg_color=COLOR_SUCCESS,
                                          hover_color="#278761",
                                          command=self.toggle_chain_run)
        self.btn_run_chain.pack(fill=tk.X, pady=(10, 7))
        sequent_toolbar_button(config, "Clear All Stages", self.clear_chain).pack(fill=tk.X)

        self.seq_timeline_dock = ModernCard(
            self.seq_timeline_viewport, bg_card=BG_PANEL
        )
        left = self.seq_timeline_dock
        initial_timeline_width = self._queue_available_width(self._sidebar_visible_width)
        left.place(
            relx=1.0, x=0, y=-1, anchor="ne",
            width=initial_timeline_width,
            relheight=1.0, height=2
        )
        self._seq_timeline_content_width = initial_timeline_width
        self._place_sequencer_timeline()
        head = sequent_dock_header(left, "Macro Chain", "STAGE TIMELINE")
        self.chain_badge = tk.Label(head, text="0 Stages", bg=BG_INPUT,
                                    fg=TEXT_MUTED, font=("Segoe UI", 8, "bold"),
                                    padx=8, pady=3)
        self.chain_badge.pack(side=tk.RIGHT, padx=(3, 9))
        filebar = tk.Frame(left, bg=BG_PANEL, padx=11, pady=9)
        filebar.pack(fill=tk.X)
        sequent_toolbar_button(filebar, "↥  Import Chain", self.load_chain_from_file).pack(
            side=tk.LEFT, padx=(0, 6))
        sequent_toolbar_button(filebar, "↧  Export Chain", self.save_chain_to_file).pack(
            side=tk.LEFT)
        self.chain_scroll = ModernScrollableFrame(left, bg=BG_PANEL)
        self.chain_scroll.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.refresh_chain_ui()

    def refresh_sequencer_preset_dropdown(self):
        menu = self.seq_preset_menu["menu"]
        menu.delete(0, "end")
        presets = self.get_preset_names()

        if not presets:
            menu.add_command(label="-- No Presets Found --", command=lambda: None)
            self.seq_preset_var.set("-- No Presets Found --")
            return

        for p in presets:
            menu.add_command(label=p, command=lambda name=p: self.seq_preset_var.set(name))
        if self.seq_preset_var.get() not in presets:
            self.seq_preset_var.set(presets[0])

    def add_chain_stage(self):
        preset = self.seq_preset_var.get()
        if preset in ("-- Select Preset --", "-- No Presets Found --", ""):
            self.update_status("Select a preset first", COLOR_DANGER)
            return

        try:
            loops = max(1, int(self.seq_loops_entry.get().strip()))
            delay = max(0.0, float(self.seq_delay_entry.get().strip()))
        except ValueError:
            self.update_status("Invalid loop count or delay", COLOR_DANGER)
            return

        self.chain_steps.append({"preset": preset, "loops": loops, "delay_after": delay})
        self.refresh_chain_ui()
        self.update_status(f"Added '{preset}' to Chain", COLOR_SUCCESS)

    def remove_chain_stage(self, idx):
        if self.chain_running: return
        if 0 <= idx < len(self.chain_steps):
            removed = self.chain_steps.pop(idx)
            self.refresh_chain_ui()
            self.update_status(f"Removed stage #{idx + 1}", TEXT_MUTED)

    def move_chain_stage(self, idx, direction):
        if self.chain_running: return
        target = idx + direction
        if 0 <= target < len(self.chain_steps):
            self.chain_steps[idx], self.chain_steps[target] = self.chain_steps[target], self.chain_steps[idx]
            self.refresh_chain_ui()

    def clear_chain(self):
        if self.chain_running: return
        self.chain_steps.clear()
        self.refresh_chain_ui()
        self.update_status("Cleared chain stages", TEXT_MUTED)

    def refresh_chain_ui(self):
        container = self.chain_scroll.scrollable_frame
        for w in container.winfo_children():
            w.destroy()

        count = len(self.chain_steps)
        self.chain_badge.config(
            text=f"{count} Stage{'s' if count != 1 else ''}",
            fg=COLOR_SUCCESS if count > 0 else TEXT_MUTED
        )

        if not self.chain_steps:
            tk.Label(
                container, text="// Chain is empty. Add presets above to build a pipeline.",
                font=("Consolas", 9), bg=BG_APP, fg=TEXT_MUTED
            ).pack(anchor="w", padx=10, pady=20)
            return

        for idx, stage in enumerate(self.chain_steps):
            is_active = (self.chain_running and self.current_chain_stage == idx)
            card_bg = BG_HEADER if is_active else BG_PANEL
            border = COLOR_SUCCESS if is_active else BG_BORDER

            card = ModernCard(container, bg_card=card_bg, border_color=border)
            card.pack(fill=tk.X, pady=(0, 6))

            row = tk.Frame(card, bg=card_bg, padx=10, pady=8)
            row.pack(fill=tk.X)

            # Stage number badge
            num_lbl = tk.Label(
                row, text=f"Stage #{idx + 1}", font=("Segoe UI", 8, "bold"),
                bg=COLOR_ACCENT if not is_active else COLOR_SUCCESS,
                fg=TEXT_ON_ACCENT if not is_active else "#ffffff", padx=6, pady=2
            )
            num_lbl.pack(side=tk.LEFT, padx=(0, 10))

            # Preset Name
            tk.Label(
                row, text=stage["preset"], font=("Consolas", 10, "bold"),
                bg=card_bg, fg=TEXT_PRIMARY
            ).pack(side=tk.LEFT, padx=(0, 12))

            # Loops Badge
            tk.Label(
                row, text=f"{stage['loops']}x Loop", font=("Segoe UI", 8, "bold"),
                bg=BG_INPUT, fg=SYNTAX_PURPLE, padx=6, pady=2,
                highlightbackground=BG_BORDER, highlightthickness=1
            ).pack(side=tk.LEFT, padx=(0, 6))

            # Post-Delay Badge
            tk.Label(
                row, text=f"+{stage['delay_after']:.1f}s Delay", font=("Segoe UI", 8, "bold"),
                bg=BG_INPUT, fg=SYNTAX_BLUE, padx=6, pady=2,
                highlightbackground=BG_BORDER, highlightthickness=1
            ).pack(side=tk.LEFT)

            # Delete Stage Button
            btn_del = tk.Label(row, text=" ✕ ", font=("Segoe UI", 9, "bold"), bg=card_bg, fg=TEXT_MUTED, cursor="hand2")
            btn_del.pack(side=tk.RIGHT, padx=(4, 0))
            btn_del.bind("<Button-1>", lambda e, i=idx: self.remove_chain_stage(i))
            btn_del.bind("<Enter>", lambda e, w=btn_del: w.config(fg="white", bg=COLOR_DANGER))
            btn_del.bind("<Leave>", lambda e, w=btn_del, bg=card_bg: w.config(fg=TEXT_MUTED, bg=bg))

            # Move Down Button
            if idx < count - 1:
                btn_down = tk.Label(row, text=" ▼ ", font=("Segoe UI", 8), bg=BG_INPUT, fg=TEXT_MUTED, cursor="hand2", padx=4, pady=2)
                btn_down.pack(side=tk.RIGHT, padx=(2, 2))
                btn_down.bind("<Button-1>", lambda e, i=idx: self.move_chain_stage(i, 1))

            # Move Up Button
            if idx > 0:
                btn_up = tk.Label(row, text=" ▲ ", font=("Segoe UI", 8), bg=BG_INPUT, fg=TEXT_MUTED, cursor="hand2", padx=4, pady=2)
                btn_up.pack(side=tk.RIGHT, padx=(2, 2))
                btn_up.bind("<Button-1>", lambda e, i=idx: self.move_chain_stage(i, -1))

    def save_chain_to_file(self):
        if not self.chain_steps:
            self.update_status("Chain Empty", COLOR_DANGER)
            return

        self.root.attributes('-topmost', False)
        try:
            file_path = filedialog.asksaveasfilename(
                parent=self.root, title="Export Macro Chain", initialdir=self.chains_dir,
                defaultextension=".chain.json", filetypes=[("Sequent Chain", "*.chain.json"), ("JSON Files", "*.json")]
            )
        finally:
            self.root.attributes('-topmost', False)

        if file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(self.chain_steps, f, indent=2)
                self.update_status("Exported Chain", COLOR_SUCCESS)
            except Exception as e:
                self.update_status(f"Export Error: {e}", COLOR_DANGER)

    def load_chain_from_file(self):
        if self.chain_running: return
        self.root.attributes('-topmost', False)
        try:
            file_path = filedialog.askopenfilename(
                parent=self.root, title="Import Macro Chain", initialdir=self.chains_dir,
                filetypes=[("Sequent Chain", "*.chain.json"), ("JSON Files", "*.json"), ("All Files", "*.*")]
            )
        finally:
            self.root.attributes('-topmost', False)

        if file_path:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, list):
                    validated = []
                    for item in loaded:
                        if isinstance(item, dict) and "preset" in item:
                            validated.append({
                                "preset": str(item["preset"]),
                                "loops": max(1, int(item.get("loops", 1))),
                                "delay_after": max(0.0, float(item.get("delay_after", 1.0)))
                            })
                    self.chain_steps = validated
                    self.refresh_chain_ui()
                    self.update_status("Imported Chain", COLOR_SUCCESS)
                else:
                    self.update_status("Invalid Chain Format", COLOR_DANGER)
            except Exception as e:
                self.update_status(f"Load Error: {e}", COLOR_DANGER)

    def toggle_chain_run(self):
        if not self.chain_running:
            self.start_chain()
        else:
            self.stop_chain()

    def start_chain(self):
        if not self.chain_steps:
            self.update_status("Chain has no stages", COLOR_DANGER)
            return

        self._prepare_mouse_execution()
        self.dynamic_engine = SequentDynamicEngine(self)
        self.chain_running = True
        self.playing = True
        self.is_paused = False
        self.current_chain_stage = 0

        self.btn_run_chain.set_state("normal", text="⏹ Stop Chain (F10)", bg=COLOR_DANGER, hover=COLOR_DANGER_HOV)
        self.pause_btn.set_state("normal", text="Pause (F9)", bg=BG_BORDER, hover=BG_HOVER)
        self.refresh_chain_ui()

        Thread(target=self.run_chain_sequence, daemon=True).start()

    def stop_chain(self):
        self.chain_running = False
        self.playing = False
        self.is_paused = False
        self.current_chain_stage = -1
        self.update_status("Chain Stopped", SYNTAX_ORANGE)
        self.btn_run_chain.set_state("normal", text="▶ Run Macro Chain (F9)", bg=COLOR_SUCCESS, hover="#1d8a4b")
        self.pause_btn.set_state("disabled", text="Pause (F9)", bg=BG_BORDER, hover=BG_HOVER)
        self.refresh_chain_ui()

    def run_chain_sequence(self):
        total_stages = len(self.chain_steps)

        for s_idx in range(total_stages):
            if not self.chain_running: break

            self.current_chain_stage = s_idx
            stage = self.chain_steps[s_idx]
            preset_name = stage["preset"]
            loops = stage["loops"]
            post_delay = stage["delay_after"]

            self.root.after(0, self.refresh_chain_ui)
            self.root.after(0, lambda n=preset_name, idx=s_idx: self.update_status(f"Stage #{idx + 1}: '{n}'", COLOR_ACCENT))

            # Load preset actions
            preset_path = os.path.join(self.presets_dir, f"{preset_name}.json")
            if not os.path.exists(preset_path):
                self.root.after(0, lambda n=preset_name: self.update_status(f"Missing preset: '{n}'", COLOR_DANGER))
                time.sleep(1.0)
                continue

            try:
                with open(preset_path, "r", encoding="utf-8") as f:
                    actions = json.load(f)
            except Exception:
                actions = []

            if not actions:
                continue

            # Run Preset for designated loop repetitions
            for l_num in range(loops):
                if not self.chain_running: break
                if any(a.get('type') in DYNAMIC_ACTION_TYPES for a in actions):
                    try:
                        result = self.dynamic_engine.run_program(actions)
                        if result.get('switch'):
                            raise MacroExecutionError('Preset switching inside a chain stage is not supported')
                    except Exception as error:
                        reason = str(error) if isinstance(error, MacroExecutionError) else type(error).__name__
                        self.chain_running = False
                        self.playing = False
                        self.root.after(0, lambda msg=reason: self._finish_chain_failed(msg))
                        return
                    continue

                # Execute individual preset sequence
                for act in actions:
                    if not self.chain_running: break

                    while self.is_paused and self.chain_running:
                        time.sleep(0.04)

                    try:
                        self.execute_single_action(act)
                    except Exception as error:
                        reason = str(error) if isinstance(error, MacroExecutionError) else type(error).__name__
                        self.chain_running = False
                        self.playing = False
                        self.root.after(0, lambda msg=reason: self._finish_chain_failed(msg))
                        return

                    actual_delay = max(0.01, self.action_delay / self.playback_speed)
                    steps = max(1, int(actual_delay / 0.02))
                    step_delay = actual_delay / steps
                    for _ in range(steps):
                        while self.is_paused and self.chain_running:
                            time.sleep(0.04)
                        if not self.chain_running: break
                        time.sleep(step_delay)

            # Post-stage delay
            if post_delay > 0 and self.chain_running:
                p_steps = max(1, int(post_delay / 0.05))
                for _ in range(p_steps):
                    while self.is_paused and self.chain_running:
                        time.sleep(0.04)
                    if not self.chain_running: break
                    time.sleep(0.05)

        if self.chain_running:
            self.root.after(0, self.finish_chain)

    def _finish_chain_failed(self, reason):
        self.stop_chain()
        self.update_status(f"Chain failed: {reason}", COLOR_DANGER)
        if self.audio_cues:
            play_cue("error")

    def finish_chain(self):
        self.chain_running = False
        self.playing = False
        self.is_paused = False
        self.current_chain_stage = -1
        self.update_status("Chain Complete", COLOR_SUCCESS)
        self.btn_run_chain.set_state("normal", text="▶ Run Macro Chain (F9)", bg=COLOR_SUCCESS, hover="#1d8a4b")
        self.pause_btn.set_state("disabled", text="Pause (F9)", bg=BG_BORDER, hover=BG_HOVER)
        self.refresh_chain_ui()

    def toggle_pause(self):
        if self.recording:
            self.recording_paused = not self.recording_paused
            if self.recording_paused:
                self.update_status("Recording Paused", SYNTAX_ORANGE)
                if self.audio_cues: play_cue("pause")
                self.pause_btn.set_state("normal", text="Resume Rec (F9)", bg=COLOR_SUCCESS, hover="#1d8a4b")
                self.queue_viewer.set_interactive(True)
                self.display_actions_in_queue(self.recorded_actions, "Recorded Sequence")
                if self.recorded_actions:
                    self.current_action_idx = len(self.recorded_actions) - 1
                self.update_scrubber_ui(show_reticle=True)
            else:
                self.queue_viewer.close_sub_menu()
                self.queue_viewer.set_interactive(False)
                self.last_event_time = time.perf_counter()
                self.update_status("Recording...", COLOR_DANGER)
                if self.audio_cues: play_cue("resume")
                self.pause_btn.set_state("normal", text="Pause Rec (F9)", bg=BG_BORDER, hover=BG_HOVER)
                if hasattr(self, 'ghost_reticle'):
                    self.ghost_reticle.hide()
            return

        if not self.playing and not self.chain_running:
            return

        self.is_paused = not self.is_paused
        # Invalidate any backlogged UI callbacks from the running thread
        self.playback_run_id = time.time()
        self._playback_ui_pending = False

        if self.is_paused:
            self.update_status("Paused", SYNTAX_ORANGE)
            if self.audio_cues: play_cue("pause")
            self.pause_btn.set_state("normal", text="Resume (F9)", bg=COLOR_SUCCESS, hover="#1d8a4b")
            self.queue_viewer.set_interactive(True)
            if hasattr(self, 'ghost_reticle'):
                self.ghost_reticle.hide()
            self.update_scrubber_ui(show_reticle=False)
        else:
            self.queue_viewer.close_sub_menu()
            self.queue_viewer.set_interactive(False)
            if hasattr(self, 'ghost_reticle'):
                self.ghost_reticle.hide()
            self.update_status(f"Resuming #{self.current_action_idx + 1}...", COLOR_ACCENT)
            if self.audio_cues: play_cue("resume")
            self.pause_btn.set_state("normal", text="Pause (F9)", bg=BG_BORDER, hover=BG_HOVER)


    def trigger_action(self, action):
        if action == "start_play":
            if self.recording or self.playing or self.chain_running:
                self.root.after(0, self.toggle_pause)
            elif not self.recording:
                self.root.after(0, self.start_macro)
        elif action == "stop_play":
            if self.chain_running:
                self.root.after(0, self.stop_chain)
            elif self.playing:
                self.is_paused = False
                self.root.after(0, self.stop_macro)
        elif action == "start_rec" and not self.recording and not self.playing and not self.chain_running:
            self.root.after(0, self.start_recording)
        elif action == "stop_rec" and self.recording:
            self.root.after(0, self.stop_recording)
       
    def get_action_summary(self, action):
        if not action: return "// No action"
        act_type = action.get("type", "")
        if act_type in DYNAMIC_ACTION_TYPES:
            kind = next((name for name,spec in DYNAMIC_ACTION_SPECS.items() if spec[0]==act_type),act_type)
            if act_type == 'if_dynamic': return f"◆ IF [{action.get('logic','ALL')}] {len(action.get('conditions',[]))} conditions"
            if act_type == 'else_dynamic': return "◇ ELSE"
            if act_type == 'end_if': return "◇ END IF"
            if act_type == 'loop_start': return f"↻ LOOP {action.get('mode','count')} ({action.get('count', '')})"
            if act_type == 'loop_end': return "↻ END LOOP"
            detail = action.get('name') or action.get('match') or action.get('path') or action.get('var_name') or action.get('kind') or ''
            return f"⚙ {kind}({str(detail)[:34]})"
        if act_type == "label": 
            return f"🏷️ flow.label('{action.get('name', '')}')"
        elif act_type == "goto": 
            return f"↪️ flow.goto('{action.get('target', '')}')"
        elif act_type == "disclaimer":
            msg = action.get("message", "")
            preview = (msg[:24] + "..") if len(msg) > 26 else msg
            return f"⚠️ ui.disclaimer('{preview}')"
        elif act_type == "if_var":
            op = action.get("op", "==")
            v_name = action.get("var_name", "status")
            v_val = action.get("val", "")
            t_type = action.get("target_type", "preset")
            tgt = action.get("target", "")
            if t_type == "label":
                return f"🔀 flow.if('{v_name}' {op} '{v_val}' ➔ goto '{tgt}')"
            elif t_type == "preset":
                return f"🔀 flow.if('{v_name}' {op} '{v_val}' ➔ preset '{tgt}')"
            elif t_type == "file":
                fname = os.path.basename(tgt)
                return f"🔀 flow.if('{v_name}' {op} '{v_val}' ➔ run '{fname}')"
        elif act_type == "drag": 
            return f"✋ mouse.drag(({action.get('start_x')},{action.get('start_y')}) ➔ ({action.get('end_x')},{action.get('end_y')}))"
        elif act_type == "scroll": 
            direction = "▲" if int(action.get('delta', 120)) > 0 else "▼"
            return f"🖱️ mouse.scroll({direction} delta={action.get('delta', 120)}, at=({action.get('x', 0)},{action.get('y', 0)}))"
        elif act_type == "wait_pixel": 
            return f"🎯 condition.wait_pixel(x={action.get('x', 0)}, y={action.get('y', 0)}, color='{action.get('color', '#FFFFFF')}')"
        elif act_type == "wait_text":
            return f"🔎 ocr.wait_text({action.get('text', '')!r}, {action.get('mode', 'appears')})"
        elif act_type == "read_clipboard": 
            kws = action.get('keywords', [])
            preview = kws[0] if len(kws) == 1 else f"{len(kws)} keywords"
            return f"📋 clipboard.find('{preview}' ➔ ${action.get('var_name')}='{action.get('value')}')"
        elif act_type == "prompt_input": 
            return f"💬 user.prompt('{action.get('prompt', '')}' ➔ ${action.get('var_name', '')})"
        elif act_type == "type_variable": 
            return f"🔤 keyboard.type_var('${action.get('var_name', '')}')"
        elif act_type == "move": 
            return f"↗️ mouse.move(x={action.get('x', 0)}, y={action.get('y', 0)})"
        elif act_type == "click": 
            btn_glyph = "🖱️ [L]" if action.get('btn') == 'left' else "🖱️ [R]"
            return f"{btn_glyph} mouse.{action.get('btn', 'left')}_click(x={action.get('x', 0)}, y={action.get('y', 0)})"
        elif act_type == "key": 
            return f"⌨️ keyboard.type('{action.get('key', '')}')"
        elif act_type == "press": 
            return f"⌨️ keyboard.press('{action.get('name', '')}')"
        elif act_type == "combo": 
            return f"⚡ keyboard.hotkey('{action.get('name', '')}')"
        elif act_type == "delay": 
            return f"⏱️ system.delay({float(action.get('seconds', 1.0)):.2f}s)"
        elif act_type == "search_area":
            if action.get("match_any") or action.get("text") in ("*", "<ANY TEXT DETECTED>"):
                return "🔍 ocr.search_area(match_any=True)"
            return f"🔍 ocr.search_area('{action.get('text', '')}')"
        elif act_type == "find_color":
            sub_action = action.get('action', 'Single Click')
            verb = "move" if sub_action == "Move Only" else ("double_click" if sub_action == "Double Click" else "click")
            return f"🎯 ocr.find_color('{action.get('color', '')}', offset=({action.get('ox', 0)},{action.get('oy', 0)}), do='{verb}')"
        elif act_type == "find_text":
            sub_action = action.get("action", "Single Click")
            verb = "move" if sub_action == "Move Only" else ("double_click" if sub_action == "Double Click" else "click")
            return f"🎯 ocr.find_text('{action.get('text', '')}', offset=({action.get('ox', 0)},{action.get('oy', 0)}), do='{verb}')"
        elif act_type == "enter": 
            return "↵ keyboard.press('Enter')"
        elif act_type == "backspace": 
            return "⌫ keyboard.press('Backspace')"
        elif act_type == "select_all": 
            return "⚡ command.select_all()"
        elif act_type == "copy": 
            return "📋 command.copy()"
        elif act_type == "paste": 
            return "📌 command.paste()"
        elif act_type == "key_action":
            sub = action.get('action', 'Hold')
            key_name = action.get('key_name', '')
            if sub == 'Hold':
                return f"⬇️ keyboard.hold('{key_name}')"
            elif sub == 'Release':
                return f"⬆️ keyboard.release('{key_name}')"
            else:
                return f"⌨️ keyboard.press('{key_name}')"
        elif act_type == "clip_fallback":
            kws = action.get('keywords', [])
            preview = kws[0] if len(kws) == 1 else f"{len(kws)} keywords"
            return f"🔀 clipboard.fallback(miss='{preview}' ➔ preset '{action.get('preset', '')}')"
        return f"{act_type}"
        

    def get_action_color(self, action):
        act_type = action.get("type", "") if action else ""
        if act_type in ("label", "goto", "if_var", "read_clipboard", "type_variable", "prompt_input"): return SYNTAX_PURPLE
        if act_type in DYNAMIC_ACTION_TYPES: return SYNTAX_GREEN if act_type not in DYNAMIC_FLOW_TYPES else SYNTAX_PURPLE
        if act_type == "disclaimer": return SYNTAX_ORANGE
        if act_type in ("wait_pixel", "wait_text", "find_color", "find_text"): return COLOR_SUCCESS
        if act_type in ("move", "click", "drag", "scroll"): return SYNTAX_BLUE
        if act_type in ("key", "press", "enter", "backspace"): return SYNTAX_ORANGE
        if act_type in ("combo", "select_all", "copy", "paste", "delay", "search_area"): return SYNTAX_PURPLE
        return TEXT_PRIMARY
    

    def update_scrubber_ui(self, show_reticle=True):
        if getattr(self, "_reticle_edit_index", None) is not None:
            if (self.current_action_idx != self._reticle_edit_index or
                self.playing or self.recording or self.chain_running):
                self.stop_reticle_edit()
        total = len(self.recorded_actions)
        if total == 0:
            self.current_action_idx = 0
            self.scrubber.set_position(0, 0)
            self.scrub_badge.config(text="[ 0 / 0 ]", fg=TEXT_MUTED)
            self.lbl_active_action.config(text="// Queue empty", fg=TEXT_MUTED)
            self.queue_viewer.highlight_action(None)
            if hasattr(self, 'ghost_reticle'):
                self.ghost_reticle.hide()
            return

        self.current_action_idx = max(0, min(self.current_action_idx, total - 1))
        self.scrubber.set_position(self.current_action_idx, total)
        self.scrub_badge.config(text=f"[ {self.current_action_idx + 1} / {total} ]", fg=SYNTAX_BLUE)

        current_act = self.recorded_actions[self.current_action_idx]
        desc = self.get_action_summary(current_act)
        self.lbl_active_action.config(text=f"Action #{self.current_action_idx + 1}: {desc}", fg=SYNTAX_GREEN)
        self.queue_viewer.highlight_action(self.current_action_idx)

        if hasattr(self, 'ghost_reticle'):
            # Display reticle when idle or explicitly paused
            is_active_running = (self.playing and not self.is_paused) or (self.recording and not self.recording_paused)
            if self._reticle_edit_index is not None and not is_active_running:
                pass  # Live preview and fast reticle movement own these windows.
            elif not is_active_running and show_reticle:
                self.ghost_reticle.update(self.recorded_actions, self.current_action_idx, app=self)
            else:
                self.ghost_reticle.hide()

    def set_active_action_index(self, index):
        if self._reticle_edit_index is not None and index != self._reticle_edit_index:
            self.stop_reticle_edit()
        self.current_action_idx = index
        self.update_scrubber_ui()

    def set_playback_step(self, index):
        if not self.playing and not self.chain_running:
            return
        total = len(self.recorded_actions)
        if total == 0: return

        self.current_action_idx = max(0, min(index, total - 1))
        self.scrubber.set_position(self.current_action_idx, total)
        self.scrub_badge.config(text=f"[ {self.current_action_idx + 1} / {total} ]", fg=SYNTAX_BLUE)

        current_act = self.recorded_actions[self.current_action_idx]
        desc = self.get_action_summary(current_act)
        self.lbl_active_action.config(text=f"Action #{self.current_action_idx + 1}: {desc}", fg=SYNTAX_GREEN)
        self.queue_viewer.highlight_action(self.current_action_idx)

        # The ghost reticle must remain completely suppressed during active playback updates
        if hasattr(self, 'ghost_reticle'):
            self.ghost_reticle.hide()

    def _on_playback_step(self, index, run_id):
        self._playback_ui_pending = False
        # Drop stale updates from prior loops or invalidated runs
        if run_id != self.playback_run_id or not self.playing:
            return
        self.set_playback_step(index)

    def on_scrub(self, index):
        if (self.recording and not self.recording_paused) or self.playing: return
        self.current_action_idx = index
        self.update_scrubber_ui()

    def jump_to_start(self):
        if (self.recording and not self.recording_paused) or self.playing or not self.recorded_actions: return
        self.current_action_idx = 0
        self.update_scrubber_ui()

    def step_backward(self):
        if (self.recording and not self.recording_paused) or self.playing or not self.recorded_actions: return
        if self.current_action_idx > 0:
            self.current_action_idx -= 1
            self.update_scrubber_ui()

    def step_forward(self):
        if (self.recording and not self.recording_paused) or self.playing or not self.recorded_actions: return
        if self.current_action_idx < len(self.recorded_actions) - 1:
            self.current_action_idx += 1
            self.update_scrubber_ui()

    def run_single_step(self):
        if (self.recording and not self.recording_paused) or self.playing or not self.recorded_actions: return
        if self.current_action_idx >= len(self.recorded_actions):
            self.current_action_idx = 0

        action = self.recorded_actions[self.current_action_idx]
        self._prepare_mouse_execution()
        if any(a.get('type') in DYNAMIC_ACTION_TYPES for a in self.recorded_actions):
            try:
                state=getattr(self,'_dynamic_debug_state',None)
                if not state or state.get('next_index')!=self.current_action_idx:
                    state={'frames':[],'next_index':self.current_action_idx}
                result=self.dynamic_engine.run_program(
                    self.recorded_actions, self.current_action_idx,
                    idle=True, max_actions=1, frames=state['frames'])
                if result.get('switch'):
                    self.update_status('Debugger: preset switch requires regular playback',SYNTAX_ORANGE)
                    return
                next_idx=result['next_index']
                self._dynamic_debug_state={'frames':result.get('frames',[]),
                                           'next_index':next_idx}
                if next_idx < len(self.recorded_actions):
                    self.current_action_idx=next_idx
                    self.update_scrubber_ui()
                    self.update_status(f'Stepped to action #{next_idx+1}',COLOR_SUCCESS)
                else:
                    self.update_status('End of dynamic workflow',COLOR_SUCCESS)
                    self._dynamic_debug_state=None
                return
            except Exception as exc:
                self._dynamic_debug_state=None
                self.update_status(f'Step Error: {exc}',COLOR_DANGER)
                return
        if action.get("type") == "wait_text":
            index = self.current_action_idx
            self.update_status(f"Checking text at step #{index + 1}...", COLOR_ACCENT)
            def worker():
                try:
                    matched = self.wait_for_screen_text(action, allow_idle=True)
                    if not matched:
                        raise MacroExecutionError(f"Text timeout at step {index + 1}")
                except Exception as error:
                    reason = str(error) if isinstance(error, MacroExecutionError) else type(error).__name__
                    self.root.after(0, lambda msg=reason: self.update_status(f"Step failed: {msg}", COLOR_DANGER))
                    return
                self.root.after(0, lambda: self._finish_single_step(index))
            Thread(target=worker, daemon=True).start()
            return

        try:
            self.execute_single_action(action)
        except Exception as error:
            self.update_status(f"Step Error: {error}", COLOR_DANGER)
            return
        self._finish_single_step(self.current_action_idx)

    def _finish_single_step(self, index):
        if self.playing or self.recording or index != self.current_action_idx:
            return
        if index < len(self.recorded_actions) - 1:
            self.current_action_idx += 1
            self.update_scrubber_ui()
            self.update_status(f"Ran step #{index + 1}", COLOR_SUCCESS)
        else:
            self.update_status("End of sequence", COLOR_SUCCESS)
        
    def execute_single_action(self, action):
        act_type = action.get("type")
        def report(message,color):
            self.root.after(0,lambda m=message,c=color:self.update_status(m,c))
        if act_type in DYNAMIC_ACTION_TYPES:
            return self.dynamic_engine.execute(action, idle=not self.playing)
        if act_type == "move":
            tx,ty=action.get('x',0),action.get('y',0)
            if action.get('win_title'):
                hwnd=find_window_by_title_substr(action['win_title'])
                if hwnd:tx,ty=client_to_screen_pos(hwnd,action.get('rel_x',tx),action.get('rel_y',ty))
            if self.human_bezier:
                for x,y in calculate_bezier_trajectory(get_mouse_pos(),(tx,ty),18):
                    move_mouse(x,y)
                    time.sleep(max(0.001,0.008/self.playback_speed))
            else:move_mouse(tx,ty)
        elif act_type == "click":
            tx, ty = action.get("x", 0), action.get("y", 0)
            if action.get('win_title'):
                hwnd=find_window_by_title_substr(action['win_title'])
                if hwnd:tx,ty=client_to_screen_pos(hwnd,action.get('rel_x',tx),action.get('rel_y',ty))
            if self.human_bezier:
                for x,y in calculate_bezier_trajectory(get_mouse_pos(),(tx,ty),14):
                    move_mouse(x,y)
                    time.sleep(max(0.001,0.006/self.playback_speed))
            else:move_mouse(tx, ty)
            wait_for_cursor_settled(tx, ty)
            click(action.get("btn", "left"))
        elif act_type == "scroll":
            tx,ty=action.get('x',0),action.get('y',0)
            if action.get('win_title'):
                hwnd=find_window_by_title_substr(action['win_title'])
                if hwnd:tx,ty=client_to_screen_pos(hwnd,action.get('rel_x',tx),action.get('rel_y',ty))
            move_mouse(tx,ty)
            time.sleep(0.02)
            delta = int(action.get("delta", 120))
            ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, ctypes.c_uint32(delta & 0xFFFFFFFF).value, 0)
        elif act_type == "drag":
            move_mouse(action.get("start_x", 0), action.get("start_y", 0))
            time.sleep(0.02)
            ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            move_mouse(action.get("end_x", 0), action.get("end_y", 0))
            ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        elif act_type == "delay":
            time.sleep(max(0.05, float(action.get("seconds", 1.0))))
        elif act_type == "disclaimer":
            d_title = action.get("title", "Disclaimer Note")
            d_msg = action.get("message", "Proceed with execution?")
            proceed = self.show_disclaimer_modal(d_title, d_msg)
            if not proceed:
                report("Disclaimer cancelled", COLOR_DANGER)
            else:
                report("Disclaimer accepted", COLOR_SUCCESS)
        elif act_type == "prompt_input":
            prompt_txt = action.get("prompt", "Enter input:")
            default_val = action.get("default", "")
            var_name = action.get("var_name", "user_data")
            entered_val = self.prompt_user_input(prompt_txt, default_val)
            if entered_val is not None:
                self.variables[var_name] = str(entered_val)
                if self.audio_cues: play_cue("match")
                report(f"Var '{var_name}' = '{entered_val}'", COLOR_SUCCESS)
        elif act_type == "read_clipboard":
            clip_content = get_clipboard_text().lower()
            keywords = [k.lower() for k in action.get("keywords", [])]
            matched = any(kw in clip_content for kw in keywords)
            var_name = action.get("var_name", "status")
            if matched:
                val = action.get("value", "")
                self.variables[var_name] = val
                if self.audio_cues: play_cue("match")
                report(f"Var '{var_name}' = '{val}'", COLOR_SUCCESS)
            else:
                report(f"Var '{var_name}': No match in clipboard", SYNTAX_ORANGE)
        elif act_type == "type_variable":
            var_name = action.get("var_name", "status")
            val = str(self.variables.get(var_name, ""))
            if val:
                type_string(val, self.playback_speed)
                report(f"Typed '{val}'", COLOR_SUCCESS)
            else:
                report(f"Var '{var_name}' is empty", SYNTAX_ORANGE)
        elif act_type == "key":
            type_string(str(action.get("key", "")), self.playback_speed)
        elif act_type == "press":
            press_virtual_key(action.get("vk", 0))
        elif act_type == "enter":
            press_virtual_key(VK_RETURN)
        elif act_type == "backspace":
            press_virtual_key(VK_BACK)
        elif act_type == "select_all":
            press_key_combo(VK_CONTROL, VK_A)
        elif act_type == "copy":
            press_key_combo(VK_CONTROL, VK_C)
        elif act_type == "paste":
            press_key_combo(VK_CONTROL, VK_V)
        elif act_type == "combo":
            press_key_combo(action.get("mods", []), action.get("vk", 0))
        elif act_type == "wait_text":
            if not self.wait_for_screen_text(action):
                raise MacroExecutionError("Wait Text timed out")
        elif act_type == "wait_pixel":
            tx,ty=action.get('x',0),action.get('y',0)
            if action.get('win_title'):
                hwnd=find_window_by_title_substr(action['win_title'])
                if hwnd:tx,ty=client_to_screen_pos(hwnd,action.get('rel_x',tx),action.get('rel_y',ty))
            expected=action.get('color','#FFFFFF').upper()
            deadline=time.monotonic()+max(0,float(action.get('timeout',15)))
            while True:
                if get_pixel_color(tx,ty)==expected:break
                if time.monotonic()>=deadline:
                    raise MacroExecutionError(f'Pixel {expected} timed out at ({tx},{ty})')
                self.dynamic_engine._pause(0.05,idle=not self.playing)
            if self.audio_cues:play_cue('match')
        elif act_type == "find_color":
            area = action.get("area", [0, 0, 100, 100])
            hex_color = action.get("color", "#FFFFFF").upper()
            found_box = self.search_color_in_area(*area, hex_color,
                            tolerance=action.get("tolerance", 8))

            if found_box:
                mode, tx, ty = perform_color_target_action(action, found_box, self.playback_speed)
                self.root.after(0, lambda b=found_box:
                                self.draw_highlight_box(*b, COLOR_SUCCESS, 800))
                self.root.after(0, lambda m=mode, x=tx, y=ty:
                                self._report_color_execution(m, x, y))
                if self.audio_cues: play_cue("match")
            else:
                raise MacroExecutionError(f"Color {hex_color} not found in search area")
        elif act_type == "find_text":
            area = action.get("area", [0, 0, 100, 100])
            target_text = action.get("text", "")
            found_box = self.find_text_rect(*area, target_text)

            if found_box:
                mode, tx, ty = perform_color_target_action(action, found_box, self.playback_speed)
                self.root.after(0, lambda b=found_box: self.draw_highlight_box(*b, COLOR_SUCCESS, 800))
                self.root.after(0, lambda m=mode, x=tx, y=ty: self._report_text_execution(m, x, y))
                if self.audio_cues: play_cue("match")
            else:
                raise MacroExecutionError(f"Text '{target_text}' not found in search area")


    def update_action_count_badge(self):
        count = len(self.recorded_actions)
        if count == 0: self.action_badge.config(text="0 Actions", fg=TEXT_MUTED)
        elif count == 1: self.action_badge.config(text="1 Action", fg=COLOR_SUCCESS)
        else: self.action_badge.config(text=f"{count} Actions", fg=COLOR_SUCCESS)

    def toggle_breakpoint(self, index):
        if index in self.breakpoints:
            self.breakpoints.remove(index)
            self.update_status(f"Removed Breakpoint (Line {index + 2})", TEXT_MUTED)
        else:
            self.breakpoints.add(index)
            self.update_status(f"Breakpoint set at Line {index + 2}", COLOR_DANGER)
        if hasattr(self, 'queue_viewer'):
            self.queue_viewer.redraw_breakpoints()

    def insert_delay_action(self, index, seconds):
        if (self.recording and not self.recording_paused) or (self.playing and not self.is_paused): return
        delay_action = {"type": "delay", "seconds": float(seconds)}
        self.recorded_actions.insert(index, delay_action)
        self.current_action_idx = index
        self.display_actions_in_queue(self.recorded_actions, self.current_source_label)
        self.update_status(f"Added {seconds:.2f}s Delay", COLOR_SUCCESS)

    def insert_search_area_action(self, index, text, area, preset, match_any=False):
        if (self.recording and not self.recording_paused) or (self.playing and not self.is_paused): return
        search_action = {
            "type": "search_area",
            "text": str(text),
            "area": [int(v) for v in area],
            "preset": str(preset),
            "match_any": bool(match_any)
        }
        self.recorded_actions.insert(index, search_action)
        self.current_action_idx = index
        self.display_actions_in_queue(self.recorded_actions, self.current_source_label)
        desc = "Any Text" if match_any else f"'{text}'"
        self.update_status(f"Added Area Search: {desc}", COLOR_SUCCESS)

    def delete_action(self, index):
        if (self.recording and not self.recording_paused) or (self.playing and not self.is_paused): return
        if 0 <= index < len(self.recorded_actions):
            act = self.recorded_actions.pop(index)
            if self.current_action_idx >= len(self.recorded_actions):
                self.current_action_idx = max(0, len(self.recorded_actions) - 1)
            self.display_actions_in_queue(self.recorded_actions, self.current_source_label)
            act_type = act.get("type", "action")
            self.update_status(f"Deleted {act_type} (Action #{index + 1})", COLOR_DANGER)

    def delete_action_range(self, start_idx, end_idx):
        if (self.recording and not self.recording_paused) or (self.playing and not self.is_paused): return
        start_idx = max(0, start_idx)
        end_idx = min(len(self.recorded_actions) - 1, end_idx)
        if start_idx <= end_idx:
            count = end_idx - start_idx + 1
            del self.recorded_actions[start_idx:end_idx + 1]
            if self.current_action_idx >= len(self.recorded_actions):
                self.current_action_idx = max(0, len(self.recorded_actions) - 1)
            self.display_actions_in_queue(self.recorded_actions, self.current_source_label)
            self.update_status(f"Deleted {count} action(s)", COLOR_DANGER)

    def remove_all_mouse_moves(self):
        if (self.recording and not self.recording_paused) or (self.playing and not self.is_paused): return
        initial_len = len(self.recorded_actions)
        self.recorded_actions = [a for a in self.recorded_actions if a.get("type") != "move"]
        removed = initial_len - len(self.recorded_actions)
        self.current_action_idx = 0
        self.display_actions_in_queue(self.recorded_actions, self.current_source_label)
        self.update_status(f"Removed {removed} mouse move(s)", COLOR_SUCCESS)

    def start_pixel_inspection(self, callback):
        """Creates a crosshair canvas, hiding it before sampling so true desktop colors are captured."""
        self.root.withdraw()
        
        vx = ctypes.windll.user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = ctypes.windll.user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = ctypes.windll.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        vh = ctypes.windll.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if vw <= 0 or vh <= 0:
            vx, vy, vw, vh = 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

        snipper = tk.Toplevel()
        snipper.attributes("-topmost", True, "-alpha", 0.20)
        snipper.configure(bg="#000000")
        snipper.overrideredirect(True)
        snipper.geometry(f"{vw}x{vh}")
        
        position_toplevel_on_screen(snipper, vx, vy, vw, vh, activate=True)
            
        snipper.config(cursor="crosshair")

        def cancel(event=None):
            try: snipper.destroy()
            except Exception: pass
            self.restore_after_desktop_picker()

        def on_click(event):
            x, y = event.x_root, event.y_root
            snipper.withdraw()
            self.root.update_idletasks()
            # Let transparent snipper/other child overlay finish withdrawing.
            self.root.after(60, lambda: finish_sampling(x, y))

        def finish_sampling(x, y):
            hex_color = None
            error = None
            try:
                hex_color = get_pixel_color(x, y)
            except Exception as exc:
                error = str(exc)
            finally:
                try: snipper.destroy()
                except Exception: pass
                self.restore_after_desktop_picker()
            if error:
                self.update_status(f"Color sampling failed: {error}", COLOR_DANGER)
            elif hex_color is not None:
                callback(x, y, hex_color)

        snipper.bind("<Button-1>", on_click)
        snipper.bind("<Button-3>", cancel)
        snipper.bind("<Escape>", cancel)
        snipper.focus_force()

    def start_screen_selection(self, callback):
        self.root.withdraw()

        vx = ctypes.windll.user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = ctypes.windll.user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = ctypes.windll.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        vh = ctypes.windll.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)

        if vw <= 0 or vh <= 0:
            vx, vy = 0, 0
            vw = self.root.winfo_screenwidth()
            vh = self.root.winfo_screenheight()

        snipper = tk.Toplevel()
        snipper.attributes('-topmost', True)
        snipper.attributes('-alpha', 0.28)
        snipper.configure(bg="#000000")
        snipper.overrideredirect(True)
        
        # 1. Establish the size explicitly for Tkinter's layout engine
        snipper.geometry(f"{vw}x{vh}")
        
        # 2. Pack the canvas immediately so it expands to the full size
        canvas = tk.Canvas(snipper, bg="#000000", highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)

        snipper.update_idletasks()
        
        # Place Tk's *outer wrapper* on the virtual desktop, not its child HWND.
        position_toplevel_on_screen(snipper, vx, vy, vw, vh, activate=True)

        snipper.config(cursor="cross")

        banner_x = vw // 2
        canvas.create_text(
            banner_x, 40,
            text="DRAG TO SELECT SEARCH AREA  |  ESC OR RIGHT-CLICK TO CANCEL",
            fill="#ffffff", font=("Segoe UI", 12, "bold")
        )

        state = {"start_root_x": 0, "start_root_y": 0, "rect": None, "closed": False}

        def cancel_snipping(event=None):
            if not state["closed"]:
                state["closed"] = True
                try: snipper.destroy()
                except Exception: pass
                self.restore_after_desktop_picker()

        cancel_badge = tk.Label(
            snipper, text="  ✕ Cancel (Esc / Right-Click)  ",
            bg=COLOR_DANGER, fg="#ffffff", font=("Segoe UI", 9, "bold"), cursor="hand2"
        )
        cancel_badge.place(x=banner_x - 120, y=65)
        cancel_badge.bind("<Button-1>", cancel_snipping)
        cancel_badge.bind("<Enter>", lambda e: cancel_badge.config(bg=COLOR_DANGER_HOV))
        cancel_badge.bind("<Leave>", lambda e: cancel_badge.config(bg=COLOR_DANGER))

        def on_mouse_down(event):
            state["start_root_x"] = event.x_root
            state["start_root_y"] = event.y_root
            c_x = event.x_root - vx
            c_y = event.y_root - vy
            if state["rect"]: canvas.delete(state["rect"])
            state["rect"] = canvas.create_rectangle(
                c_x, c_y, c_x, c_y,
                outline=COLOR_ACCENT, width=2, fill=COLOR_ACCENT, stipple="gray25"
            )

        def on_mouse_drag(event):
            if state["rect"]:
                start_cx = state["start_root_x"] - vx
                start_cy = state["start_root_y"] - vy
                curr_cx = event.x_root - vx
                curr_cy = event.y_root - vy
                canvas.coords(state["rect"], start_cx, start_cy, curr_cx, curr_cy)

        def on_mouse_up(event):
            if state["closed"]: return
            end_root_x, end_root_y = event.x_root, event.y_root
            x = min(state["start_root_x"], end_root_x)
            y = min(state["start_root_y"], end_root_y)
            w = abs(end_root_x - state["start_root_x"])
            h = abs(end_root_y - state["start_root_y"])

            state["closed"] = True
            try: snipper.destroy()
            except Exception: pass
            # Let the action editor return to the foreground once Sequent is
            # finished remapping its main window, regardless of drag size.
            self.restore_after_desktop_picker()
            if w > 8 and h > 8:
                callback([x, y, w, h])
            else:
                self.update_status("Search area unchanged (drag a box at least 9px wide and tall)", SYNTAX_ORANGE)

        canvas.bind("<Button-1>", on_mouse_down)
        canvas.bind("<B1-Motion>", on_mouse_drag)
        canvas.bind("<ButtonRelease-1>", on_mouse_up)
        canvas.bind("<Button-3>", cancel_snipping)
        snipper.bind("<Button-3>", cancel_snipping)
        canvas.bind("<Escape>", cancel_snipping)
        snipper.bind("<Escape>", cancel_snipping)
        snipper.focus_force()

        def poll_hardware_escape():
            if not state["closed"]:
                if (ctypes.windll.user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000) != 0:
                    cancel_snipping()
                    return
                snipper.after(40, poll_hardware_escape)

        snipper.after(40, poll_hardware_escape)

    def capture_screen_area_to_bmp(self, x, y, w, h, filepath):
        """Capture the selected screen rectangle as a BMP for WinRT OCR.

        Typed GDI signatures are required for x64 Python. GetDIBits must be
        called after deselecting the bitmap from its memory device context.
        """
        x, y, w, h = map(int, (x, y, w, h))
        if w <= 0 or h <= 0:
            raise MacroExecutionError("OCR capture area must have positive width and height")
        scale = min(6, max(1, int(160 / h)))
        tw, th = w * scale, h * scale
        if tw * th > 32_000_000:
            raise MacroExecutionError("OCR capture region is too large")

        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        HANDLE = ctypes.c_void_p
        user32.GetDC.argtypes, user32.GetDC.restype = [HANDLE], HANDLE
        user32.ReleaseDC.argtypes, user32.ReleaseDC.restype = [HANDLE, HANDLE], ctypes.c_int
        gdi32.CreateCompatibleDC.argtypes, gdi32.CreateCompatibleDC.restype = [HANDLE], HANDLE
        gdi32.CreateCompatibleBitmap.argtypes = [HANDLE, ctypes.c_int, ctypes.c_int]
        gdi32.CreateCompatibleBitmap.restype = HANDLE
        gdi32.SelectObject.argtypes, gdi32.SelectObject.restype = [HANDLE, HANDLE], HANDLE
        gdi32.SetStretchBltMode.argtypes = [HANDLE, ctypes.c_int]
        gdi32.SetStretchBltMode.restype = ctypes.c_int
        gdi32.StretchBlt.argtypes = [HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, HANDLE, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        gdi32.StretchBlt.restype = ctypes.c_int
        gdi32.GetDIBits.argtypes = [HANDLE, HANDLE, ctypes.c_uint, ctypes.c_uint,
                                  HANDLE, HANDLE, ctypes.c_uint]
        gdi32.GetDIBits.restype = ctypes.c_int
        gdi32.DeleteObject.argtypes, gdi32.DeleteObject.restype = [HANDLE], ctypes.c_int
        gdi32.DeleteDC.argtypes, gdi32.DeleteDC.restype = [HANDLE], ctypes.c_int

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                        ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                        ("biClrImportant", ctypes.c_uint32)]

        stride = ((tw * 3 + 3) // 4) * 4
        image_size = stride * th
        pixels = (ctypes.c_ubyte * image_size)()
        bmi = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER),
                               biWidth=tw, biHeight=th, biPlanes=1,
                               biBitCount=24, biCompression=0, biSizeImage=image_size)
        screen_dc = user32.GetDC(None)
        if not screen_dc:
            raise MacroExecutionError("OCR screenshot: GetDC failed")
        mem_dc = bitmap = selected = None
        try:
            mem_dc = gdi32.CreateCompatibleDC(screen_dc)
            if not mem_dc:
                raise MacroExecutionError("OCR screenshot: CreateCompatibleDC failed")
            bitmap = gdi32.CreateCompatibleBitmap(screen_dc, tw, th)
            if not bitmap:
                raise MacroExecutionError("OCR screenshot: CreateCompatibleBitmap failed")
            selected = gdi32.SelectObject(mem_dc, bitmap)
            if not selected or selected == ctypes.c_void_p(-1).value:
                selected = None
                raise MacroExecutionError("OCR screenshot: SelectObject failed")
            gdi32.SetStretchBltMode(mem_dc, 4)  # HALFTONE
            if not gdi32.StretchBlt(mem_dc, 0, 0, tw, th, screen_dc,
                                    x, y, w, h, 0x00CC0020 | 0x40000000):
                raise MacroExecutionError("OCR screenshot: StretchBlt failed")
            # GDI explicitly requires the bitmap to be deselected before GetDIBits.
            gdi32.SelectObject(mem_dc, selected)
            selected = None
            if gdi32.GetDIBits(screen_dc, bitmap, 0, th, pixels,
                              ctypes.byref(bmi), 0) != th:
                raise MacroExecutionError("OCR screenshot: GetDIBits failed")
            file_header = struct.pack('<2sIHHI', b'BM', 54 + image_size, 0, 0, 54)
            dib_header = struct.pack('<IiiHHIIiiII', 40, tw, th, 1, 24, 0,
                                     image_size, 2835, 2835, 0, 0)
            with open(filepath, 'wb') as f:
                f.write(file_header)
                f.write(dib_header)
                f.write(pixels)
        finally:
            if selected and mem_dc:
                gdi32.SelectObject(mem_dc, selected)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if mem_dc:
                gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, screen_dc)

    def find_text_rect(self, x, y, w, h, target_text, match_any=False):
        """Return the exact matched text's screen bbox (exclusive right/bottom)."""
        x, y, w, h = map(int, (x, y, w, h))
        if w <= 0 or h <= 0:
            raise MacroExecutionError("Find Text requires a positive search area")
        if not str(target_text).strip() and not match_any:
            raise MacroExecutionError("Find Text requires nonempty target text")
        scale = min(6, max(1, int(160 / h)))
        temp_fd, temp_bmp = tempfile.mkstemp(suffix=".bmp")
        os.close(temp_fd)
        try:
            self.capture_screen_area_to_bmp(x, y, w, h, temp_bmp)
            abs_bmp_path = os.path.abspath(temp_bmp).replace("'", "''")
            ps_script = f"""
$ErrorActionPreference = 'Stop'
$stream = $null
try {{
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    [void][Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]
    [void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
    [void][Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
    [void][Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType=WindowsRuntime]
    [void][Windows.Media.Ocr.OcrResult, Windows.Media.Ocr, ContentType=WindowsRuntime]
    [void][Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
    [void][Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Storage.Streams, ContentType=WindowsRuntime]

    $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    }})[0]
    if (-not $asTaskGeneric) {{ throw 'WinRT AsTask method unavailable' }}
    function Await-Op($asyncOp, $type) {{
        $task = $asTaskGeneric.MakeGenericMethod($type).Invoke($null, @($asyncOp))
        if (-not $task.Wait(9000)) {{ throw 'OCR async operation timed out' }}
        return $task.Result
    }}
    $file = Await-Op ([Windows.Storage.StorageFile]::GetFileFromPathAsync('{abs_bmp_path}')) ([Windows.Storage.StorageFile])
    $stream = Await-Op ($file.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
    $decoder = Await-Op ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await-Op ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if (-not $engine) {{
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('en-US'))
    }}
    if (-not $engine) {{
        $available = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages
        if ($available -and $available.Count -gt 0) {{
            $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($available[0])
        }}
    }}
    if (-not $engine) {{ throw 'No OCR language available on this computer' }}
    $recognized = Await-Op ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $results = @()
    $lineIndex = 0
    if ($recognized -and $recognized.Lines) {{
        foreach ($line in $recognized.Lines) {{
            foreach ($word in $line.Words) {{
                $r = $word.BoundingRect
                $results += [PSCustomObject]@{{ Line = $lineIndex; Text = $word.Text;
                    X = $r.X; Y = $r.Y; Width = $r.Width; Height = $r.Height }}
            }}
            $lineIndex++
        }}
    }}
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    ConvertTo-Json -InputObject $results -Compress -Depth 4
}} catch {{
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output ('ERR:' + $_.Exception.Message)
}} finally {{
    if ($stream) {{ $stream.Dispose() }}
}}
"""
            encoded_cmd = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
            try:
                proc = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                     "Bypass", "-EncodedCommand", encoded_cmd],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=0x08000000, timeout=12
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise MacroExecutionError(f"Find Text OCR process failed: {exc}") from exc
            out = proc.stdout.strip().lstrip("\ufeff")
            if out.startswith("ERR:"):
                raise MacroExecutionError(f"Find Text OCR: {out[4:]}")
            if proc.returncode != 0 or not out:
                detail = proc.stderr.strip() or "No OCR response"
                raise MacroExecutionError(f"Find Text OCR failed: {detail[:300]}")
            try:
                words = json.loads(out)
            except json.JSONDecodeError as exc:
                raise MacroExecutionError(f"Find Text OCR returned invalid data: {out[:160]}") from exc
            if not isinstance(words, list):
                words = [words] if words else []
            rect = find_ocr_word_match(words, target_text, match_any)
            if rect is None:
                return None
            x1, y1, x2, y2 = rect
            return (x + math.floor(x1 / scale), y + math.floor(y1 / scale),
                    x + max(math.floor(x1 / scale) + 1, math.ceil(x2 / scale)),
                    y + max(math.floor(y1 / scale) + 1, math.ceil(y2 / scale)))
        finally:
            try:
                os.remove(temp_bmp)
            except OSError:
                pass


    def search_color_in_area(self, x, y, w, h, hex_color, tolerance=8):
        """Capture screen pixels and locate the largest connected color region.

        Returns screen-space (left, top, right, bottom) with exclusive edges.
        Does not combine disconnected UI elements into one enclosing box.
        """
        x, y, w, h = map(int, (x, y, w, h))
        if w <= 0 or h <= 0:
            return None
        color = str(hex_color).strip().lstrip("#")
        if not re.fullmatch(r"[0-9A-Fa-f]{6}", color):
            raise ValueError(f"Invalid RGB color: {hex_color!r}")
        rgb = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        HANDLE = ctypes.c_void_p
        # Explicit signatures are necessary: Win32 GDI handles are 64-bit on
        # 64-bit Python, while ctypes otherwise defaults to a 32-bit int.
        user32.GetDC.argtypes, user32.GetDC.restype = [HANDLE], HANDLE
        user32.ReleaseDC.argtypes, user32.ReleaseDC.restype = [HANDLE, HANDLE], ctypes.c_int
        gdi32.CreateCompatibleDC.argtypes, gdi32.CreateCompatibleDC.restype = [HANDLE], HANDLE
        gdi32.CreateCompatibleBitmap.argtypes = [HANDLE, ctypes.c_int, ctypes.c_int]
        gdi32.CreateCompatibleBitmap.restype = HANDLE
        gdi32.SelectObject.argtypes, gdi32.SelectObject.restype = [HANDLE, HANDLE], HANDLE
        gdi32.BitBlt.argtypes = [HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        gdi32.BitBlt.restype = ctypes.c_int
        gdi32.GetDIBits.argtypes = [HANDLE, HANDLE, ctypes.c_uint, ctypes.c_uint,
                                  HANDLE, HANDLE, ctypes.c_uint]
        gdi32.GetDIBits.restype = ctypes.c_int
        gdi32.DeleteObject.argtypes, gdi32.DeleteObject.restype = [HANDLE], ctypes.c_int
        gdi32.DeleteDC.argtypes, gdi32.DeleteDC.restype = [HANDLE], ctypes.c_int

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                        ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                        ("biClrImportant", ctypes.c_uint32)]

        stride = ((w * 3 + 3) // 4) * 4
        buffer = (ctypes.c_ubyte * (stride * h))()
        bmi = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER),
                               biWidth=w, biHeight=-h, biPlanes=1,
                               biBitCount=24, biCompression=0, biSizeImage=stride * h)

        screen_dc = user32.GetDC(None)
        if not screen_dc:
            raise MacroExecutionError("Could not access screen device context")
        mem_dc = bmp = previous = None
        try:
            mem_dc = gdi32.CreateCompatibleDC(screen_dc)
            if not mem_dc:
                raise MacroExecutionError("Could not create screen capture context")
            bmp = gdi32.CreateCompatibleBitmap(screen_dc, w, h)
            if not bmp:
                raise MacroExecutionError("Could not allocate screen capture bitmap")
            previous = gdi32.SelectObject(mem_dc, bmp)
            if not previous:
                raise MacroExecutionError("Could not select screen capture bitmap")
            if not gdi32.BitBlt(mem_dc, 0, 0, w, h, screen_dc, x, y, 0x00CC0020):
                raise MacroExecutionError("Screen capture failed")

            # Crucial: GetDIBits requires the bitmap NOT be selected into a DC.
            gdi32.SelectObject(mem_dc, previous)
            previous = None
            scanlines = gdi32.GetDIBits(screen_dc, bmp, 0, h, buffer,
                                        ctypes.byref(bmi), 0)
            if scanlines != h:
                raise MacroExecutionError("Could not read screen capture pixels")
        finally:
            if previous and mem_dc:
                gdi32.SelectObject(mem_dc, previous)
            if bmp:
                gdi32.DeleteObject(bmp)
            if mem_dc:
                gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, screen_dc)

        box = largest_color_component(buffer, w, h, stride, rgb, tolerance)
        if box is None:
            return None
        x1, y1, x2, y2 = box
        return x + x1, y + y1, x + x2, y + y2

    def draw_highlight_box(self, x1, y1, x2, y2, outline_color="#35a879", duration_ms=800):
        """Draws a transparent bounding box overlay on the screen where the color was found."""
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True, "-transparentcolor", "black")
        win.config(bg="black")
        
        w = max(4, x2 - x1)
        h = max(4, y2 - y1)
        
        pad = 2
        ww, wh = w + pad*2, h + pad*2
        wx, wy = x1 - pad, y1 - pad
        win.geometry(f"{ww}x{wh}")
        
        canvas = tk.Canvas(win, width=ww, height=wh, bg="black", highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)
        canvas.create_rectangle(pad, pad, w + pad, h + pad, outline=outline_color, width=2)
        
        position_toplevel_on_screen(win, wx, wy, ww, wh)
        set_overlay_input_passthrough(win, True)

        self.root.after(duration_ms, lambda: win.destroy() if win.winfo_exists() else None)


    def ocr_screen_area(self, x, y, w, h):
        x = int(x)
        y = int(y)
        w = max(1, int(w))
        h = max(1, int(h))

        temp_fd, temp_bmp = tempfile.mkstemp(suffix=".bmp")
        os.close(temp_fd)

        try:
            self.capture_screen_area_to_bmp(x, y, w, h, temp_bmp)
        except Exception as e:
            try: os.remove(temp_bmp)
            except Exception: pass
            return f"ERR:Screen capture failed: {e}"

        abs_bmp_path = os.path.abspath(temp_bmp).replace("'", "''")

        ps_script = f"""
$ErrorActionPreference = 'Stop'
try {{
    Add-Type -AssemblyName System.Runtime.WindowsRuntime

    [void][Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]
    [void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
    [void][Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
    [void][Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType=WindowsRuntime]
    [void][Windows.Media.Ocr.OcrResult, Windows.Media.Ocr, ContentType=WindowsRuntime]
    [void][Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
    [void][Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Storage.Streams, ContentType=WindowsRuntime]

    $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    }})[0]

    function Await-Op($asyncOp, $type) {{
        $task = $asTaskGeneric.MakeGenericMethod($type).Invoke($null, @($asyncOp))
        if (-not $task.Wait(5000)) {{
            throw "WinRT Async operation timed out after 5 seconds."
        }}
        return $task.Result
    }}

    $file = Await-Op ([Windows.Storage.StorageFile]::GetFileFromPathAsync('{abs_bmp_path}')) ([Windows.Storage.StorageFile])
    $stream = Await-Op ($file.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
    $decoder = Await-Op ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $sBmp = Await-Op ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if (-not $engine) {{
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('en-US'))
    }}
    if (-not $engine) {{
        $avail = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages
        if ($avail -and $avail.Count -gt 0) {{
            $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($avail[0])
        }}
    }}
    if (-not $engine) {{
        throw "No OCR language available on system."
    }}

    $ocrResult = Await-Op ($engine.RecognizeAsync($sBmp)) ([Windows.Media.Ocr.OcrResult])
    if ($ocrResult -and $ocrResult.Text) {{
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        Write-Output $ocrResult.Text
    }}
    if ($stream) {{ $stream.Dispose() }}
}} catch {{
    Write-Output "ERR:$($_.Exception.Message)"
}}
"""
        try:
            encoded_cmd = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=0x08000000,
                timeout=12
            )
            out = proc.stdout.strip()

            if not out and proc.stderr.strip():
                err_raw = proc.stderr.strip()
                if "<S S=\"Error\">" in err_raw:
                    matches = re.findall(r'<S S="Error">(.*?)</S>', err_raw, re.DOTALL)
                    if matches:
                        clean_err = "".join(matches).replace("_x000D__x000A_", "\n").strip()
                        return f"ERR:{clean_err.splitlines()[0]}"
                lines = [l.strip() for l in err_raw.splitlines() if l.strip() and not l.startswith("#<")]
                return f"ERR:{lines[0] if lines else 'PowerShell Host Error'}"

            return out
        except Exception as e:
            return f"ERR:{str(e)}"
        finally:
            try: os.remove(temp_bmp)
            except Exception: pass

    def wait_for_screen_text(self, action, allow_idle=False):
        """Poll WinRT OCR for user-defined text; False means timeout or cancellation.

        OCR is an external PowerShell call, so timeouts are approximate and each
        recognition attempt can take longer than the configured polling interval.
        """
        area = action.get("area")
        if not isinstance(area, (list, tuple)) or len(area) != 4:
            raise MacroExecutionError("Wait Text needs a selected screen area")
        try:
            x, y, w, h = (int(n) for n in area)
            timeout = float(action.get("timeout", 15))
        except (ValueError, TypeError, OverflowError):
            raise MacroExecutionError("Invalid Wait Text screen area or timeout") from None
        if w <= 0 or h <= 0 or not math.isfinite(timeout) or timeout < 0:
            raise MacroExecutionError("Invalid Wait Text screen area or timeout")

        target = str(action.get("text", "")).strip()
        mode = action.get("mode", "appears")
        if not target or mode not in ("appears", "disappears"):
            raise MacroExecutionError("Invalid Wait Text target or mode")

        def normalize(text):
            return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())

        target_normalized = normalize(target)
        if not target_normalized:
            raise MacroExecutionError("Wait Text target contains no searchable characters")

        deadline = time.monotonic() + timeout
        absent_checks = 0
        first_check = True
        while allow_idle or self.playing:
            pause_start = None
            while self.is_paused and self.playing:
                if pause_start is None:
                    pause_start = time.monotonic()
                time.sleep(0.05)
            if pause_start is not None:
                deadline += time.monotonic() - pause_start
            if not allow_idle and not self.playing:
                return False
            if not first_check and time.monotonic() >= deadline:
                return False

            raw = self.ocr_screen_area(x, y, w, h)
            if raw.startswith("ERR:"):
                raise MacroExecutionError("OCR failed while checking text")
            # Stop requests can arrive during the blocking OCR operation.
            if not allow_idle and not self.playing:
                return False

            present = target_normalized in normalize(raw)
            if mode == "appears" and present:
                return True
            if mode == "disappears":
                absent_checks = 0 if present else absent_checks + 1
                if absent_checks >= 2:
                    return True

            first_check = False
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
        return False

    def switch_to_preset_and_start(self, preset_name):
        self.switching_preset = False
        preset_file = os.path.join(self.presets_dir, f"{preset_name}.json")
        if not os.path.exists(preset_file):
            self.update_status(f"Preset '{preset_name}' Missing", COLOR_DANGER)
            self.finish_macro("failed", "Macro file not found")
            return

        self.on_preset_file_selected(preset_name, preset_file)
        self.update_status(f"Switching to '{preset_name}'...", COLOR_ACCENT)
        self.root.after(150, self.start_macro)

    def switch_to_file_and_start(self, file_path):
        self.switching_preset = False
        if not os.path.exists(file_path):
            self.update_status(f"File Missing: {os.path.basename(file_path)}", COLOR_DANGER)
            self.finish_macro("failed", "Macro file not found")
            return

        stem = os.path.splitext(os.path.basename(file_path))[0]
        self.on_preset_file_selected(stem, file_path)
        self.update_status(f"Switching to '{stem}'...", COLOR_ACCENT)
        self.root.after(150, self.start_macro)

    def on_sound_theme_change(self, val):
        global ACTIVE_SOUND_THEME
        ACTIVE_SOUND_THEME = val
        if self.audio_cues:
            play_cue("start")

    def load_settings(self):
        global ACTIVE_SOUND_THEME, CUSTOM_SOUND_PATHS
        try:
            if os.path.exists("settings.json"):
                with open("settings.json", "r") as f:
                    config = json.load(f)
                # Upgrade legacy light_mode without losing a user's preference.
                legacy = "Light" if config.get("light_mode", False) else "Dark"
                choice = config.get("theme_mode", legacy)
                self.theme_mode = choice if choice in ("Dark", "Light", "Match System") else legacy
                self.light_mode = (self.theme_mode == "Light")
                try:
                    self.accent_color = normalize_accent_color(config.get("accent_color", DEFAULT_ACCENT_COLOR))
                except ValueError:
                    self.accent_color = DEFAULT_ACCENT_COLOR
                self.entertain_me = config.get("entertain_me", True)
                BuiltInMacroApp.entertain_mode = self.entertain_me
                self.playback_speed = config.get("playback_speed", 1.0)
                self.action_delay = config.get("action_delay", 0.20)
                self.fluid_mouse = config.get("fluid_mouse", True)
                self.human_bezier = config.get("human_bezier", False)
                self.clicks_and_keys_only = config.get("clicks_and_keys_only", False)
                self.window_relative = config.get("window_relative", False)
                self.audio_cues = config.get("audio_cues", True)
                self.delay_jitter = config.get("delay_jitter", False)
                self.natural_timing = config.get("natural_timing", False)
                ACTIVE_SOUND_THEME = config.get("sound_theme", "Classic")
                
                saved_sounds = config.get("custom_sounds", {})
                for k in CUSTOM_SOUND_PATHS.keys():
                    if k in saved_sounds:
                        CUSTOM_SOUND_PATHS[k] = saved_sounds[k]
        except Exception as e:
            print(f"Error loading settings: {e}")

    def save_settings(self):
        global ACTIVE_SOUND_THEME, CUSTOM_SOUND_PATHS
        config = {
            "light_mode": self.theme_mode == "Light",  # Compatibility with older Sequent versions
            "theme_mode": self.theme_mode,
            "accent_color": self.accent_color,
            "entertain_me": self.entertain_me,
            "playback_speed": self.playback_speed,
            "action_delay": self.action_delay,
            "fluid_mouse": self.fluid_mouse,
            "human_bezier": self.human_bezier,
            "clicks_and_keys_only": self.clicks_and_keys_only,
            "window_relative": self.window_relative,
            "audio_cues": self.audio_cues,
            "delay_jitter": self.delay_jitter,
            "natural_timing": self.natural_timing,
            "sound_theme": ACTIVE_SOUND_THEME,
            "custom_sounds": CUSTOM_SOUND_PATHS
        }
        try:
            with open("settings.json", "w") as f:
                json.dump(config, f, indent=4)
            self.update_status("Settings saved as default", COLOR_SUCCESS)
        except Exception as e:
            self.update_status(f"Error saving settings: {e}", COLOR_DANGER)

    def start_walkthrough(self):
        """Launch a safe, on-screen guide over the existing interface."""
        if self.recording or self.playing or self.chain_running or self.is_binding:
            self.update_status("Finish recording/playback before starting the walkthrough", COLOR_DANGER)
            return
        existing = getattr(self, "_walkthrough", None)
        if existing is not None and not existing._closed:
            existing.card.lift()
            return
        # Any in-flight navigation must finish before highlighting widgets.
        self._settle_tab_transition()
        self._walkthrough = SequentWalkthrough(self)

    def build_settings_view(self):
        self.settings_scroll = ModernScrollableFrame(self.frame_settings, bg=BG_APP)
        self.settings_scroll.pack(fill=tk.BOTH, expand=True)

        scroll_container = self.settings_scroll.scrollable_frame

        tk.Label(
            scroll_container, text="Preferences",
            font=("Segoe UI", 15, "bold"), bg=BG_APP, fg=TEXT_PRIMARY
        ).pack(anchor="w", padx=20, pady=(19, 3))
        tk.Label(scroll_container, text="Configure interface, playback, input, audio and hotkeys.",
                 font=("Segoe UI", 9), bg=BG_APP, fg=TEXT_MUTED).pack(
                     anchor="w", padx=20, pady=(0, 16))

        def add_setting_toggle(container, text, initial, callback):
            r = tk.Frame(container, bg=BG_PANEL)
            r.pack(fill=tk.X, pady=(0, 10))
            tk.Label(
                r, text=text, font=("Segoe UI", 9, "bold"),
                bg=BG_PANEL, fg=TEXT_PRIMARY, width=24, anchor="w"
            ).pack(side=tk.LEFT)
            t = ModernPillToggle(r, initial_state=initial, command=callback)
            t.pack(side=tk.LEFT)
            return t

        # --- SECTION 1: APPEARANCE & UI ---
        tk.Label(scroll_container, text="APPEARANCE & UI", font=("Segoe UI", 8, "bold"), bg=BG_APP, fg=TEXT_MUTED).pack(anchor="w", padx=22, pady=(0, 4))
        card_app = ModernCard(scroll_container, bg_card=BG_PANEL)
        card_app.pack(fill=tk.X, padx=20, pady=(0, 16))
        f_app = tk.Frame(card_app, bg=BG_PANEL, padx=16, pady=14)
        f_app.pack(fill=tk.X)

        self.build_appearance_settings(f_app)
        self.build_entertain_me_toggle(f_app)

        # Guided help can be restarted without reopening Sequent.
        tk.Label(scroll_container, text="HELP & ONBOARDING", font=("Segoe UI", 8, "bold"),
                 bg=BG_APP, fg=TEXT_MUTED).pack(anchor="w", padx=22, pady=(0, 4))
        tour_card = ModernCard(scroll_container, bg_card=BG_PANEL)
        tour_card.pack(fill=tk.X, padx=20, pady=(0, 16))
        tour_row = tk.Frame(tour_card, bg=BG_PANEL, padx=16, pady=12)
        tour_row.pack(fill=tk.X)
        tk.Label(tour_row, text="New to Sequent?", bg=BG_PANEL, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        tk.Label(tour_row, text="Explore the application with highlighted controls and step-by-step guidance.",
                 bg=BG_PANEL, fg=TEXT_MUTED, font=("Segoe UI", 9),
                 anchor="w").pack(fill=tk.X, pady=(3, 8))
        sequent_toolbar_button(tour_row, "▶  Start Guided Walkthrough", self.start_walkthrough).pack(anchor="w")

        # --- SECTION 2: PLAYBACK & TIMING ---
        tk.Label(scroll_container, text="PLAYBACK & TIMING", font=("Segoe UI", 8, "bold"), bg=BG_APP, fg=TEXT_MUTED).pack(anchor="w", padx=22, pady=(0, 4))
        card_timing = ModernCard(scroll_container, bg_card=BG_PANEL)
        card_timing.pack(fill=tk.X, padx=20, pady=(0, 16))
        f_timing = tk.Frame(card_timing, bg=BG_PANEL, padx=16, pady=14)
        f_timing.pack(fill=tk.X)

        # Speed Slider
        speed_row = tk.Frame(f_timing, bg=BG_PANEL)
        speed_row.pack(fill=tk.X, pady=(0, 12))
        tk.Label(speed_row, text="Playback Speed", font=("Segoe UI", 9, "bold"), bg=BG_PANEL, fg=TEXT_PRIMARY, width=24, anchor="w").pack(side=tk.LEFT)
        speed_controls = tk.Frame(speed_row, bg=BG_PANEL)
        speed_controls.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.speed_slider = ModernSlider(speed_controls, min_val=1.0, max_val=10.0, step=0.5, initial_val=self.playback_speed, command=self.on_speed_slider_change, width=180, height=24)
        self.speed_slider.pack(side=tk.LEFT, padx=(0, 10))
        self.speed_lbl = tk.Label(speed_controls, text=f"{self.playback_speed:.1f}x", font=("Consolas", 9, "bold"), bg=BG_INPUT, fg=SYNTAX_BLUE, width=6)
        self.speed_lbl.pack(side=tk.LEFT)

        # Delay Slider
        delay_row = tk.Frame(f_timing, bg=BG_PANEL)
        delay_row.pack(fill=tk.X, pady=(0, 12))
        tk.Label(delay_row, text="Action Delay", font=("Segoe UI", 9, "bold"), bg=BG_PANEL, fg=TEXT_PRIMARY, width=24, anchor="w").pack(side=tk.LEFT)
        delay_controls = tk.Frame(delay_row, bg=BG_PANEL)
        delay_controls.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.delay_slider = ModernSlider(delay_controls, min_val=0.05, max_val=1.5, step=0.05, initial_val=self.action_delay, command=self.on_delay_slider_change, width=180, height=24)
        self.delay_slider.pack(side=tk.LEFT, padx=(0, 10))
        self.delay_lbl = tk.Label(delay_controls, text=f"{self.action_delay:.2f}s", font=("Consolas", 9, "bold"), bg=BG_INPUT, fg=SYNTAX_BLUE, width=6)
        self.delay_lbl.pack(side=tk.LEFT)

        self.jitter_toggle = add_setting_toggle(f_timing, "Delay Jitter (±15%)", self.delay_jitter, lambda s: setattr(self, 'delay_jitter', s))
        self.timing_toggle = add_setting_toggle(f_timing, "Natural Timing Mode", self.natural_timing, lambda s: setattr(self, 'natural_timing', s))

        # --- SECTION 3: MOUSE & INPUT DYNAMICS ---
        tk.Label(scroll_container, text="MOUSE & INPUT DYNAMICS", font=("Segoe UI", 8, "bold"), bg=BG_APP, fg=TEXT_MUTED).pack(anchor="w", padx=22, pady=(0, 4))
        card_mouse = ModernCard(scroll_container, bg_card=BG_PANEL)
        card_mouse.pack(fill=tk.X, padx=20, pady=(0, 16))
        f_mouse = tk.Frame(card_mouse, bg=BG_PANEL, padx=16, pady=14)
        f_mouse.pack(fill=tk.X)

        self.fluid_toggle = add_setting_toggle(f_mouse, "Fluid Mouse Playback", self.fluid_mouse, self.set_fluid_mouse)
        self.bezier_toggle = add_setting_toggle(f_mouse, "Human Bézier Curves", self.human_bezier, lambda s: setattr(self, 'human_bezier', s))
        self.filter_toggle = add_setting_toggle(f_mouse, "Clicks & Keys Only", self.clicks_and_keys_only, lambda s: setattr(self, 'clicks_and_keys_only', s))
        self.win_rel_toggle = add_setting_toggle(f_mouse, "Window Anchoring", self.window_relative, lambda s: setattr(self, 'window_relative', s))

        # --- SECTION 4: SYSTEM & WORKSPACE ---
        tk.Label(scroll_container, text="SYSTEM & WORKSPACE", font=("Segoe UI", 8, "bold"), bg=BG_APP, fg=TEXT_MUTED).pack(anchor="w", padx=22, pady=(0, 4))
        card_sys = ModernCard(scroll_container, bg_card=BG_PANEL)
        card_sys.pack(fill=tk.X, padx=20, pady=(0, 16))
        f_sys = tk.Frame(card_sys, bg=BG_PANEL, padx=16, pady=14)
        f_sys.pack(fill=tk.X)

        self.audio_toggle = add_setting_toggle(f_sys, "Audio Cues", self.audio_cues, lambda s: setattr(self, 'audio_cues', s))

        global ACTIVE_SOUND_THEME, CUSTOM_SOUND_PATHS
        theme_row = tk.Frame(f_sys, bg=BG_PANEL)
        theme_row.pack(fill=tk.X, pady=(0, 10))
        tk.Label(theme_row, text="Sound Theme", font=("Segoe UI", 9, "bold"), bg=BG_PANEL, fg=TEXT_PRIMARY, width=24, anchor="w").pack(side=tk.LEFT)
        self.sound_theme_var = tk.StringVar(value=ACTIVE_SOUND_THEME)
        theme_menu = tk.OptionMenu(theme_row, self.sound_theme_var, "Classic", "Subtle", "Sci-Fi", command=self.on_sound_theme_change)
        theme_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=BG_HOVER, font=("Segoe UI", 8, "bold"), highlightthickness=1, highlightbackground=BG_BORDER, bd=0, relief="flat")
        theme_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT, bd=1, relief="flat")
        theme_menu.pack(side=tk.LEFT)

        def add_custom_sound_row(parent, label_text, cue_key):
            row = tk.Frame(parent, bg=BG_PANEL)
            row.pack(fill=tk.X, pady=(4, 0))
            tk.Label(row, text=label_text, font=("Segoe UI", 9, "bold"), bg=BG_PANEL, fg=TEXT_PRIMARY, width=24, anchor="w").pack(side=tk.LEFT)
            
            controls = tk.Frame(row, bg=BG_PANEL)
            controls.pack(side=tk.LEFT, fill=tk.X, expand=True)
            
            path_box = tk.Frame(controls, bg=BG_INPUT, highlightbackground=BG_BORDER, highlightthickness=1)
            path_box.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
            
            current_path = CUSTOM_SOUND_PATHS.get(cue_key, "")
            disp_text = self.shorten_path(current_path) if current_path else "Default Theme Sound"
            lbl_path = tk.Label(path_box, text=disp_text, font=("Consolas", 8), bg=BG_INPUT, fg=SYNTAX_GREEN if current_path else TEXT_MUTED, anchor="w", padx=8, pady=4)
            lbl_path.pack(side=tk.LEFT, fill=tk.X, expand=True)
            
            def _browse():
                self.root.attributes('-topmost', False)
                try:
                    # Updated to allow both MP3 and WAV
                    path = filedialog.askopenfilename(
                        parent=self.root, 
                        title=f"Select Sound for {label_text}", 
                        filetypes=[("Audio Files", "*.wav *.mp3"), ("WAV Audio", "*.wav"), ("MP3 Audio", "*.mp3")]
                    )
                finally:
                    self.root.attributes('-topmost', False)
                if path:
                    CUSTOM_SOUND_PATHS[cue_key] = path
                    lbl_path.config(text=self.shorten_path(path), fg=SYNTAX_GREEN)


            def _clear():
                CUSTOM_SOUND_PATHS[cue_key] = ""
                lbl_path.config(text="Default Theme Sound", fg=TEXT_MUTED)

            btn_clear = tk.Label(path_box, text=" ✕ ", font=("Segoe UI", 9, "bold"), bg=BG_INPUT, fg=TEXT_MUTED, cursor="hand2")
            btn_clear.pack(side=tk.RIGHT, padx=(2, 0))
            btn_clear.bind("<Button-1>", lambda e: _clear())
            btn_clear.bind("<Enter>", lambda e: btn_clear.config(fg=COLOR_DANGER))
            btn_clear.bind("<Leave>", lambda e: btn_clear.config(fg=TEXT_MUTED))
            
            btn_browse = tk.Label(controls, text="Browse", font=("Segoe UI", 8, "bold"), bg=COLOR_ACCENT, fg=TEXT_ON_ACCENT, cursor="hand2", padx=10, pady=4)
            btn_browse.pack(side=tk.RIGHT)
            btn_browse.bind("<Button-1>", lambda e: _browse())

        add_custom_sound_row(f_sys, "Start Recording Sound", "start_rec")
        add_custom_sound_row(f_sys, "Stop Recording Sound", "stop_rec")
        add_custom_sound_row(f_sys, "Pause Sequence Sound", "pause")
        add_custom_sound_row(f_sys, "Run Macro Sound", "start")

        folder_row = tk.Frame(f_sys, bg=BG_PANEL)
        folder_row.pack(fill=tk.X, pady=(10, 0))
        tk.Label(folder_row, text="Preset Directory", font=("Segoe UI", 9, "bold"), bg=BG_PANEL, fg=TEXT_PRIMARY, width=24, anchor="w").pack(side=tk.LEFT)
        folder_controls = tk.Frame(folder_row, bg=BG_PANEL)
        folder_controls.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.folder_display = tk.Label(
            folder_controls, text=self.shorten_path(self.presets_dir), font=("Consolas", 8),
            bg=BG_INPUT, fg=TEXT_MUTED, anchor="w", padx=8, pady=4,
            highlightbackground=BG_BORDER, highlightthickness=1
        )
        self.folder_display.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        btn_browse_folder = tk.Label(
            folder_controls, text="Browse", font=("Segoe UI", 8, "bold"),
            bg=COLOR_ACCENT, fg=TEXT_ON_ACCENT, cursor="hand2", padx=10, pady=4
        )
        btn_browse_folder.pack(side=tk.RIGHT)
        btn_browse_folder.bind("<Button-1>", lambda e: self.choose_presets_folder())

        # --- SECTION 5: GLOBAL KEYBINDINGS ---
        tk.Label(scroll_container, text="GLOBAL HOTKEYS", font=("Segoe UI", 8, "bold"), bg=BG_APP, fg=TEXT_MUTED).pack(anchor="w", padx=22, pady=(0, 4))
        keys_card = ModernCard(scroll_container, bg_card=BG_PANEL)
        keys_card.pack(fill=tk.X, padx=20, pady=(0, 20))
        keys_frame = tk.Frame(keys_card, bg=BG_PANEL, padx=16, pady=14)
        keys_frame.pack(fill=tk.X)

        self.create_hotkey_row(keys_frame, "Start Recording", "start_rec")
        self.create_hotkey_row(keys_frame, "Stop Recording", "stop_rec")
        self.create_hotkey_row(keys_frame, "Start Playback", "start_play")
        self.create_hotkey_row(keys_frame, "Stop Playback", "stop_play")

        # --- SECTION 6: SET DEFAULTS ---
        btn_save_def = ModernButton(scroll_container, text="Save Settings as Default", bg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOV, command=self.save_settings)
        btn_save_def.pack(padx=20, pady=(10, 30), fill=tk.X)


    def build_entertain_me_toggle(self, parent):
        row = tk.Frame(parent, bg=BG_PANEL)
        row.pack(fill=tk.X, pady=(0, 2))

        tk.Label(
            row, text="Entertain me", font=("Segoe UI", 9, "bold"),
            bg=BG_PANEL, fg=TEXT_PRIMARY, width=24, anchor="w"
        ).pack(side=tk.LEFT)

        self.sparkle_canvas = tk.Canvas(row, width=80, height=28, bg=BG_PANEL, highlightthickness=0, bd=0)
        self.sparkle_canvas.pack(side=tk.LEFT)

        self.entertain_pill = ModernPillToggle(
            self.sparkle_canvas, initial_state=self.entertain_me,
            command=self.toggle_entertain_me
        )
        self.sparkle_canvas.create_window(0, 14, window=self.entertain_pill, anchor="w")

        return self.entertain_pill


    def build_appearance_settings(self, parent):
        """Persistent, easy-to-read theme selector and shared accent picker."""
        theme_row = tk.Frame(parent, bg=BG_PANEL)
        theme_row.pack(fill=tk.X, pady=(0, 10))
        tk.Label(theme_row, text="Application Theme", font=("Segoe UI", 9, "bold"),
                 bg=BG_PANEL, fg=TEXT_PRIMARY, width=24, anchor="w").pack(side=tk.LEFT)
        self.theme_mode_var = tk.StringVar(value=self.theme_mode)
        theme_menu = tk.OptionMenu(theme_row, self.theme_mode_var,
                                   "Dark", "Light", "Match System", command=self.set_theme_mode)
        theme_menu.configure(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=BG_HOVER,
                             activeforeground=TEXT_PRIMARY, font=("Segoe UI", 9),
                             highlightthickness=1, highlightbackground=BG_BORDER,
                             bd=0, relief="flat", width=16, anchor="w")
        theme_menu["menu"].configure(bg=BG_INPUT, fg=TEXT_PRIMARY,
                                      activebackground=COLOR_ACCENT,
                                      activeforeground=TEXT_ON_ACCENT, bd=1, relief="flat")
        theme_menu.pack(side=tk.LEFT)

        accent_row = tk.Frame(parent, bg=BG_PANEL)
        accent_row.pack(fill=tk.X, pady=(3, 5))
        tk.Label(accent_row, text="Accent Color", font=("Segoe UI", 9, "bold"),
                 bg=BG_PANEL, fg=TEXT_PRIMARY, width=24, anchor="w").pack(side=tk.LEFT)
        self.accent_swatch = tk.Label(accent_row, text="  ", width=3,
                                       bg=self.accent_color, relief="flat",
                                       highlightbackground=BG_BORDER, highlightthickness=1)
        self.accent_swatch.pack(side=tk.LEFT, padx=(0, 7))
        self.accent_hex_var = tk.StringVar(value=self.accent_color)
        entry = tk.Entry(accent_row, textvariable=self.accent_hex_var, width=10,
                         bg=BG_INPUT, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                         highlightthickness=1, highlightbackground=BG_BORDER,
                         font=("Consolas", 10), relief="flat")
        entry.pack(side=tk.LEFT, padx=(0, 6), ipady=4)
        entry.bind("<Return>", lambda event: self.apply_accent_hex())
        sequent_toolbar_button(accent_row, "Apply", self.apply_accent_hex).pack(side=tk.LEFT, padx=(0, 6))
        sequent_toolbar_button(accent_row, "Customize...", self.choose_accent_color).pack(side=tk.LEFT)

        presets_row = tk.Frame(parent, bg=BG_PANEL)
        presets_row.pack(fill=tk.X, pady=(0, 9))
        tk.Label(presets_row, text="Quick Colors", font=("Segoe UI", 9),
                 bg=BG_PANEL, fg=TEXT_MUTED, width=24, anchor="w").pack(side=tk.LEFT)
        for name, value in ACCENT_PRESETS:
            selected = (value.upper() == self.accent_color)
            swatch = tk.Button(presets_row, text="", width=2, height=1,
                               bg=value, activebackground=value, relief="flat",
                               bd=0, cursor="hand2", takefocus=True,
                               highlightthickness=2 if selected else 1,
                               highlightbackground=TEXT_PRIMARY if selected else BG_BORDER,
                               command=lambda chosen=value: self.set_accent_color(chosen))
            swatch.pack(side=tk.LEFT, padx=(0, 5))
            ModernTooltip(swatch, name)
        sequent_toolbar_button(presets_row, "Reset", lambda: self.set_accent_color(DEFAULT_ACCENT_COLOR)).pack(side=tk.LEFT, padx=(7, 0))
        tk.Label(parent, text="Saved automatically. Match System follows Windows app theme.",
                 bg=BG_PANEL, fg=TEXT_MUTED, font=("Segoe UI", 8), anchor="w").pack(fill=tk.X, pady=(0, 9))

    def resolve_theme_mode(self):
        if self.theme_mode == "Light":
            return "light"
        if self.theme_mode == "Match System":
            return read_windows_app_theme()
        return "dark"

    def _persist_appearance(self):
        """Save only appearance keys; don't accidentally commit other unsaved preferences."""
        path = "settings.json"
        try:
            config = {}
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if not isinstance(loaded, dict):
                    raise ValueError("settings.json must contain a JSON object")
                config.update(loaded)
            config.update({
                "theme_mode": self.theme_mode,
                "light_mode": self.theme_mode == "Light",
                "accent_color": self.accent_color,
            })
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=".",
                                             prefix="sequent_settings_", suffix=".tmp",
                                             delete=False) as handle:
                temp_path = handle.name
                json.dump(config, handle, indent=4)
            try:
                os.replace(temp_path, path)
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
        except (OSError, ValueError, TypeError) as exc:
            self.update_status(f"Unable to save appearance: {exc}", COLOR_DANGER)

    def set_theme_mode(self, value):
        if value not in ("Dark", "Light", "Match System"):
            return
        self.theme_mode = value
        self.light_mode = (value == "Light")
        self._persist_appearance()
        desired = self.resolve_theme_mode()
        if desired != self._effective_theme:
            self.apply_theme(desired)
        self.update_status(f"Theme: {value}", COLOR_SUCCESS)

    def set_light_mode(self, enabled):
        """Backward-compatible hook; Settings now uses the three-way selector."""
        self.set_theme_mode("Light" if enabled else "Dark")

    def apply_accent_hex(self):
        try:
            self.set_accent_color(self.accent_hex_var.get())
        except ValueError as exc:
            self.update_status(str(exc), COLOR_DANGER)

    def choose_accent_color(self):
        """Open Sequent's own HSV accent editor; apply only on confirmation."""
        existing = getattr(self, "_accent_color_dialog", None)
        if existing is not None and existing.window.winfo_exists():
            existing.window.lift()
            existing.window.focus_force()
            return
        self._accent_color_dialog = SequentAccentColorDialog(self)

    def set_accent_color(self, value):
        color = normalize_accent_color(value)
        if self.accent_color == color:
            if hasattr(self, "accent_hex_var"):
                self.accent_hex_var.set(color)
            return
        self.accent_color = color
        self._persist_appearance()
        self.apply_theme(self.resolve_theme_mode())
        self.update_status(f"Accent color: {color}", COLOR_SUCCESS)

    def _schedule_system_theme_poll(self):
        # Poll instead of replacing the Win32 window procedure: this is safe
        # for native and borderless Tk windows and follows changes while open.
        if self.root.winfo_exists():
            self._system_theme_job = self.root.after(3000, self._poll_system_theme)

    def _poll_system_theme(self):
        self._system_theme_job = None
        try:
            if self.theme_mode == "Match System":
                desired = self.resolve_theme_mode()
                if desired != self._effective_theme and not (
                    self.playing or self.recording or self.chain_running or self.is_binding
                ) and getattr(self, "_reticle_edit_index", None) is None:
                    self.apply_theme(desired)
        finally:
            self._schedule_system_theme_poll()

    def _cancel_old_ui_jobs(self):
        """Cancel asynchronous jobs and widget animations before UI teardown."""
        self._ui_generation += 1
        self._queue_animation_active = False
        self._cancel_sidebar_animation()
        for name in ("_queue_resize_job", "_tab_anim_id", "_sparkle_anim_id",
                     "_resize_job", "_resize_repaint_job"):
            job = getattr(self, name, None)
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
                setattr(self, name, None)
        self._sparkle_particles.clear()

        container = getattr(self, "main_container", None)
        if container is None or not container.winfo_exists():
            return
        stack = [container]
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            # ModernButton / ModernPillToggle animations are widget-local.
            for name in ("_fade_job", "_anim_id", "_after_id"):
                job = getattr(widget, name, None)
                if job is not None:
                    try:
                        widget.after_cancel(job)
                    except tk.TclError:
                        pass
                    setattr(widget, name, None)
            for tip in getattr(widget, "_sequent_tooltips", ()):
                tip._cancel()
                tip.hide()

    def _update_theme_tokens(self, theme_name):
        global BG_APP, BG_SIDEBAR, BG_HEADER, BG_PANEL, BG_INPUT, BG_GUTTER
        global BG_SCROLL_TRACK, BG_BORDER, BG_HOVER, BG_SELECTED, TRACK_INACTIVE, TRACK_HOVER, TRACK_ACTIVE
        global TEXT_PRIMARY, TEXT_MUTED, TEXT_HEADER, COLOR_ACCENT, COLOR_ACCENT_HOV, TEXT_ON_ACCENT
        global SYNTAX_BLUE, SYNTAX_GREEN, SYNTAX_PURPLE, SYNTAX_ORANGE, DISABLED

        if theme_name == "light":
            BG_APP = "#eef1f5"
            BG_SIDEBAR = "#e4e9ef"
            BG_HEADER = "#d6dde7"
            BG_PANEL = "#ffffff"
            BG_INPUT = "#f2f5f9"
            BG_GUTTER = "#e7ebf1"
            BG_SCROLL_TRACK = "#dce3eb"
            BG_BORDER = "#c5ceda"
            BG_HOVER = "#d6e1ef"
            BG_SELECTED = "#c6dcf8"
            TRACK_INACTIVE = "#a8b4c3"
            TRACK_HOVER = "#798da5"
            TRACK_ACTIVE = COLOR_ACCENT
            TEXT_PRIMARY = "#17202b"
            TEXT_MUTED = "#506073"
            TEXT_HEADER = "#17202b"
            SYNTAX_BLUE = "#245da8"
            SYNTAX_GREEN = "#146a52"
            SYNTAX_PURPLE = "#7b53a7"
            SYNTAX_ORANGE = "#a46f23"
            DISABLED = "#d6dce5"
        else:
            BG_APP = "#17191e"
            BG_SIDEBAR = "#1c1f26"
            BG_HEADER = "#111318"
            BG_PANEL = "#23262e"
            BG_INPUT = "#181b21"
            BG_GUTTER = "#15171c"
            BG_SCROLL_TRACK = "#15171c"
            BG_BORDER = "#353a43"
            BG_HOVER = "#303642"
            BG_SELECTED = "#293b54"
            TRACK_INACTIVE = "#4a5260"
            TRACK_HOVER = "#657185"
            TRACK_ACTIVE = COLOR_ACCENT
            TEXT_PRIMARY = "#edf1f6"
            TEXT_MUTED = "#a0aab8"
            TEXT_HEADER = "#ffffff"
            SYNTAX_BLUE = "#79b8ff"
            SYNTAX_GREEN = "#65d4aa"
            SYNTAX_PURPLE = "#c4a3ed"
            SYNTAX_ORANGE = "#f3ba65"
            DISABLED = "#363b45"

        # Single source of truth for the entire UI's configurable accent.
        COLOR_ACCENT = self.accent_color
        COLOR_ACCENT_HOV = blend_hex(COLOR_ACCENT,
                                    "#000000" if theme_name == "light" else "#FFFFFF", 0.85)
        TRACK_ACTIVE = COLOR_ACCENT
        BG_SELECTED = blend_hex(COLOR_ACCENT, BG_PANEL,
                                0.18 if theme_name == "light" else 0.27)
        TEXT_ON_ACCENT = accent_text_color(COLOR_ACCENT)

    def apply_theme(self, theme_name):
        if getattr(self, '_theme_lock', False): return
        if getattr(self, "_walkthrough", None) is not None:
            self._walkthrough.close(restore=False)
        self._theme_lock = True
        self._theme_rebuilding = True

        self._update_theme_tokens(theme_name)
        if getattr(self, 'native_chrome', None) is not None:
            self.native_chrome.refresh_dwm_frame()

        active_tab = getattr(self, 'current_tab_name', 'playback') or 'playback'
        self._cancel_old_ui_jobs()
        try:
            self.root.configure(bg=BG_APP)

            # Resize grips live outside main_container and persist between themes.
            for grip_name in ('grip_e', 'grip_s', 'grip_w', 'grip_se'):
                grip = getattr(self, grip_name, None)
                if grip is not None and grip.winfo_exists():
                    grip.configure(bg=BG_APP)
            for grip in getattr(self, '_native_grips', ()):
                if grip.winfo_exists():
                    grip.configure(bg=BG_APP)

            # All sidebar, dock, Settings, and overlay children belong to the
            # main UI hierarchy except the overlay. Destroy the tree only
            # after all its delayed jobs have been cancelled.
            old_main = getattr(self, 'main_container', None)
            old_overlay = getattr(self, 'overlay', None)
            if old_main is not None and old_main.winfo_exists():
                old_main.destroy()
            if old_overlay is not None and old_overlay.winfo_exists():
                old_overlay.destroy()

            self.setup_ui(initial_tab=active_tab, animate=False)
            for grip in getattr(self, '_native_grips', ()):
                if grip.winfo_exists():
                    grip.lift()
            self._effective_theme = theme_name
            if self.recorded_actions:
                self.display_actions_in_queue(
                    self.recorded_actions, self.current_source_label
                )
        finally:
            self._theme_rebuilding = False
            self._theme_lock = False

        # Recompute once, after the new widgets have been fully constructed.
        # Generation guard protects against any deferred event from an old UI.
        generation = self._ui_generation
        workspace = self.workspace
        self.root.after_idle(
            lambda: self._settle_queue_resize(workspace, generation)
        )


    def toggle_entertain_me(self, state):
        self.entertain_me = bool(state)
        BuiltInMacroApp.entertain_mode = bool(state)
        # Disabling entertainment also finishes any ongoing navigation slide.
        if not self.entertain_me:
            self._cancel_sidebar_animation()
            self._set_sidebar_visible_width(
                self.sidebar_width if self.sidebar_open else 0, commit=True
            )

        # Clear any ongoing animation jobs and particles
        if self._sparkle_anim_id:
            try: self.root.after_cancel(self._sparkle_anim_id)
            except Exception: pass
            self._sparkle_anim_id = None

        if hasattr(self, 'sparkle_canvas') and self.sparkle_canvas.winfo_exists():
            self.sparkle_canvas.delete("sparkle")
        self._sparkle_particles.clear()

        if self.entertain_me and hasattr(self, 'sparkle_canvas') and self.sparkle_canvas.winfo_exists():
            # Center of the pill toggle relative to the new constrained canvas is x=22, y=14
            btn_cx, btn_cy = 22, 14
            sparkle_chars = ['✦', '✧', '⋆', '✨', '★']
            sparkle_colors = ['#FFE066', '#FFD166', '#70D6FF', '#FF70A6', '#E0AAFF', '#FFFFFF', '#06D6A0']

            for _ in range(16):
                angle = random.uniform(0, 2 * math.pi)
                rx = 22 + random.uniform(2, 8)
                ry = 11 + random.uniform(2, 6)
                sx = btn_cx + (rx * math.cos(angle))
                sy = btn_cy + (ry * math.sin(angle))

                speed = random.uniform(0.35, 0.95)
                vx = math.cos(angle) * speed
                vy = (math.sin(angle) * speed) - 0.25

                item_id = self.sparkle_canvas.create_text(
                    sx, sy, text=random.choice(sparkle_chars), fill=random.choice(sparkle_colors),
                    font=("Segoe UI", random.randint(7, 10), "bold"), tags="sparkle"
                )
                self._sparkle_particles.append({
                    "id": item_id,
                    "vx": vx,
                    "vy": vy,
                    "life": 0,
                    "max_life": random.randint(14, 22)
                })

            self._tick_sparkles()


    def _tick_sparkles(self):
        if not hasattr(self, 'sparkle_canvas') or not self.sparkle_canvas.winfo_exists():
            self._sparkle_anim_id = None
            return

        surviving = []
        for p in self._sparkle_particles:
            p["life"] += 1
            self.sparkle_canvas.move(p["id"], p["vx"], p["vy"])

            if p["life"] >= p["max_life"]:
                self.sparkle_canvas.delete(p["id"])
            else:
                surviving.append(p)

        self._sparkle_particles = surviving

        # Cease recursion when all burst particles have dissipated
        if self._sparkle_particles:
            self._sparkle_anim_id = self.root.after(35, self._tick_sparkles)
        else:
            self._sparkle_anim_id = None


    def choose_presets_folder(self):
        self.root.attributes('-topmost', False)
        try:
            chosen = filedialog.askdirectory(parent=self.root, initialdir=self.presets_dir, title="Select Macro Presets Folder")
        finally:
            self.root.attributes('-topmost', False)

        if chosen:
            self.presets_dir = os.path.abspath(chosen)
            if hasattr(self, 'folder_display'): self.folder_display.config(text=self.shorten_path(self.presets_dir))
            self.refresh_preset_menu()
            self.update_status("Preset Folder Updated", COLOR_SUCCESS)

    def on_speed_slider_change(self, val):
        self.playback_speed = float(val)
        if hasattr(self, 'speed_lbl'): self.speed_lbl.config(text=f"{self.playback_speed:.1f}x")

    def on_delay_slider_change(self, val):
        self.action_delay = round(float(val), 2)
        if hasattr(self, 'delay_lbl'): self.delay_lbl.config(text=f"{self.action_delay:.2f}s")

    def set_fluid_mouse(self, enabled): self.fluid_mouse = enabled

    def create_hotkey_row(self, parent, label_text, action_key):
        row = tk.Frame(parent, bg=BG_PANEL)
        row.pack(fill=tk.X, pady=(0, 8))

        tk.Label(row, text=label_text, font=("Segoe UI", 10, "bold"), bg=BG_PANEL, fg=TEXT_PRIMARY, width=20, anchor="w").pack(side=tk.LEFT)
        hk_field = SequentHotkeyField(row, action_key, self.hotkeys[action_key]["name"], self)
        hk_field.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def refresh_preset_menu(self, selected=None):
        menu = self.preset_menu["menu"]
        menu.delete(0, "end")

        if not os.path.exists(self.presets_dir):
            try: os.makedirs(self.presets_dir, exist_ok=True)
            except Exception: pass

        json_files = []
        if os.path.isdir(self.presets_dir):
            try: json_files = sorted([f for f in os.listdir(self.presets_dir) if f.lower().endswith(".json")])
            except Exception: json_files = []

        if not json_files:
            menu.add_command(label="-- No Presets Found --", command=lambda: None)
            self.preset_var.set("-- No Presets Found --")
            return

        menu.add_command(label="-- Select Preset --", command=lambda: self.preset_var.set("-- Select Preset --"))
        for fname in json_files:
            stem = os.path.splitext(fname)[0]
            full_path = os.path.join(self.presets_dir, fname)
            menu.add_command(label=stem, command=lambda s=stem, p=full_path: self.on_preset_file_selected(s, p))

        if selected and any(os.path.splitext(f)[0] == selected for f in json_files):
            self.preset_var.set(selected)
        else:
            self.preset_var.set("-- Select Preset --")

    def on_preset_file_selected(self, stem, file_path):
        if self.recording or self.playing: return
        try:
            with open(file_path, "r", encoding="utf-8") as f: loaded_data = json.load(f)
            if isinstance(loaded_data, list):
                validated_actions = []
                for act in loaded_data:
                    if isinstance(act, dict) and "type" in act:
                        cleaned_act = dict(act)
                        if "x" in cleaned_act: cleaned_act["x"] = int(cleaned_act["x"])
                        if "y" in cleaned_act: cleaned_act["y"] = int(cleaned_act["y"])
                        if "delta" in cleaned_act: cleaned_act["delta"] = int(cleaned_act["delta"])
                        if "seconds" in cleaned_act: cleaned_act["seconds"] = float(cleaned_act["seconds"])
                        if "area" in cleaned_act and isinstance(cleaned_act["area"], list):
                            cleaned_act["area"] = [int(v) for v in cleaned_act["area"][:4]]
                        if act.get("type") == "wait_pixel":
                            cleaned_act["timeout"] = float(cleaned_act.get("timeout", 15.0))
                            cleaned_act["color"] = str(cleaned_act.get("color", "#FFFFFF")).upper()
                        if act.get("type") == "find_color":
                            cleaned_act["ox"] = int(cleaned_act.get("ox", 0))
                            cleaned_act["oy"] = int(cleaned_act.get("oy", 0))
                            cleaned_act["color"] = str(cleaned_act.get("color", "#FFFFFF")).upper()
                            cleaned_act["action"] = str(cleaned_act.get("action", "Single Click"))
                            cleaned_act["anchor"] = str(cleaned_act.get("anchor", "Center"))
                            cleaned_act["tolerance"] = max(0, min(255, int(cleaned_act.get("tolerance", 8))))
                        if "match_any" in act:
                            cleaned_act["match_any"] = bool(act["match_any"])
                        validated_actions.append(cleaned_act)

                if validated_actions:
                    self.recorded_actions = validated_actions
                    self.current_action_idx = 0
                    self.preset_var.set(stem)
                    self.current_source_label = f"Preset: {stem}"
                    self.display_actions_in_queue(self.recorded_actions, self.current_source_label)
                    self.update_status(f"Loaded '{stem}'", COLOR_SUCCESS)
                else: self.update_status("Empty Preset File", COLOR_DANGER)
            else: self.update_status("Invalid Format", COLOR_DANGER)
        except Exception: self.update_status("Preset Load Error", COLOR_DANGER)
  
    def save_macro_to_file(self):
        if not self.recorded_actions:
            self.update_status("Queue Empty", COLOR_DANGER)
            return

        self.root.attributes('-topmost', False)
        try:
            file_path = filedialog.asksaveasfilename(
                parent=self.root, title="Export Macro File", initialdir=self.presets_dir,
                defaultextension=".json", filetypes=[("JSON Macro File", "*.json"), ("All Files", "*.*")]
            )
        finally:
            self.root.attributes('-topmost', False)

        if file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f: json.dump(self.recorded_actions, f, indent=2)
                self.update_status("Exported File", COLOR_SUCCESS)
                if os.path.dirname(os.path.abspath(file_path)) == self.presets_dir:
                    stem = os.path.splitext(os.path.basename(file_path))[0]
                    self.refresh_preset_menu(selected=stem)
            except Exception: self.update_status("Export Error", COLOR_DANGER)

    def load_macro_from_file(self):
        if self.recording or self.playing: return
        self.root.attributes('-topmost', False)
        try:
            file_path = filedialog.askopenfilename(
                parent=self.root, title="Import Macro File", initialdir=self.presets_dir,
                filetypes=[("JSON Macro File", "*.json"), ("All Files", "*.*")]
            )
        finally:
            self.root.attributes('-topmost', False)

        if file_path:
            try:
                with open(file_path, "r", encoding="utf-8") as f: 
                    loaded_data = json.load(f)
                if isinstance(loaded_data, list):
                    validated_actions = []
                    for act in loaded_data:
                        if isinstance(act, dict) and "type" in act:
                            cleaned_act = dict(act)
                            if "x" in cleaned_act: cleaned_act["x"] = int(cleaned_act["x"])
                            if "y" in cleaned_act: cleaned_act["y"] = int(cleaned_act["y"])
                            if "delta" in cleaned_act: cleaned_act["delta"] = int(cleaned_act["delta"])
                            if "seconds" in cleaned_act: cleaned_act["seconds"] = float(cleaned_act["seconds"])
                            if "area" in cleaned_act and isinstance(cleaned_act["area"], list):
                                cleaned_act["area"] = [int(v) for v in cleaned_act["area"][:4]]
                            if act.get("type") == "wait_pixel":
                                cleaned_act["timeout"] = float(cleaned_act.get("timeout", 15.0))
                                cleaned_act["color"] = str(cleaned_act.get("color", "#FFFFFF")).upper()
                            if act.get("type") == "find_color":
                                cleaned_act["ox"] = int(cleaned_act.get("ox", 0))
                                cleaned_act["oy"] = int(cleaned_act.get("oy", 0))
                                cleaned_act["color"] = str(cleaned_act.get("color", "#FFFFFF")).upper()
                                cleaned_act["action"] = str(cleaned_act.get("action", "Single Click"))
                            cleaned_act["anchor"] = str(cleaned_act.get("anchor", "Center"))
                            cleaned_act["tolerance"] = max(0, min(255, int(cleaned_act.get("tolerance", 8))))
                            if "match_any" in act:
                                cleaned_act["match_any"] = bool(act["match_any"])
                            validated_actions.append(cleaned_act)

                    if validated_actions:
                        self.recorded_actions = validated_actions
                        self.current_action_idx = 0
                        self.preset_var.set("-- Select Preset --")
                        file_name = file_path.replace("\\", "/").split("/")[-1]
                        self.current_source_label = f"File: {file_name}"
                        self.display_actions_in_queue(self.recorded_actions, self.current_source_label)
                        self.update_status("Loaded File", COLOR_SUCCESS)
                    else: 
                        self.update_status("Empty/Invalid File", COLOR_DANGER)
                else: 
                    self.update_status("Invalid File", COLOR_DANGER)
            except Exception: 
                self.update_status("Import Error", COLOR_DANGER)

    def display_actions_in_queue(self, actions, source_label=None):
        is_new_source = (source_label is not None and source_label != self.current_source_label)
        if source_label is None: 
            source_label = self.current_source_label

        # Save current scroll position unless loading a completely new macro file
        old_scroll = self.queue_viewer.code_text.yview() if not is_new_source else None

        self.current_source_label = source_label

        self.queue_viewer.clear()
        self.queue_viewer.append_line(f"// {source_label}", SYNTAX_GREEN, auto_scroll=is_new_source)

        for act in actions:
            desc = self.get_action_summary(act)
            color = self.get_action_color(act)
            self.queue_viewer.append_line(desc, color, auto_scroll=False)

        self.update_action_count_badge()
        self.update_scrubber_ui()
        self.queue_viewer.redraw_breakpoints()

        # Restore exact scroll offset
        if not is_new_source and old_scroll:
            self.queue_viewer.code_text.yview_moveto(old_scroll[0])
            self.queue_viewer.gutter.yview_moveto(old_scroll[0])
    
    def _place_settings_page(self):
        """Place the Settings form at rest; never move it per animation frame.

        The scrollable canvas has many embedded Tk windows. Repositioning and
        resizing them 18 times during a tab transition caused corrupted draws
        on some Windows/Tk configurations.
        """
        left = int(self._sidebar_visible_width) + 13
        self.frame_settings.place(
            x=left, y=76,
            relwidth=1.0, width=-(left + 13),
            relheight=1.0, height=-89
        )

    def _capture_settings_snapshot(self):
        """Capture the *visible* Settings panel as one Tk PhotoImage.

        Windows uses native GDI and Python's built-in Tk PhotoImage; no extra
        packages are needed. If capture fails, skip the animation instead of
        animating an unrelated grey rectangle.
        """
        form = self.frame_settings
        if not form.winfo_ismapped():
            return None
        self.root.update_idletasks()
        width, height = form.winfo_width(), form.winfo_height()
        if width < 2 or height < 2:
            return None
        sx, sy = form.winfo_rootx(), form.winfo_rooty()

        if os.name != "nt":
            return None  # Settings animation snapshots require native Windows GDI.

        import ctypes.wintypes as wt
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        # Set handle/pointer types explicitly for 64-bit Python.
        user32.GetDC.argtypes = [wt.HWND]
        user32.GetDC.restype = wt.HDC
        user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
        gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
        gdi32.CreateCompatibleDC.restype = wt.HDC
        gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
        gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
        gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
        gdi32.SelectObject.restype = wt.HGDIOBJ
        gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
        gdi32.DeleteDC.argtypes = [wt.HDC]
        gdi32.BitBlt.argtypes = [
            wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD
        ]

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wt.DWORD), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)
            ]

        screen_dc = memory_dc = bitmap = old_bitmap = None
        try:
            screen_dc = user32.GetDC(None)
            if not screen_dc:
                return None
            memory_dc = gdi32.CreateCompatibleDC(screen_dc)
            if not memory_dc:
                return None
            bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
            if not bitmap:
                return None
            old_bitmap = gdi32.SelectObject(memory_dc, bitmap)
            if not gdi32.BitBlt(memory_dc, 0, 0, width, height,
                                screen_dc, sx, sy, 0x00CC0020):
                return None
            stride = ((width * 3 + 3) // 4) * 4
            count = stride * height
            pixels = ctypes.create_string_buffer(count)
            header = BITMAPINFOHEADER()
            header.biSize = ctypes.sizeof(header)
            header.biWidth = width
            header.biHeight = -height   # top-down scan lines
            header.biPlanes = 1
            header.biBitCount = 24
            header.biSizeImage = count
            # GetDIBits requires the bitmap NOT to be selected into a DC.
            if old_bitmap:
                gdi32.SelectObject(memory_dc, old_bitmap)
                old_bitmap = None
            gdi32.GetDIBits.argtypes = [
                wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
                ctypes.c_void_p, wt.UINT
            ]
            gdi32.GetDIBits.restype = ctypes.c_int
            if gdi32.GetDIBits(memory_dc, bitmap, 0, height, pixels,
                                ctypes.byref(header), 0) != height:
                return None
            bgr = b"".join(pixels.raw[i:i + width * 3]
                           for i in range(0, count, stride))
            rgb = bytearray(width * height * 3)
            rgb[0::3] = bgr[2::3]
            rgb[1::3] = bgr[1::3]
            rgb[2::3] = bgr[0::3]
            return tk.PhotoImage(
                data=f"P6\n{width} {height}\n255\n".encode("ascii") + rgb,
                format="PPM"
            )
        except (OSError, ValueError, tk.TclError):
            return None
        finally:
            if old_bitmap and memory_dc:
                gdi32.SelectObject(memory_dc, old_bitmap)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if memory_dc:
                gdi32.DeleteDC(memory_dc)
            if screen_dc:
                user32.ReleaseDC(None, screen_dc)

    def _settings_snapshot_size(self):
        """Expected Settings size in the current workspace (screen coordinates)."""
        left = int(self._sidebar_visible_width) + 13
        return (max(1, self.workspace.winfo_width() - left - 13),
                max(1, self.workspace.winfo_height() - 89))

    def _prepare_settings_motion(self):
        """Animate a static copy of Settings, without moving its native widgets.

        Prefer an already-painted snapshot. Capturing newly shown widgets every
        time caused the incoming Settings page to flash at its final position
        before its bounce-in animation had even started.
        """
        expected_width, expected_height = self._settings_snapshot_size()
        shot = getattr(self, "_settings_cached_image", None)
        if shot is not None and (shot.width(), shot.height()) != (expected_width, expected_height):
            shot = None

        if shot is None:
            if not self.frame_settings.winfo_ismapped():
                # First visit only: Settings has never been painted. We need
                # an initial real rendering to obtain a faithful snapshot.
                self._place_settings_page()
                self.frame_settings.lift()
                # This call is only for the first ever capture (or after a
                # window-size change). Later visits use the cached image.
                self.root.update()
            shot = self._capture_settings_snapshot()
            if shot is None:
                return False
            self._settings_cached_image = shot

        self._settings_motion_image = shot
        self._settings_motion_view.delete("all")
        self._settings_motion_view.config(width=shot.width(), height=shot.height())
        self._settings_motion_view.create_image(0, 0, anchor="nw", image=shot)
        # Hide the actual controls BEFORE the first animation frame.
        self.frame_settings.place_forget()
        self._settings_motion_active = True
        return True

    def _position_settings_motion(self, offset):
        """Slide the entire frozen form as one composited panel."""
        if not self._settings_motion_active:
            return
        left = int(self._sidebar_visible_width) + 13
        self._settings_motion_view.place(
            x=left + int(offset), y=76,
            width=self._settings_motion_image.width(),
            height=self._settings_motion_image.height()
        )
        tk.Misc.tkraise(self._settings_motion_view)

    def _clear_settings_motion(self):
        self._settings_motion_view.place_forget()
        self._settings_motion_view.delete("all")
        self._settings_motion_image = None
        self._settings_motion_active = False

    def _show_docks_for_tab(self, tab_name):
        """Stage a page's actual docks without hiding another page's docks.

        Tab animations must move the independent dock containers, not just
        frame_playback / frame_sequencer (which no longer own those docks).
        """
        if tab_name == "playback":
            self._place_action_queue(self._sidebar_visible_width, resize_content=True)
            self._place_controls_dock()
        elif tab_name == "sequencer":
            self._place_sequencer_timeline(self._sidebar_visible_width, resize_content=True)
            self._place_stage_builder_dock()

    def _offset_tab_docks(self, tab_name, offset):
        """Slide a docked page without resizing any of its child widgets.

        The stationary, right-anchored positions are restored by
        _sync_controls_dock_visibility() at the end of the transition.
        """
        offset = int(offset)
        left = self._sidebar_visible_width + 13 + offset
        if tab_name == "playback":
            if self.queue_viewport.winfo_manager() == "place":
                self.queue_viewport.place_configure(x=left)
            if self.control_dock.winfo_manager() == "place":
                self.control_dock.place_configure(x=-13 + offset)
        elif tab_name == "sequencer":
            if self.seq_timeline_viewport.winfo_manager() == "place":
                self.seq_timeline_viewport.place_configure(x=left)
            if self.stage_builder_dock.winfo_manager() == "place":
                self.stage_builder_dock.place_configure(x=-13 + offset)

    def _settle_tab_transition(self):
        """Finish/interupt animation and leave exactly one page visible."""
        job = getattr(self, "_tab_anim_id", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self._tab_anim_id = None
        self._tab_anim_phase = None
        if hasattr(self, "_settings_motion_view"):
            self._clear_settings_motion()
        current = self.current_tab_name
        if current in ("playback", "sequencer", "settings"):
            self._set_tab_heading(current)
        for name, frame in (
            ("playback", self.frame_playback),
            ("sequencer", self.frame_sequencer),
            ("settings", self.frame_settings),
        ):
            if name != current:
                frame.place_forget()
            elif name == "settings":
                self._place_settings_page()
            else:
                frame.place(x=0, y=0, relwidth=1, relheight=1)

        self._sync_controls_dock_visibility()
        if current == "settings":
            # Settings must appear above the dock siblings, not behind them.
            self.frame_settings.lift()
        else:
            self.frame_playback.tkraise() if current == "playback" else self.frame_sequencer.tkraise()
        if hasattr(self, "sidebar_host") and self.sidebar_host.winfo_exists():
            self.sidebar_host.lift()

    def _set_tab_heading(self, tab_name):
        headings = {
            "playback": ("Playback", "Record, edit and execute actions"),
            "sequencer": ("Sequencer", "Arrange presets into repeatable pipelines"),
            "settings": ("Settings", "Customize your automation workspace"),
        }
        title, subtitle = headings[tab_name]
        self.workspace_heading.config(text=title)
        self.workspace_subtitle.config(text=subtitle)

    def show_tab(self, tab_name, animate=True):
        guide = getattr(self, "_walkthrough", None)
        if guide is not None and not guide._closed and not guide._navigate:
            guide.close(restore=False)
        self.root.focus_set()
        if hasattr(self, "active_hotkey_field") and self.active_hotkey_field:
            self.active_hotkey_field.focus_out()
        self.is_binding = False

        headings = {
            "playback": ("Playback", "Record, edit and execute actions"),
            "sequencer": ("Sequencer", "Arrange presets into repeatable pipelines"),
            "settings": ("Settings", "Customize your automation workspace"),
        }
        if tab_name not in headings:
            return

        old_tab = getattr(self, "current_tab_name", None)
        if old_tab == tab_name:
            return

        # If navigation is clicked rapidly, finish the previous transition
        # first, so no widgets are stranded at an intermediate x-offset.
        interrupted = (getattr(self, "_tab_anim_id", None) is not None
                       or getattr(self, "_tab_anim_phase", None) is not None)
        if interrupted:
            self._settle_tab_transition()

        # Settings is already fully painted while active. Snapshot it BEFORE
        # switching pages, instead of flashing the live form during next entry.
        if (old_tab == "settings" and not interrupted
                and self.frame_settings.winfo_ismapped()):
            # A just-settled transition may not have painted the form yet.
            # Keep its previously valid cached image in that case.
            self._settings_cached_image = self._capture_settings_snapshot()

        # Keep the outgoing page title until its exit animation has finished.
        # We update the heading when the new page actually begins entering.
        self.btn_tab_playback.set_active(tab_name == "playback")
        self.btn_tab_sequencer.set_active(tab_name == "sequencer")
        self.btn_tab_settings.set_active(tab_name == "settings")
        if tab_name == "sequencer":
            self.refresh_sequencer_preset_dropdown()

        self.current_tab_name = tab_name
        tab_frames = {
            "playback": self.frame_playback,
            "sequencer": self.frame_sequencer,
            "settings": self.frame_settings,
        }

        if not animate or not self.entertain_me or old_tab not in tab_frames:
            self._set_tab_heading(tab_name)
            self._settle_tab_transition()
            return

        # The exit and entrance are TWO animations, deliberately not a crossfade.
        # Playback/Sequencer each move two docks; Settings moves one cohesive
        # panel. Phase 2 only begins after Phase 1 has fully finished.
        for name, frame in tab_frames.items():
            if name not in (old_tab, tab_name):
                frame.place_forget()
        order = {"playback": 0, "sequencer": 1, "settings": 2}
        direction = 1 if order[tab_name] > order[old_tab] else -1
        self._tab_transition_from = old_tab
        self._tab_transition_to = tab_name
        self._tab_transition_direction = direction
        self._start_tab_animation_phase("out")

    def _hide_tab_visual(self, tab_name):
        """Hide only the page that just completed its exit animation."""
        if tab_name == "playback":
            self.queue_viewport.place_forget()
            self.control_dock.place_forget()
            self.frame_playback.place_forget()
        elif tab_name == "sequencer":
            self.seq_timeline_viewport.place_forget()
            self.stage_builder_dock.place_forget()
            self.frame_sequencer.place_forget()
        elif tab_name == "settings":
            self._clear_settings_motion()
            self.frame_settings.place_forget()

    def _start_tab_animation_phase(self, phase):
        """Stage one real page at a time: exit first, then enter."""
        if not self.root.winfo_exists():
            return
        tab = (self._tab_transition_from if phase == "out"
               else self._tab_transition_to)
        if phase == "in":
            self._set_tab_heading(tab)
        self._tab_anim_phase = phase
        self._tab_anim_id = None

        if tab == "settings":
            # Settings contains a scrollable native-widget form. Capture its
            # visible controls once so the whole form can bounce as one panel
            # without forcing Tk to redraw each embedded window per frame.
            if not self._prepare_settings_motion():
                if phase == "out":
                    self._hide_tab_visual("settings")
                    self._start_tab_animation_phase("in")
                else:
                    self._settle_tab_transition()
                return
        else:
            self._show_docks_for_tab(tab)
            frame = (self.frame_playback if tab == "playback"
                     else self.frame_sequencer)
            frame.place(x=0, y=0, relwidth=1, relheight=1)

        self.animate_tab_transition(phase, step=0)

    def animate_tab_transition(self, phase, step=0, total_steps=18):
        """Bounce OUT the previous page, then bounce IN the selected page.

        No entering docks appear until the exiting docks are fully hidden.
        This ensures Settings is one animated panel; the other pages animate
        their two docks together, all with consistent timing and easing.
        """
        if not self.root.winfo_exists():
            return
        tab = (self._tab_transition_from if phase == "out"
               else self._tab_transition_to)
        direction = self._tab_transition_direction
        dpi = getattr(self, "dpi_scale", 1.0)
        progress = min(1.0, step / total_steps)

        def back_in(t, s=1.5):
            return t * t * ((s + 1.0) * t - s)

        def back_out(t, s=1.5):
            u = t - 1.0
            return u * u * ((s + 1.0) * u + s) + 1.0

        if phase == "out":
            # Exiting dock(s) retract with a small anticipation/bounce.
            offset = round(-direction * 120 * dpi * back_in(progress))
        else:
            # Incoming dock(s) overshoot their target, then spring into place.
            offset = round(direction * 220 * dpi * (1.0 - back_out(progress)))

        if tab == "settings":
            self._position_settings_motion(offset)
        else:
            self._offset_tab_docks(tab, offset)

        if (hasattr(self, "overlay") and self.overlay.winfo_exists()
                and self.overlay.winfo_ismapped()):
            tk.Misc.tkraise(self.overlay)
        if hasattr(self, "sidebar_host") and self.sidebar_host.winfo_exists():
            self.sidebar_host.lift()

        if step < total_steps:
            self._tab_anim_id = self.root.after(
                14, lambda: self.animate_tab_transition(
                    phase, step + 1, total_steps
                )
            )
            return

        self._tab_anim_id = None
        if phase == "out":
            self._hide_tab_visual(tab)
            self._start_tab_animation_phase("in")
        else:
            self._settle_tab_transition()

    def _position_mini_hud_button(self, event=None):
        """Keep Mini HUD fixed beside the expanded sidebar boundary."""
        button = getattr(self, "mini_hud_button", None)
        brand = getattr(self, "_topbar_brand", None)
        if not button or not button.winfo_exists() or not brand or not brand.winfo_exists():
            return
        brand_right = brand.winfo_x() + brand.winfo_width()
        # Use the sidebar's full width, not its animated visible width.
        x = max(brand_right + 8, self.sidebar_width + 12)
        if button.winfo_x() != x:
            button.place_configure(x=x)

    def _cancel_sidebar_animation(self):
        """Cancel a pending frame before reversing or rebuilding navigation."""
        job = getattr(self, "_sidebar_anim_id", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self._sidebar_anim_id = None

    def _set_sidebar_visible_width(self, visible_width, commit=False):
        """Slide navigation and the Queue viewport without per-frame reflow.

        The Queue's visible left border follows the sidebar continuously.
        Controls and the Queue's right edge stay fixed on screen.
        """
        if not self.sidebar_host.winfo_exists():
            return
        width = max(0, min(self.sidebar_width, round(visible_width)))
        self._sidebar_visible_width = width
        self.sidebar_host.place(
            x=width - self.sidebar_width, y=0,
            width=self.sidebar_width + 1, relheight=1
        )
        # The heading and non-Playback pages still travel with navigation.
        # Keep their widths stable until the transition commits.
        if commit:
            self._sidebar_committed_width = width
        self.workspace_main.place_configure(
            x=width, width=-self._sidebar_committed_width
        )
        if getattr(self, 'current_tab_name', None) == 'playback':
            # Keep queue border, toolbar, and editor moving as one.
            self._place_action_queue(width, resize_content=True)
        elif getattr(self, 'current_tab_name', None) == 'sequencer':
            # The Stage Builder stays pinned; the timeline's left edge follows
            # the sidebar, including its header, filebar, and stage list.
            self._place_sequencer_timeline(width, resize_content=True)
        elif getattr(self, 'current_tab_name', None) == 'settings':
            # Do not drag/rescale the entire Settings Canvas every 20ms.
            # Allow the sidebar to pass over the stationary form; commit the
            # final Settings layout once when the animation has finished.
            if commit and hasattr(self, 'frame_settings') and self.frame_settings.winfo_exists():
                self._place_settings_page()
        if commit:
            self._queue_animation_active = False
        self.sidebar_host.lift()

    def _set_sidebar_open(self, opened):
        """Animate only with Entertain Me enabled; support rapid reversals."""
        opened = bool(opened)
        self._cancel_sidebar_animation()
        if getattr(self, "_tab_anim_id", None) is not None:
            self._settle_tab_transition()
        self.sidebar_open = opened
        target_width = self.sidebar_width if opened else 0

        if not self.entertain_me or self._sidebar_visible_width == target_width:
            self._set_sidebar_visible_width(target_width, commit=True)
            return

        # Animate only the active page's primary dock (Action Queue or
        # Macro Chain timeline); its right-hand inspector stays anchored.
        self._prepare_queue_for_transition()

        start_width = self._sidebar_visible_width
        started = time.perf_counter()
        duration = 0.22

        def tick():
            if not self.sidebar_host.winfo_exists():
                self._sidebar_anim_id = None
                return
            progress = min(1.0, (time.perf_counter() - started) / duration)
            eased = 1.0 - (1.0 - progress) ** 3  # ease out cubic
            self._set_sidebar_visible_width(
                start_width + (target_width - start_width) * eased
            )
            if progress < 1.0:
                self._sidebar_anim_id = self.root.after(20, tick)
            else:
                self._set_sidebar_visible_width(target_width, commit=True)
                self._sidebar_anim_id = None

        tick()

    def toggle_sidebar(self):
        self._set_sidebar_open(not self.sidebar_open)

    def open_sidebar(self):
        self._set_sidebar_open(True)

    def generate_blur_overlay(self):
        h_screen_dc = None
        h_dc_small = None
        h_bmp_small = None
        h_old_small = None
        h_dc_full = None
        h_bmp_full = None
        h_old_full = None

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        try:
            self.root.update_idletasks()
            rx = self.root.winfo_rootx()
            ry = self.root.winfo_rooty()
            rw = max(10, self.root.winfo_width())
            rh = max(10, self.root.winfo_height())

            h_screen_dc = user32.GetDC(0)
            if not h_screen_dc:
                raise RuntimeError("Failed to acquire screen DC")

            scale_factor = 12
            sw = max(2, rw // scale_factor)
            sh = max(2, rh // scale_factor)

            h_dc_small = gdi32.CreateCompatibleDC(h_screen_dc)
            h_bmp_small = gdi32.CreateCompatibleBitmap(h_screen_dc, sw, sh)
            h_old_small = gdi32.SelectObject(h_dc_small, h_bmp_small)

            pt = POINT()
            gdi32.SetStretchBltMode(h_dc_small, 4)
            gdi32.SetBrushOrgEx(h_dc_small, 0, 0, ctypes.byref(pt))
            gdi32.StretchBlt(h_dc_small, 0, 0, sw, sh, h_screen_dc, rx, ry, rw, rh, 0x00CC0020)

            h_dc_full = gdi32.CreateCompatibleDC(h_screen_dc)
            h_bmp_full = gdi32.CreateCompatibleBitmap(h_screen_dc, rw, rh)
            h_old_full = gdi32.SelectObject(h_dc_full, h_bmp_full)

            gdi32.SetStretchBltMode(h_dc_full, 4)
            gdi32.SetBrushOrgEx(h_dc_full, 0, 0, ctypes.byref(pt))
            gdi32.StretchBlt(h_dc_full, 0, 0, rw, rh, h_dc_small, 0, 0, sw, sh, 0x00CC0020)

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", ctypes.c_uint32),
                    ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32),
                    ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16),
                    ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32),
                    ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32),
                    ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32),
                ]

            stride = ((rw * 24 + 31) // 32) * 4
            image_size = stride * rh
            buf = (ctypes.c_char * image_size)()

            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth = rw
            bmi.biHeight = -rh
            bmi.biPlanes = 1
            bmi.biBitCount = 24
            bmi.biCompression = 0
            bmi.biSizeImage = image_size

            res = gdi32.GetDIBits(h_dc_full, h_bmp_full, 0, rh, buf, ctypes.byref(bmi), 0)
            top_down = True
            if res == 0:
                bmi.biHeight = rh
                res = gdi32.GetDIBits(h_dc_full, h_bmp_full, 0, rh, buf, ctypes.byref(bmi), 0)
                top_down = False

            if res == 0:
                raise RuntimeError("Failed to extract bitmap bits")

            raw_buf = bytes(buf)
            row_order = range(rh) if top_down else reversed(range(rh))
            raw_bgr = b"".join(raw_buf[y * stride : y * stride + rw * 3] for y in row_order)

            lut_r = bytes(min(255, int(i * 0.38 + 14)) for i in range(256))
            lut_g = bytes(min(255, int(i * 0.38 + 16)) for i in range(256))
            lut_b = bytes(min(255, int(i * 0.42 + 22)) for i in range(256))

            b = raw_bgr[0::3].translate(lut_b)
            g = raw_bgr[1::3].translate(lut_g)
            r = raw_bgr[2::3].translate(lut_r)

            rgb = bytearray(rw * rh * 3)
            rgb[0::3] = r
            rgb[1::3] = g
            rgb[2::3] = b

            ppm_data = f"P6 {rw} {rh} 255\n".encode("ascii") + rgb
            self.blur_image = tk.PhotoImage(data=ppm_data)

            self.overlay.delete("all")
            self.overlay.create_image(0, 0, anchor="nw", image=self.blur_image)
        except Exception:
            self.blur_image = None
            self.overlay.delete("all")
            self.overlay.create_rectangle(0, 0, 3000, 3000, fill="#1e1f22")
        finally:
            if h_dc_small and h_old_small:
                gdi32.SelectObject(h_dc_small, h_old_small)
            if h_bmp_small:
                gdi32.DeleteObject(h_bmp_small)
            if h_dc_small:
                gdi32.DeleteDC(h_dc_small)

            if h_dc_full and h_old_full:
                gdi32.SelectObject(h_dc_full, h_old_full)
            if h_bmp_full:
                gdi32.DeleteObject(h_bmp_full)
            if h_dc_full:
                gdi32.DeleteDC(h_dc_full)

            if h_screen_dc:
                user32.ReleaseDC(0, h_screen_dc)

                
    def close_sidebar(self):
        self._set_sidebar_open(False)

    def on_hotkey_change(self, action_key, vk, mod_vks, name):
        self.hotkeys[action_key] = {"vk": vk, "mod_vks": mod_vks, "name": name}
        self.update_main_buttons()

    def update_main_buttons(self):
        if not self.recording and not self.playing:
            self.record_btn.label.config(text=self.get_btn_text("start_rec"))
            self.play_btn.label.config(text=self.get_btn_text("start_play"))
        elif self.recording:
            self.record_btn.label.config(text=self.get_btn_text("stop_rec"))
        elif self.playing:
            self.play_btn.label.config(text=self.get_btn_text("stop_play"))


    def check_hotkey_active(self, action_key):
        hk = self.hotkeys[action_key]
        if hk["vk"] is None: return False
        if not (ctypes.windll.user32.GetAsyncKeyState(hk["vk"]) & 0x8000): return False
            
        for m_vk in hk["mod_vks"]:
            if not (ctypes.windll.user32.GetAsyncKeyState(m_vk) & 0x8000): return False
                
        ctrl = bool(ctypes.windll.user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
        shift = bool(ctypes.windll.user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
        alt = bool(ctypes.windll.user32.GetAsyncKeyState(VK_MENU) & 0x8000)
        win = bool((ctypes.windll.user32.GetAsyncKeyState(VK_LWIN) & 0x8000) or 
                   (ctypes.windll.user32.GetAsyncKeyState(VK_RWIN) & 0x8000))
        
        req_ctrl = VK_CONTROL in hk["mod_vks"]
        req_shift = VK_SHIFT in hk["mod_vks"]
        req_alt = VK_MENU in hk["mod_vks"]
        req_win = any(m in (VK_LWIN, VK_RWIN) for m in hk["mod_vks"])
        
        if ctrl != req_ctrl or shift != req_shift or alt != req_alt or win != req_win:
            return False
        return True

    def global_hotkey_listener(self):
        held_states = {k: False for k in self.hotkeys.keys()}
        while True:
            if not self.is_binding:
                for action in self.hotkeys.keys():
                    if self.check_hotkey_active(action):
                        if not held_states[action]:
                            held_states[action] = True
                            self.trigger_action(action)
                    else:
                        held_states[action] = False
            time.sleep(0.05)

    def toggle_recording(self):
        if not self.recording: self.start_recording()
        else: self.stop_recording()

    def toggle_playback(self):
        if not self.playing: self.start_macro()
        else: self.stop_macro()

    def start_recording(self):
        self.recording = True
        self.recording_paused = False
        self.last_event_time = time.perf_counter()
        self.preset_var.set("-- Select Preset --")
        self.show_tab("playback")
        self.update_status("Recording...", COLOR_DANGER)
        if self.audio_cues: play_cue("start_rec")

        self.queue_viewer.set_interactive(False)
        self.record_btn.set_state("normal", text=self.get_btn_text("stop_rec"), bg=BG_BORDER, hover=BG_HOVER)
        self.play_btn.set_state("disabled")
        self.pause_btn.set_state("normal", text="Pause Rec (F9)", bg=BG_BORDER, hover=BG_HOVER)

        self.recorded_actions.clear()
        self.current_action_idx = 0
        self.update_action_count_badge()
        self.queue_viewer.clear()
        self.current_source_label = "Recorded Sequence"
        self.queue_viewer.append_line(f"// {self.current_source_label}", SYNTAX_GREEN)

        # Run the wheel hook on a dedicated background thread with its own message pump
        Thread(target=self._mouse_wheel_hook_worker, daemon=True).start()
        Thread(target=self.poll_inputs, daemon=True).start()
        
    def stop_recording(self):
        self.recording = False
        self.recording_paused = False
        self.remove_mouse_wheel_hook()

        self.current_action_idx = 0
        self.update_action_count_badge()
        self.update_status(f"Actions: {len(self.recorded_actions)}", TEXT_MUTED)
        if self.audio_cues: play_cue("stop_rec") 

        self.record_btn.set_state("normal", text=self.get_btn_text("start_rec"), bg=COLOR_DANGER, hover=COLOR_DANGER_HOV)
        self.play_btn.set_state("normal")
        self.pause_btn.set_state("disabled", text="Pause (F9)", bg=BG_BORDER, hover=BG_HOVER)

        self.display_actions_in_queue(self.recorded_actions, "Recorded Sequence")
        self.queue_viewer.set_interactive(True)

    def _mouse_wheel_hook_worker(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # Complete Win32 tagMSG structure including lPrivate
        class MSG(ctypes.Structure):
            _fields_ = [
                ("hwnd", ctypes.c_void_p),
                ("message", ctypes.c_uint32),
                ("wParam", ctypes.c_size_t),
                ("lParam", ctypes.c_size_t),
                ("time", ctypes.c_uint32),
                ("pt", POINT),
                ("lPrivate", ctypes.c_uint32)
            ]

        HOOKPROC = getattr(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE)(
            ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_size_t
        )

        def low_level_mouse_proc(nCode, wParam, lParam):
            if nCode >= 0 and wParam == WM_MOUSEWHEEL:
                if self.recording and not self.recording_paused:
                    try:
                        # Extract directly from memory address to avoid pointer truncation
                        info = MSLLHOOKSTRUCT.from_address(lParam)
                        delta = ctypes.c_short((info.mouseData >> 16) & 0xFFFF).value
                        self.root.after(0, self.record_scroll_event, delta, info.pt.x, info.pt.y)
                    except Exception:
                        pass
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._wheel_hook_proc = HOOKPROC(low_level_mouse_proc)

        # Explicitly configure 64-bit argument and return types
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, ctypes.c_uint32]

        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_size_t]

        user32.UnhookWindowsHookEx.restype = ctypes.c_bool
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]

        user32.PeekMessageW.restype = ctypes.c_bool
        user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
        user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]

        # Global WH_MOUSE_LL hooks run locally and do not require a module handle
        self._wheel_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._wheel_hook_proc, None, 0)

        if not self._wheel_hook:
            # Fallback with explicit 64-bit handle return type
            kernel32.GetModuleHandleW.restype = ctypes.c_void_p
            kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
            hMod = kernel32.GetModuleHandleW(None)
            self._wheel_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._wheel_hook_proc, hMod, 0)

        msg = MSG()
        while self.recording and self._wheel_hook:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.001)

        if self._wheel_hook:
            try:
                user32.UnhookWindowsHookEx(self._wheel_hook)
            except Exception:
                pass
            self._wheel_hook = None
        self._wheel_hook_proc = None

    def remove_mouse_wheel_hook(self):
        if self._wheel_hook:
            try: ctypes.windll.user32.UnhookWindowsHookEx(self._wheel_hook)
            except Exception: pass
            self._wheel_hook = None
        self._wheel_hook_proc = None

    def record_scroll_event(self, delta, x, y):
        if not self.recording or self.recording_paused:
            return

        # Ignore wheel scrolling over the Sequent application window
        try:
            rx = self.root.winfo_rootx()
            ry = self.root.winfo_rooty()
            rw = self.root.winfo_width()
            rh = self.root.winfo_height()
            if rx <= x <= (rx + rw) and ry <= y <= (ry + rh):
                return
        except Exception:
            pass

        # Natural timing interval gap
        if self.natural_timing:
            now_perf = time.perf_counter()
            gap = round(now_perf - self.last_event_time, 2)
            if 0.05 <= gap <= 2.5:
                self.recorded_actions.append({"type": "delay", "seconds": gap})
                self.queue_viewer.append_line(f"system.delay({gap:.2f}s)", SYNTAX_PURPLE)
            self.last_event_time = now_perf

        hwnd, wx, wy, title = get_active_window_info() if self.window_relative else (None, 0, 0, "")
        scroll_act = {"type": "scroll", "delta": int(delta), "x": int(x), "y": int(y)}
        if self.window_relative and hwnd:
            rx, ry = screen_to_client_pos(hwnd, x, y)
            scroll_act.update({"win_title": title, "rel_x": rx, "rel_y": ry})

        self.recorded_actions.append(scroll_act)
        self.queue_viewer.append_line(f"mouse.scroll(delta={delta}, x={x}, y={y})", SYNTAX_BLUE)
        self.update_action_count_badge()
        
    def poll_inputs(self):
        key_states = {i: bool(ctypes.windll.user32.GetAsyncKeyState(i) & 0x8000) for i in range(1, 255)}
        self.active_keys = [] 
        mod_used_in_combo = {VK_SHIFT: False, VK_CONTROL: False, VK_MENU: False, VK_LWIN: False, VK_RWIN: False}

        last_x, last_y = get_mouse_pos()
        last_move_time = time.time()
        self.last_event_time = time.perf_counter()

        lbutton_down_time = 0.0
        lbutton_down_pos = None
        rbutton_down_time = 0.0
        rbutton_down_pos = None

        while self.recording:
            if self.recording_paused:
                time.sleep(0.04)
                last_x, last_y = get_mouse_pos()
                self.last_event_time = time.perf_counter()
                key_states = {i: bool(ctypes.windll.user32.GetAsyncKeyState(i) & 0x8000) for i in range(1, 255)}
                continue

            win_pressed = bool((ctypes.windll.user32.GetAsyncKeyState(VK_LWIN) & 0x8000) or 
                               (ctypes.windll.user32.GetAsyncKeyState(VK_RWIN) & 0x8000))
            ctrl_pressed = bool(ctypes.windll.user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
            alt_pressed = bool(ctypes.windll.user32.GetAsyncKeyState(VK_MENU) & 0x8000)
            shift_pressed = bool(ctypes.windll.user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
            
            curr_x, curr_y = get_mouse_pos()
            now = time.time()

            # 1:1 Live Mouse Movement Tracking (~30 Hz ensures smooth recording without flooding UI)
            if not self.clicks_and_keys_only and (curr_x, curr_y) != (last_x, last_y):
                if now - last_move_time >= 0.03:
                    move_act = {"type": "move", "x": curr_x, "y": curr_y}
                    if self.window_relative:
                        hwnd, wx, wy, title = get_active_window_info()
                        if hwnd:
                            rx, ry = screen_to_client_pos(hwnd, curr_x, curr_y)
                            move_act.update({"win_title": title, "rel_x": rx, "rel_y": ry})

                    self.recorded_actions.append(move_act)
                    self.root.after(0, self.queue_viewer.append_line, f"mouse.move(x={curr_x}, y={curr_y})", SYNTAX_BLUE)
                    self.root.after(0, self.update_action_count_badge)

                    last_x, last_y = curr_x, curr_y
                    last_move_time = now
                    self.last_event_time = time.perf_counter()

            for vk in range(1, 255):
                if vk in (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5): continue
                state = ctypes.windll.user32.GetAsyncKeyState(vk)
                is_pressed = (state & 0x8000) != 0

                if is_pressed and not key_states[vk]:
                    key_states[vk] = True
                    self.active_keys.append(vk) 

                    # Track Mouse Button Down for Drag Support
                    if vk == VK_LBUTTON:
                        lbutton_down_time = time.time()
                        lbutton_down_pos = (curr_x, curr_y)
                        continue
                    elif vk == VK_RBUTTON:
                        rbutton_down_time = time.time()
                        rbutton_down_pos = (curr_x, curr_y)
                        continue

                    if self.check_hotkey_active("stop_rec") or self.check_hotkey_active("start_rec") or self.check_hotkey_active("start_play"):
                        for m in mod_used_in_combo: mod_used_in_combo[m] = True
                        continue

                    if self.natural_timing:
                        delta = round(time.perf_counter() - self.last_event_time, 2)
                        if 0.05 <= delta <= 2.5:
                            self.recorded_actions.append({"type": "delay", "seconds": delta})
                            self.root.after(0, self.queue_viewer.append_line, f"system.delay({delta:.2f}s)", SYNTAX_PURPLE)

                    self.last_event_time = time.perf_counter()

                    if vk in MODIFIER_VK_MAP:
                        mod_used_in_combo[vk] = False
                    else:
                        if win_pressed: mod_used_in_combo[VK_LWIN] = True; mod_used_in_combo[VK_RWIN] = True
                        if ctrl_pressed: mod_used_in_combo[VK_CONTROL] = True
                        if alt_pressed: mod_used_in_combo[VK_MENU] = True
                        if shift_pressed: mod_used_in_combo[VK_SHIFT] = True
                        self.handle_input_event(vk, win_pressed, ctrl_pressed, alt_pressed, shift_pressed)

                elif not is_pressed and key_states[vk]:
                    key_states[vk] = False
                    if vk in self.active_keys: self.active_keys.remove(vk) 

                    if vk in (VK_LBUTTON, VK_RBUTTON):
                        btn = "left" if vk == VK_LBUTTON else "right"
                        start_time = lbutton_down_time if vk == VK_LBUTTON else rbutton_down_time
                        start_pos = lbutton_down_pos if vk == VK_LBUTTON else rbutton_down_pos

                        if vk == VK_LBUTTON:
                            lbutton_down_time = 0.0; lbutton_down_pos = None
                        else:
                            rbutton_down_time = 0.0; rbutton_down_pos = None

                        if start_time == 0.0 or start_pos is None:
                            continue

                        hold_dur = round(time.time() - start_time, 2)
                        dist = math.hypot(curr_x - start_pos[0], curr_y - start_pos[1])

                        if self.natural_timing:
                            delta = round(time.perf_counter() - self.last_event_time, 2)
                            if 0.05 <= delta <= 2.5:
                                self.recorded_actions.append({"type": "delay", "seconds": delta})
                                self.root.after(0, self.queue_viewer.append_line, f"system.delay({delta:.2f}s)", SYNTAX_PURPLE)
                        self.last_event_time = time.perf_counter()

                        hwnd, wx, wy, title = get_active_window_info() if self.window_relative else (None, 0, 0, "")

                        if hold_dur >= 0.18 and dist >= 12:
                            drag_act = {
                                "type": "drag", "btn": btn,
                                "start_x": start_pos[0], "start_y": start_pos[1],
                                "end_x": curr_x, "end_y": curr_y,
                                "duration": max(0.2, hold_dur)
                            }
                            if self.window_relative and hwnd:
                                rx1, ry1 = screen_to_client_pos(hwnd, start_pos[0], start_pos[1])
                                rx2, ry2 = screen_to_client_pos(hwnd, curr_x, curr_y)
                                drag_act.update({"win_title": title, "rel_start_x": rx1, "rel_start_y": ry1, "rel_end_x": rx2, "rel_end_y": ry2})

                            self.recorded_actions.append(drag_act)
                            self.root.after(0, self.queue_viewer.append_line, f"mouse.drag(from=({start_pos[0]},{start_pos[1]}), to=({curr_x},{curr_y}), dur={hold_dur}s)", SYNTAX_BLUE)
                            self.root.after(0, self.update_action_count_badge)
                        else:
                            click_act = {"type": "click", "btn": btn, "x": start_pos[0], "y": start_pos[1]}
                            if self.window_relative and hwnd:
                                rx, ry = screen_to_client_pos(hwnd, start_pos[0], start_pos[1])
                                click_act.update({"win_title": title, "rel_x": rx, "rel_y": ry})

                            self.recorded_actions.append(click_act)
                            self.root.after(0, self.queue_viewer.append_line, f"mouse.{btn}_click(x={start_pos[0]}, y={start_pos[1]})", SYNTAX_BLUE)
                            self.root.after(0, self.update_action_count_badge)
                        continue

                    if vk in MODIFIER_VK_MAP:
                        if not mod_used_in_combo.get(vk, False):
                            name = MODIFIER_VK_MAP[vk]
                            self.recorded_actions.append({"type": "press", "vk": vk, "name": name})
                            self.root.after(0, self.queue_viewer.append_line, f"keyboard.press('{name}')", SYNTAX_ORANGE)
                            self.root.after(0, self.update_action_count_badge)
                        mod_used_in_combo[vk] = False

            time.sleep(0.005)

    def handle_input_event(self, vk, win_pressed, ctrl_pressed, alt_pressed, shift_pressed):
        if vk == VK_ESCAPE:
            self.root.after(0, self.stop_recording)
            return

        active_mods, mod_names = [], []
        if win_pressed: active_mods.append(VK_LWIN); mod_names.append("Win")
        if ctrl_pressed: active_mods.append(VK_CONTROL); mod_names.append("Ctrl")
        if alt_pressed: active_mods.append(VK_MENU); mod_names.append("Alt")
        if shift_pressed: active_mods.append(VK_SHIFT); mod_names.append("Shift")

        if active_mods:
            if ctrl_pressed and not (win_pressed or alt_pressed or shift_pressed):
                if vk == VK_A:
                    self.recorded_actions.append({"type": "select_all"})
                    self.root.after(0, self.queue_viewer.append_line, "command.select_all()", SYNTAX_PURPLE)
                    self.root.after(0, self.update_action_count_badge)
                    return
                elif vk == VK_C:
                    self.recorded_actions.append({"type": "copy"})
                    self.root.after(0, self.queue_viewer.append_line, "command.copy()", SYNTAX_PURPLE)
                    self.root.after(0, self.update_action_count_badge)
                    return
                elif vk == VK_V:
                    self.recorded_actions.append({"type": "paste"})
                    self.root.after(0, self.queue_viewer.append_line, "command.paste()", SYNTAX_PURPLE)
                    self.root.after(0, self.update_action_count_badge)
                    return

            key_name = SPECIAL_KEY_NAMES.get(vk)
            if not key_name:
                if vk == VK_RETURN: key_name = "Enter"
                elif vk == VK_BACK: key_name = "Backspace"
                else:
                    char = get_char_from_vk(vk)
                    key_name = char.upper() if char else f"Key_{vk}"

            combo_name = " + ".join(mod_names + [key_name])
            self.recorded_actions.append({"type": "combo", "mods": active_mods, "vk": vk, "name": combo_name})
            self.root.after(0, self.queue_viewer.append_line, f"keyboard.hotkey('{combo_name}')", SYNTAX_PURPLE)
            self.root.after(0, self.update_action_count_badge)
            return

        if vk == VK_RETURN:
            self.recorded_actions.append({"type": "enter"})
            self.root.after(0, self.queue_viewer.append_line, "keyboard.press('Enter')", SYNTAX_ORANGE)
            self.root.after(0, self.update_action_count_badge)
            return

        if vk == VK_BACK:
            self.recorded_actions.append({"type": "backspace"})
            self.root.after(0, self.queue_viewer.append_line, "keyboard.press('Backspace')", SYNTAX_ORANGE)
            self.root.after(0, self.update_action_count_badge)
            return

        if vk in SPECIAL_KEY_NAMES:
            name = SPECIAL_KEY_NAMES[vk]
            self.recorded_actions.append({"type": "press", "vk": vk, "name": name})
            self.root.after(0, self.queue_viewer.append_line, f"keyboard.press('{name}')", SYNTAX_ORANGE)
            self.root.after(0, self.update_action_count_badge)
            return

        char = get_char_from_vk(vk)
        if char and char.isprintable():
            self.recorded_actions.append({"type": "key", "key": char})
            self.root.after(0, self.queue_viewer.append_line, f"keyboard.type('{char}')", SYNTAX_ORANGE)
            self.root.after(0, self.update_action_count_badge)
    
    def start_macro(self):
        if not self.recorded_actions:
            self.update_status("Queue Empty", COLOR_DANGER)
            return
        self._prepare_mouse_execution()
        self.dynamic_engine = SequentDynamicEngine(self)
        self.playing = True
        self.show_tab("playback") 
        self.update_status("Running Script...", COLOR_ACCENT)
        self.is_paused = False
        self.pause_btn.set_state("normal", text="Pause (F9)", bg=BG_BORDER, hover=BG_HOVER)
        
        self.queue_viewer.set_interactive(False)
        self.record_btn.set_state("disabled")
        self.play_btn.set_state("normal", text=self.get_btn_text("stop_play"), bg=BG_BORDER, hover=BG_HOVER)
        
        Thread(target=self.run_sequence, daemon=True).start()

    def stop_macro(self):
        # Capture view position before playhead reset
        old_scroll = self.queue_viewer.code_text.yview()

        self.playback_run_id = time.time()
        self._playback_ui_pending = False
        self.playing = False
        self.is_paused = False
        self.update_status("Stopped by user", SYNTAX_ORANGE)
        if self.audio_cues: 
            play_cue("stop_rec")

        if hasattr(self, 'ghost_reticle'):
            self.ghost_reticle.hide()

        self.current_action_idx = 0
        self.pause_btn.set_state("disabled", text="Pause (F9)", bg=BG_BORDER, hover=BG_HOVER)
        self.play_btn.set_state("normal", text=self.get_btn_text("start_play"), bg=COLOR_ACCENT, hover=COLOR_ACCENT_HOV)
        self.record_btn.set_state("normal", text=self.get_btn_text("start_rec"), bg=COLOR_DANGER, hover=COLOR_DANGER_HOV)
        self.queue_viewer.set_interactive(True)
        self.update_scrubber_ui(show_reticle=False)

        # Restore view position
        self.queue_viewer.code_text.yview_moveto(old_scroll[0])
        self.queue_viewer.gutter.yview_moveto(old_scroll[0])


    def prompt_user_input(self, prompt_text, default_val=""):
        import threading
        evt = threading.Event()
        result = [default_val]

        def _show():
            dlg = tk.Toplevel(self.root)
            dlg.resizable(False, False)
            if os.name != 'nt':
                dlg.overrideredirect(True)
            dlg.attributes('-topmost', True)
            dlg.configure(bg=BG_BORDER)

            w, h = 360, 150

            # Detect the active monitor work area using Win32 API
            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_uint32),
                    ("rcMonitor", RECT),
                    ("rcWork", RECT),
                    ("dwFlags", ctypes.c_uint32)
                ]

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            MONITOR_DEFAULTTONEAREST = 2

            if hwnd:
                hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
            else:
                pt = POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                hmon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)

            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                mon_x = mi.rcWork.left
                mon_y = mi.rcWork.top
                mon_w = mi.rcWork.right - mi.rcWork.left
                mon_h = mi.rcWork.bottom - mi.rcWork.top
            else:
                mon_x, mon_y = 0, 0
                mon_w, mon_h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

            # Center prompt window on the active monitor
            rx = mon_x + (mon_w - w) // 2
            ry = mon_y + (mon_h - h) // 2
            dlg.geometry(f"{w}x{h}+{rx}+{ry}")
            if os.name == 'nt':
                dlg._seq_native_chrome = install_sequent_chrome(dlg, resizable=False)

            card = tk.Frame(dlg, bg=BG_PANEL)
            card.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

            # Header bar with drag handles
            head = tk.Frame(card, bg=BG_HEADER, height=40, cursor="fleur")
            head.pack(fill=tk.X)

            drag_icon = tk.Label(head, text=" ⠇ ", font=("Segoe UI", 9), bg=BG_HEADER, fg=TEXT_MUTED, cursor="fleur")
            drag_icon.pack(side=tk.LEFT, padx=(4, 0))

            lbl_title = tk.Label(head, text="INPUT REQUESTED", font=("Segoe UI", 8, "bold"), bg=BG_HEADER, fg=SYNTAX_BLUE, cursor="fleur")
            lbl_title.pack(side=tk.LEFT, padx=4, pady=5)

            # Dragging mechanics
            drag_data = {"x": 0, "y": 0, "win_x": 0, "win_y": 0}

            def _start_drag(e):
                if os.name == 'nt' and getattr(dlg, '_seq_native_chrome', None):
                    begin_native_window_motion(dlg, 2)
                    return
                drag_data["x"] = e.x_root
                drag_data["y"] = e.y_root
                drag_data["win_x"] = dlg.winfo_x()
                drag_data["win_y"] = dlg.winfo_y()

            def _do_drag(e):
                if os.name == 'nt' and getattr(dlg, '_seq_native_chrome', None):
                    return
                dx = e.x_root - drag_data["x"]
                dy = e.y_root - drag_data["y"]
                dlg.geometry(f"+{drag_data['win_x'] + dx}+{drag_data['win_y'] + dy}")

            for widget in (head, drag_icon, lbl_title):
                widget.bind("<Button-1>", _start_drag)
                widget.bind("<B1-Motion>", _do_drag)

            body = tk.Frame(card, bg=BG_PANEL, padx=14, pady=10)
            body.pack(fill=tk.BOTH, expand=True)

            tk.Label(body, text=prompt_text, font=("Segoe UI", 9, "bold"), bg=BG_PANEL, fg=TEXT_PRIMARY, anchor="w").pack(fill=tk.X, pady=(0, 6))

            entry = tk.Entry(body, bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Consolas", 10), insertbackground=TEXT_PRIMARY, bd=0, highlightbackground=BG_BORDER, highlightthickness=1)
            entry.pack(fill=tk.X, ipady=3, pady=(0, 10))
            if default_val:
                entry.insert(0, default_val)
                entry.select_range(0, "end")
            entry.focus_force()

            btn_row = tk.Frame(body, bg=BG_PANEL)
            btn_row.pack(fill=tk.X)

            def submit(event=None):
                result[0] = entry.get()
                try: dlg.destroy()
                except Exception: pass
                evt.set()

            def cancel(event=None):
                try: dlg.destroy()
                except Exception: pass
                evt.set()

            entry.bind("<Return>", submit)
            dlg.bind("<Escape>", cancel)

            ok_btn = ModernButton(btn_row, text="Submit", bg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOV, command=submit)
            ok_btn.pack(side=tk.RIGHT)

        self.root.after(0, _show)
        while not evt.is_set():
            if not self.playing and not self.recording:
                break
            evt.wait(0.05)

        return result[0]
        
    def show_disclaimer_modal(self, title_text, message_text):
        """Displays a centered, draggable warning/disclaimer modal with Proceed and Abort options."""
        import threading
        evt = threading.Event()
        proceed = [False]

        def _show():
            dlg = tk.Toplevel(self.root)
            dlg.resizable(False, False)
            if os.name != 'nt':
                dlg.overrideredirect(True)
            dlg.attributes('-topmost', True)
            dlg.configure(bg=BG_BORDER)

            w, h = 380, 210

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_uint32),
                    ("rcMonitor", RECT),
                    ("rcWork", RECT),
                    ("dwFlags", ctypes.c_uint32)
                ]

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            MONITOR_DEFAULTTONEAREST = 2

            if hwnd:
                hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
            else:
                pt = POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                hmon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)

            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                mon_x = mi.rcWork.left
                mon_y = mi.rcWork.top
                mon_w = mi.rcWork.right - mi.rcWork.left
                mon_h = mi.rcWork.bottom - mi.rcWork.top
            else:
                mon_x, mon_y = 0, 0
                mon_w, mon_h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

            rx = mon_x + (mon_w - w) // 2
            ry = mon_y + (mon_h - h) // 2
            dlg.geometry(f"{w}x{h}+{rx}+{ry}")
            if os.name == 'nt':
                dlg._seq_native_chrome = install_sequent_chrome(dlg, resizable=False)

            card = tk.Frame(dlg, bg=BG_PANEL)
            card.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

            head = tk.Frame(card, bg=BG_HEADER, height=40, cursor="fleur")
            head.pack(fill=tk.X)

            drag_icon = tk.Label(head, text=" ⠇ ", font=("Segoe UI", 9), bg=BG_HEADER, fg=TEXT_MUTED, cursor="fleur")
            drag_icon.pack(side=tk.LEFT, padx=(4, 0))

            lbl_title = tk.Label(head, text=f"⚠️  {title_text.upper()}", font=("Segoe UI", 8, "bold"), bg=BG_HEADER, fg=SYNTAX_ORANGE, cursor="fleur")
            lbl_title.pack(side=tk.LEFT, padx=4, pady=5)

            drag_data = {"x": 0, "y": 0, "win_x": 0, "win_y": 0}

            def _start_drag(e):
                if os.name == 'nt' and getattr(dlg, '_seq_native_chrome', None):
                    begin_native_window_motion(dlg, 2)
                    return
                drag_data["x"] = e.x_root
                drag_data["y"] = e.y_root
                drag_data["win_x"] = dlg.winfo_x()
                drag_data["win_y"] = dlg.winfo_y()

            def _do_drag(e):
                if os.name == 'nt' and getattr(dlg, '_seq_native_chrome', None):
                    return
                dx = e.x_root - drag_data["x"]
                dy = e.y_root - drag_data["y"]
                dlg.geometry(f"+{drag_data['win_x'] + dx}+{drag_data['win_y'] + dy}")

            for widget in (head, drag_icon, lbl_title):
                widget.bind("<Button-1>", _start_drag)
                widget.bind("<B1-Motion>", _do_drag)

            body = tk.Frame(card, bg=BG_PANEL, padx=16, pady=12)
            body.pack(fill=tk.BOTH, expand=True)

            msg_box = tk.Message(
                body, text=message_text, font=("Segoe UI", 9), bg=BG_PANEL,
                fg=TEXT_PRIMARY, width=340, anchor="w", justify="left"
            )
            msg_box.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

            btn_row = tk.Frame(body, bg=BG_PANEL)
            btn_row.pack(fill=tk.X)

            def on_confirm(event=None):
                proceed[0] = True
                try: dlg.destroy()
                except Exception: pass
                evt.set()

            def on_cancel(event=None):
                proceed[0] = False
                try: dlg.destroy()
                except Exception: pass
                evt.set()

            dlg.bind("<Return>", on_confirm)
            dlg.bind("<Escape>", on_cancel)

            btn_abort = ModernButton(btn_row, text="Abort Macro", bg_color=BG_BORDER, hover_color=COLOR_DANGER, command=on_cancel)
            btn_abort.pack(side=tk.LEFT)

            btn_proceed = ModernButton(btn_row, text="Proceed ▶", bg_color=COLOR_SUCCESS, hover_color="#1d8a4b", command=on_confirm)
            btn_proceed.pack(side=tk.RIGHT)

        self.root.after(0, _show)
        while not evt.is_set():
            if not self.playing and not self.recording:
                break
            evt.wait(0.05)

        return proceed[0]

    def _run_structured_macro(self):
        """Regular playback using the same interpreter as macro chains."""
        total = len(self.recorded_actions)
        self.playback_run_id = time.time()
        self._playback_ui_pending = False
        run_id = self.playback_run_id
        reason = ''
        if self.audio_cues:play_cue('start')
        try:
            loops = 1000000 if self.loop_mode == '∞ Loop' else int(self.loop_mode.replace('x',''))
            for rep in range(loops):
                if not self.playing:break
                start = self.current_action_idx if rep==0 and 0 <= self.current_action_idx < total else 0
                def on_step(index,action):
                    if index in self.breakpoints:
                        self.is_paused = True
                        self.root.after(0,lambda i=index:self.update_status(f'Breakpoint at step {i+1}',COLOR_DANGER))
                        self.root.after(0,lambda:self.pause_btn.set_state('normal',text='Resume (F9)',bg=COLOR_SUCCESS,hover=COLOR_SUCCESS))
                        while self.is_paused and self.playing:time.sleep(.04)
                    self.root.after(0,lambda i=index,r=run_id:self._on_playback_step(i,r))
                result = self.dynamic_engine.run_program(self.recorded_actions,start,on_step=on_step)
                if result.get('switch'):
                    self.switching_preset=True
                    self.playing=False
                    target=result.get('target','')
                    if result['switch']=='preset':
                        self.root.after(150,lambda t=target:self.switch_to_preset_and_start(t))
                    elif result['switch']=='file':
                        self.root.after(150,lambda t=target:self.switch_to_file_and_start(t))
                    return
                if result.get('cancelled'):break
        except Exception as exc:
            reason = str(exc) if isinstance(exc,MacroExecutionError) else f'Unexpected {type(exc).__name__}: {exc}'
            self.playing=False
            if self.audio_cues:play_cue('error')
        finally:
            if not self.switching_preset:
                state = 'failed' if reason else 'cancelled' if not self.playing else 'completed'
                self.root.after(0,self.finish_macro,state,reason)

    def _stop_webhook_listener(self):
        server=self._webhook_server
        self._webhook_server=None
        self._webhook_config=None
        if server is not None:
            # Do not wait for HTTP shutdown inside the Tk event loop.
            Thread(target=lambda: (server.shutdown(),server.server_close()),daemon=True).start()

    def _ensure_webhook_listener(self, trigger):
        """Bound to loopback and authenticated; never exposes an unauthenticated desktop macro."""
        token=os.environ.get('SEQUENT_WEBHOOK_TOKEN','').strip()
        if not token or len(token)<12:
            self._stop_webhook_listener()
            return 'Set SEQUENT_WEBHOOK_TOKEN to a secret of at least 12 characters'
        try:
            port=int(trigger.get('port',8765))
            if not 1024<=port<=65535:raise ValueError
        except (TypeError,ValueError):
            return 'Webhook port must be between 1024 and 65535'
        route=str(trigger.get('value','/sequent')).strip() or '/sequent'
        if not route.startswith('/') or '?' in route or '#' in route:
            return 'Webhook route must start with / and omit query strings'
        config=(port,route,token)
        if self._webhook_server and self._webhook_config==config:
            return None
        self._stop_webhook_listener()
        events=self._webhook_events
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                provided=self.headers.get('X-Sequent-Token','')
                if self.path!=route:
                    self.send_error(404);return
                if not hmac.compare_digest(provided,token):
                    self.send_error(401);return
                # Discard body: macro arguments are not accepted over HTTP.
                size=int(self.headers.get('Content-Length','0') or '0')
                if size>4096 or size<0:
                    self.send_error(413);return
                if size:self.rfile.read(size)
                events.put(time.monotonic())
                self.send_response(202)
                self.send_header('Content-Length','0')
                self.end_headers()
            def log_message(self,*args):
                pass  # Never log authentication data or user requests.
        try:
            server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
            server.daemon_threads=True
            server.timeout=0.5
        except OSError as exc:
            return f'Webhook port cannot be opened: {exc}'
        self._webhook_server=server
        self._webhook_config=config
        Thread(target=server.serve_forever,daemon=True).start()
        return None

    def _watch_dynamic_trigger(self):
        """Monitor first-action triggers while Sequent is open; autostart on edges."""
        try:
            actions=self.recorded_actions
            trigger=actions[0] if actions and actions[0].get('type')=='trigger_start' else None
            if not trigger or not trigger.get('enabled',True):
                self._auto_trigger_key=None
                self._auto_trigger_state=None
                self._stop_webhook_listener()
            else:
                key=json.dumps(trigger,sort_keys=True)
                kind=trigger.get('kind','file')
                target=str(trigger.get('value','')).strip()
                busy=self.playing or self.recording or self.chain_running
                if kind=='webhook':
                    error=self._ensure_webhook_listener(trigger)
                    if error:
                        if self._auto_trigger_state!=error:
                            self.update_status(error,COLOR_DANGER)
                            self._auto_trigger_state=error
                    else:
                        triggered=False
                        while True:
                            try:self._webhook_events.get_nowait();triggered=True
                            except Empty:break
                        if triggered and not busy:
                            self.update_status('Webhook received: launching macro',COLOR_SUCCESS)
                            self.current_action_idx=0
                            self.root.after(0,self.start_macro)
                    self._auto_trigger_key=key
                else:
                    self._stop_webhook_listener()
                    state=None
                    if target:
                        if kind=='file':
                            matched=glob.glob(dynamic_resolve_path(target))
                            state=tuple(sorted((path,os.path.getmtime(path))
                                               for path in matched if os.path.isfile(path)))
                        elif kind=='window':
                            state=bool(find_window_by_title_substr(target))
                        elif kind=='clipboard':
                            state=get_clipboard_text()
                    if self._auto_trigger_key!=key:
                        self._auto_trigger_key=key
                        self._auto_trigger_state=state  # Arm without firing on existing data.
                    else:
                        previous=self._auto_trigger_state
                        self._auto_trigger_state=state  # Update during playback too.
                        active=not busy and bool(state) and state!=previous
                        if kind=='window':active=not busy and bool(state and not previous)
                        if kind=='clipboard':
                            active=(not busy and state!=previous and
                                    target.casefold() in str(state).casefold())
                        if active:
                            self.update_status(f'Auto-start triggered by {kind}',COLOR_SUCCESS)
                            self.current_action_idx=0
                            self.root.after(0,self.start_macro)
        except (OSError,ValueError,tk.TclError) as exc:
            try:self.update_status(f'Trigger watch error: {exc}',COLOR_DANGER)
            except tk.TclError:pass
        finally:
            try:self.root.after(1200,self._watch_dynamic_trigger)
            except tk.TclError:pass

    def run_sequence(self):
        # Structured workflows run through the shared dynamic action interpreter.
        # Legacy-only recordings retain their existing high-fidelity playback path.
        if any(a.get('type') in DYNAMIC_ACTION_TYPES for a in self.recorded_actions):
            return self._run_structured_macro()
        total_actions = len(self.recorded_actions)
        if total_actions == 0:
            self.root.after(0, self.finish_macro)
            return

        start_idx = self.current_action_idx if (0 <= self.current_action_idx < total_actions) else 0
        if start_idx >= total_actions - 1:
            start_idx = 0
            self.current_action_idx = 0

        self.playback_run_id = time.time()
        self._playback_ui_pending = False
        run_id = self.playback_run_id

        self.root.after(0, lambda: self.update_status(f"Running (from #{start_idx + 1})...", COLOR_ACCENT))
        if self.audio_cues: play_cue("start")

        held_modifiers = set()
        failure_reason = ""
        max_loops = float('inf') if self.loop_mode == "∞ Loop" else int(self.loop_mode.replace("x", ""))
        current_loop = 0

        try:
            while self.playing and current_loop < max_loops:
                current_loop += 1
                loop_start = start_idx if current_loop == 1 else 0
                step_ptr = loop_start

                while step_ptr < total_actions:
                    if not self.playing: break

                    while self.is_paused and self.playing:
                        time.sleep(0.04)

                    if not self.playing: break

                    # --- Breakpoint Hit Check ---
                    if step_ptr in self.breakpoints:
                        if self.audio_cues: play_cue("pause")
                        self.is_paused = True
                        self.playback_run_id = time.time()
                        self._playback_ui_pending = False
                        bp_line = step_ptr + 2
                        self.root.after(0, lambda l=bp_line: self.update_status(f"Breakpoint hit at Line {l}", COLOR_DANGER))
                        self.root.after(0, lambda: self.pause_btn.set_state("normal", text="Resume (F9)", bg=COLOR_SUCCESS, hover="#1d8a4b"))
                        while self.is_paused and self.playing:
                            time.sleep(0.04)
                        if not self.playing: break

                    action = self.recorded_actions[step_ptr]
                    act_type = action.get("type")

                    # Synchronized UI step notification
                    i = step_ptr
                    if not self._playback_ui_pending or act_type != "move":
                        self._playback_ui_pending = True
                        self.root.after(0, lambda idx=i, rid=run_id: self._on_playback_step(idx, rid))

                    # Flow Labels & Goto Jumps
                    if act_type == "label":
                        step_ptr += 1
                        continue

                    if act_type == "goto":
                        target = action.get("target", "")
                        found_idx = next((k for k, a in enumerate(self.recorded_actions) if a.get("type") == "label" and a.get("name") == target), None)
                        if found_idx is not None:
                            if self.audio_cues: play_cue("match")
                            step_ptr = found_idx + 1
                            continue
                        else:
                            step_ptr += 1
                            continue

                    # --- Disclaimer / Warning Note ---
                    if act_type == "disclaimer":
                        d_title = action.get("title", "Disclaimer Note")
                        d_msg = action.get("message", "Proceed with execution?")
                        proceed = self.show_disclaimer_modal(d_title, d_msg)
                        if not proceed:
                            self.root.after(0, self.stop_macro)
                            return
                        step_ptr += 1
                        continue

                    # --- Conditional Branch (Jump, Switch Preset, or Run File) ---
                    if act_type == "if_var":
                        v_name = action.get("var_name", "status")
                        actual_val = str(self.variables.get(v_name, ""))
                        op = action.get("op", "==")
                        exp_val = str(action.get("val", ""))

                        match = False
                        if op == "==": match = (actual_val.lower() == exp_val.lower())
                        elif op == "!=": match = (actual_val.lower() != exp_val.lower())
                        elif op == "contains": match = (exp_val.lower() in actual_val.lower())
                        elif op == "not empty": match = bool(actual_val.strip())

                        if match:
                            if self.audio_cues: play_cue("match")
                            t_type = action.get("target_type", "preset")
                            target = action.get("target", "")

                            if t_type == "label":
                                found_idx = next((k for k, a in enumerate(self.recorded_actions) if a.get("type") == "label" and a.get("name") == target), None)
                                if found_idx is not None:
                                    step_ptr = found_idx + 1
                                    continue
                                else:
                                    step_ptr += 1
                                    continue
                            elif t_type == "preset":
                                self.switching_preset = True
                                self.playing = False
                                self.root.after(150, lambda p=target: self.switch_to_preset_and_start(p))
                                return
                            elif t_type == "file":
                                self.switching_preset = True
                                self.playing = False
                                self.root.after(150, lambda f=target: self.switch_to_file_and_start(f))
                                return
                        else:
                            step_ptr += 1
                            continue

                    # --- Request User Input ---
                    if act_type == "prompt_input":
                        prompt_txt = action.get("prompt", "Enter input:")
                        default_val = action.get("default", "")
                        var_name = action.get("var_name", "user_data")
                        entered_val = self.prompt_user_input(prompt_txt, default_val)
                        if entered_val is not None:
                            self.variables[var_name] = str(entered_val)
                            if self.audio_cues: play_cue("match")
                            self.root.after(0, lambda n=var_name, v=entered_val: self.update_status(f"Var '{n}' = '{v}'", COLOR_SUCCESS))
                        step_ptr += 1
                        continue

                    # --- Read Clipboard & Store Variable ---
                    if act_type == "read_clipboard":
                        time.sleep(0.05)
                        clip_content = get_clipboard_text().lower()
                        keywords = [k.lower() for k in action.get("keywords", [])]
                        matched = any(kw in clip_content for kw in keywords)
                        var_name = action.get("var_name", "status")

                        if matched:
                            val = action.get("value", "")
                            self.variables[var_name] = val
                            if self.audio_cues: play_cue("match")
                            self.root.after(0, lambda n=var_name, v=val: self.update_status(f"Var '{n}' = '{v}'", COLOR_SUCCESS))
                        else:
                            self.root.after(0, lambda n=var_name: self.update_status(f"Var '{n}': No match in clipboard", SYNTAX_ORANGE))

                        step_ptr += 1
                        continue

                    # --- Type Stored Variable ---
                    if act_type == "type_variable":
                        var_name = action.get("var_name", "status")
                        val = str(self.variables.get(var_name, ""))
                        if val:
                            type_string(val, self.playback_speed)
                            self.root.after(0, lambda v=val: self.update_status(f"Typed '{v}'", COLOR_SUCCESS))
                        else:
                            self.root.after(0, lambda n=var_name: self.update_status(f"Var '{n}' empty", SYNTAX_ORANGE))

                        raw_delay = max(0.01, self.action_delay / self.playback_speed)
                        actual_delay = raw_delay * (random.uniform(0.85, 1.15) if self.delay_jitter else 1.0)
                        time.sleep(actual_delay)
                        step_ptr += 1
                        continue

                    # --- Delay Execution with Jitter ---
                    if act_type == "delay":
                        raw_sec = float(action.get("seconds", 1.0))
                        delay_sec = raw_sec * (random.uniform(0.85, 1.15) if self.delay_jitter else 1.0)
                        steps = max(1, int(delay_sec / 0.02))
                        step_time = delay_sec / steps
                        for _ in range(steps):
                            while self.is_paused and self.playing: time.sleep(0.04)
                            if not self.playing: break
                            time.sleep(step_time)
                        step_ptr += 1
                        continue

                    # --- Wait for Text ---
                    if act_type == "wait_text":
                        matched = self.wait_for_screen_text(action)
                        if not self.playing:
                            break
                        if not matched:
                            raise MacroExecutionError(f"Wait Text timed out at step {step_ptr + 1}")
                        if self.audio_cues:
                            play_cue("match")
                        step_ptr += 1
                        continue

                    # --- Wait Pixel Condition ---
                    if act_type == "wait_pixel":
                        tx, ty = action.get("x", 0), action.get("y", 0)
                        if action.get("win_title"):
                            h = find_window_by_title_substr(action.get("win_title"))
                            if h: tx, ty = client_to_screen_pos(h, action.get("rel_x", tx), action.get("rel_y", ty))

                        expected_color = action.get("color", "#FFFFFF").upper()
                        timeout_sec = float(action.get("timeout", 15.0))
                        start_wait = time.time()
                        matched = False

                        while self.playing:
                            while self.is_paused and self.playing: time.sleep(0.04)
                            if not self.playing: break
                            if get_pixel_color(tx, ty) == expected_color:
                                matched = True
                                break
                            if time.time() - start_wait >= timeout_sec: break
                            time.sleep(0.01)

                        if not self.playing:
                            break
                        if not matched:
                            raise MacroExecutionError(f"Pixel timeout at step {step_ptr + 1}")
                        if self.audio_cues:
                            play_cue("match")

                        step_ptr += 1
                        continue

                    # --- Search Selected Area OCR ---
                    if act_type == "search_area":
                        target_text = action.get("text", "").strip()
                        match_any = action.get("match_any", False) or target_text in ("*", "<ANY TEXT DETECTED>")
                        area = action.get("area", [0, 0, 0, 0])
                        target_preset = action.get("preset", "")

                        status_label = "any text" if match_any else f"'{target_text}'"
                        self.root.after(0, lambda l=status_label: self.lbl_active_action.config(text=f"// OCR searching: {l}", fg=SYNTAX_PURPLE))
                        time.sleep(0.2)

                        x, y, w, h = area
                        raw_recognized = self.ocr_screen_area(x, y, w, h)
                        if raw_recognized.startswith("ERR:"): raw_recognized = ""

                        clean_rec = " ".join(re.sub(r'[^\w\s]', ' ', raw_recognized.lower()).split())
                        found_match = (len(clean_rec.strip()) > 0) if match_any else (target_text.lower() in clean_rec)

                        if found_match:
                            if self.audio_cues: play_cue("match")
                            self.switching_preset = True
                            self.playing = False
                            self.root.after(150, lambda p=target_preset: self.switch_to_preset_and_start(p))
                            return
                        else:
                            step_ptr += 1
                            continue
                            
                    # --- Find Color & Click (DYNAMIC BOUNDING BOX ANCHOR) ---
                    if act_type == "find_color":
                        area = action.get("area", [0, 0, 100, 100])
                        hex_color = action.get("color", "#FFFFFF").upper()
                        found_box = self.search_color_in_area(*area, hex_color,
                                    tolerance=action.get("tolerance", 8))

                        if found_box:
                            mode, tx, ty = perform_color_target_action(action, found_box, self.playback_speed)
                            self.root.after(0, lambda b=found_box:
                                            self.draw_highlight_box(*b, COLOR_SUCCESS, 800))
                            self.root.after(0, lambda m=mode, x=tx, y=ty:
                                            self._report_color_execution(m, x, y))
                            if self.audio_cues: play_cue("match")
                        else:
                            raise MacroExecutionError(f"Color {hex_color} not found in search area")
                            
                        step_ptr += 1
                        continue

                    # --- Find Text & Click (WinRT OCR word-box target) ---
                    if act_type == "find_text":
                        area = action.get("area", [0, 0, 100, 100])
                        target_text = action.get("text", "")
                        found_box = self.find_text_rect(*area, target_text)
                        if not self.playing:
                            break  # OCR may finish after a user cancels playback.
                        if not found_box:
                            raise MacroExecutionError(f"Text '{target_text}' not found in search area")
                        mode, tx, ty = perform_color_target_action(action, found_box, self.playback_speed)
                        self.root.after(0, lambda b=found_box:
                                        self.draw_highlight_box(*b, COLOR_SUCCESS, 800))
                        self.root.after(0, lambda m=mode, x=tx, y=ty:
                                        self._report_text_execution(m, x, y))
                        if self.audio_cues:
                            play_cue("match")
                        step_ptr += 1
                        continue

                    # --- Dynamic automation action (shared with chain / debugger) ---
                    if act_type in DYNAMIC_ACTION_TYPES:
                        self.dynamic_engine.run_action_with_recovery(action)
                        step_ptr += 1
                        continue

                    # --- Mouse Movement ---
                    if act_type == "move":
                        tx, ty = action.get("x", 0), action.get("y", 0)
                        if action.get("win_title"):
                            h = find_window_by_title_substr(action.get("win_title"))
                            if h: tx, ty = client_to_screen_pos(h, action.get("rel_x", tx), action.get("rel_y", ty))

                        if self.human_bezier:
                            cur_pos = get_mouse_pos()
                            pts = calculate_bezier_trajectory(cur_pos, (tx, ty), steps=18)
                            for bx, by in pts:
                                move_mouse(bx, by)
                                time.sleep(max(0.001, 0.008 / self.playback_speed))
                        elif self.fluid_mouse:
                            move_mouse(tx, ty)
                            time.sleep(max(0.0005, 0.010 / self.playback_speed))

                        step_ptr += 1
                        continue

                    # --- Mouse Drag & Drop ---
                    if act_type == "drag":
                        sx, sy = action.get("start_x", 0), action.get("start_y", 0)
                        ex, ey = action.get("end_x", 0), action.get("end_y", 0)
                        btn = action.get("btn", "left")
                        down_flag = MOUSEEVENTF_LEFTDOWN if btn == "left" else MOUSEEVENTF_RIGHTDOWN
                        up_flag = MOUSEEVENTF_LEFTUP if btn == "left" else MOUSEEVENTF_RIGHTUP

                        move_mouse(sx, sy)
                        time.sleep(0.04)
                        ctypes.windll.user32.mouse_event(down_flag, 0, 0, 0, 0)
                        time.sleep(0.05)

                        pts = calculate_bezier_trajectory((sx, sy), (ex, ey), steps=24)
                        for bx, by in pts:
                            move_mouse(bx, by)
                            time.sleep(max(0.002, 0.012 / self.playback_speed))

                        move_mouse(ex, ey)
                        time.sleep(0.04)
                        ctypes.windll.user32.mouse_event(up_flag, 0, 0, 0, 0)
                        step_ptr += 1
                        continue

                    # --- Mouse Scroll (Wheel) ---
                    if act_type == "scroll":
                        tx, ty = action.get("x", 0), action.get("y", 0)
                        if action.get("win_title"):
                            h = find_window_by_title_substr(action.get("win_title"))
                            if h: tx, ty = client_to_screen_pos(h, action.get("rel_x", tx), action.get("rel_y", ty))

                        if self.human_bezier:
                            pts = calculate_bezier_trajectory(get_mouse_pos(), (tx, ty), steps=12)
                            for bx, by in pts: move_mouse(bx, by); time.sleep(0.004)
                        else:
                            move_mouse(tx, ty)

                        time.sleep(max(0.001, 0.02 / self.playback_speed))
                        delta = int(action.get("delta", 120))
                        ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, ctypes.c_uint32(delta & 0xFFFFFFFF).value, 0)

                        raw_delay = max(0.01, self.action_delay / self.playback_speed)
                        actual_delay = raw_delay * (random.uniform(0.85, 1.15) if self.delay_jitter else 1.0)
                        steps = max(1, int(actual_delay / 0.02))
                        step_delay = actual_delay / steps
                        for _ in range(steps):
                            while self.is_paused and self.playing: time.sleep(0.04)
                            if not self.playing: break
                            time.sleep(step_delay)

                        step_ptr += 1
                        continue

                    if act_type != "combo":
                        for m in list(held_modifiers):
                            m_scan = ctypes.windll.user32.MapVirtualKeyW(m, 0)
                            ctypes.windll.user32.keybd_event(m, m_scan, KEYEVENTF_KEYUP, 0)
                        held_modifiers.clear()

                    # --- Mouse Click ---
                    if act_type == "click":
                        tx, ty = action.get("x", 0), action.get("y", 0)
                        if action.get("win_title"):
                            h = find_window_by_title_substr(action.get("win_title"))
                            if h: tx, ty = client_to_screen_pos(h, action.get("rel_x", tx), action.get("rel_y", ty))

                        if self.human_bezier:
                            pts = calculate_bezier_trajectory(get_mouse_pos(), (tx, ty), steps=14)
                            for bx, by in pts: move_mouse(bx, by); time.sleep(0.005)
                        else:
                            move_mouse(tx, ty)

                        wait_for_cursor_settled(tx, ty)
                        click(action.get("btn", "left"))

                    elif act_type == "key":
                        type_string(str(action.get("key", "")), self.playback_speed)
                    elif act_type == "press":
                        press_virtual_key(action.get("vk", 0))
                    elif act_type == "enter":
                        press_virtual_key(VK_RETURN)
                    elif act_type == "backspace":
                        press_virtual_key(VK_BACK)
                    elif act_type == "select_all":
                        press_key_combo(VK_CONTROL, VK_A)
                    elif act_type == "copy":
                        press_key_combo(VK_CONTROL, VK_C)
                    elif act_type == "paste":
                        press_key_combo(VK_CONTROL, VK_V)
                    elif act_type == "combo":
                        mods = set(action.get("mods", []))
                        vk = action.get("vk", 0)
                        for m in (mods - held_modifiers):
                            m_scan = ctypes.windll.user32.MapVirtualKeyW(m, 0)
                            ctypes.windll.user32.keybd_event(m, m_scan, 0, 0)
                        held_modifiers.update(mods)
                        time.sleep(0.04)
                        vk_scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
                        ctypes.windll.user32.keybd_event(vk, vk_scan, 0, 0)
                        time.sleep(0.06)
                        ctypes.windll.user32.keybd_event(vk, vk_scan, KEYEVENTF_KEYUP, 0)

                    raw_delay = max(0.01, self.action_delay / self.playback_speed)
                    actual_delay = raw_delay * (random.uniform(0.85, 1.15) if self.delay_jitter else 1.0)
                    steps = max(1, int(actual_delay / 0.02))
                    step_delay = actual_delay / steps
                    for _ in range(steps):
                        while self.is_paused and self.playing: time.sleep(0.04)
                        if not self.playing: break
                        time.sleep(step_delay)

                    step_ptr += 1

        except Exception as error:
            failure_reason = (str(error) if isinstance(error, MacroExecutionError)
                              else f"Unexpected {type(error).__name__}")
            self.playing = False
            if self.audio_cues:
                play_cue("error")
        finally:
            for m in list(held_modifiers):
                m_scan = ctypes.windll.user32.MapVirtualKeyW(m, 0)
                ctypes.windll.user32.keybd_event(m, m_scan, KEYEVENTF_KEYUP, 0)
            if not self.switching_preset:
                outcome = ("failed" if failure_reason else
                           "cancelled" if not self.playing else "completed")
                self.root.after(0, self.finish_macro, outcome, failure_reason)


    def finish_macro(self, outcome="completed", reason=""):
        self.playback_run_id = time.time()
        self._playback_ui_pending = False
        self.playing = False
        self.is_paused = False
        if outcome == "failed":
            self.update_status(f"Failed: {reason}", COLOR_DANGER)
            self.lbl_active_action.config(text="// Execution failed", fg=COLOR_DANGER)
        elif outcome == "cancelled":
            self.update_status("Cancelled", SYNTAX_ORANGE)
            self.lbl_active_action.config(text="// Execution cancelled", fg=SYNTAX_ORANGE)
        else:
            self.update_status("Completed", COLOR_SUCCESS)
            self.lbl_active_action.config(text="// Execution complete", fg=SYNTAX_GREEN)
            if self.audio_cues:
                play_cue("stop")

        if hasattr(self, 'ghost_reticle'):
            self.ghost_reticle.hide()

        self.current_action_idx = 0
        self.queue_viewer.set_interactive(True)
        self.record_btn.set_state("normal", text=self.get_btn_text("start_rec"), bg=COLOR_DANGER, hover=COLOR_DANGER_HOV)
        self.play_btn.set_state("normal", text=self.get_btn_text("start_play"), bg=COLOR_ACCENT, hover=COLOR_ACCENT_HOV)
        self.pause_btn.set_state("disabled", text="Pause (F9)", bg=BG_BORDER, hover=BG_HOVER)
        self.update_scrubber_ui(show_reticle=False)



if __name__ == "__main__":
    root = tk.Tk()
    try:
        app = BuiltInMacroApp(root)
        root.mainloop()
    except KeyboardInterrupt:
        # Ctrl+C from the terminal is a normal shutdown request, not an
        # application error. Avoid Tkinter's unhandled-interrupt traceback.
        print("\nSequent stopped from terminal (Ctrl+C).")
    finally:
        # Tk may already have been destroyed by Sequent's Close button.
        try:
            if root.winfo_exists():
                root.destroy()
        except tk.TclError:
            pass
