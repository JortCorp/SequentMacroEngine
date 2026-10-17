# Sequent

**A Windows macro recorder, visual debugger, and automation workflow engine built with Python and native Windows APIs.**

Sequent helps turn repetitive desktop work into editable, repeatable workflows. Record keyboard and mouse actions, inspect the resulting action queue, add conditions and visual checks, then replay a macro or combine saved macros into multi-stage sequences.

Designed for people who want more control than a basic click recorder—without having to write an automation script from scratch.

> **Project status:** Sequent is under active development. Some features described here are from the latest development build and may not yet be present in the version published on GitHub. Test automations with harmless data before relying on them for important work.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Workspace guide](#workspace-guide)
- [Action reference](#action-reference)
- [Presets and macro chains](#presets-and-macro-chains)
- [Hotkeys](#hotkeys)
- [Settings and customization](#settings-and-customization)
- [Troubleshooting and limitations](#troubleshooting-and-limitations)
- [Contributing and license](#contributing-and-license)

## Features

### Recording and playback

- **Windows-native input handling:** Captures keyboard events, mouse clicks, drags, and scroll-wheel input using Win32 APIs and low-level hooks.
- **Live action queue:** Watch recorded actions appear in order, then edit or remove them before playback.
- **Window-relative coordinates:** Save client-area coordinates alongside window information to help macros adapt when an application window moves.
- **Keyboard shortcuts:** Record modifier combinations, key presses, typing, and familiar editing commands.
- **Playback controls:** Start, stop, pause, repeat, and change playback speed or timing behavior.
- **Optional humanized motion:** Bézier-based cursor movement and timing variation for more natural-looking playback.

### Visual Action Queue and debugger

- **Syntax-style action viewer:** Readable, color-coded action summaries and line numbers.
- **Searchable Action Library:** Browse actions by category, find them by name, and configure them in a dedicated properties panel.
- **Breakpoints:** Click the queue gutter to pause before a particular action.
- **Playhead and step controls:** Navigate through the queue and execute individual steps while troubleshooting.
- **Coordinate nudging:** Adjust a selected position by one pixel with `Alt + Arrow`, or five pixels with `Alt + Shift + Arrow`.
- **Ghost reticles:** Preview click and movement positions on the desktop; multi-selection can display a trail of locations.

### Recognition, conditions, and variables

- **Wait for Pixel:** Poll a screen coordinate for an expected hexadecimal color, with a configurable timeout.
- **Wait for Text:** Use Windows OCR to wait until text **appears** or **disappears** in a chosen screen region; a failed check can stop playback.
- **Search Selected Area:** Run OCR on a selected region and optionally branch to a preset when a match is found.
- **Clipboard checks:** Detect keywords in copied text and store values in runtime variables.
- **User input prompts:** Ask for a value while a macro runs.
- **Variable typing:** Enter a stored value into the active application.
- **Conditional flow:** Compare variables and jump to labels, switch presets, or launch another macro file.
- **Labels and GOTO:** Create reusable checkpoints and conditional paths within a recorded sequence.

> OCR uses the Windows OCR engine and is not infallible. Its timing depends on the screen capture and recognition process; a configured timeout may not be a strict wall-clock deadline.

### Macro Chain Sequencer

- Build a **Macro Chain** from existing saved presets.
- Configure a loop count and post-stage delay for each stage.
- Reorder or remove stages, then run the chain as a sequence.
- Import and export chain configurations in `.chain.json` format.

### Interface and onboarding

- **Three workspaces:** Playback, Sequencer, and Settings, accessed from a collapsible navigation sidebar.
- **Docked panels:** A large Action Queue beside pinned Controls; a Macro Chain timeline beside a pinned Stage Builder.
- **Custom borderless window:** Sequent-styled window controls and resize grips.
- **Entertain Me:** Optional animated sidebar, panel, and workspace transitions; turn it off for immediate navigation.
- **Dark and light themes:** Switch themes in Settings. Recent development builds coalesce rapid theme-toggle requests to reduce repeated UI rebuilds.
- **Guided Tour:** A 12-step, non-destructive walkthrough that highlights real controls, including Import, saved presets, Mini HUD, recording, debugging, and chain building. Start it from the sidebar or Help & Onboarding in Settings.
- **Mini HUD:** Compact floating status and playback controls, including Pause, Stop, and Expand, while the main workspace is hidden.
- **Audio feedback:** Built-in Classic, Subtle, and Sci-Fi cues plus configurable custom WAV sounds.
- **Configurable global hotkeys:** Change the default recording and playback hotkeys in Settings.

## Requirements

| Requirement | Details |
| --- | --- |
| OS | Windows 10 or Windows 11 |
| Python | Python 3.8 or later; compatibility can vary by Python/Tk version |
| Libraries | Python standard library, including `ctypes`, `tkinter`, `json`, `threading`, `subprocess`, and `winsound` |
| OCR | Windows OCR support and an installed OCR language pack |

Sequent does not require third-party `pip` packages for its current Python implementation. OCR uses Windows' built-in `Windows.Media.Ocr` through a PowerShell/WinRT bridge.

**Permissions:** Many desktop workflows can run without administrator rights, but Sequent cannot guarantee control over elevated windows, secured desktops, or applications that block simulated input. Follow your organization's automation and information-security policies.

## Installation

1. Install Python for Windows and ensure **Tkinter** is available.
2. Clone the repository:

   ```powershell
   git clone https://github.com/JortCorp/SequentMacroEngine.git
   cd SequentMacroEngine
   ```

3. Launch Sequent:

   ```powershell
   py Sequent.py
   ```

   Alternatively, run `python Sequent.py` if `python` is the configured command on your machine.

4. Open **Settings** to choose a preset directory, set hotkeys, and customize the interface.

No separate installation or build step is required when running the Python source.

## Quick start

### 1. Learn the interface

Open the **sidebar → Guided Tour**. It points to the controls used throughout Sequent and does **not** record input, run macros, or alter your current preset.

### 2. Record a harmless macro

1. Open a safe test application such as Notepad.
2. In Sequent's **Playback** workspace, click **Start Recording** or press `F7`.
3. Perform a short sequence, such as typing a line in Notepad.
4. Stop recording with `F8`.
5. Review the actions in the **Action Queue** before playback.
6. Click **Run Macro** or press `F9` to replay. Press `F10` to stop playback.

**Tip:** Avoid recording passwords, private messages, or confidential business information. Review a recorded macro before exporting or sharing it.

### 3. Edit or add an action

- **Double-click** an existing action to edit it.
- **Right-click** a queue line or use the line gutter to open the **Action Library**.
- Search or select an action category, configure its properties, and save it.
- Use the playhead, breakpoints, and step controls to test individual actions.

### 4. Wait for a visual result

For an interface with unpredictable loading times, add a **Wait for Text** action:

1. Set the text to look for (for example, `Complete`).
2. Choose **Appears** or **Disappears**.
3. Select the relevant screen region.
4. Set a timeout and save the action.

This is generally safer than assuming an application finishes after a fixed delay. Visual checks still require testing; OCR can miss text, and the target application can change its layout.

### 5. Save or load a macro

Use **Export** above the Action Queue to save the current actions as a `.json` file. Use **Import** to load a file, or select an existing preset from the toolbar's **Preset** menu.

**Important:** Importing or selecting a preset **replaces the current queue**; export unfinished work first. Loading a preset does not execute it automatically.

## Workspace guide

| Workspace | Purpose | Main components |
| --- | --- | --- |
| **Playback** | Record, edit, inspect, and execute macros | Action Queue, debugger/playhead, Controls dock, Import/Export |
| **Sequencer** | Chain saved macros together | Macro Chain timeline, Stage Builder, chain Import/Export |
| **Settings** | Customize Sequent and access help | Appearance, playback options, sound, directories, hotkeys, Guided Tour |

### Mini HUD

Choose **Mini HUD** in the top toolbar to hide the main workspace and show a small floating control bar. Use its controls to pause/resume playback, stop playback, or **Expand** back into the application. The Guided Tour explains the HUD without activating it.

## Action reference

The Action Queue shows *readable representations* of actions. These are not Python commands to paste into a script; use Sequent's recorder and Action Library to create or edit them.

| Action shown in queue | Purpose |
| --- | --- |
| `mouse.move(x, y)` | Move the cursor to a recorded position. |
| `mouse.left_click(x, y)` | Click at a recorded position (right-click is also supported). |
| `mouse.drag((x1, y1) ➔ (x2, y2))` | Drag between two positions. |
| `mouse.scroll(delta, x, y)` | Scroll at a recorded location. |
| `keyboard.type('text')` | Type recorded text. |
| `keyboard.press('Enter')` | Press a named key. |
| `keyboard.hotkey('Ctrl + C')` | Execute a key combination. |
| `system.delay(1.50s)` | Wait for a specified duration. |
| `condition.wait_pixel(x, y, color)` | Wait for a pixel to match a color. |
| `ocr.wait_text('Complete', appears)` | Wait for text to appear or disappear. |
| `ocr.search_area('target')` | Search a region using OCR and optionally branch on a match. |
| `clipboard.find('keyword' ➔ …)` | Check clipboard text and set a variable on a match. |
| `user.prompt('Message' ➔ $variable)` | Ask the user for a runtime value. |
| `keyboard.type_var('$variable')` | Type a stored variable's value. |
| `flow.label('checkpoint_1')` | Define a named point in the queue. |
| `flow.goto('checkpoint_1')` | Jump to a named point. |
| `flow.if('var' == 'value' ➔ …)` | Branch based on a variable comparison. |
| `ui.disclaimer('Message')` | Display a warning or message during execution. |

The Action Library groups available additions under **Timing & Waiting**, **Screen & Recognition**, **Variables & Inputs**, **Logic & Flow**, and **User Interaction**.

## Presets and macro chains

### Preset files (`.json`)

Preset files contain a JSON list of action objects. By default, Sequent looks for presets in a `presets` directory relative to the **current working directory**; you can change that directory in Settings.

For example, a minimal delay-only preset can look like this:

```json
[
  {"type": "delay", "seconds": 1.5}
]
```

Import macros from other folders with **Import**, or use the top toolbar's Preset selector for files in the configured preset directory.

### Chain files (`.chain.json`)

In **Sequencer**, choose saved presets in **Stage Builder**, then configure how often they run and how long to wait afterward. Chain configurations are imported and exported as `.chain.json` files. The default chain folder is `chains` relative to the current working directory.

A chain configuration contains entries like:

```json
[
  {"preset": "ExamplePreset", "loops": 2, "delay_after": 1.0}
]
```

The referenced preset must be available in the configured preset directory when the chain runs.

## Hotkeys

| Default | Action |
| --- | --- |
| `F7` | Start recording |
| `F8` | Stop recording |
| `F9` | Start playback |
| `F10` | Stop playback |

Customize hotkeys (including modifier combinations) under **Settings → Global Hotkeys**. The Playback controls also expose pause/resume and step actions, independently of the configurable global hotkeys.

## Settings and customization

- **Appearance:** Dark/light theme; **Entertain Me** to enable or disable interface animations.
- **Playback:** Timing controls, playback speed, movement behavior, and optional delay variation.
- **Audio:** Built-in sound themes and supported custom `.wav` paths.
- **Paths:** Preset directory and related file locations.
- **Hotkeys:** Rebind start/stop controls.
- **Help & Onboarding:** Relaunch the Guided Tour.

If you need to isolate a problem involving Sequent's custom borderless window, you can launch with Windows' native window decorations for troubleshooting:

```powershell
$env:SEQUENT_WINDOW_MODE = "native"
py Sequent.py
```

This is a diagnostic option; the normal default is Sequent's custom borderless window.

## Troubleshooting and limitations

**OCR doesn't find expected text**

Check the selected screen region, text spelling, display scaling, and installed Windows OCR language pack. Try a larger region and test again. OCR depends on what is actually visible on screen and may take longer than the selected polling interval.

**A macro clicks the wrong location**

Ensure the target application is in the expected state. Window-relative coordinates can help when windows move, but changing layouts, minimized windows, scaling, or missing target windows can still cause incorrect clicks. Test with a harmless workflow first.

**A macro stops after a visual check**

A `Wait for Text` or `Wait Pixel` timeout is treated as a failed condition in the current development build; inspect the failing action and its target before retrying. Don't automatically repeat a Save/Submit operation unless you know it is safe to do so.

**Theme switching or animated panels render incorrectly**

Try disabling **Entertain Me**, return to the default theme, and restart Sequent. Current development work includes safeguards for delayed Tkinter callbacks and rapid theme switching, but rendering may vary with your Windows and Python/Tk version. Include your Python version and a traceback when reporting the issue.

**Global hotkeys or inputs don't work in a particular program**

Some elevated, protected, remote, or specialized applications may not accept simulated input from Sequent. Follow application and organizational policies; don't assume macros will work universally.

**Known limitations:** Sequent is a screen/input automation tool, not an application API integration. Text recognition, screen coordinates, and window matching can be sensitive to UI changes. It does not automatically understand every possible application error or guarantee transaction correctness.

## Contributing and license

Issues, bug reports, documentation improvements, and pull requests are welcome through the [GitHub repository](https://github.com/JortCorp/SequentMacroEngine). When reporting a problem, include reproduction steps, your Windows and Python versions, and relevant traceback details. Remove any confidential application data from screenshots, logs, or sample presets.

Sequent is distributed under the **GNU General Public License v3.0 (GPL-3.0)**. See [LICENSE](LICENSE) for the full license text.
