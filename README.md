# Sequent

**A visual desktop automation IDE for Windows — record, edit, debug, and replay workflows.**

Sequent combines a macro recorder, an editable action queue, screen recognition, conditional logic, and a visual debugger in one Python application. Start by recording a repetitive task, then refine it with variables, waits, branching, and position-aware actions instead of relying on a fixed sequence of clicks.

Built with Python's standard library and native Windows APIs. **No third-party Python packages are required.** An **optional experimental C DLL** is available for fully borderless, natively resizable windows.

> **Development status:** Sequent is actively developed and should be treated as experimental, especially native window chrome, OCR, image matching, and screen-based targeting. This README documents the **latest local development source** (including the post-v6.6 fixes and interface updates). The GitHub repository may not include these changes until they are committed. Test workflows in a safe application before using them with important data.

## Contents

- [Highlights](#highlights)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Find Color & Click](#find-color--click)
- [Search Selected Area](#search-selected-area)
- [Workspace and debugger](#workspace-and-debugger)
- [Actions and automation logic](#actions-and-automation-logic)
- [Advanced automation and data workflows](#advanced-automation-and-data-workflows)
- [Presets and macro chains](#presets-and-macro-chains)
- [Appearance and window management](#appearance-and-window-management)
- [Custom vector icons](#custom-vector-icons)
- [Hotkeys](#hotkeys)
- [Troubleshooting and limitations](#troubleshooting-and-limitations)
- [Contributing and license](#contributing-and-license)

## Highlights

### Record and replay

- Capture keyboard input, shortcuts, clicks, movement, dragging, and wheel scrolling using Windows APIs and low-level hooks.
- Watch events appear in the live Action Queue, then reorder your workflow by inserting, editing, or removing actions.
- Replay with pause/stop controls, loops, adjustable speed, window-relative coordinates, and optional Bézier mouse movement or timing variation.
- Run recorded-only and mixed conditional/dynamic workflows through the **same execution engine** rather than switching playback behavior based on the actions in the queue.
- Preserve mouse-drag button, duration, and window-relative endpoints; support basic cursor movement even when mouse-smoothing options are disabled.
- Use the compact Mini HUD when you want playback controls without the full workspace visible.

### Build visually

- Browse a searchable, categorized **Action Library** with dedicated properties editors.
- Use **breakpoints**, an execution playhead, step controls, and syntax-style line descriptions to debug actions.
- Preview mouse positions with an on-screen **Ghost Reticle**, including multi-action trajectory previews.
- Add OCR, pixel checks, variables, prompts, and control-flow actions to turn a recording into a more adaptive workflow.
- Combine saved presets into repeatable, multi-stage **Macro Chains**.
- Work with **native-drawn toolbar and action icons**, rather than relying on emoji glyphs that vary by font or Windows version.
- Keep **Record, Run, and Pause** accessible in a pinned controls section when the window is resized shorter; scroll to reach secondary transport settings.

### New in the latest development build

| Area | Improvements |
| --- | --- |
| Visual design | Custom Tkinter Canvas icon system for playback, navigation, toolbar actions, Action Library, search tools, and Mini HUD; hover, theme, and live-state updates |
| Compact layouts | **Record / Run / Pause** pinned near the top of the Controls dock; secondary transport, playhead, and reticle options in a scrollable section |
| Search Selected Area | Exact **Win32 virtual-screen coordinates** for the desktop selection, negative-monitor support, rectangle validation, and **Preview Actual Search Area** |
| Color targeting | Connected-color-region detection with an optimized scanner, RGB tolerance, selectable anchors, and freely offset click targets |
| Visual editor | Draggable Ghost Reticle **inside or outside** the match bounds, keyboard nudging, live detection feedback, and a shared preview/execution target calculation |
| Playback consistency | Single structured interpreter for regular playback, chains, and debugger steps; consistent branching and legacy action handling |
| Mouse & keyboard reliability | Preserved drag button/duration/window offsets; cursor settle checks; checked Win32 input; Unicode `SendInput` including surrogate pairs |
| Safer cancellation | Cancelling a disclaimer aborts the macro; stopped searches and waits do not continue into a late click; cancellation is not handled as a retryable error |
| Advanced workflows | Windows UI Automation selectors, image matching, smarter waits, conditions and loops, OCR-to-variable, CSV/XLSX actions, recovery rules, and triggers |
| Appearance & window chrome | **Dark / Light / Match System** modes, custom accent picker, Windows-managed resizing, and an optional experimental native borderless DLL |
| Stability | 64-bit handle/pointer signatures, shared `POINT` geometry, monitored screen bounds, and cleaner terminal interrupt handling |

These are **implemented features in the development source**, not a claim that every possible Windows application, monitor layout, or DPI configuration has been tested.

## Requirements

| Requirement | Details |
| --- | --- |
| Operating system | Windows 10 or Windows 11 |
| Python | Python 3.8+ with Tkinter; behavior can vary by Python/Tk and Windows version |
| Python dependencies | Standard library only (`ctypes`, `tkinter`, `threading`, `json`, `winsound`, etc.) |
| OCR | Windows OCR support and an installed OCR language pack |
| Smart UI Element | Windows PowerShell 5.1 and built-in Microsoft UI Automation assemblies |
| Spreadsheet / image actions | Built-in Python CSV/ZIP/XML tools and Windows GDI/GDI+; no third-party Python packages |
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

**No `pip install` step is needed** to run the Python application, use the vector icons, or read/write CSV and XLSX files. The native window DLL described below is optional; build it from its C source if you want to try the experimental borderless mode.

## Quick start

### 1. Record a test macro

1. Open a harmless target such as Notepad.
2. In **Playback**, click **Start Recording** or press `F7`.
3. Type a short phrase or perform a small sequence of actions.
4. Stop with `F8`.
5. Inspect the Action Queue and edit any unwanted actions.
6. Run with **Run Macro** or `F9`; stop with `F10`.

The **Record, Run, and Pause** controls stay pinned above the scrollable transport panel, and the buttons use custom-drawn icons that reflect their current state.

> **Privacy:** A macro recorder can capture sensitive typing and clipboard contents. Avoid recording passwords, tokens, private messages, or confidential data. Review exported presets before sharing them.

### 2. Modify an action

- **Double-click** an action in the queue to open its editor.
- Use the queue gutter, plus control, or right-click menu to insert actions from the Action Library.
- Set breakpoints and use the playhead/step controls to check what the action actually does.
- Save presets as JSON to reuse or chain later.

### 3. Add a smarter wait

Instead of assuming a page finishes loading after a fixed delay, add **Wait for Text**, **Wait Pixel**, or **Smart Wait & Retry**. Configure the expected result, screen area or selector, and timeout; then test how the macro behaves when the application is slow or the result is missing.

### 4. Preview a saved screen search

1. Add **Search Selected Area** from the Action Library, or double-click an existing one.
2. Use **Snip** and drag around the part of the desktop that should be searched.
3. Click **Preview Actual Search Area** to highlight the saved rectangle without running the macro.
4. Save the action only after confirming its location and dimensions.

See [Search Selected Area](#search-selected-area) for multi-monitor and DPI notes.

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

The connected-component scanner has been optimized for larger search regions while maintaining the same connected-object selection behavior. Searches still analyze a fixed rectangular area in **screen pixels**, and visually similar neighboring objects can affect the result.

## Search Selected Area

**Search Selected Area** performs OCR inside a rectangle you select on the desktop. It can match a specific phrase or use **Match Any Detected Text**, then switch to a chosen saved macro preset if a match is found.

### Capture and inspect the correct rectangle

1. Add or edit **Search Selected Area** in the Action Queue.
2. Click **Snip** and drag over the desired region. The desktop picker takes its saved points from native Windows `GetCursorPos`, not Tkinter's potentially DPI-virtualized mouse-event coordinates.
3. The saved area is stored as **`[x, y, width, height]`** in absolute **virtual-desktop pixels**. Dragging in either direction is supported; the picker clamps the selected area to the current virtual desktop.
4. Select **Preview Actual Search Area** to draw an outline of the **exact saved rectangle** and show its position and dimensions before running the macro.
5. Choose a target phrase (or **Match Any Detected Text**) and a saved preset to run on success; then save.

### Multi-monitor behavior and limits

- Monitors positioned **to the left or above** the primary screen can have **negative coordinates**; these are supported.
- Saved rectangles are checked against the **current** virtual desktop. If a monitor disconnects or the layout changes and the rectangle no longer fits, Sequent raises an error and asks you to reselect it rather than quietly shifting or truncating the search.
- Search regions are **absolute desktop locations**. They do **not** automatically follow a target window when that window moves; reselect the area after rearranging windows or monitors.
- The visible content of the selected rectangle still matters. Overlapping windows, scroll position, resolution, scaling, and OCR recognition quality can affect results.
- The **Preview Actual Search Area** outline is a diagnostic aid; confirm it before running any potentially destructive automation.

Example search action:

```json
{
  "type": "search_area",
  "text": "Ready",
  "area": [100, 120, 640, 320],
  "preset": "NextStage",
  "match_any": false
}
```

The runtime makes several short OCR attempts before deciding whether to switch presets; a failed match leaves the workflow to continue to the next action.

## Workspace and debugger

| Workspace | Purpose | Key tools |
| --- | --- | --- |
| **Playback** | Record, edit, run, and debug macros | Action Queue, breakpoints, playhead, scrubber, Controls, Import/Export |
| **Sequencer** | Build and execute macro chains | Stage Builder, chain timeline, loops, delays, Import/Export |
| **Settings** | Customize the application | Themes, accent colors, hotkeys, playback, sound, paths, onboarding |

**Guided Tour:** A 12-step, non-destructive walkthrough introduces the interface, recording, presets, Mini HUD, debugger, and sequencer. Open it from the sidebar or **Settings → Help & Onboarding**.

**Mini HUD:** A small floating control panel with custom-drawn controls for pause/resume, stop, and returning to the main workspace.

**Compact window behavior:** The **Record, Run, and Pause** buttons are pinned below the Controls header so they are not pushed out by the transport panel when the window is made shorter. Scroll the remainder of the Controls dock for step controls, playhead, loop settings, the active-action readout, and reticle settings. Extremely small sizes remain limited by Windows/Tk minimum geometry.

**Debugging:** Queue line numbers, click-to-select playhead navigation, breakpoints, step execution, and visual reticles help you inspect a macro rather than replay the entire sequence each time. Regular playback, macro chains, and single-step execution use the same action dispatcher to reduce discrepancies between workflows.

## Actions and automation logic

The queue displays readable *action summaries*, not executable Python code. Create them through recording or the Action Library.

| Action | Purpose |
| --- | --- |
| Mouse move / click / drag / scroll | Interact at a recorded position or follow a recorded mouse path; drag playback retains button, duration, and window-relative endpoints |
| Keyboard type / press / hotkey | Enter text and send key combinations; Unicode typing uses native `SendInput` for characters without a standard virtual key |
| Delay | Wait a specified amount of time |
| Wait Pixel | Wait for a pixel to match a chosen color |
| Wait for Text | Watch a screen region for OCR text to appear or disappear |
| **Search Selected Area** | OCR a native-screen-pixel rectangle; match a phrase or any detected text, then optionally switch presets |
| **Find Color & Click** | Locate a connected color region and move/click at an anchored, offset position |
| **Find Text & Click** | Locate a text bounding box with Windows OCR and move/click relative to its anchor |
| **Find Image & Click** | Match an image template to a desktop region and move/click relative to the detected bounds |
| **Smart UI Element & Click** | Find a Windows accessibility/UI Automation element by name, ID, or control type |
| Read Clipboard | Detect keywords and store a value in a variable |
| Request Input | Ask the user for a value during playback |
| Type Variable | Type the current contents of a variable |
| If Variable Condition | Compare a variable and jump to a label, switch presets, or run another saved file |
| **IF / ELSE / END IF** | Evaluate conditions and execute only the matching action block |
| **LOOP Start / LOOP End** | Repeat by count, while/until conditions, or each row of a loaded table |
| **Smart Wait & Retry** | Wait for text, windows, colors, images, UI controls, files, clipboard values, or variables |
| **OCR → Variable** | Capture OCR text or a regex match into a variable |
| **Load / Export CSV or Excel** | Import or export tables without pandas or openpyxl |
| **Update Table Cell** | Write or update a cell in the current table row |
| **Recovery Policy** | Choose stop/skip/fallback label, retry count/delay, and optional failure screenshot |
| **Auto-start Trigger** | Start a workflow on eligible file, window, clipboard, or authenticated local webhook events |
| **Advanced Key** | Press, hold, or release a selected virtual key |
| Label / Jump to Label | Mark a location and jump to it |
| Disclaimer / Warning | Display a user-facing message; cancelling **aborts** the macro |

OCR and visual recognition are best-effort. A failed match, timeout, or unexpected UI state may stop a workflow; avoid blindly repeating destructive actions. When **Stop** is requested during a target search or text wait, the interpreter does not proceed to a late click. A cancelled disclaimer is treated as user cancellation, not a retryable error.

### Playback engine and input consistency

- **One interpreter:** Recording-only macros, macros with conditions, chains, and the debugger execute through the same structured action pipeline. Adding a dynamic action no longer selects a different legacy playback loop.
- **Recorded drags:** Left/right button, recorded duration, and window-relative start/end positions are honored; the button is released after the drag even if execution fails.
- **Mouse movement:** Movement continues to execute with both Fluid Mouse and Human Bézier switched off. Bézier movement remains optional.
- **Text entry:** Native Windows `SendInput` handles UTF-16 scan codes, including surrogate pairs used by some emoji; the target application may still choose how it handles injected keystrokes.
- **Control flow:** Older variable-condition actions without an explicit target type default to their legacy **preset** behavior.
- **Click checks:** Mouse targeting uses cursor settle checks and reports Win32 injection failures instead of assuming every click was accepted.

## Advanced automation and data workflows

These tools are available from the **Action Library** and use native Windows facilities or Python's standard library—**no `pip install` commands**.

### UI Automation, images, and OCR

- **Smart UI Element & Click** searches Microsoft's accessibility tree by a window title substring, accessible name, Automation ID, and/or control type. It uses Windows PowerShell 5.1 with Microsoft's built-in UI Automation assemblies; it does **not** import the third-party `uiautomation` Python package. You can capture a selector from a recorded click where the target exposes accessibility metadata.
- **Find Image & Click** searches for an image template in a screen rectangle using Windows image APIs and Python image-matching logic, then reuses anchored move/click positioning. Image appearance, UI scaling, and confidence thresholds affect reliability.
- **OCR → Variable** reads a rectangle, saves the full text or a regex capture group, and exposes it to subsequent actions using `${variable_name}` interpolation.
- **Smart Wait & Retry** supports appearing/disappearing conditions with configurable timeout and polling interval. It can watch text, window titles, colors, images, UI elements, clipboard values, files, and variables.

### Conditions, loops, and recovery

Use **IF (Dynamic Condition)** / **ELSE** / **END IF** for branching and **LOOP Start** / **LOOP End** for counted, while/until, and table-row repetition. Conditions support **ALL** or **ANY** grouping. Place labels and jumps where you want explicit control flow.

A **Recovery Policy** can define retries, retry delays, skip/stop behavior, jumping to a fallback label, and an optional screenshot on final failure. Recovery is intended for ordinary action errors; **explicit user cancellation is never automatically retried**.

### CSV and Excel without dependencies

Use **Load CSV / Excel** to read a `.csv` or `.xlsx` file into a named table, **LOOP Start → for_each** to iterate rows, **Update Table Cell** to edit values, and **Export CSV / Excel** to save results. CSV uses `csv`; XLSX uses the built-in ZIP and XML tooling rather than `openpyxl` or pandas. Keep input files backed up when testing write operations.

### Auto-start triggers

An **Auto-start Trigger** must be placed as the **first action**. While Sequent is open and idle, it can react to a newly seen window, a changed clipboard state, or a new/modified matching file. Webhook mode accepts local requests on **`127.0.0.1`** with an `X-Sequent-Token` header. For webhook mode, set an unpredictable **`SEQUENT_WEBHOOK_TOKEN`** environment variable (at least 12 characters) before launching Sequent. The listener is intentionally loopback-only.

Triggers are edge-based and best-effort, not a substitute for a system scheduling service. Do not expose the local listener to an untrusted network.

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

The custom picker has a hue strip, saturation/brightness selection, HEX and RGB fields, before/after preview, and **Apply/Cancel**. Accent changes update supported interface elements, including the **MACRO WORKSPACE** branding and compatible vector icon controls. Appearance settings persist in `settings.json`; older `light_mode` settings are supported for compatibility.

### Custom window chrome (experimental)

Sequent uses custom-painted headers while preserving Windows-managed movement and **live resizing**. The latest source offers an **optional native borderless mode** implemented by `native/SequentChrome.c` so low-level resize messages are handled in compiled C rather than Python callbacks.

**Without the DLL**, Sequent still runs and uses the safer thin-frame resizing implementation. The fully borderless path is **not yet verified as a stable Windows release**.

To try the optional mode on **Windows x64**:

1. Install **Visual Studio Build Tools** with **Desktop development with C++** and a Windows SDK.
2. Ensure the repository includes the `native` directory from the source package.
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

## Custom vector icons

Sequent's updated interface uses a lightweight **`SequentIcon`** renderer built from Tkinter `Canvas` shapes instead of emoji, bitmap image files, third-party SVG packages, or special icon fonts.

- **Playback:** Record, Play, Pause/Resume, Stop, previous/next, and Step controls.
- **Navigation:** Playback, Sequencer, Settings, and Guided Tour icons.
- **Action Library:** Consistent symbols for screen recognition, timing, input, files, conditions, and other action families.
- **Toolbars and editors:** Import, Export, Search, Save, Refresh, Snip, preview, targeting, and related actions.
- **Mini HUD:** Matching visual controls in the compact floating view.
- **Interaction:** Colors update for themes, hover, and changing button states (for example, Record → Stop Recording and Pause → Resume).

The renderer uses native Tk Canvas geometry and needs **no icon downloads, assets folder, font installation, or Python dependencies**. Some ordinary UI text and non-button symbols may still use Unicode; this feature replaces the key **interactive iconography**, not every Unicode character in the application.

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
Update to the latest source. A previous window-motion implementation changed the shared `GetCursorPos` signature to an incompatible local structure type; the updated development source uses the common `POINT` type.

**The window has a border or resizing behaves unexpectedly**  
The compiled native borderless DLL is optional and experimental. Set `SEQUENT_WINDOW_MODE=legacy` for the safer thin-frame resizing mode, or `native` for normal Windows decorations. Include your Windows/Python versions and a screenshot when reporting a problem.

**Find Color & Click misses, flickers, or clicks the wrong point**  
Confirm the search rectangle, sampled HEX color, RGB tolerance, anchor, and offsets. Review the live bounding box and reticle. Avoid capturing multiple disconnected objects of the same color in one search region. For click delivery failures, verify that **When found** is not **Move Only** and that the target accepts simulated input.

**Search Selected Area scans the wrong location or stops after display changes**  
Open the action and click **Preview Actual Search Area**. Reselect the region after moving the target window, disconnecting a monitor, changing its arrangement, or altering display scaling. The saved rectangle uses absolute screen pixels and does not follow a particular window automatically. A rectangle outside the current desktop now produces a clear error rather than being silently moved.

**Record / Run / Pause disappear when the window is short**  
Use the latest Controls dock with pinned execution buttons. Secondary transport controls are in a scrollable panel beneath them; check the application's minimum practical size if even the pinned header cannot fit.

**Icons look different or do not update**  
Use the updated source with `SequentIcon`. It draws icons locally through Tkinter and does not rely on emoji support or installing a font. If a particular label still shows an older Unicode symbol, it may be decorative text rather than a converted interactive icon.

**OCR misses text**  
Check the selected screen area, scaling, target text, language pack, and whether the text is visible. Windows OCR can be slow or inaccurate.

**A macro is blocked by another application**  
Some elevated, protected, remote, or specialized applications ignore synthetic input. Sequent cannot bypass the Windows secure desktop or guarantee access across integrity levels.

**Smart UI Element cannot find a control**  
Some applications, custom-rendered controls, and elevated windows do not expose stable accessibility metadata. Check the captured selector, window-title filter, target privilege level, and whether the control appears in Windows' accessibility tree.

**XLSX / image actions behave unexpectedly**  
These features use Windows APIs and standard-library implementations; no third-party package is required, but real-world workbook and image formats vary. Validate results against a small sample before using production files.

**Closing from a terminal shows `KeyboardInterrupt`**  
Ctrl+C interrupts Tkinter's event loop. The current development source handles this more gracefully; use Sequent's normal window controls for ordinary shutdown.

**General limitations:** Sequent is desktop screen/input automation, not a universal application API. A successful visual match or Windows input injection does not guarantee that a task was completed. Design macros to verify outcomes and avoid repeating irreversible actions without checking their state.

## Contributing and license

Issues, reproducible bug reports, documentation improvements, and pull requests are welcome in the [SequentMacroEngine repository](https://github.com/JortCorp/SequentMacroEngine). For GUI issues, include your Windows version, Python version, window mode, DPI scaling, number/arrangement of monitors, exact reproduction steps, and relevant traceback (without sensitive information).

Sequent is licensed under the **GNU General Public License v3.0 (GPL-3.0)**. See [LICENSE](LICENSE).
