#!/usr/bin/env python3
"""
Autoclicker with a small on-screen interface (macOS / cross-platform).

Features
- Custom start/stop hotkey: click "Set Hotkey" then press any key
  (a letter like `y`, a function key like `F6`, or the default backtick),
  and that single press toggles the action on/off. (macOS)
- Two modes (pick one):
    * Autoclick : toggle on/off with the hotkey -> clicks repeatedly every
      <delay> ms (numbers only). Default 1 click every 100 ms.
    * Hold      : toggle on/off with the hotkey -> holds the mouse button
      down (LMB/RMB); a second press releases it. The Delay field is
      disabled in this mode.
- Left mouse button (LMB) and right mouse button (RMB) both supported.

macOS note:
On macOS the global hotkey and the synthetic mouse clicks both need
Accessibility permission. The app checks this on startup; if missing it shows
a "Grant Access" button that opens System Settings -> Privacy & Security ->
Accessibility. Grant it once, then relaunch the app.
"""

import platform
import subprocess
import sys
import threading
import time
import tkinter as tk

from tkinter import ttk

IS_MACOS = platform.system() == "Darwin"
BACKTICK = chr(96)  # the ` character

try:
    import pyautogui
except ImportError:
    pyautogui = None

try:
    from pynput import keyboard
except ImportError:
    keyboard = None


def _is_backtick(key):
    """True when `key` represents the US-layout backtick / grave key."""
    if getattr(key, "char", None) == BACKTICK:
        return True
    try:
        return key == keyboard.Key.backtick
    except (AttributeError, NameError):
        return False


def _key_repr(key):
    """Human-readable name for a captured key, e.g. `y`, `F6`, `Esc`."""
    if getattr(key, "char", None) is not None:
        return key.char
    name = str(key)
    if name.startswith("Key."):
        name = name[4:]
    # Turn e.g. "f6" -> "F6", "caps_lock" -> "Caps_lock"
    return name.title() if len(name) <= 3 else name


def is_ax_trusted():
    """macOS: is this process allowed to observe/control via Accessibility?"""
    if not IS_MACOS:
        return True
    try:
        from ApplicationServices import AXIsProcessTrusted
        return bool(AXIsProcessTrusted())
    except Exception:
        return True  # don't hard-block if the check can't run


def open_accessibility_prefs():
    """Open the macOS Accessibility privacy pane so the user can grant access."""
    urls = [
        "x-apple.systempreferences:com.apple.security.privacy.accessibility",
        "x-apple.systempreferences:",
    ]
    for url in urls:
        try:
            subprocess.run(["open", url], check=False)
            return
        except Exception:
            continue


