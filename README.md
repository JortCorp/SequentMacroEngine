# Sequent

A Discord-themed desktop macro recorder, automation engine, and visual debugger built in Python for Windows.

Sequent combines low-level input tracking (`ctypes`, Win32 API) with a VS Code-style execution queue, visual trajectory inspection, optical character recognition (OCR), dynamic variable management, and conditional branching.

---

## Features

### 🎮 Low-Level Recording & Playback
- **Low-Level Hooks**: Intercepts keyboard events, mouse clicks, drags, and vertical scroll wheel notches (`WH_MOUSE_LL`) without locking the cursor or dropping frames.
- **1:1 Live Queue Updates**: Displays inputs live in the playback queue as they occur.
- **Window-Relative Anchoring**: Captures coordinates relative to the target application's client window instead of fixed screen coordinates.
- **Modifier & Shortcut Mapping**: Captures modifier keys (`Ctrl`, `Shift`, `Alt`, `Win`) and composite shortcuts (`Ctrl + C`, `Alt + Tab`, etc.).

### 🤖 Humanized Emulation
- **Bézier Trajectories**: Calculates cubic Bézier curves with randomized control point offsets to simulate natural human hand motion.
- **Natural Timing Mode**: Records and plays back real-world inter-keystroke and movement delays.
- **Delay Jitter**: Adds randomized micro-variances (±15%) to execution delays to avoid robotic detection patterns.

### 🔍 Visual Debugger & Trajectory Inspector
- **VS Code-Style Queue Viewer**: Syntax-highlighted timeline with interactive insertion markers, line deletion, and right-click contextual actions.
- **Visual Ghost Reticles & After-Images**: Selecting multiple actions renders a multi-point reticle trail with an alpha gradient (oldest actions are translucent, newest actions are solid) directly on your desktop.
- **Breakpoints**: Click the gutter margin to place red breakpoint dots (`●`). The engine halts execution right before hitting the specified step.
- **Scrubber & Stepper**: Step forward, step backward, or run single actions with `⏮`, `◀ Step`, `Step ▶`, and `▶ Run Step`.
- **In-Place Nudging**: Use `Alt + Arrow` (or `Alt + Shift + Arrow` for 5x) to adjust click and movement coordinates without re-recording.

### 🧠 Logic, Control Flow & Variables
- **Conditional Jumps (`flow.if`)**: Branch execution dynamically based on variable values (`==`, `!=`, `contains`, `not empty`). Jump to an internal queue label (`flow.goto`), launch a preset, or switch to an external macro file.
- **Clipboard Keyword Detection**: Read Windows clipboard text, check for specific phrases (e.g., `Please Scrap`, `Please Return`), and store matching values in variables.
- **Interactive Prompts**: Display draggable input dialogs centered on the monitor you were most recently active on to store runtime values.
- **Variable Typing**: Inject stored runtime variables into active input fields via emulated keystrokes.
- **OCR Text Detection**: Native Windows Media OCR integration via WinRT to scan designated screen regions and switch presets when target text appears.
- **Pixel Color Watcher**: Halt execution until a screen coordinate matches an exact hexadecimal color (`#RRGGBB`), complete with an interactive desktop pixel crosshair sampler.

### ⛓️ Macro Chain Sequencer
- Assemble independent preset files into an automated pipeline.
- Configure per-stage loop counts and customizable post-stage delay buffers.
- Export and import modular chain configurations (`.chain.json`).

### 🪟 Interface & Ergonomics
- Frameless Discord dark-mode styling (with a toggleable Light Mode) and native Aero drop-shadow integration.
- Collapsible **Mini HUD** overlay (265 x 42 px) to pause, stop, and monitor status while keeping your workspace clear.
- Non-blocking native audio cues (`kernel32.Beep`) with selectable themes (Classic, Subtle, Sci-Fi) and support for custom `.wav` files for start, stop, and pause events.
- Global hotkey bindings (`F7`–`F10` by default) configurable with modifier keys.

---

## Subaction Syntax Reference

Actions in Sequent's queue viewer display clean syntax representations:

| Action Signature | Description |
| :--- | :--- |
| `mouse.move(x, y)` | Moves the cursor to coordinates `(x, y)` (screen or window-relative). |
| `mouse.left_click(x, y)` | Moves and executes a left mouse click. |
| `mouse.drag((x1, y1) -> (x2, y2))` | Performs a mouse-down drag between two coordinate sets. |
| `mouse.scroll(delta, x, y)` | Dispatches mouse wheel delta pulses at target coordinates. |
| `keyboard.type('text')` | Types raw characters via `VkKeyScanW` key events. |
| `keyboard.hotkey('Ctrl + C')` | Holds modifier keys and presses the target virtual key. |
| `system.delay(1.50s)` | Suspends playback for a configured duration. |
| `clipboard.find('kw', set='var', val='X')` | Checks clipboard contents for keywords and sets `var = val`. |
| `user.prompt('Message', var='name')` | Prompts the user with an active-monitor dialog and stores the input. |
| `keyboard.type_var('var_name')` | Types the stored value of variable `var_name`. |
| `flow.label('checkpoint_1')` | Declares an execution destination marker in the queue. |
| `flow.goto('checkpoint_1')` | Unconditionally jumps playback to a label. |
| `flow.if('var' == 'val' -> ...)` | Evaluates a condition to jump to a label, switch preset, or run a macro file. |
| `condition.wait_pixel(x, y, color)` | Halts until the pixel at `(x, y)` matches hex color `#RRGGBB`. |
| `ocr.search_area('target')` | Reads desktop region text via WinRT OCR and triggers a macro switch on match. |

---

## Requirements

- **Operating System**: Windows 10 or Windows 11 (64-bit recommended)
- **Python**: Python 3.8+
- **Dependencies**: Standard Library only (`ctypes`, `tkinter`, `json`, `subprocess`, `threading`, `winsound`, etc.). No external `pip` dependencies are required.

> **Note**: OCR capabilities utilize the built-in `Windows.Media.Ocr` engine available in Windows 10/11 through a non-interactive PowerShell bridge. Ensure an OCR language pack (e.g., English) is installed in Windows Settings.

---

## Installation & Launch

1. Clone the repository:
   ```bash
   git clone [https://github.com/jortcorp/sequent.git](https://github.com/jortcorp/sequent.git)
   cd sequent
