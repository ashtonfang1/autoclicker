# Autoclicker

A tiny macOS mouse autoclicker with a custom start/stop hotkey, two modes
(Autoclick and Hold), and a small on-screen interface.

Everything is one Python file plus a `setup.py` that packages it into a real
double-clickable `.app` bundle with `py2app`, so you can drop it in
`/Applications` and open it like any other app — the global hotkey and mouse
control start automatically.

## Features

- **Custom start/stop hotkey.** Click **Set Hotkey**, press any key (a letter
  like `y`, a function key like `F6`, or the default backtick `` ` ``), and
  that single press toggles the action on/off.
- **Two modes** (pick one with the radio buttons):
  - **Autoclick** — toggle on/off with the hotkey; clicks repeatedly every
    `<delay>` ms.
  - **Hold** — toggle on/off with the hotkey; holds the mouse button down
    (LMB/RMB); a second press releases it. The Delay field is disabled in
    this mode.
- **Left / Right mouse button** selection.
- **Delay (ms)** is numbers-only.
- **macOS Accessibility trust check** on startup, with a **Grant Access**
  button that opens the right System Settings pane when permission is missing.

## Requirements

- macOS (tested on arm64 / Apple Silicon)
- Python 3.13
- `pyautogui`, `pynput`, `py2app`, `rubicon-objc`

## Install

```bash
pip install pyautogui pynput py2app rubicon-objc
```

## Run as a script

```bash
python3 autoclicker.py
```

## Build the `.app` bundle

```bash
./build.sh
```

This runs `python3 setup.py py2app` and produces `dist/Autoclicker.app`.
Copy it to `/Applications`:

```bash
cp -R dist/Autoclicker.app /Applications/
```

Then open it from Finder or Launchpad like any other app.

## macOS Accessibility permission

On macOS the global hotkey and the synthetic mouse clicks both need
Accessibility permission. The app checks this on startup; if it is missing it
shows a **Grant Access** button that opens System Settings → Privacy &
Security → Accessibility. Grant it once, then relaunch the app.

## Hotkey behavior

- One press of the hotkey starts the action; one press again stops it.
- Default hotkey is the backtick `` ` `` key.
- Click **Set Hotkey** → press a key → that key becomes the new hotkey.

## License

MIT