class AutoClicker:
    def __init__(self):
        self.running = False
        self.delay_ms = 100          # default: 1 click per 100 ms
        self.click_type = "lmb"       # default button
        self.mode = "autoclick"      # "autoclick" | "hold"
        self.thread = None
        self._stop_event = threading.Event()

        # Hotkey state
        self.hotkey = BACKTICK        # current start/stop hotkey (default)
        self.hotkey_repr = BACKTICK
        self.capturing = False        # True while waiting for a keypress
        self._listener = None         # global hotkey listener
        self._capture_listener = None  # one-shot key capture listener

        self._build_gui()
        self._refresh_trust_state()
        self._start_hotkey_listener()

    # ------------------------------------------------------------------
    # GUI
    # ------------------------------------------------------------------
    def _build_gui(self):
        self.root = tk.Tk()
        self.root.title("Autoclicker")
        self.root.geometry("290x225")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        # --- Delay input (numbers only) ---
        tk.Label(self.root, text="Delay (ms):").grid(
            row=0, column=0, padx=10, pady=8, sticky="w"
        )
        vcmd = self.root.register(self._validate_number)
        self.delay_entry = tk.Entry(
            self.root, width=12, validate="key",
            validatecommand=(vcmd, "%P"),
        )
        self.delay_entry.insert(0, "100")
        self.delay_entry.grid(row=0, column=1, padx=10, pady=8, sticky="w")
        self.delay_entry.bind("<Return>", lambda e: self._apply_delay())
        self.delay_entry.bind("<FocusOut>", lambda e: self._apply_delay())

        # --- Button selector (LMB / RMB) ---
        tk.Label(self.root, text="Button:").grid(
            row=1, column=0, padx=10, pady=2, sticky="w"
        )
        self.button_var = tk.StringVar(value="lmb")
        self.button_var.trace_add("write", self._on_button_change)
        tk.Radiobutton(
            self.root, text="Left (LMB)", variable=self.button_var, value="lmb",
        ).grid(row=1, column=1, sticky="w", padx=10)
        tk.Radiobutton(
            self.root, text="Right (RMB)", variable=self.button_var, value="rmb",
        ).grid(row=2, column=1, sticky="w", padx=10)

        # --- Mode selector (Autoclick / Hold) ---
        tk.Label(self.root, text="Mode:").grid(
            row=3, column=0, padx=10, pady=2, sticky="w"
        )
        self.mode_var = tk.StringVar(value="autoclick")
        self.mode_var.trace_add("write", self._on_mode_change)
        tk.Radiobutton(
            self.root, text="Autoclick", variable=self.mode_var,
            value="autoclick",
        ).grid(row=3, column=1, sticky="w", padx=10)
        tk.Radiobutton(
            self.root, text="Hold", variable=self.mode_var,
            value="hold",
        ).grid(row=4, column=1, sticky="w", padx=10)

        # --- Status ---
        self.status_label = tk.Label(
            self.root, text="Stopped", fg="red", font=("Segoe UI", 10, "bold")
        )
        self.status_label.grid(row=5, column=0, columnspan=2, pady=6)

        # --- Set Hotkey button (replaces the Start/Stop button) ---
        self.set_hotkey_btn = tk.Button(
            self.root, text="Set Hotkey", command=self._set_hotkey_clicked,
        )
        self.set_hotkey_btn.grid(row=6, column=0, columnspan=2, pady=2)

        # --- Hint ---
        self.hint_label = tk.Label(
            self.root, text="Press %s to start/stop" % BACKTICK, fg="gray"
        )
        self.hint_label.grid(row=7, column=0, columnspan=2)

        # Optional "Grant Access" button (shown only when not trusted)
        self.grant_btn = tk.Button(
            self.root, text="Grant Access", command=open_accessibility_prefs,
            fg="blue",
        )

        self._refresh_hint()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _validate_number(self, proposed):
        """Entry validator: allow digits only (or empty while editing)."""
        return proposed.isdigit() or proposed == ""

    def _on_button_change(self, *_):
        if self.running:
            self._stop_action()
        self.click_type = self.button_var.get()
        self._update_delay_state()

    def _on_mode_change(self, *_):
        if self.running:
            self._stop_action()
        self.mode = self.mode_var.get()
        self._refresh_hint()
        self._update_delay_state()

    def _refresh_hint(self):
        rep = self.hotkey_repr
        if self.mode == "autoclick":
            hint = "Press %s to start/stop" % rep
        else:
            hint = "Press %s to hold / release" % rep
        self.hint_label.config(text=hint)

    def _update_delay_state(self):
        """Gray out the delay field while in Hold mode (delay is irrelevant)."""
        if self.mode == "hold":
            self.delay_entry.config(state="disabled",
                                    disabledbackground="#f0f0f0")
        else:
            self.delay_entry.config(state="normal")

    def _apply_delay(self):
        try:
            val = int(self.delay_entry.get())
            if val < 1:
                val = 1
            self.delay_ms = val
        except ValueError:
            self.delay_entry.delete(0, tk.END)
            self.delay_entry.insert(0, str(self.delay_ms))

    # ------------------------------------------------------------------
    # Trust / Accessibility
    # ------------------------------------------------------------------
    def _refresh_trust_state(self):
        trusted = is_ax_trusted()
        if not trusted:
            self.grant_btn.grid(
                row=7, column=0, columnspan=2, pady=(2, 0)
            )
            self.status_label.config(
                text="Grant Accessibility access", fg="darkorange"
            )
            self.hint_label.config(text="Click 'Grant Access' above", fg="gray")

    def _hide_grant_btn(self):
        try:
            self.grant_btn.grid_remove()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Clicking logic
    # ------------------------------------------------------------------
    def _button_name(self):
        return "right" if self.click_type == "rmb" else "left"

    def _click_once(self):
        if pyautogui is None:
            return
        pyautogui.click(button=self._button_name())

    def _do_mouse_down(self):
        if pyautogui is None:
            return
        pyautogui.mouseDown(button=self._button_name())

    def _do_mouse_up(self):
        if pyautogui is None:
            return
        pyautogui.mouseUp(button=self._button_name())

    def _run_loop(self):
        self._stop_event.clear()
        interval = self.delay_ms / 1000.0
        while not self._stop_event.is_set() and self.running:
            self._click_once()
            self._stop_event.wait(interval)

    def _start_action(self):
        """Begin the current mode's action (autoclick loop or button hold)."""
        if self.running:
            return
        if not is_ax_trusted():
            self._flash_not_trusted()
            return
        self._hide_grant_btn()
        self.running = True
        if self.mode == "hold":
            self._do_mouse_down()
            self.status_label.config(text="Holding", fg="green")
            self.thread = None
        else:  # autoclick
            self._stop_event.clear()
            self.status_label.config(text="Running  (clicking)", fg="green")
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()

    def _stop_action(self):
        """Stop the current mode's action."""
        self.running = False
        if self.mode == "hold":
            self._do_mouse_up()
        else:
            self._stop_event.set()
            if self.thread is not None:
                self.thread.join(timeout=1.0)
                self.thread = None
        self.status_label.config(text="Stopped", fg="red")

    def toggle_action(self):
        if self.running:
            self._stop_action()
        else:
            self._start_action()

    # ------------------------------------------------------------------
    # Hotkey (custom key capture + global listener)
    # ------------------------------------------------------------------
    def _matches_hotkey(self, key):
        try:
            if self.hotkey == BACKTICK:
                return _is_backtick(key)
            if isinstance(self.hotkey, keyboard.Key):
                return key == self.hotkey
            return getattr(key, "char", None) == self.hotkey
        except Exception:
            return False

    def _set_hotkey_clicked(self):
        """Enter capture mode: the next keypress becomes the start/stop hotkey."""
        if self.capturing:
            return
        if self.running:
            self._stop_action()
        self.capturing = True
        self._hide_grant_btn()
        self.status_label.config(text="Press a key...", fg="blue")
        self.set_hotkey_btn.config(text="Cancel")
        self._stop_hotkey_listener()
        self._start_capture_listener()

    def _start_capture_listener(self):
        if keyboard is None:
            return

        def on_press(key):
            if self.capturing:
                self.root.after(0, lambda: self._on_hotkey_captured(key))

        self._capture_listener = keyboard.Listener(on_press=on_press)
        self._capture_listener.daemon = True
        self._capture_listener.start()

    def _on_hotkey_captured(self, key):
        if not self.capturing:
            return
        if _is_backtick(key):
            rep = BACKTICK
            self.hotkey = BACKTICK
        elif getattr(key, "char", None) is not None:
            rep = key.char
            self.hotkey = key.char
        else:
            rep = _key_repr(key)
            self.hotkey = key  # store the Key object (e.g. F6)

        self.capturing = False
        self._stop_capture_listener()
        self.hotkey_repr = rep
        self._start_hotkey_listener()
        self.set_hotkey_btn.config(text="Set Hotkey")
        self.status_label.config(text="Hotkey set: %s" % rep, fg="green")
        self._refresh_hint()
        self.root.after(1500, lambda: self.status_label.config(
            text="Stopped", fg="red"
        ))

    def _stop_capture_listener(self):
        listener = getattr(self, "_capture_listener", None)
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
            self._capture_listener = None

    def _on_hotkey_press(self):
        """One press toggles the action; one press again stops it."""
        if not is_ax_trusted():
            self._flash_not_trusted()
            return
        self.toggle_action()

    def _flash_not_trusted(self):
        self.status_label.config(text="Accessibility access required!", fg="red")
        self.root.after(1500, lambda: self.status_label.config(
            text="Stopped", fg="red"
        ))

    def _stop_hotkey_listener(self):
        listener = getattr(self, "_listener", None)
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
            self._listener = None

    def _start_hotkey_listener(self):
        """Listen for the configured hotkey globally (works unfocused)."""
        if keyboard is None:
            return

        def on_press(key):
            try:
                if self.capturing:
                    return  # let the capture listener handle it
                if self._matches_hotkey(key):
                    self.root.after(0, self._on_hotkey_press)
            except Exception:
                pass

        self._listener = keyboard.Listener(on_press=on_press)
        self._listener.daemon = True
        self._listener.start()

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------
    def _on_close(self):
        self._stop_action()
        self._stop_hotkey_listener()
        self._stop_capture_listener()
        self.root.quit()


if __name__ == "__main__":
    if pyautogui is None:
        print("pyautogui not installed. Run:  pip install pyautogui pynput")
        sys.exit(1)
    AutoClicker().root.mainloop()
