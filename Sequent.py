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
import math
import random
import winsound

ACTIVE_SOUND_THEME = "Classic"
CUSTOM_SOUND_PATHS = {
    "start_rec": "",
    "pause": "",
    "start": ""  # Used for Run Macro
}

# --- Native Audio Feedback (Non-Blocking Kernel32 Beeps & WAVs) ---
def play_cue(cue_type):
    global ACTIVE_SOUND_THEME, CUSTOM_SOUND_PATHS
    def _beep():
        # 1. Play custom WAV if set
        custom_path = CUSTOM_SOUND_PATHS.get(cue_type, "")
        if custom_path and os.path.exists(custom_path) and custom_path.lower().endswith(".wav"):
            try:
                winsound.PlaySound(custom_path, winsound.SND_FILENAME | winsound.SND_NODEFAULT)
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
            
            # Fallback for start_rec to use 'start' theme notes if no custom file exists
            theme_cue = "start" if cue_type == "start_rec" else cue_type
            
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
    if not query: return None
    target_hwnd = [None]
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, extra):
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(512)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
            if query.lower() in buf.value.lower():
                target_hwnd[0] = hwnd
                return False
        return True
    ctypes.windll.user32.EnumWindows(EnumWindowsProc(callback), 0)
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

# --- Activate Windows Per-Monitor DPI Awareness ---

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


try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2) # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

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
    ctypes.windll.user32.SetCursorPos(int(x), int(y))

def get_pixel_color(x, y):
    """Fetches screen pixel color as an uppercase hex string (#RRGGBB) via Win32 GDI."""
    hdc = ctypes.windll.user32.GetDC(0)
    color = ctypes.windll.gdi32.GetPixel(hdc, int(x), int(y))
    ctypes.windll.user32.ReleaseDC(0, hdc)
    if color == -1:
        return "#000000"
    r = color & 0xFF
    g = (color >> 8) & 0xFF
    b = (color >> 16) & 0xFF
    return f"#{r:02X}{g:02X}{b:02X}"

def click(button="left"):
    if button == "left":
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    elif button == "right":
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)

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

# --- Modern Theme Palette ---
BG_APP = "#313338"
BG_SIDEBAR = "#2b2d31"
BG_HEADER = "#1e1f22"
BG_PANEL = "#2b2d31"
BG_INPUT = "#1e1f22"
BG_GUTTER = "#16171a"
BG_SCROLL_TRACK = "#141517"
BG_BORDER = "#383a40"
BG_HOVER = "#35373c"
TRACK_INACTIVE = "#3f4147"
TRACK_HOVER = "#585b64"
TRACK_ACTIVE = "#5865f2"

TEXT_PRIMARY = "#f2f3f5"
TEXT_MUTED = "#949ba4"
TEXT_HEADER = "#ffffff"

COLOR_ACCENT = "#5865f2"
COLOR_ACCENT_HOV = "#4752c4"
COLOR_DANGER = "#da373c"
COLOR_DANGER_HOV = "#a12828"
COLOR_SUCCESS = "#23a55a"
DISABLED = "#383a40"

SYNTAX_BLUE = "#00a8fc"
SYNTAX_ORANGE = "#f0b232"
SYNTAX_PURPLE = "#c586c0"
SYNTAX_GREEN = "#23a55a"

