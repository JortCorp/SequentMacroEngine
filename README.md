# Sequent

A Discord-themed desktop macro recorder, automation engine, and visual debugger built in Python for Windows.

Sequent combines low-level input tracking (`ctypes`, Win32 API) with a VS Code-style execution queue, visual trajectory inspection, optical character recognition (OCR), dynamic variable management, and conditional branching.

---

## Features

### 🎮 Low-Level Recording & Playback
* **Low-Level Hooks**: Intercepts keyboard events, mouse clicks, drags, and vertical scroll wheel notches (`WH_MOUSE_LL`) without locking the cursor or dropping frames.
* **1:1 Live Queue Updates**: Displays inputs live in the playback queue as they occur.
* **Window-Relative Anchoring**: Captures coordinates relative to the target application's client window instead of fixed screen coordinates.
* **Modifier & Shortcut Mapping**: Captures modifier keys (`Ctrl`, `Shift`, `Alt`, `Win`) and composite shortcuts (`Ctrl + C`, `Alt + Tab`, etc.).

### 🤖 Humanized Emulation
* **Bézier Trajectories**: Calculates cubic Bézier curves with randomized control point offsets to simulate natural human hand motion.
* **Natural Timing Mode**: Records and plays back real-world inter-keystroke and movement delays.
* **Delay Jitter**: Adds randomized micro-variances ($\pm15\%$) to execution delays to avoid robotic detection patterns.

### 🔍 Visual Debugger & Trajectory Inspector
* **VS Code-Style Queue Viewer**: Syntax-highlighted timeline with interactive insertion markers, line deletion, and right-click contextual actions.
* **Visual Ghost Reticles & After-Images**: Selecting multiple actions renders a multi-point reticle trail with an alpha gradient (oldest actions are translucent, newest actions are solid) directly on your desktop.
* **Breakpoints**: Click the gutter margin to place red breakpoint dots (`●`). The engine halts execution right before hitting the specified step.
* **Scrubber & Stepper**: Step forward, step backward, or run single actions with `⏮`, `◀ Step`, `Step ▶`, and `▶ Run Step`.
* **In-Place Nudging**: Use `Alt + Arrow` (or `Alt + Shift + Arrow` for $5\times$) to adjust click and movement coordinates without re-recording.

### 🧠 Logic, Control Flow & Variables
* **Conditional Jumps (`flow.if`)**: Branch execution dynamically based on variable values (`==`, `!=`, `contains`, `not empty`). Jump to an internal queue label (`flow.goto`), launch a preset, or switch to an external macro file.
* **Clipboard Keyword Detection**: Read Windows clipboard text, check for specific phrases (e.g., `Please Scrap`, `Please Return`), and store matching values in variables.
* **Interactive Prompts**: Display draggable input dialogs centered on the monitor you were most recently active on to store runtime values.
* **Variable Typing**: Inject stored runtime variables into active input fields via emulated keystrokes.
* **OCR Text Detection**: Native Windows Media OCR integration via WinRT to scan designated screen regions and switch presets when target text appears.
* **Pixel Color Watcher**: Halt execution until a screen coordinate matches an exact hexadecimal color (`#RRGGBB`), complete with an interactive desktop pixel crosshair sampler.

### ⛓️ Macro Chain Sequencer
* Assemble independent preset files into an automated pipeline.
* Configure per-stage loop counts and customizable post-stage delay buffers.
* Export and import modular chain configurations (`.chain.json`).

### 🪟 Interface & Ergonomics
* Frameless Discord dark-mode styling with native Aero drop-shadow integration.
* Collapsible **Mini HUD** overlay ($265 \times 42$ px) to pause, stop, and monitor status while keeping your workspace clear.
* Non-blocking native audio beeps (`kernel32.Beep`) for start, stop, pause, resume, condition match, and error events.
* Global hotkey bindings (`F7`–`F10` by default) configurable with modifier keys.

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

