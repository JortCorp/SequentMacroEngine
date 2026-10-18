# Sequent

**A visual desktop automation IDE for Windows — record, edit, debug, and replay workflows.**

Sequent combines a macro recorder, an editable action queue, screen recognition, conditional logic, and a visual debugger in one Python application. Start by recording a repetitive task, then refine it with variables, waits, branching, and position-aware actions instead of relying on a fixed sequence of clicks.

Built with Python's standard library and native Windows APIs. **No third-party Python packages are required.** An **optional experimental C DLL** is available for fully borderless, natively resizable windows.

> **Development status:** Sequent is actively developed and should be treated as experimental, especially its new native window chrome and screen-based targeting. The features below describe the current development source (v6.6); the public repository may lag until the corresponding code is committed. Test workflows in a safe application before using them with important data.

## Contents

- [Highlights](#highlights)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Find Color & Click](#find-color--click)
- [Workspace and debugger](#workspace-and-debugger)
- [Actions and automation logic](#actions-and-automation-logic)
- [Presets and macro chains](#presets-and-macro-chains)
- [Appearance and window management](#appearance-and-window-management)
- [Hotkeys](#hotkeys)
- [Troubleshooting and limitations](#troubleshooting-and-limitations)
- [Contributing and license](#contributing-and-license)

## Highlights

### Record and replay

- Capture keyboard input, shortcuts, clicks, movement, dragging, and wheel scrolling using Windows APIs and low-level hooks.
- Watch events appear in the live Action Queue, then reorder your workflow by inserting, editing, or removing actions.
- Replay with pause/stop controls, loops, adjustable speed, window-relative coordinates, and optional Bézier mouse movement or timing variation.
- Use the compact Mini HUD when you want playback controls without the full workspace visible.

### Build visually

- Browse a searchable, categorized **Action Library** with dedicated properties editors.
- Use **breakpoints**, an execution playhead, step controls, and syntax-style line descriptions to debug actions.
- Preview mouse positions with an on-screen **Ghost Reticle**, including multi-action trajectory previews.
- Add OCR, pixel checks, variables, prompts, and control-flow actions to turn a recording into a more adaptive workflow.
- Combine saved presets into repeatable, multi-stage **Macro Chains**.

### New in the latest development build

| Area | Improvements |
| --- | --- |
| Color targeting | Connected-color-region detection, adjustable RGB tolerance, selectable anchors, and relative click offsets |
| Visual editor | Draggable Ghost Reticle that can sit **inside or outside** the detected bounds, keyboard nudging, and live detection feedback |
| Action editing | Double-click **Find Color & Click** to redefine search bounds, resample color, and preserve saved targeting options |
| Click reliability | Shared targeting calculations across playback paths, cursor-settle checks, and checked `SendInput` click events |
| Appearance | **Dark / Light / Match System** themes, saved accent colors, custom color picker with hue, saturation/brightness, HEX/RGB input, and presets |
| Window chrome | Custom headers and live Windows-managed resizing; optional experimental native borderless DLL with safe fallback modes |
| Stability | 64-bit GDI handle signatures, shared `POINT` type for cursor polling, and cleaner terminal interrupt handling |

## Requirements

| Requirement | Details |
| --- | --- |
| Operating system | Windows 10 or Windows 11 |
| Python | Python 3.8+ with Tkinter; behavior can vary by Python/Tk and Windows version |
| Python dependencies | Standard library only (`ctypes`, `tkinter`, `threading`, `json`, `winsound`, etc.) |
| OCR | Windows OCR support and an installed OCR language pack |
| Optional native borderless mode | 64-bit Windows/Python; MSVC C++ Build Tools and the Windows SDK to compile the included DLL |

OCR uses Windows' `Windows.Media.Ocr` through a PowerShell/WinRT bridge. **Administrator privileges are not a blanket requirement**, but Windows input security prevents Sequent from controlling some elevated, protected, or security-sensitive interfaces.

## Installation

1. Install Python for Windows with Tkinter support.
2. Clone the repository:

   ```powershell
   git clone https://github.com/JortCorp/SequentMacroEngine.git
   cd SequentMacroEngine
   ```

3. Launch:

   ```powershell
   py Sequent.py
   ```

   Or use `python Sequent.py` if that is your configured command.

4. Open **Settings** to configure the appearance, preset paths, hotkeys, and playback preferences.

**No `pip install` step is needed** to run the Python application. The native window DLL described below is optional and is **not** included as a compiled binary in the v6.6 source bundle.

## Quick start

### 1. Record a test macro

1. Open a harmless target such as Notepad.
2. In **Playback**, click **Start Recording** or press `F7`.
3. Type a short phrase or perform a small sequence of actions.
4. Stop with `F8`.
5. Inspect the Action Queue and edit any unwanted actions.
6. Run with **Run Macro** or `F9`; stop with `F10`.

> **Privacy:** A macro recorder can capture sensitive typing and clipboard contents. Avoid recording passwords, tokens, private messages, or confidential data. Review exported presets before sharing them.

### 2. Modify an action

- **Double-click** an action in the queue to open its editor.
- Use the queue gutter, plus control, or right-click menu to insert actions from the Action Library.
- Set breakpoints and use the playhead/step controls to check what the action actually does.
- Save presets as JSON to reuse or chain later.

### 3. Add a smarter wait

Instead of assuming a page finishes loading after a fixed delay, add **Wait for Text** or **Wait Pixel**, configure its expected result and search position, then test how it behaves when the application is slow or the result is missing.

## Find Color & Click

**Find Color & Click** uses a color on the desktop as a *visual reference point*. The mouse target is calculated relative to that reference—not restricted to the colored object itself.

**Detection region** → **matching color component** → **bounding-box anchor** → **X/Y offsets** → **mouse destination**

### Configure a new color action

1. In the Action Queue, add **Find Color & Click** from **Screen & Recognition**.
2. Choose **Define Search Box** and drag a search region on your desktop. You can also enter its `X`, `Y`, `Width`, and `Height` numerically.
3. Click **Sample Color** or enter a `#RRGGBB` value, then choose the RGB tolerance.
4. Select an anchor (Center by default, or another edge/corner anchor).
5. Set **When found** to **Move Only**, **Single Click**, or **Double Click**.
6. Choose **Save & Open Live Reticle Editor** to preview and refine the final cursor position.

### Refine with the Ghost Reticle

- A **search-area outline** shows where Sequent is looking.
- A **match bounding box** shows the largest connected region matching your selected color, subject to tolerance.
- The **Ghost Reticle** marks the actual cursor destination. **Drag it anywhere, including outside the match box**, to change the saved anchor-relative X/Y offsets.
- While editing, use **Arrow keys** for 1-pixel nudges or **Shift + Arrow** for 5-pixel nudges. The queue's `Alt + Arrow` / `Alt + Shift + Arrow` shortcuts are also supported.
- A live readout displays the current match position/size and calculated target. Color scanning occurs independently of the reticle's movement to reduce overlay flashing.
- Finish reticle editing before executing the action; the interactive overlay is dismissed before mouse input is sent.

To change an action later, **double-click the same queue entry** and use **Redefine Search Box on Desktop**, edit the coordinate fields, or sample a new color. Existing anchors and offsets are preserved unless you change them.

**Example:** If a reference color's center is `(500, 300)` and your offset is `(+250, +120)`, the click target is `(750, 420)`. If the reference moves to `(900, 500)`, the target becomes `(1150, 620)`.

A color action in an exported JSON preset can look like this:

```json
{
  "type": "find_color",
  "area": [100, 100, 600, 400],
  "color": "#35A879",
  "tolerance": 8,
  "anchor": "Center",
  "ox": 40,
  "oy": 15,
  "action": "Single Click"
}
```

**Important limitations:** The preview outlines a **rectangular bounding box**, not a traced pixel-perfect silhouette. Matching chooses the largest connected region; multiple similar-colored objects or changes in lighting, scaling, and antialiasing can still affect the result. An accepted synthetic mouse event does not guarantee the target application acts on it. Test each visual target carefully.

## Workspace and debugger

| Workspace | Purpose | Key tools |
| --- | --- | --- |
| **Playback** | Record, edit, run, and debug macros | Action Queue, breakpoints, playhead, scrubber, Controls, Import/Export |
| **Sequencer** | Build and execute macro chains | Stage Builder, chain timeline, loops, delays, Import/Export |
| **Settings** | Customize the application | Themes, accent colors, hotkeys, playback, sound, paths, onboarding |

**Guided Tour:** A 12-step, non-destructive walkthrough introduces the interface, recording, presets, Mini HUD, debugger, and sequencer. Open it from the sidebar or **Settings → Help & Onboarding**.

**Mini HUD:** A small floating control panel for pause/resume, stop, and returning to the main workspace.

**Debugging:** Queue line numbers, click-to-select playhead navigation, breakpoints, step execution, and visual reticles help you inspect a macro rather than replay the entire sequence each time.

## Actions and automation logic

The queue displays readable *action summaries*, not executable Python code. Create them through recording or the Action Library.

| Action | Purpose |
| --- | --- |
| Mouse move / click / drag / scroll | Interact at a recorded position or follow the recorded mouse path |
| Keyboard type / press / hotkey | Enter text and send key combinations |
| Delay | Wait a specified amount of time |
| Wait Pixel | Wait for a pixel to match a chosen color |
| Wait for Text | Watch a screen region for OCR text to appear or disappear |
| Search selected area | OCR a region and optionally branch to a preset |
| **Find Color & Click** | Locate a connected color region and move/click at an anchored, offset position |
| Read Clipboard | Detect keywords and store a value in a variable |
| Request Input | Ask the user for a value during playback |
| Type Variable | Type the current contents of a variable |
| If Variable Condition | Branch according to a variable comparison |
| Label / Jump to Label | Mark a location and jump to it |
| Disclaimer / Warning | Display a user-facing message during execution |

OCR and visual recognition are best-effort. A failed match, timeout, or unexpected UI state may stop a workflow; avoid blindly repeating destructive actions.

## Presets and macro chains

### Macros (`.json`)

Export from the Playback workspace or import a saved preset. By default, Sequent uses the `presets` folder relative to the working directory unless configured otherwise.

```json
[
  {"type": "delay", "seconds": 1.5}
]
```

**Loading a preset replaces the current queue**; export unfinished work before switching presets. Importing does not automatically run it.

### Macro chains (`.chain.json`)

Use the Sequencer's Stage Builder to choose saved macros, set each stage's loop count and post-stage delay, then reorder or export the chain.

```json
[
  {"preset": "ExamplePreset", "loops": 2, "delay_after": 1.0}
]
```

The referenced preset must exist in the configured preset directory when the chain runs.

## Appearance and window management

### Themes and accent colors

Go to **Settings → Appearance**:

- **Dark** — use Sequent's dark color scheme.
- **Light** — use its light color scheme.
- **Match System** — follow Windows' app-theme preference while Sequent runs.
- **Accent Color** — choose a quick swatch, enter a HEX value, or open **Customize...** for Sequent's own color editor.

The custom picker has a hue strip, saturation/brightness selection, HEX and RGB fields, before/after preview, and **Apply/Cancel**. Accent changes update supported interface elements, including the **MACRO WORKSPACE** branding. Appearance settings persist in `settings.json`; older `light_mode` settings are supported for compatibility.

### Custom window chrome (experimental)

Sequent uses custom-painted headers while preserving Windows-managed movement and **live resizing**. The latest source offers an **optional native borderless mode** implemented by `native/SequentChrome.c` so low-level resize messages are handled in compiled C rather than Python callbacks.

**Without the DLL**, Sequent still runs and uses the safer thin-frame resizing implementation. The fully borderless path is **not yet verified as a stable Windows release**.

To try the optional mode on **Windows x64**:

1. Install **Visual Studio Build Tools** with **Desktop development with C++** and a Windows SDK.
2. Ensure the repository includes the `native` directory from the v6.6 source package.
3. Open an **x64 Native Tools Command Prompt for VS** in the project directory.
4. Build:

   ```bat
   native\build_native.cmd
   ```

5. The script should create `SequentChrome.dll` **beside `Sequent.py`**. Launch with `py Sequent.py` and test resizing, maximizing, snapping, and dialog focus.

The DLL is loaded only when present and compatible. Keep it beside `Sequent.py`; it is not a Python `pip` dependency.

**Window modes (PowerShell):**

```powershell
# Default: native borderless DLL if available, safe fallback otherwise
$env:SEQUENT_WINDOW_MODE = "custom"
py Sequent.py

# Safe fallback: custom Sequent header + thin native resize frame
$env:SEQUENT_WINDOW_MODE = "legacy"
py Sequent.py

# Diagnostic fallback: ordinary Windows decorations
$env:SEQUENT_WINDOW_MODE = "native"
py Sequent.py
```

Set one mode before each launch; these examples show alternatives, not commands that must all be run. You can also remove or rename the optional DLL **while Sequent is closed** to disable the experimental native mode.

> **Technical note:** The native code handles `WM_NCCALCSIZE` and `WM_NCHITTEST` without a Python `WNDPROC` callback. This is intended to avoid the GIL-related interpreter crash observed when resizing through a Python-level window subclass. Native behavior still needs real Windows testing across different displays and versions.

## Hotkeys

| Default | Action |
| --- | --- |
| `F7` | Start recording |
| `F8` | Stop recording |
| `F9` | Start playback |
| `F10` | Stop playback |

Global hotkeys can be changed under **Settings → Global Hotkeys**. Other pause, step, and reticle controls are available from the interface.

## Troubleshooting and limitations

**Recording crashes with `expected LP_POINT instance`**  
Update to the latest source. A previous window-motion implementation changed the shared `GetCursorPos` signature to an incompatible local structure type; the v6.6 source uses the common `POINT` type.

**The window has a border or resizing behaves unexpectedly**  
The compiled native borderless DLL is optional and experimental. Set `SEQUENT_WINDOW_MODE=legacy` for the safer thin-frame resizing mode, or `native` for normal Windows decorations. Include your Windows/Python versions and a screenshot when reporting a problem.

**Find Color & Click misses, flickers, or clicks the wrong point**  
Confirm the search rectangle, sampled HEX color, RGB tolerance, anchor, and offsets. Review the live bounding box and reticle. Avoid capturing multiple disconnected objects of the same color in one search region. For click delivery failures, verify that **When found** is not **Move Only** and that the target accepts simulated input.

**OCR misses text**  
Check the selected screen area, scaling, target text, language pack, and whether the text is visible. Windows OCR can be slow or inaccurate.

**A macro is blocked by another application**  
Some elevated, protected, remote, or specialized applications ignore synthetic input. Sequent cannot bypass the Windows secure desktop or guarantee access across integrity levels.

**Closing from a terminal shows `KeyboardInterrupt`**  
Ctrl+C interrupts Tkinter's event loop. The current development source handles this more gracefully; use Sequent's normal window controls for ordinary shutdown.

**General limitations:** Sequent is desktop screen/input automation, not a universal application API. A successful visual match or Windows input injection does not guarantee that a task was completed. Design macros to verify outcomes and avoid repeating irreversible actions without checking their state.

## Contributing and license

Issues, reproducible bug reports, documentation improvements, and pull requests are welcome in the [SequentMacroEngine repository](https://github.com/JortCorp/SequentMacroEngine). For GUI issues, include your Windows version, Python version, window mode, DPI scaling, exact reproduction steps, and relevant traceback (without sensitive information).

Sequent is licensed under the **GNU General Public License v3.0 (GPL-3.0)**. See [LICENSE](LICENSE).