class ModernTooltip:
    """Renders a sleek Modern-themed floating micro-tooltip with hotkey badges."""
    def __init__(self, widget, text, hotkey=None, delay_ms=350):
        self.widget = widget
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

            
# --- VS Code-Style Playback Queue Viewer ---
class VSCodeQueueViewer(tk.Frame):
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
            font=("Consolas", 10), bd=0, padx=10, pady=8,
            highlightthickness=0, wrap="none", cursor="xterm",
            yscrollcommand=self._on_code_scroll
        )
        self.code_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.plus_btn = tk.Label(
            self.gutter_margin, text="+", font=("Segoe UI", 8, "bold"),
            bg=BG_GUTTER, fg=TEXT_MUTED, cursor="hand2", padx=0, pady=0,
            anchor="center"
        )
        self.plus_btn.bind("<Enter>", lambda e: self.plus_btn.config(fg="#ffffff", bg=COLOR_ACCENT))
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
        self.gutter.tag_configure("active_line", background=COLOR_ACCENT, foreground="#ffffff")
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
                if act.get("type") in ("move", "click", "wait_pixel", "scroll"):
                    coord_items.append(act)

            if not coord_items:
                self.app.ghost_reticle.hide()
                return

            n = len(coord_items)
            reticle_data = []
            for i, act in enumerate(coord_items):
                # Gradient alpha: lowest line count (oldest) = 0.22, highest line count (newest) = 0.92
                alpha = 0.85 if n == 1 else (0.22 + (0.70 * (i / (n - 1))))
                is_click = (act.get("type") in ("click", "scroll"))
                reticle_data.append((act.get("x", 0), act.get("y", 0), is_click, alpha))

            self.app.ghost_reticle.show_multi(reticle_data)
        except Exception:
            pass

    def clear(self):
        self.close_sub_menu()
        self.plus_btn.place_forget()
        self.code_text.tag_remove("active_line", "1.0", "end")
        self.gutter.tag_remove("active_line", "1.0", "end")
        self.line_count = 0
        self.gutter.delete("1.0", "end")
        self.code_text.delete("1.0", "end")
        self.gutter.configure(width=5)

    def append_line(self, text, color=TEXT_PRIMARY):
        self.line_count += 1
        needed_width = max(5, len(str(self.line_count)) + 2)
        if int(self.gutter.cget("width")) < needed_width:
            self.gutter.configure(width=needed_width)

        self.gutter.insert("end", f"{self.line_count}\n", "right")

        tag_name = f"col_{color.replace('#', '')}"
        if tag_name not in self.code_text.tag_names():
            self.code_text.tag_configure(tag_name, foreground=color)

        self.code_text.insert("end", f"{text}\n", tag_name)
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
        if not self.interactive_enabled: return

        TYPE_TO_LABEL = {
            "delay": "Delay",
            "disclaimer": "Disclaimer / Warning",
            "if_var": "If Variable Condition",
            "read_clipboard": "Read Clipboard",
            "prompt_input": "Request Input",
            "type_variable": "Type Variable",
            "search_area": "Search selected area",
            "wait_pixel": "Wait Pixel",
            "label": "Label Marker",
            "goto": "Jump to Label"
        }

        editing_act = None
        if edit_idx is not None and 0 <= edit_idx < len(self.app.recorded_actions):
            editing_act = self.app.recorded_actions[edit_idx]
            if editing_act.get("type") not in TYPE_TO_LABEL:
                return

        initial_action = TYPE_TO_LABEL.get(editing_act.get("type"), "Delay") if editing_act else "Delay"

        menu_w = 340
        base_h = 400 if initial_action in ("Search selected area", "If Variable Condition") else (
            370 if initial_action == "Disclaimer / Warning" else (
                350 if initial_action == "Read Clipboard" else (
                    300 if initial_action == "Request Input" else (
                        280 if initial_action in ("Wait Pixel", "Type Variable") else 250
                    )
                )
            )
        )
        menu_h = base_h - 30 if edit_idx is not None else base_h

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

        init_x = mon_x + max(0, (mon_w - menu_w) // 2)
        init_y = mon_y + max(0, (mon_h - menu_h) // 2)

        root = self.app.root
        self.sub_menu = tk.Toplevel(root)
        self.sub_menu.overrideredirect(True)
        self.sub_menu.attributes('-topmost', True)
        self.sub_menu.geometry(f"{menu_w}x{menu_h}+{init_x}+{init_y}")
        self.sub_menu.configure(bg=BG_BORDER)
        self.sub_menu.update_idletasks()

        if getattr(self.app, 'entertain_me', True):
            self.sub_menu.attributes('-alpha', 0.0)
            def _fade_in_modal(alpha=0.0):
                if not self.sub_menu or not self.sub_menu.winfo_exists(): return
                alpha = min(1.0, alpha + 0.18)
                self.sub_menu.attributes('-alpha', alpha)
                if alpha < 1.0:
                    self.sub_menu.after(12, lambda: _fade_in_modal(alpha))
            self.sub_menu.after(10, _fade_in_modal)
        else:
            self.sub_menu.attributes('-alpha', 1.0)

        card = tk.Frame(self.sub_menu, bg=BG_PANEL)
        card.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        def _start_drag(e):
            self._sub_drag_start_x = e.x_root; self._sub_drag_start_y = e.y_root
            self._sub_drag_win_x = self.sub_menu.winfo_x(); self._sub_drag_win_y = self.sub_menu.winfo_y()

        def _do_drag(e):
            dx = e.x_root - self._sub_drag_start_x; dy = e.y_root - self._sub_drag_start_y
            self.sub_menu.geometry(f"+{self._sub_drag_win_x + dx}+{self._sub_drag_win_y + dy}")

        head = tk.Frame(card, bg=BG_HEADER, height=28, cursor="fleur")
        head.pack(fill=tk.X)
        drag_icon = tk.Label(head, text=" ⠇ ", font=("Segoe UI", 9), bg=BG_HEADER, fg=TEXT_MUTED, cursor="fleur")
        drag_icon.pack(side=tk.LEFT, padx=(4, 0))

        title_lbl = "EDIT ACTION" if edit_idx is not None else ("LINE OPTIONS" if line_num >= 2 else "ADD ACTION")
        lbl_title = tk.Label(head, text=title_lbl, font=("Segoe UI", 8, "bold"), bg=BG_HEADER, fg=COLOR_SUCCESS if edit_idx is not None else TEXT_MUTED, cursor="fleur")
        lbl_title.pack(side=tk.LEFT, padx=4, pady=5)
        lbl_line = tk.Label(head, text=f"Line {line_num}", font=("Consolas", 8, "bold"), bg=BG_INPUT, fg=SYNTAX_BLUE, padx=4, pady=1, cursor="fleur")
        lbl_line.pack(side=tk.LEFT, padx=2)

        for w in (head, drag_icon, lbl_title, lbl_line):
            w.bind("<Button-1>", _start_drag); w.bind("<B1-Motion>", _do_drag)

        btn_close = tk.Label(head, text=" ✕ ", font=("Segoe UI", 9), bg=BG_HEADER, fg=TEXT_MUTED, cursor="hand2")
        btn_close.pack(side=tk.RIGHT, padx=4)
        btn_close.bind("<Button-1>", lambda e: self.close_sub_menu())

        tk.Frame(card, bg=BG_BORDER, height=1).pack(fill=tk.X)

        top_row = tk.Frame(card, bg=BG_PANEL, padx=12, pady=8)
        top_row.pack(fill=tk.X)
        tk.Label(top_row, text="Action:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED, width=7, anchor="w").pack(side=tk.LEFT)

        action_options = [
            "Delay", 
            "Disclaimer / Warning",
            "If Variable Condition",
            "Read Clipboard", 
            "Request Input",
            "Type Variable", 
            "Search selected area", 
            "Wait Pixel", 
            "Label Marker", 
            "Jump to Label"
        ]
        action_var = tk.StringVar(value=initial_action)
        action_menu = tk.OptionMenu(top_row, action_var, *action_options, command=lambda val: self._switch_action_view(val, line_num, edit_idx=edit_idx))
        action_menu.config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=BG_HOVER, font=("Segoe UI", 8, "bold"), highlightthickness=1, highlightbackground=BG_BORDER, bd=0, relief="flat")
        action_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT, bd=1, relief="flat")
        action_menu.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.action_content_frame = tk.Frame(card, bg=BG_PANEL, padx=12, pady=4)
        self.action_content_frame.pack(fill=tk.BOTH, expand=True)
        self._switch_action_view(initial_action, line_num, edit_idx=edit_idx)

    def _switch_action_view(self, action_type, line_num, edit_idx=None):
        if not self.sub_menu or not self.sub_menu.winfo_exists(): return
        menu_w = 340
        menu_h = 400 if action_type in ("Search selected area", "If Variable Condition") else (
            370 if action_type == "Disclaimer / Warning" else (
                350 if action_type == "Read Clipboard" else (
                    300 if action_type == "Request Input" else (
                        280 if action_type in ("Wait Pixel", "Type Variable") else 250
                    )
                )
            )
        )
        if edit_idx is not None:
            menu_h -= 30

        curr_x, curr_y = self.sub_menu.winfo_x(), self.sub_menu.winfo_y()
        self.sub_menu.geometry(f"{menu_w}x{menu_h}+{curr_x}+{curr_y}")

        for child in self.action_content_frame.winfo_children(): child.destroy()

        if action_type == "Delay": self._render_delay_view(line_num, edit_idx=edit_idx)
        elif action_type == "Disclaimer / Warning": self._render_disclaimer_view(line_num, edit_idx=edit_idx)
        elif action_type == "If Variable Condition": self._render_if_var_view(line_num, edit_idx=edit_idx)
        elif action_type == "Read Clipboard": self._render_read_clipboard_view(line_num, edit_idx=edit_idx)
        elif action_type == "Request Input": self._render_request_input_view(line_num, edit_idx=edit_idx)
        elif action_type == "Type Variable": self._render_type_variable_view(line_num, edit_idx=edit_idx)
        elif action_type == "Search selected area": self._render_search_area_view(line_num, edit_idx=edit_idx)
        elif action_type == "Wait Pixel": self._render_wait_pixel_view(line_num, edit_idx=edit_idx)
        elif action_type == "Label Marker": self._render_label_view(line_num, edit_idx=edit_idx)
        elif action_type == "Jump to Label": self._render_goto_view(line_num, edit_idx=edit_idx)


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

                btn_b = tk.Label(f_row, text="Browse", font=("Segoe UI", 8, "bold"), bg=COLOR_ACCENT, fg=TEXT_PRIMARY, cursor="hand2", padx=8, pady=3)
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
            self.app.start_screen_selection(lambda a: (on_area_snip_completed(a), self.sub_menu.deiconify() if self.sub_menu else None))

        btn_snip = tk.Label(area_row, text="⛶ Snip", font=("Segoe UI", 8, "bold"), bg=COLOR_ACCENT, fg=TEXT_PRIMARY, cursor="hand2", padx=8, pady=3)
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

        # Bind globally, filter dynamically by cursor location
        self.bind_all("<MouseWheel>", self._on_mousewheel, add="+")


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
                self.label.configure(bg=self.hover_color)
                self.current_color = self.hover_color
    
    def on_leave(self, event):
        if not self.disabled:
            if getattr(BuiltInMacroApp, 'entertain_mode', True):
                self._fade_to(self.bg_color)
            else:
                self.configure(bg=self.bg_color)
                self.label.configure(bg=self.bg_color)
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

class OBSHotkeyField(tk.Frame):
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
        color = BG_HOVER if is_active else BG_SIDEBAR
        fg_color = TEXT_PRIMARY if is_active else TEXT_MUTED
        ind_color = TEXT_PRIMARY if is_active else color
        self.configure(bg=color)
        self.label.configure(bg=color, fg=fg_color)
        self.indicator.configure(bg=ind_color)

class GhostReticle:
    def __init__(self, root):
        self.root = root
        self.pool = []
        self.hide_timer = None

    def _create_reticle_window(self):
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg="#da373c")

        win.update_idletasks()
        try:
            hwnd = win.winfo_id()
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TRANSPARENT = 0x00000020
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT)
        except Exception:
            pass

        canvas = tk.Canvas(win, width=28, height=28, bg="#1e1f22", highlightthickness=1, highlightbackground="#da373c")
        canvas.pack()
        return win, canvas

    def show_multi(self, items):
        if self.hide_timer:
            try: self.root.after_cancel(self.hide_timer)
            except Exception: pass
            self.hide_timer = None

        if not items:
            self.hide()
            return

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
            win.geometry(f"{size}x{size}+{int(x - size // 2)}+{int(y - size // 2)}")
            win.deiconify()

        for i in range(len(items), len(self.pool)):
            self.pool[i][0].withdraw()

        self.hide_timer = self.root.after(3500, self.hide)

    def update(self, actions, index):
        if not actions or index < 0 or index >= len(actions):
            self.hide()
            return
        
        act = actions[index]
        if act.get("type") in ("move", "click", "wait_pixel", "scroll"):
            self.show(act.get("x", 0), act.get("y", 0), is_click=(act.get("type") in ("click", "scroll")), alpha=0.85)
        else:
            self.hide()

    def show(self, x, y, is_click=False, alpha=0.85):
        self.show_multi([(x, y, is_click, alpha)])

    def hide(self):
        if self.hide_timer:
            try: self.root.after_cancel(self.hide_timer)
            except Exception: pass
            self.hide_timer = None
        for win, _ in self.pool:
            if win.winfo_exists():
                win.withdraw()

