#!/usr/bin/env python3
"""
lmb_toggle.py — simple LMB (left mouse button) autoclicker.

- Toggles left-clicking ON/OFF with the ` (backtick) key.
- Default: 1 click every 100 ms. Type a new delay (ms) and press Enter to change.
- Cross-platform (macOS / Windows) via pyautogui.

On macOS, allow the terminal/python app Accessibility permission
(System Preferences > Security & Privacy > Privacy > Accessibility)
so the global ` hotkey and clicks work.
"""

import sys
import threading
import time
import tkinter as tk

try:
    import pyautogui
except ImportError:
    pyautogui = None

try:
    from pynput import keyboard
except ImportError:
    keyboard = None


class LMBToggle:
    def __init__(self):
        self.running = False
        self.delay_ms = 100
        self.thread = None

        self.root = tk.Tk()
        self.root.title("LMB Toggle")
        self.root.geometry("250x140")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        tk.Label(self.root, text="Delay (ms):").grid(
            row=0, column=0, padx=8, pady=10, sticky="w"
        )
        self.entry = tk.Entry(self.root, width=12)
        self.entry.insert(0, "100")
        self.entry.grid(row=0, column=1, padx=8, pady=10)
        self.entry.focus_set()
        self.entry.bind("<Return>", lambda e: self.apply_delay())

        self.status = tk.Label(
            self.root, text="Stopped", fg="red", font=("Segoe UI", 10, "bold")
        )
        self.status.grid(row=1, column=0, columnspan=2, pady=4)

        self.btn = tk.Button(self.root, text="Start (press `)", command=self.toggle)
        self.btn.grid(row=2, column=0, columnspan=2, pady=4)

        tk.Label(self.root, text="Press ` to start/stop", fg="gray").grid(
            row=3, column=0, columnspan=2
        )

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def apply_delay(self):
        try:
            v = int(self.entry.get())
            self.delay_ms = max(1, v)
        except ValueError:
            self.entry.delete(0, tk.END)
            self.entry.insert(0, str(self.delay_ms))

    def click_lmb(self):
        if pyautogui is not None:
            pyautogui.click()

    def run_loop(self):
        interval = self.delay_ms / 1000.0
        while self.running:
            self.click_lmb()
            time.sleep(interval)

    def toggle(self):
        self.running = not self.running
        if self.running:
            self.status.config(text="Running (LMB)", fg="green")
            self.btn.config(text="Stop (press `)")
            self.thread = threading.Thread(target=self.run_loop, daemon=True)
            self.thread.start()
        else:
            self.status.config(text="Stopped", fg="red")
            self.btn.config(text="Start (press `)")

    def on_close(self):
        self.running = False
        ctypes_cleanup()
        self.root.quit()

    def start_global_listener(self):
        """Listen for the ` key globally so toggle works unfocused."""
        if keyboard is None:
            return

        def on_press(key):
            try:
                is_backtick = (
                    key == keyboard.Key.backtick
                    or getattr(key, "char", None) == "`"
                )
                if is_backtick:
                    self.root.after(0, self.toggle)
            except Exception:
                pass

        listener = keyboard.Listener(on_press=on_press)
        listener.daemon = True
        listener.start()

    def run(self):
        self.start_global_listener()
        self.root.mainloop()


def ctypes_cleanup():
    pass


if __name__ == "__main__":
    if pyautogui is None:
        print("pyautogui not installed. Run:  pip install pyautogui pynput")
        sys.exit(1)
    LMBToggle().run()