* **Operating System**: Windows 10 or Windows 11 (64-bit recommended)
* **Python**: Python 3.8+
* **Dependencies**: Standard Library only (`ctypes`, `tkinter`, `json`, `subprocess`, `threading`, etc.). No external `pip` dependencies are required.

> **Note**: OCR capabilities utilize the built-in `Windows.Media.Ocr` engine available in Windows 10/11 through a non-interactive PowerShell bridge. Ensure an OCR language pack (e.g., English) is installed in Windows Settings.

---

## Installation & Launch

1. Clone the repository:
   ```bash
   git clone [https://github.com/your-username/sequent.git](https://github.com/your-username/sequent.git)
   cd sequent
   ```

2. Run the application:
   ```bash
   python Sequent.py
   ```

---

## Usage Guide

### 1. Recording a Macro
* Press **F7** (or click **Start Recording**).
* Perform your mouse movements, clicks, scrolls, and keystrokes.
* Press **F8** (or click **Stop Recording**) to end recording.
* Use **Pause Rec (F9)** to temporarily halt capture. While paused, you can scrub the timeline, nudge coordinates, set breakpoints, or insert subactions directly.

### 2. Setting Breakpoints & Debugging
* Click the left gutter margin next to any line number to set or clear a red breakpoint (`●`).
* When running the macro (**F9**), execution automatically pauses when it reaches that line.
* Use the scrubber controls:
  * `⏮`: Reset playback to step 1.
  * `◀ Step` / `Step ▶`: Scrub backward and forward.
  * `▶ Run Step` (or `Space`): Execute only the currently selected line.
  * `Alt + Left/Right/Up/Down`: Nudge coordinates in real time.

### 3. Inserting Subactions
* Hover over the queue viewer gutter and click the blue **`+`** button (or right-click an action line).
* Choose an action from the dropdown:
  * **Delay**: Insert pause buffers.
  * **If Variable Condition**: Set dynamic branch logic based on variable checks.
  * **Read Clipboard**: Search copied text for keywords and assign output variables.
  * **Request Input**: Prompt the operator for text to be typed later.
  * **Type Variable**: Output a stored variable as automated typing.
  * **Search selected area**: Snip a region of your screen to monitor for specific text via OCR.
  * **Wait Pixel**: Crosshair-inspect a pixel color and block execution until it appears.
  * **Label Marker / Jump to Label**: Create loops and sub-routines.

### 4. Macro Chain Sequencer
* Open the left navigation sidebar (**☰** icon) and select **Sequencer**.
* Pick saved presets from the dropdown, specify loop counts, and add post-stage delay buffers.
* Click **Add Stage** to append to the pipeline.
* Save the sequence as a `.chain.json` file to export or automate multi-stage workflows.

---

## Configuration & Hotkeys

All hotkey combinations and runtime preferences can be modified in the **Settings** panel (`# settings` in the sidebar):

* **Playback Speed**: Adjust speed multiplier from `1.0x` to `10.0x`.
* **Action Delay**: Configure baseline inter-action pauses (`0.05s` to `1.50s`).
* **Fluid Mouse Playback**: Toggle linear mouse interpolation.
* **Clicks & Keys Only**: Filter out intermediate mouse moves to streamline scripts.
* **Human Bézier Curves**: Route cursor paths through randomized mathematical curves.
* **Delay Jitter**: Toggle humanized micro-timing variations.
* **Natural Timing Mode**: Replay exact recorded user intervals.
* **Window Anchoring**: Toggle client-window relative coordinate offsets.
* **Audio Cues**: Enable or disable system frequency sound prompts.
* **Hotkeys**: Click any hotkey field and press your preferred key combination.

---

## Directory Structure

```text
sequent/
├── Sequent.py           # Application entrypoint and macro engine
├── presets/             # Saved macro routines (.json)
├── chains/              # Sequencer pipeline definitions (.chain.json)
└── README.md
```

---

## License

Distributed under the GNU Public License. See `LICENSE` for more information.