# --- Main Application ---
class BuiltInMacroApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Sequent")

        # Dynamic DPI Scaling & Centering
        self.dpi_scale = get_dpi_scale()
        win_w = int(780 * self.dpi_scale)
        win_h = int(640 * self.dpi_scale)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        pos_x = max(0, (sw - win_w) // 2)
        pos_y = max(0, (sh - win_h) // 2)

        self.root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
        self.root.configure(bg=BG_APP)
        self.root.attributes('-topmost', True)

        self.root.overrideredirect(True)
        self.set_app_window()

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
        
        # Playback Thread Synchronization IDs
        self.playback_run_id = 0
        self._playback_ui_pending = False

        # Theme & Entertainment Mode State
        self.light_mode = False
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

        self.sidebar_open = False
        self.sidebar_width = int(220 * self.dpi_scale)
        self.current_sidebar_x = -self.sidebar_width

        self.hotkeys = {
            "start_rec": {"vk": 0x76, "mod_vks": [], "name": "F7"},
            "stop_rec": {"vk": 0x77, "mod_vks": [], "name": "F8"},
            "start_play": {"vk": 0x78, "mod_vks": [], "name": "F9"},
            "stop_play": {"vk": 0x79, "mod_vks": [], "name": "F10"}
        }

        self.load_settings()

        self.setup_ui()
        self.ghost_reticle = GhostReticle(self.root)
        self.refresh_preset_menu()
        self.setup_resizing_handles()
        Thread(target=self.global_hotkey_listener, daemon=True).start()

    
    def set_app_window(self):
        hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)

        try:
            margins = MARGINS(1, 1, 1, 1)
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
        except Exception: pass

        self.root.wm_withdraw()
        self.root.after(10, lambda: self.root.wm_deiconify())

    def start_drag(self, event):
        self.drag_x = event.x
        self.drag_y = event.y

    def drag_window(self, event):
        x = self.root.winfo_x() + (event.x - self.drag_x)
        y = self.root.winfo_y() + (event.y - self.drag_y)
        self.root.geometry(f"+{x}+{y}")

    def minimize_window(self):
        self.root.overrideredirect(False)
        self.root.iconify()
        self.root.bind("<FocusIn>", self.on_restore)

    def on_restore(self, event=None):
        self.root.unbind("<FocusIn>")
        self.root.overrideredirect(True)

    def setup_resizing_handles(self):
        # Use minimal 2px invisible frames to allow edge resizing without covering UI elements
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

        btn_expand = tk.Label(card, text="⤢", font=("Segoe UI", 9, "bold"), bg=COLOR_ACCENT, fg="#ffffff", cursor="hand2", padx=7, pady=2)
        btn_expand.pack(side=tk.RIGHT, padx=6)
        btn_expand.bind("<Button-1>", lambda e: self.toggle_mini_hud())

    def update_status(self, text, fg_color=TEXT_MUTED):
        def _apply():
            if hasattr(self, 'status_text') and self.status_text.winfo_exists():
                self.status_text.config(text=f"● {text}", fg=fg_color)
            if self.mini_hud and self.mini_hud.winfo_exists() and hasattr(self, 'hud_status_lbl'):
                trunc = (text[:14] + "..") if len(text) > 16 else text
                self.hud_status_lbl.config(text=f"● {trunc}", fg=fg_color)
        self.root.after(0, _apply)


    def _start_resize(self, event, edge):
        self._resize_edge = edge
        self._resize_start_x = event.x_root
        self._resize_start_y = event.y_root
        self._resize_start_w = self.root.winfo_width()
        self._resize_start_h = self.root.winfo_height()
        self._resize_start_win_x = self.root.winfo_x()
        self._resize_start_win_y = self.root.winfo_y()

    def _do_resize(self, event):
        dx = event.x_root - self._resize_start_x
        dy = event.y_root - self._resize_start_y
        w, h = self._resize_start_w, self._resize_start_h
        x, y = self._resize_start_win_x, self._resize_start_win_y

        scale = getattr(self, 'dpi_scale', 1.0)
        min_w = int(600 * scale)
        min_h = int(480 * scale)

        if "e" in self._resize_edge: w = max(min_w, self._resize_start_w + dx)
        if "s" in self._resize_edge: h = max(min_h, self._resize_start_h + dy)
        if "w" in self._resize_edge:
            possible_w = self._resize_start_w - dx
            if possible_w >= min_w:
                w = possible_w
                x = self._resize_start_win_x + dx

        self.root.geometry(f"{int(w)}x{int(h)}+{int(x)}+{int(y)}")


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
        self.main_container = tk.Frame(self.root, bg=BG_APP)
        self.main_container.pack(fill=tk.BOTH, expand=True)

        self.root.bind("<Left>", lambda e: self._on_scrub_nav("prev"))
        self.root.bind("<Right>", lambda e: self._on_scrub_nav("next"))
        self.root.bind("<Home>", lambda e: self._on_scrub_nav("home"))
        self.root.bind("<End>", lambda e: self._on_scrub_nav("end"))
        self.root.bind("<space>", lambda e: self._on_scrub_nav("run_step"))

        nudge_keys = [
            ("<Alt-Left>", -1, 0), ("<Alt-Right>", 1, 0),
            ("<Alt-Up>", 0, -1), ("<Alt-Down>", 0, 1),
            ("<Alt-Shift-Left>", -5, 0), ("<Alt-Shift-Right>", 5, 0),
            ("<Alt-Shift-Up>", 0, -5), ("<Alt-Shift-Down>", 0, 5),
        ]
        for seq, dx, dy in nudge_keys:
            self.root.bind(seq, lambda e, x=dx, y=dy: self.nudge_active_coordinate(x, y))

        top_bar = tk.Frame(self.main_container, bg=BG_HEADER)
        top_bar.pack(fill=tk.X)
        top_bar.bind("<Button-1>", self.start_drag)
        top_bar.bind("<B1-Motion>", self.drag_window)

        self.hamburger = tk.Label(top_bar, text=" ☰ ", font=("Segoe UI", 12), bg=BG_HEADER, fg=TEXT_MUTED, cursor="hand2")
        self.hamburger.pack(side=tk.LEFT, padx=6, pady=4)
        self.hamburger.bind("<Button-1>", lambda e: self.toggle_sidebar())
        self.hamburger.bind("<Enter>", lambda e: self.hamburger.config(bg=BG_HOVER, fg=TEXT_PRIMARY))
        self.hamburger.bind("<Leave>", lambda e: self.hamburger.config(bg=BG_HEADER, fg=TEXT_MUTED))
        ModernTooltip(self.hamburger, "Open Navigation & Settings", "Menu")

        title_lbl = tk.Label(top_bar, text="Sequent", font=("Segoe UI", 10, "bold"), bg=BG_HEADER, fg=TEXT_PRIMARY)
        title_lbl.pack(side=tk.LEFT, padx=(6, 8))
        title_lbl.bind("<Button-1>", self.start_drag)
        title_lbl.bind("<B1-Motion>", self.drag_window)

        btn_hud = tk.Label(top_bar, text="⧉ Mini HUD", font=("Segoe UI", 8, "bold"), bg=BG_INPUT, fg=SYNTAX_BLUE, cursor="hand2", padx=8, pady=2, highlightbackground=BG_BORDER, highlightthickness=1)
        btn_hud.pack(side=tk.LEFT, padx=(0, 8))
        btn_hud.bind("<Button-1>", lambda e: self.toggle_mini_hud())
        btn_hud.bind("<Enter>", lambda e: btn_hud.config(bg=BG_HOVER, fg=TEXT_PRIMARY))
        btn_hud.bind("<Leave>", lambda e: btn_hud.config(bg=BG_INPUT, fg=SYNTAX_BLUE))
        ModernTooltip(btn_hud, "Collapse to Floating HUD", "Compact")

        win_controls = tk.Frame(top_bar, bg=BG_HEADER)
        win_controls.pack(side=tk.RIGHT)

        btn_close = tk.Label(win_controls, text=" ✕ ", font=("Segoe UI", 9), bg=BG_HEADER, fg=TEXT_MUTED, cursor="hand2", padx=12, pady=6)
        btn_close.pack(side=tk.RIGHT)
        btn_close.bind("<Button-1>", lambda e: self.root.destroy())
        btn_close.bind("<Enter>", lambda e: btn_close.config(bg=COLOR_DANGER, fg="white"))
        btn_close.bind("<Leave>", lambda e: btn_close.config(bg=BG_HEADER, fg=TEXT_MUTED))

        btn_min = tk.Label(win_controls, text=" — ", font=("Segoe UI", 9), bg=BG_HEADER, fg=TEXT_MUTED, cursor="hand2", padx=12, pady=6)
        btn_min.pack(side=tk.RIGHT)
        btn_min.bind("<Button-1>", lambda e: self.minimize_window())
        btn_min.bind("<Enter>", lambda e: btn_min.config(bg=BG_HOVER, fg=TEXT_PRIMARY))
        btn_min.bind("<Leave>", lambda e: btn_min.config(bg=BG_HEADER, fg=TEXT_MUTED))

        self.status_text = tk.Label(top_bar, text="● Ready", font=("Segoe UI", 9, "bold"), bg=BG_HEADER, fg=COLOR_SUCCESS)
        self.status_text.pack(side=tk.RIGHT, padx=(10, 15))
        self.status_text.bind("<Button-1>", self.start_drag)
        self.status_text.bind("<B1-Motion>", self.drag_window)

        preset_card = ModernCard(top_bar, bg_card=BG_INPUT)
        preset_card.pack(side=tk.RIGHT, padx=(0, 10), pady=4)

        tk.Label(preset_card, text="📁 PRESET", font=("Segoe UI", 7, "bold"), bg=BG_INPUT, fg=TEXT_MUTED).pack(side=tk.LEFT, padx=(8, 2))

        self.preset_var = tk.StringVar(value="-- Select Preset --")
        self.preset_menu = tk.OptionMenu(preset_card, self.preset_var, "-- Select Preset --")
        self.preset_menu.config(
            bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=BG_HOVER, activeforeground=TEXT_PRIMARY,
            font=("Segoe UI", 8, "bold"), highlightthickness=0, bd=0, relief="flat", cursor="hand2"
        )
        self.preset_menu["menu"].config(
            bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT, activeforeground=TEXT_PRIMARY,
            font=("Segoe UI", 9), bd=1, relief="flat"
        )
        self.preset_menu.pack(side=tk.LEFT, padx=(2, 2), pady=2)

        btn_refresh = tk.Label(preset_card, text="🔄", font=("Segoe UI", 8), bg=BG_INPUT, fg=TEXT_MUTED, cursor="hand2", padx=6, pady=2)
        btn_refresh.pack(side=tk.LEFT, padx=(0, 4), pady=2)
        btn_refresh.bind("<Button-1>", lambda e: self.refresh_preset_menu())
        btn_refresh.bind("<Enter>", lambda e: btn_refresh.config(fg=TEXT_PRIMARY, bg=BG_HOVER))
        btn_refresh.bind("<Leave>", lambda e: btn_refresh.config(fg=TEXT_MUTED, bg=BG_INPUT))
        ModernTooltip(btn_refresh, "Reload presets directory", "Sync")

        tk.Frame(self.main_container, bg=BG_BORDER, height=1).pack(fill=tk.X)

        self.content_frame = tk.Frame(self.main_container, bg=BG_APP)
        self.content_frame.pack(fill=tk.BOTH, expand=True)

        self.current_tab_name = None
        self._tab_anim_id = None

        self.frame_playback = tk.Frame(self.content_frame, bg=BG_APP)
        self.frame_sequencer = tk.Frame(self.content_frame, bg=BG_APP)
        self.frame_settings = tk.Frame(self.content_frame, bg=BG_APP)

        self.build_playback_view()
        self.build_sequencer_view()
        self.build_settings_view()

        self.overlay = tk.Canvas(self.root, highlightthickness=0, bg=BG_APP)
        self.overlay.bind("<Button-1>", lambda e: self.close_sidebar())

        self.sidebar_frame = tk.Frame(self.root, bg=BG_SIDEBAR, width=self.sidebar_width)
        self.sidebar_frame.pack_propagate(False)
        self.build_sidebar()

        self.show_tab(initial_tab, animate=animate)



    def _on_scrub_nav(self, action):
        focus = self.root.focus_get()
        is_busy = self.playing or (self.recording and not self.recording_paused)
        if isinstance(focus, tk.Entry) or is_busy:
            return
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
        if (self.recording and not self.recording_paused) or self.playing or not self.recorded_actions:
            return "break"

        if not (0 <= self.current_action_idx < len(self.recorded_actions)):
            return "break"

        act = self.recorded_actions[self.current_action_idx]
        if act.get("type") not in ("move", "click", "wait_pixel", "scroll"):
            return "break"

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

        self.ghost_reticle.update(self.recorded_actions, self.current_action_idx)
        return "break"
        
    def build_sidebar(self):
        header_frame = tk.Frame(self.sidebar_frame, bg=BG_SIDEBAR, height=40)
        header_frame.pack(fill=tk.X, pady=(10, 0))
        
        tk.Label(header_frame, text="SEQUENT", font=("Segoe UI", 9, "bold"), bg=BG_SIDEBAR, fg=TEXT_MUTED, anchor="w").pack(fill=tk.X, padx=16)
        tk.Frame(self.sidebar_frame, bg=BG_BORDER, height=1).pack(fill=tk.X, padx=12, pady=8)

        self.btn_tab_playback = SidebarButton(self.sidebar_frame, text="PLAYBACK", command=lambda: self.show_tab("playback"))
        self.btn_tab_playback.pack(fill=tk.X)

        self.btn_tab_sequencer = SidebarButton(self.sidebar_frame, text="SEQUENCER", command=lambda: self.show_tab("sequencer"))
        self.btn_tab_sequencer.pack(fill=tk.X)

        self.btn_tab_settings = SidebarButton(self.sidebar_frame, text="SETTINGS", command=lambda: self.show_tab("settings"))
        self.btn_tab_settings.pack(fill=tk.X)
    
    def build_playback_view(self):
        playback_header = tk.Frame(self.frame_playback, bg=BG_APP)
        playback_header.pack(side=tk.TOP, fill=tk.X, padx=20, pady=(15, 8))

        left_header = tk.Frame(playback_header, bg=BG_APP)
        left_header.pack(side=tk.LEFT)

        tk.Label(left_header, text="Playback Queue", font=("Segoe UI", 12, "bold"), bg=BG_APP, fg=TEXT_PRIMARY).pack(side=tk.LEFT)
        self.action_badge = tk.Label(left_header, text="0 Actions", font=("Segoe UI", 8, "bold"), bg=BG_INPUT, fg=TEXT_MUTED, padx=8, pady=2)
        self.action_badge.pack(side=tk.LEFT, padx=(10, 0))

        file_card = ModernCard(playback_header, bg_card=BG_PANEL)
        file_card.pack(side=tk.RIGHT)

        btn_load_file = tk.Label(file_card, text="📥 Import", font=("Segoe UI", 8, "bold"), bg=BG_INPUT, fg=TEXT_PRIMARY, cursor="hand2", padx=10, pady=4)
        btn_load_file.pack(side=tk.LEFT, padx=(3, 2), pady=3)
        btn_load_file.bind("<Button-1>", lambda e: self.load_macro_from_file())
        btn_load_file.bind("<Enter>", lambda e: btn_load_file.config(bg=BG_HOVER))
        btn_load_file.bind("<Leave>", lambda e: btn_load_file.config(bg=BG_INPUT))
        ModernTooltip(btn_load_file, "Import external macro routine", ".json")

        btn_save_file = tk.Label(file_card, text="📤 Export", font=("Segoe UI", 8, "bold"), bg=BG_INPUT, fg=TEXT_PRIMARY, cursor="hand2", padx=10, pady=4)
        btn_save_file.pack(side=tk.LEFT, padx=(2, 3), pady=3)
        btn_save_file.bind("<Button-1>", lambda e: self.save_macro_to_file())
        btn_save_file.bind("<Enter>", lambda e: btn_save_file.config(bg=BG_HOVER))
        btn_save_file.bind("<Leave>", lambda e: btn_save_file.config(bg=BG_INPUT))
        ModernTooltip(btn_save_file, "Export current queue", ".json")

        # Scrubber Header Card
        self.scrub_card = ModernCard(self.frame_playback, bg_card=BG_PANEL)
        self.scrub_card.pack(side=tk.TOP, fill=tk.X, padx=20, pady=(0, 10))

        top_scrub_row = tk.Frame(self.scrub_card, bg=BG_PANEL, padx=8, pady=6)
        top_scrub_row.pack(fill=tk.X)

        btn_reset = tk.Label(top_scrub_row, text="⏮", font=("Segoe UI", 10), bg=BG_PANEL, fg=TEXT_MUTED, cursor="hand2", padx=6, pady=2)
        btn_reset.pack(side=tk.LEFT, padx=(0, 2))
        btn_reset.bind("<Button-1>", lambda e: self.jump_to_start())
        btn_reset.bind("<Enter>", lambda e: btn_reset.config(fg=TEXT_PRIMARY, bg=BG_HOVER))
        btn_reset.bind("<Leave>", lambda e: btn_reset.config(fg=TEXT_MUTED, bg=BG_PANEL))
        ModernTooltip(btn_reset, "Reset to Start", "Home")

        btn_step_back = tk.Label(top_scrub_row, text="◀ Step", font=("Segoe UI", 8, "bold"), bg=BG_INPUT, fg=TEXT_PRIMARY, cursor="hand2", padx=8, pady=3, highlightbackground=BG_BORDER, highlightthickness=1)
        btn_step_back.pack(side=tk.LEFT, padx=(0, 4))
        btn_step_back.bind("<Button-1>", lambda e: self.step_backward())
        btn_step_back.bind("<Enter>", lambda e: btn_step_back.config(bg=BG_HOVER))
        btn_step_back.bind("<Leave>", lambda e: btn_step_back.config(bg=BG_INPUT))
        ModernTooltip(btn_step_back, "Previous Action", "Left Arrow")

        btn_step_fwd = tk.Label(top_scrub_row, text="Step ▶", font=("Segoe UI", 8, "bold"), bg=BG_INPUT, fg=TEXT_PRIMARY, cursor="hand2", padx=8, pady=3, highlightbackground=BG_BORDER, highlightthickness=1)
        btn_step_fwd.pack(side=tk.LEFT, padx=(0, 6))
        btn_step_fwd.bind("<Button-1>", lambda e: self.step_forward())
        btn_step_fwd.bind("<Enter>", lambda e: btn_step_fwd.config(bg=BG_HOVER))
        btn_step_fwd.bind("<Leave>", lambda e: btn_step_fwd.config(bg=BG_INPUT))
        ModernTooltip(btn_step_fwd, "Next Action", "Right Arrow")

        btn_step_run = tk.Label(top_scrub_row, text="⚡ Run Step", font=("Segoe UI", 8, "bold"), bg=BG_INPUT, fg=SYNTAX_GREEN, cursor="hand2", padx=8, pady=3, highlightbackground=BG_BORDER, highlightthickness=1)
        btn_step_run.pack(side=tk.LEFT, padx=(0, 10))
        btn_step_run.bind("<Button-1>", lambda e: self.run_single_step())
        btn_step_run.bind("<Enter>", lambda e: btn_step_run.config(bg=BG_HOVER))
        btn_step_run.bind("<Leave>", lambda e: btn_step_run.config(bg=BG_INPUT))
        ModernTooltip(btn_step_run, "Execute Selected Step", "Space")

        self.scrubber = ModernScrubberTrack(top_scrub_row, command=self.on_scrub)
        self.scrubber.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        self.scrub_badge = tk.Label(
            top_scrub_row, text="[ 0 / 0 ]", font=("Consolas", 8, "bold"),
            bg=BG_INPUT, fg=SYNTAX_BLUE, padx=8, pady=3,
            highlightbackground=BG_BORDER, highlightthickness=1
        )
        self.scrub_badge.pack(side=tk.RIGHT)
        ModernTooltip(self.scrub_badge, "Nudge Selected Coordinates", "Alt + Arrows")

        self.loop_var = tk.StringVar(value=self.loop_mode)
        self.loop_menu = tk.OptionMenu(
            top_scrub_row, self.loop_var, *self.loop_options,
            command=lambda val: setattr(self, 'loop_mode', val)
        )
        self.loop_menu.config(
            bg=BG_INPUT, fg=SYNTAX_PURPLE, activebackground=BG_HOVER, activeforeground=TEXT_PRIMARY,
            font=("Segoe UI", 8, "bold"), highlightthickness=1, highlightbackground=BG_BORDER,
            bd=0, relief="flat", cursor="hand2"
        )
        self.loop_menu["menu"].config(
            bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT, bd=1, relief="flat"
        )
        self.loop_menu.pack(side=tk.RIGHT, padx=(0, 6))
        ModernTooltip(self.loop_menu, "Loop Repetitions", "Cycle")

        bot_preview_row = tk.Frame(self.scrub_card, bg=BG_HEADER, padx=10, pady=4)
        bot_preview_row.pack(fill=tk.X)

        tk.Label(bot_preview_row, text="PLAYHEAD:", font=("Segoe UI", 7, "bold"), bg=BG_HEADER, fg=TEXT_MUTED).pack(side=tk.LEFT, padx=(0, 8))

        self.lbl_active_action = tk.Label(
            bot_preview_row, text="// Queue idle", font=("Consolas", 8, "bold"),
            bg=BG_HEADER, fg=TEXT_MUTED, anchor="w"
        )
        self.lbl_active_action.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Bottom Action Buttons - Packed to BOTTOM first to prevent truncation
        controls_frame = tk.Frame(self.frame_playback, bg=BG_APP)
        controls_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=(0, 20))

        self.record_btn = ModernButton(
            controls_frame, text=self.get_btn_text("start_rec"), 
            bg_color=COLOR_DANGER, hover_color=COLOR_DANGER_HOV, command=self.toggle_recording
        )
        self.record_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ModernTooltip(self.record_btn, "Toggle Recording", self.hotkeys["start_rec"]["name"])

        self.pause_btn = ModernButton(
            controls_frame, text="Pause (F9)",
            bg_color=BG_BORDER, hover_color=BG_HOVER, command=self.toggle_pause
        )
        self.pause_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        self.pause_btn.set_state("disabled")
        ModernTooltip(self.pause_btn, "Pause / Resume Execution", "F9")

        self.play_btn = ModernButton(
            controls_frame, text=self.get_btn_text("start_play"), 
            bg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOV, command=self.toggle_playback
        )
        self.play_btn.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(4, 0))
        ModernTooltip(self.play_btn, "Run Active Sequence", self.hotkeys["start_play"]["name"])

        # Center Queue Viewer - Expands into all remaining vertical space
        self.queue_viewer = VSCodeQueueViewer(self.frame_playback, app_instance=self)
        self.queue_viewer.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=20, pady=(0, 15))
        self.queue_viewer.append_line("// Awaiting input...", SYNTAX_GREEN)

        nudge_keys = [
            ("<Alt-Left>", -1, 0), ("<Alt-Right>", 1, 0),
            ("<Alt-Up>", 0, -1), ("<Alt-Down>", 0, 1),
            ("<Alt-Shift-Left>", -5, 0), ("<Alt-Shift-Right>", 5, 0),
            ("<Alt-Shift-Up>", 0, -5), ("<Alt-Shift-Down>", 0, 5),
        ]
        for seq, dx, dy in nudge_keys:
            self.queue_viewer.code_text.bind(seq, lambda e, x=dx, y=dy: self.nudge_active_coordinate(x, y))


        
    def build_sequencer_view(self):
        seq_header = tk.Frame(self.frame_sequencer, bg=BG_APP)
        seq_header.pack(side=tk.TOP, fill=tk.X, padx=20, pady=(15, 8))

        left_header = tk.Frame(seq_header, bg=BG_APP)
        left_header.pack(side=tk.LEFT)

        tk.Label(left_header, text="Macro Chain Sequencer", font=("Segoe UI", 12, "bold"), bg=BG_APP, fg=TEXT_PRIMARY).pack(side=tk.LEFT)
        self.chain_badge = tk.Label(left_header, text="0 Stages", font=("Segoe UI", 8, "bold"), bg=BG_INPUT, fg=TEXT_MUTED, padx=8, pady=2)
        self.chain_badge.pack(side=tk.LEFT, padx=(10, 0))

        file_card = ModernCard(seq_header, bg_card=BG_PANEL)
        file_card.pack(side=tk.RIGHT)

        btn_load_chain = tk.Label(file_card, text="↑ Import Chain", font=("Segoe UI", 8, "bold"), bg=BG_INPUT, fg=TEXT_PRIMARY, cursor="hand2", padx=10, pady=3)
        btn_load_chain.pack(side=tk.LEFT, padx=(3, 2), pady=3)
        btn_load_chain.bind("<Button-1>", lambda e: self.load_chain_from_file())
        btn_load_chain.bind("<Enter>", lambda e: btn_load_chain.config(bg=BG_HOVER))
        btn_load_chain.bind("<Leave>", lambda e: btn_load_chain.config(bg=BG_INPUT))

        btn_save_chain = tk.Label(file_card, text="↓ Export Chain", font=("Segoe UI", 8, "bold"), bg=BG_INPUT, fg=TEXT_PRIMARY, cursor="hand2", padx=10, pady=3)
        btn_save_chain.pack(side=tk.LEFT, padx=(2, 3), pady=3)
        btn_save_chain.bind("<Button-1>", lambda e: self.save_chain_to_file())
        btn_save_chain.bind("<Enter>", lambda e: btn_save_chain.config(bg=BG_HOVER))
        btn_save_chain.bind("<Leave>", lambda e: btn_save_chain.config(bg=BG_INPUT))

        # Add Stage Card Builder
        add_card = ModernCard(self.frame_sequencer, bg_card=BG_PANEL)
        add_card.pack(side=tk.TOP, fill=tk.X, padx=20, pady=(0, 10))

        add_row = tk.Frame(add_card, bg=BG_PANEL, padx=10, pady=8)
        add_row.pack(fill=tk.X)

        tk.Label(add_row, text="Preset:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED).pack(side=tk.LEFT, padx=(0, 4))
        
        self.seq_preset_var = tk.StringVar(value="-- Select Preset --")
        self.seq_preset_menu = tk.OptionMenu(add_row, self.seq_preset_var, "-- Select Preset --")
        self.seq_preset_menu.config(
            bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=BG_HOVER,
            font=("Segoe UI", 8, "bold"), highlightthickness=1, highlightbackground=BG_BORDER,
            bd=0, relief="flat", cursor="hand2"
        )
        self.seq_preset_menu["menu"].config(bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=COLOR_ACCENT, bd=1, relief="flat")
        self.seq_preset_menu.pack(side=tk.LEFT, padx=(0, 10))

        tk.Label(add_row, text="Loops:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED).pack(side=tk.LEFT, padx=(0, 4))
        self.seq_loops_entry = tk.Entry(add_row, width=4, bg=BG_INPUT, fg=SYNTAX_PURPLE, font=("Consolas", 9, "bold"), bd=0, highlightbackground=BG_BORDER, highlightthickness=1, justify="center")
        self.seq_loops_entry.insert(0, "1")
        self.seq_loops_entry.pack(side=tk.LEFT, padx=(0, 10), ipady=2)

        tk.Label(add_row, text="Post-Delay:", font=("Segoe UI", 8, "bold"), bg=BG_PANEL, fg=TEXT_MUTED).pack(side=tk.LEFT, padx=(0, 4))
        self.seq_delay_entry = tk.Entry(add_row, width=5, bg=BG_INPUT, fg=SYNTAX_BLUE, font=("Consolas", 9, "bold"), bd=0, highlightbackground=BG_BORDER, highlightthickness=1, justify="center")
        self.seq_delay_entry.insert(0, "1.0")
        self.seq_delay_entry.pack(side=tk.LEFT, padx=(0, 12), ipady=2)

        btn_add_stage = ModernButton(add_row, text="+ Add Stage", bg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOV, command=self.add_chain_stage)
        btn_add_stage.pack(side=tk.RIGHT)

        # Bottom Chain Controls - Packed to BOTTOM first to prevent truncation
        chain_controls = tk.Frame(self.frame_sequencer, bg=BG_APP)
        chain_controls.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=(0, 20))

        btn_clear_chain = ModernButton(chain_controls, text="Clear All Stages", bg_color=BG_BORDER, hover_color=COLOR_DANGER, command=self.clear_chain)
        btn_clear_chain.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_run_chain = ModernButton(chain_controls, text="▶ Run Macro Chain (F9)", bg_color=COLOR_SUCCESS, hover_color="#1d8a4b", command=self.toggle_chain_run)
        self.btn_run_chain.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(6, 0))

        # Scrollable Chain Stages Timeline - Expands into all remaining vertical space
        self.chain_scroll = ModernScrollableFrame(self.frame_sequencer, bg=BG_APP)
        self.chain_scroll.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=20, pady=(0, 15))


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
                bg=COLOR_ACCENT if not is_active else COLOR_SUCCESS, fg="#ffffff", padx=6, pady=2
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
            self.root.attributes('-topmost', True)

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
            self.root.attributes('-topmost', True)

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
                
                # Execute individual preset sequence
                for act in actions:
                    if not self.chain_running: break

                    while self.is_paused and self.chain_running:
                        time.sleep(0.04)

                    self.execute_single_action(act)

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

        self.root.after(0, self.finish_chain)

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
        return f"{act_type}"
        

    def get_action_color(self, action):
        act_type = action.get("type", "") if action else ""
        if act_type in ("label", "goto", "if_var", "read_clipboard", "type_variable", "prompt_input"): return SYNTAX_PURPLE
        if act_type == "disclaimer": return SYNTAX_ORANGE
        if act_type == "wait_pixel": return COLOR_SUCCESS
        if act_type in ("move", "click", "drag", "scroll"): return SYNTAX_BLUE
        if act_type in ("key", "press", "enter", "backspace"): return SYNTAX_ORANGE
        if act_type in ("combo", "select_all", "copy", "paste", "delay", "search_area"): return SYNTAX_PURPLE
        return TEXT_PRIMARY
    

    def update_scrubber_ui(self, show_reticle=True):
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
            # Only display reticle during explicit manual inspection/scrubbing, never during playback
            is_active_running = (self.playing and not self.is_paused) or (self.recording and not self.recording_paused)
            if not is_active_running and not self.playing and show_reticle:
                self.ghost_reticle.update(self.recorded_actions, self.current_action_idx)
            else:
                self.ghost_reticle.hide()

    def set_active_action_index(self, index):
        # Ignore delayed queue dispatches if macro playback has already been stopped
        if not self.playing and not self.recording and not self.chain_running:
            return
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
        try:
            self.execute_single_action(action)
            self.update_status(f"Ran step #{self.current_action_idx + 1}", COLOR_SUCCESS)
        except Exception as e:
            self.update_status(f"Step Error: {e}", COLOR_DANGER)

        if self.current_action_idx < len(self.recorded_actions) - 1:
            self.current_action_idx += 1
            self.update_scrubber_ui()
        else:
            self.update_status("End of sequence", COLOR_SUCCESS)
        
    def execute_single_action(self, action):
        act_type = action.get("type")
        if act_type == "move":
            move_mouse(action.get("x", 0), action.get("y", 0))
        elif act_type == "click":
            move_mouse(action.get("x", 0), action.get("y", 0))
            time.sleep(0.02)
            click(action.get("btn", "left"))
        elif act_type == "scroll":
            move_mouse(action.get("x", 0), action.get("y", 0))
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
                self.update_status("Disclaimer cancelled", COLOR_DANGER)
            else:
                self.update_status("Disclaimer accepted", COLOR_SUCCESS)
        elif act_type == "prompt_input":
            prompt_txt = action.get("prompt", "Enter input:")
            default_val = action.get("default", "")
            var_name = action.get("var_name", "user_data")
            entered_val = self.prompt_user_input(prompt_txt, default_val)
            if entered_val is not None:
                self.variables[var_name] = str(entered_val)
                if self.audio_cues: play_cue("match")
                self.update_status(f"Var '{var_name}' = '{entered_val}'", COLOR_SUCCESS)
        elif act_type == "read_clipboard":
            clip_content = get_clipboard_text().lower()
            keywords = [k.lower() for k in action.get("keywords", [])]
            matched = any(kw in clip_content for kw in keywords)
            var_name = action.get("var_name", "status")
            if matched:
                val = action.get("value", "")
                self.variables[var_name] = val
                if self.audio_cues: play_cue("match")
                self.update_status(f"Var '{var_name}' = '{val}'", COLOR_SUCCESS)
            else:
                self.update_status(f"Var '{var_name}': No match in clipboard", SYNTAX_ORANGE)
        elif act_type == "type_variable":
            var_name = action.get("var_name", "status")
            val = str(self.variables.get(var_name, ""))
            if val:
                type_string(val, self.playback_speed)
                self.update_status(f"Typed '{val}'", COLOR_SUCCESS)
            else:
                self.update_status(f"Var '{var_name}' is empty", SYNTAX_ORANGE)
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
        elif act_type == "wait_pixel":
            cur = get_pixel_color(action.get("x", 0), action.get("y", 0))
            expected = action.get("color", "#FFFFFF").upper()
            match = (cur == expected)
            if match and self.audio_cues: play_cue("match")
            self.update_status(f"Pixel: {cur} (Match: {match})", COLOR_SUCCESS if match else COLOR_DANGER)


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
        if (self.recording and not self.recording_paused) or self.playing: return
        delay_action = {"type": "delay", "seconds": float(seconds)}
        self.recorded_actions.insert(index, delay_action)
        self.current_action_idx = index
        self.display_actions_in_queue(self.recorded_actions, self.current_source_label)
        self.update_status(f"Added {seconds:.2f}s Delay", COLOR_SUCCESS)

    def insert_search_area_action(self, index, text, area, preset, match_any=False):
        if (self.recording and not self.recording_paused) or self.playing: return
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
        if (self.recording and not self.recording_paused) or self.playing: return
        if 0 <= index < len(self.recorded_actions):
            act = self.recorded_actions.pop(index)
            if self.current_action_idx >= len(self.recorded_actions):
                self.current_action_idx = max(0, len(self.recorded_actions) - 1)
            self.display_actions_in_queue(self.recorded_actions, self.current_source_label)
            act_type = act.get("type", "action")
            self.update_status(f"Deleted {act_type} (Action #{index + 1})", COLOR_DANGER)

    def delete_action_range(self, start_idx, end_idx):
        if (self.recording and not self.recording_paused) or self.playing: return
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
        if (self.recording and not self.recording_paused) or self.playing: return
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
        snipper.geometry(f"{vw}x{vh}+{vx}+{vy}")
        snipper.config(cursor="crosshair")

        def cancel(event=None):
            try: snipper.destroy()
            except Exception: pass
            self.root.deiconify()
            self.set_app_window()
            if hasattr(self, 'queue_viewer') and self.queue_viewer.sub_menu:
                self.queue_viewer.sub_menu.deiconify()

        def on_click(event):
            x, y = event.x_root, event.y_root
            snipper.withdraw()
            self.root.update_idletasks()
            time.sleep(0.04)

            hex_color = get_pixel_color(x, y)
            try: snipper.destroy()
            except Exception: pass

            self.root.deiconify()
            self.set_app_window()
            if hasattr(self, 'queue_viewer') and self.queue_viewer.sub_menu:
                self.queue_viewer.sub_menu.deiconify()
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
        snipper.geometry(f"{vw}x{vh}+{vx}+{vy}")
        snipper.config(cursor="cross")

        canvas = tk.Canvas(snipper, bg="#000000", highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)

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
                self.root.deiconify()
                self.set_app_window()
                if hasattr(self, 'queue_viewer') and self.queue_viewer.sub_menu:
                    self.queue_viewer.sub_menu.deiconify()

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
                outline=COLOR_ACCENT, width=2, fill="#5865f2", stipple="gray25"
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
            self.root.deiconify()
            self.set_app_window()

            if w > 8 and h > 8: callback([x, y, w, h])

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
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        scale = max(4, int(160 / max(1, h)))
        scale = min(scale, 6)
        tw = w * scale
        th = h * scale

        h_screen_dc = user32.GetDC(0)
        h_mem_dc = gdi32.CreateCompatibleDC(h_screen_dc)
        h_bitmap = gdi32.CreateCompatibleBitmap(h_screen_dc, tw, th)
        h_old = gdi32.SelectObject(h_mem_dc, h_bitmap)

        try:
            gdi32.SetStretchBltMode(h_mem_dc, 4)
            pt = POINT()
            gdi32.SetBrushOrgEx(h_mem_dc, 0, 0, ctypes.byref(pt))

            gdi32.StretchBlt(
                h_mem_dc, 0, 0, tw, th,
                h_screen_dc, x, y, w, h,
                0x00CC0020 | 0x40000000
            )

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

            row_size = ((tw * 24 + 31) // 32) * 4
            image_size = row_size * th
            buf = (ctypes.c_char * image_size)()

            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth = tw
            bmi.biHeight = th
            bmi.biPlanes = 1
            bmi.biBitCount = 24
            bmi.biCompression = 0
            bmi.biSizeImage = image_size

            gdi32.GetDIBits(h_mem_dc, h_bitmap, 0, th, buf, ctypes.byref(bmi), 0)

            file_header = struct.pack('<2sIHHI', b'BM', 54 + image_size, 0, 0, 54)
            dib_header = struct.pack('<IiiHHIIiiII', 40, tw, th, 1, 24, 0, image_size, 2835, 2835, 0, 0)

            with open(filepath, 'wb') as f:
                f.write(file_header)
                f.write(dib_header)
                f.write(buf)
        finally:
            gdi32.SelectObject(h_mem_dc, h_old)
            gdi32.DeleteObject(h_bitmap)
            gdi32.DeleteDC(h_mem_dc)
            user32.ReleaseDC(0, h_screen_dc)


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

    def switch_to_preset_and_start(self, preset_name):
        self.switching_preset = False
        preset_file = os.path.join(self.presets_dir, f"{preset_name}.json")
        if not os.path.exists(preset_file):
            self.update_status(f"Preset '{preset_name}' Missing", COLOR_DANGER)
            self.finish_macro()
            return

        self.on_preset_file_selected(preset_name, preset_file)
        self.update_status(f"Switching to '{preset_name}'...", COLOR_ACCENT)
        self.root.after(150, self.start_macro)

    def switch_to_file_and_start(self, file_path):
        self.switching_preset = False
        if not os.path.exists(file_path):
            self.update_status(f"File Missing: {os.path.basename(file_path)}", COLOR_DANGER)
            self.finish_macro()
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
                self.light_mode = config.get("light_mode", False)
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
            "light_mode": self.light_mode,
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

    def build_settings_view(self):
        self.settings_scroll = ModernScrollableFrame(self.frame_settings, bg=BG_APP)
        self.settings_scroll.pack(fill=tk.BOTH, expand=True)

        scroll_container = self.settings_scroll.scrollable_frame

        tk.Label(
            scroll_container, text="Settings & Preferences",
            font=("Segoe UI", 16, "bold"), bg=BG_APP, fg=TEXT_PRIMARY
        ).pack(anchor="w", padx=20, pady=(20, 14))

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

        add_setting_toggle(f_app, "Light Mode Theme", self.light_mode, self.set_light_mode)
        self.build_entertain_me_toggle(f_app)

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
                    path = filedialog.askopenfilename(parent=self.root, title=f"Select Sound for {label_text}", filetypes=[("WAV Audio", "*.wav")])
                finally:
                    self.root.attributes('-topmost', True)
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
            
            btn_browse = tk.Label(controls, text="Browse", font=("Segoe UI", 8, "bold"), bg=COLOR_ACCENT, fg="#ffffff", cursor="hand2", padx=10, pady=4)
            btn_browse.pack(side=tk.RIGHT)
            btn_browse.bind("<Button-1>", lambda e: _browse())

        add_custom_sound_row(f_sys, "Start Recording Sound", "start_rec")
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
            bg=COLOR_ACCENT, fg="#ffffff", cursor="hand2", padx=10, pady=4
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


    def set_light_mode(self, enabled):
        self.light_mode = bool(enabled)
        self.apply_theme("light" if self.light_mode else "dark")

    def apply_theme(self, theme_name):
        if getattr(self, '_theme_lock', False): return
        self._theme_lock = True

        global BG_APP, BG_SIDEBAR, BG_HEADER, BG_PANEL, BG_INPUT, BG_GUTTER
        global BG_SCROLL_TRACK, BG_BORDER, BG_HOVER, TRACK_INACTIVE, TRACK_HOVER, TRACK_ACTIVE
        global TEXT_PRIMARY, TEXT_MUTED, TEXT_HEADER, COLOR_ACCENT, COLOR_ACCENT_HOV

        if theme_name == "light":
            BG_APP = "#f0f2f5"
            BG_SIDEBAR = "#e2e5e9"
            BG_HEADER = "#d3d7de"
            BG_PANEL = "#ffffff"
            BG_INPUT = "#e4e7eb"
            BG_GUTTER = "#e2e5e9"
            BG_SCROLL_TRACK = "#dcdfe4"
            BG_BORDER = "#b8bcc4"
            BG_HOVER = "#d1d5db"
            TRACK_INACTIVE = "#b0b5be"
            TRACK_HOVER = "#888e99"
            TRACK_ACTIVE = "#4752c4"
            TEXT_PRIMARY = "#111214"
            TEXT_MUTED = "#4a4f57"
            TEXT_HEADER = "#111214"
        else:
            BG_APP = "#313338"
            BG_SIDEBAR = "#2b2d31"
            BG_HEADER = "#1e1f22"
            BG_PANEL = "#2b2d31"
            BG_INPUT = "#1e1f22"
            BG_GUTTER = "#16171a"
            BG_SCROLL_TRACK = "#141517"
            BG_BORDER = "#3f4147"
            BG_HOVER = "#35373c"
            TRACK_INACTIVE = "#3f4147"
            TRACK_HOVER = "#585b64"
            TRACK_ACTIVE = "#5865f2"
            TEXT_PRIMARY = "#ffffff"
            TEXT_MUTED = "#a6acb5"
            TEXT_HEADER = "#ffffff"

        if getattr(self, '_sparkle_anim_id', None):
            try: self.root.after_cancel(self._sparkle_anim_id)
            except Exception: pass
            self._sparkle_anim_id = None
        self._sparkle_particles.clear()

        if getattr(self, '_tab_anim_id', None):
            try: self.root.after_cancel(self._tab_anim_id)
            except Exception: pass
            self._tab_anim_id = None

        active_tab = getattr(self, 'current_tab_name', "settings") or "settings"

        self.root.configure(bg=BG_APP)

        # Sync persistent grip backgrounds to the new theme so they don't leave ghost lines
        if hasattr(self, 'grip_e'):
            self.grip_e.config(bg=BG_APP)
            self.grip_s.config(bg=BG_APP)
            self.grip_w.config(bg=BG_APP)
            self.grip_se.config(bg=BG_APP)

        if hasattr(self, 'main_container') and self.main_container.winfo_exists():
            self.main_container.destroy()
        if hasattr(self, 'sidebar_frame') and self.sidebar_frame.winfo_exists():
            self.sidebar_frame.destroy()
        if hasattr(self, 'overlay') and self.overlay.winfo_exists():
            self.overlay.destroy()

        self.setup_ui(initial_tab=active_tab, animate=False)
        if self.recorded_actions:
            self.display_actions_in_queue(self.recorded_actions, self.current_source_label)

        self.root.after(350, lambda: setattr(self, '_theme_lock', False))


        
    def toggle_entertain_me(self, state):
        self.entertain_me = bool(state)
        BuiltInMacroApp.entertain_mode = bool(state)

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
            self.root.attributes('-topmost', True)

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
        hk_field = OBSHotkeyField(row, action_key, self.hotkeys[action_key]["name"], self)
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
            self.root.attributes('-topmost', True)

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
            self.root.attributes('-topmost', True)

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
        if source_label is None: source_label = self.current_source_label
        self.current_source_label = source_label

        self.queue_viewer.clear()
        self.queue_viewer.append_line(f"// {source_label}", SYNTAX_GREEN)

        for act in actions:
            desc = self.get_action_summary(act)
            color = self.get_action_color(act)
            self.queue_viewer.append_line(desc, color)

        self.update_action_count_badge()
        self.update_scrubber_ui()
        self.queue_viewer.redraw_breakpoints()
    
    def show_tab(self, tab_name, animate=True):
        self.root.focus_set()
        if hasattr(self, 'active_hotkey_field') and self.active_hotkey_field:
            self.active_hotkey_field.focus_out()
        self.is_binding = False

        # Retract sidebar smoothly
        self.close_sidebar()

        self.btn_tab_playback.set_active(tab_name == "playback")
        self.btn_tab_sequencer.set_active(tab_name == "sequencer")
        self.btn_tab_settings.set_active(tab_name == "settings")

        if tab_name == "sequencer":
            self.refresh_sequencer_preset_dropdown()

        if getattr(self, 'current_tab_name', None) == tab_name:
            return

        old_tab = getattr(self, 'current_tab_name', None)
        self.current_tab_name = tab_name

        tab_frames = {
            "playback": self.frame_playback,
            "sequencer": self.frame_sequencer,
            "settings": self.frame_settings
        }

        # Keep overlay and sidebar elevated without canvas method conflict
        if hasattr(self, 'overlay') and self.overlay.winfo_exists() and self.overlay.winfo_ismapped():
            tk.Misc.tkraise(self.overlay)
        if hasattr(self, 'sidebar_frame') and self.sidebar_frame.winfo_exists():
            self.sidebar_frame.lift()

        if not animate or not getattr(self, 'entertain_me', True) or old_tab is None or old_tab not in tab_frames:
            for name, frame in tab_frames.items():
                if name == tab_name:
                    frame.place(x=0, y=0, relwidth=1, relheight=1)
                    frame.tkraise()
                else:
                    frame.place_forget()
            return

        if getattr(self, '_tab_anim_id', None):
            try: self.root.after_cancel(self._tab_anim_id)
            except Exception: pass
            self._tab_anim_id = None

        outgoing_frame = tab_frames[old_tab]
        incoming_frame = tab_frames[tab_name]

        tab_order = {"playback": 0, "sequencer": 1, "settings": 2}
        direction = 1 if tab_order.get(tab_name, 0) > tab_order.get(old_tab, 0) else -1

        for name, frame in tab_frames.items():
            if name != old_tab and name != tab_name:
                frame.place_forget()

        self.animate_tab_transition(outgoing_frame, incoming_frame, direction)


    def animate_tab_transition(self, outgoing_frame, incoming_frame, direction, step=0, total_steps=18):
        def _back_out(t, s=1.5):
            t -= 1.0
            return t * t * ((s + 1.0) * t + s) + 1.0

        def _ease_out_quad(t):
            return 1.0 - (1.0 - t) * (1.0 - t)

        dpi = getattr(self, 'dpi_scale', 1.0)
        start_distance = int(220 * dpi) * direction
        exit_distance = int(120 * dpi) * direction

        t = min(1.0, step / total_steps)
        in_offset = int((1.0 - _back_out(t, s=1.5)) * start_distance)
        out_offset = int(-_ease_out_quad(t) * exit_distance)

        outgoing_frame.place(x=out_offset, y=0, relwidth=1, relheight=1)
        incoming_frame.place(x=in_offset, y=0, relwidth=1, relheight=1)
        incoming_frame.tkraise()

        # Lift overlay and sidebar using clean hierarchy calls
        if hasattr(self, 'overlay') and self.overlay.winfo_exists() and self.overlay.winfo_ismapped():
            tk.Misc.tkraise(self.overlay)
        if hasattr(self, 'sidebar_frame') and self.sidebar_frame.winfo_exists():
            self.sidebar_frame.lift()

        if step < total_steps:
            self._tab_anim_id = self.root.after(
                14, lambda: self.animate_tab_transition(outgoing_frame, incoming_frame, direction, step + 1, total_steps)
            )
        else:
            self._tab_anim_id = None
            outgoing_frame.place_forget()
            incoming_frame.place(x=0, y=0, relwidth=1, relheight=1)


    def toggle_sidebar(self):
        if self.sidebar_open: self.close_sidebar()
        else: self.open_sidebar()

    def open_sidebar(self):
        if not self.sidebar_open:
            self.sidebar_open = True
            if getattr(self, '_sidebar_anim_id', None):
                try: self.root.after_cancel(self._sidebar_anim_id)
                except Exception: pass
                self._sidebar_anim_id = None

            if getattr(self, 'entertain_me', True):
                self.generate_blur_overlay()
            else:
                self.overlay.delete("all")
                self.overlay.create_rectangle(0, 0, 3000, 3000, fill="#1e1f22")

            # Place the overlay across the window and place the sidebar above it
            self.overlay.place(x=0, y=0, relwidth=1, relheight=1)
            self.sidebar_frame.place(x=self.current_sidebar_x, y=0, width=self.sidebar_width, relheight=1)
            
            # Use tk.Misc.tkraise to elevate the canvas without triggering Canvas.tag_raise
            tk.Misc.tkraise(self.overlay)
            self.sidebar_frame.lift()
            
            self.animate_sidebar(0)
    
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
        if self.sidebar_open:
            self.sidebar_open = False
            if getattr(self, '_sidebar_anim_id', None):
                try: self.root.after_cancel(self._sidebar_anim_id)
                except Exception: pass
                self._sidebar_anim_id = None
            self.animate_sidebar(-self.sidebar_width)


    def animate_sidebar(self, target_x):
        if not getattr(self, 'entertain_me', True):
            self.current_sidebar_x = target_x
            self.sidebar_frame.place(x=self.current_sidebar_x, y=0, width=self.sidebar_width, relheight=1)
            self.sidebar_frame.lift()
            if target_x < 0:
                self.overlay.place_forget()
                self.sidebar_frame.place_forget()
                self.overlay.delete("all")
                self.blur_image = None
            return

        diff = target_x - self.current_sidebar_x

        if abs(diff) <= 2:
            self.current_sidebar_x = target_x
            self.sidebar_frame.place(x=self.current_sidebar_x, y=0, width=self.sidebar_width, relheight=1)
            self.sidebar_frame.lift()
            if target_x < 0:
                self.overlay.place_forget()
                self.sidebar_frame.place_forget()
                self.overlay.delete("all")
                self.blur_image = None
            self._sidebar_anim_id = None
            return

        step = int(diff * 0.34)
        if step == 0:
            step = 1 if diff > 0 else -1

        self.current_sidebar_x += step
        self.sidebar_frame.place(x=self.current_sidebar_x, y=0, width=self.sidebar_width, relheight=1)
        self.sidebar_frame.lift()

        self._sidebar_anim_id = self.root.after(12, lambda: self.animate_sidebar(target_x))        

    
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
        if self.audio_cues: play_cue("stop")

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
        # Immediately invalidate all pending playback callbacks
        self.playback_run_id = time.time()
        self._playback_ui_pending = False
        self.playing = False
        self.is_paused = False
        self.update_status("Stopped by user", SYNTAX_ORANGE)
        if self.audio_cues: play_cue("stop")

        if hasattr(self, 'ghost_reticle'):
            self.ghost_reticle.hide()

        self.current_action_idx = 0
        self.pause_btn.set_state("disabled", text="Pause (F9)", bg=BG_BORDER, hover=BG_HOVER)
        self.play_btn.set_state("normal", text=self.get_btn_text("start_play"), bg=COLOR_ACCENT, hover=COLOR_ACCENT_HOV)
        self.record_btn.set_state("normal", text=self.get_btn_text("start_rec"), bg=COLOR_DANGER, hover=COLOR_DANGER_HOV)
        self.queue_viewer.set_interactive(True)
        self.update_scrubber_ui(show_reticle=False)


    def prompt_user_input(self, prompt_text, default_val=""):
        import threading
        evt = threading.Event()
        result = [default_val]

        def _show():
            dlg = tk.Toplevel(self.root)
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

            card = tk.Frame(dlg, bg=BG_PANEL)
            card.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

            # Header bar with drag handles
            head = tk.Frame(card, bg=BG_HEADER, height=28, cursor="fleur")
            head.pack(fill=tk.X)

            drag_icon = tk.Label(head, text=" ⠇ ", font=("Segoe UI", 9), bg=BG_HEADER, fg=TEXT_MUTED, cursor="fleur")
            drag_icon.pack(side=tk.LEFT, padx=(4, 0))

            lbl_title = tk.Label(head, text="INPUT REQUESTED", font=("Segoe UI", 8, "bold"), bg=BG_HEADER, fg=SYNTAX_BLUE, cursor="fleur")
            lbl_title.pack(side=tk.LEFT, padx=4, pady=5)

            # Dragging mechanics
            drag_data = {"x": 0, "y": 0, "win_x": 0, "win_y": 0}

            def _start_drag(e):
                drag_data["x"] = e.x_root
                drag_data["y"] = e.y_root
                drag_data["win_x"] = dlg.winfo_x()
                drag_data["win_y"] = dlg.winfo_y()

            def _do_drag(e):
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

            card = tk.Frame(dlg, bg=BG_PANEL)
            card.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

            head = tk.Frame(card, bg=BG_HEADER, height=28, cursor="fleur")
            head.pack(fill=tk.X)

            drag_icon = tk.Label(head, text=" ⠇ ", font=("Segoe UI", 9), bg=BG_HEADER, fg=TEXT_MUTED, cursor="fleur")
            drag_icon.pack(side=tk.LEFT, padx=(4, 0))

            lbl_title = tk.Label(head, text=f"⚠️  {title_text.upper()}", font=("Segoe UI", 8, "bold"), bg=BG_HEADER, fg=SYNTAX_ORANGE, cursor="fleur")
            lbl_title.pack(side=tk.LEFT, padx=4, pady=5)

            drag_data = {"x": 0, "y": 0, "win_x": 0, "win_y": 0}

            def _start_drag(e):
                drag_data["x"] = e.x_root
                drag_data["y"] = e.y_root
                drag_data["win_x"] = dlg.winfo_x()
                drag_data["win_y"] = dlg.winfo_y()

            def _do_drag(e):
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

    def run_sequence(self):
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
        time.sleep(0.3) 

        held_modifiers = set()
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

                        if matched and self.audio_cues: play_cue("match")
                        if not matched and self.playing:
                            if self.audio_cues: play_cue("error")
                            self.root.after(0, lambda: self.update_status(f"Pixel timeout ({expected_color})", COLOR_DANGER))

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

                        time.sleep(max(0.001, 0.04 / self.playback_speed))
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

        except Exception as e:
            if self.audio_cues: play_cue("error")
            self.root.after(0, self.update_status, f"Error: {e}", COLOR_DANGER)
        finally:
            for m in list(held_modifiers):
                m_scan = ctypes.windll.user32.MapVirtualKeyW(m, 0)
                ctypes.windll.user32.keybd_event(m, m_scan, KEYEVENTF_KEYUP, 0)
            if not self.switching_preset:
                self.root.after(0, self.finish_macro)


    def finish_macro(self):
        self.playback_run_id = time.time()
        self._playback_ui_pending = False
        self.playing = False
        self.is_paused = False
        self.update_status("Finished", COLOR_SUCCESS)
        if self.audio_cues: play_cue("stop")
        self.lbl_active_action.config(text="// Execution complete", fg=SYNTAX_GREEN)

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
    app = BuiltInMacroApp(root)
    root.mainloop()
