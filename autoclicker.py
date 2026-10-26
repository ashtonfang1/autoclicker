#!/usr/bin/env python3
"""A small macOS mouse and keyboard automation app."""

import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

IS_MACOS = platform.system() == "Darwin"
APP_NAME = "macOS autoclicker"
APP_BUNDLE_ID = "com.ashton.autoclicker"
LOG = logging.getLogger(APP_NAME)
BACKTICK = chr(96)
DELAY_UNIT_MS = {"ms": 1, "s": 1_000, "min": 60_000, "hr": 3_600_000}
MIN_DELAY_MS = 20
MIN_INTERVAL_SECONDS = MIN_DELAY_MS / 1_000


def app_bundle_path(executable=None):
    """Return the enclosing .app bundle for a packaged executable, if any."""
    executable_path = Path(executable or sys.executable).resolve()
    return next(
        (parent for parent in executable_path.parents if parent.suffix == ".app"),
        None,
    )


def delay_to_ms(value, unit):
    """Convert a displayed delay to milliseconds without rounding away fractions."""
    return float(value) * DELAY_UNIT_MS[unit]


def write_json_atomically(path, data):
    """Write JSON beside its destination and atomically replace the old file."""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(data, temporary_file, indent=2)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def preserve_invalid_json(path):
    """Keep invalid app data for recovery without loading it on every launch."""
    backup_path = path.with_name(f"{path.name}.corrupt")
    if backup_path.exists():
        backup_path = path.with_name(f"{path.name}.{time.time_ns()}.corrupt")
    path.replace(backup_path)
    return backup_path


try:
    import pyautogui
except ImportError:
    pyautogui = None

if pyautogui is not None:
    pyautogui.PAUSE = 0
    pyautogui.MINIMUM_DURATION = 0
    if hasattr(pyautogui, "DARWIN_CATCH_UP_TIME"):
        pyautogui.DARWIN_CATCH_UP_TIME = 0

try:
    from pynput import keyboard, mouse
except ImportError:
    keyboard = None
    mouse = None

if IS_MACOS and keyboard is not None:
    from pynput._util.darwin import ListenerMixin as _DarwinListenerMixin
    from pynput._util.darwin import keycode_context as _darwin_keycode_context
else:
    _DarwinListenerMixin = None
    _darwin_keycode_context = None


def _is_backtick(key):
    if getattr(key, "char", None) == BACKTICK:
        return True
    try:
        return keyboard is not None and key == keyboard.Key.backtick
    except AttributeError:
        return False


def _key_repr(key):
    char = getattr(key, "char", None)
    if char is not None:
        return char
    name = str(key)
    if getattr(key, "name", None) is None:
        name = _special_key_name(key) or name
    if name.startswith("Key."):
        name = name[4:]
    return name.title() if len(name) <= 3 else name


def _special_key_name(key):
    if keyboard is None:
        return None
    vk = getattr(key, "vk", None)
    if vk is None:
        return None
    for special_key in keyboard.Key:
        value = special_key.value
        if getattr(value, "vk", None) == vk:
            return special_key.name
    return None


def _serialize_key(key):
    char = getattr(key, "char", None)
    if char is not None:
        return {"char": char}
    name = getattr(key, "name", None)
    if name is None:
        name = _special_key_name(key)
    if name is not None:
        return {"special": name}
    return None


def _deserialize_key(value):
    if keyboard is None:
        raise RuntimeError("pynput is required to play keyboard recordings.")
    if "char" in value:
        return keyboard.KeyCode.from_char(value["char"])
    key = getattr(keyboard.Key, value["special"], None)
    if key is None:
        raise ValueError("Recording contains an unsupported key.")
    return key


def is_ax_trusted():
    """Return whether macOS Accessibility permission has been granted."""
    if not IS_MACOS:
        return True
    try:
        from ApplicationServices import AXIsProcessTrusted
    except ImportError:
        LOG.exception("Accessibility preflight API is unavailable")
        return False
    try:
        trusted = bool(AXIsProcessTrusted())
    except Exception:
        LOG.exception("Accessibility preflight check failed")
        return False
    LOG.debug("Accessibility preflight result: trusted=%s", trusted)
    return trusted


def is_input_monitoring_trusted():
    """Return whether macOS Input Monitoring permission has been granted."""
    if not IS_MACOS:
        return True
    try:
        core_graphics = ctypes.CDLL(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        preflight = core_graphics.CGPreflightListenEventAccess
        preflight.restype = ctypes.c_bool
        trusted = bool(preflight())
    except (AttributeError, OSError):
        LOG.exception("Input Monitoring preflight check failed")
        return False
    LOG.debug("Input Monitoring preflight result: trusted=%s", trusted)
    return trusted


def request_input_monitoring_access():
    """Request Input Monitoring access after an explicit user action."""
    if not IS_MACOS:
        LOG.error("Input Monitoring access requests are only supported on macOS")
        return False
    try:
        core_graphics = ctypes.CDLL(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        request = core_graphics.CGRequestListenEventAccess
        request.restype = ctypes.c_bool
        granted = bool(request())
    except (AttributeError, OSError):
        LOG.exception("CGRequestListenEventAccess failed")
        return False
    LOG.info("CGRequestListenEventAccess returned: granted=%s", granted)
    return granted


def _open_system_settings_pane(pane):
    subprocess.run(
        [
            "open",
            f"x-apple.systempreferences:com.apple.preference.security?{pane}",
        ],
        check=True,
    )


def open_accessibility_prefs():
    _open_system_settings_pane("Privacy_Accessibility")


def open_input_monitoring_prefs():
    _open_system_settings_pane("Privacy_ListenEvent")


def configure_logging():
    log_dir = (
        Path.home() / "Library" / "Logs"
        if IS_MACOS
        else Path.home() / ".local" / "state"
    )
    console_stream = sys.stderr if sys.stderr is not None else sys.__stderr__
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "macOS autoclicker.log",
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError as error:
        for handler in LOG.handlers[:]:
            LOG.removeHandler(handler)
            handler.close()
        LOG.handlers.clear()
        if console_stream is not None:
            console_handler = logging.StreamHandler(console_stream)
            console_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s"
                )
            )
            LOG.addHandler(console_handler)
        LOG.setLevel(logging.INFO)
        LOG.propagate = False
        LOG.error("Could not configure application log file in %s: %s", log_dir, error)
    else:
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s"
            )
        )
        for handler in LOG.handlers[:]:
            LOG.removeHandler(handler)
            handler.close()
        LOG.handlers.clear()
        LOG.setLevel(logging.DEBUG)
        LOG.propagate = False
        LOG.addHandler(file_handler)
        if console_stream is not None:
            console_handler = logging.StreamHandler(console_stream)
            console_handler.setLevel(logging.INFO)
            console_handler.setFormatter(
                logging.Formatter("[%(levelname)s] %(message)s")
            )
            LOG.addHandler(console_handler)
    LOG.info(
        "=== Application Started: bundle_id=%s executable=%s bundle_path=%s ===",
        APP_BUNDLE_ID,
        sys.executable,
        app_bundle_path(),
    )


def open_guide():
    resources_dir = Path(__file__).resolve().parent
    guide_paths = (
        resources_dir / "Resources" / "README.md",
        resources_dir / "README.md",
    )
    for guide_path in guide_paths:
        if guide_path.is_file():
            subprocess.run(["open", str(guide_path)], check=True)
            return
    raise FileNotFoundError("Could not find the app's README guide.")


class AutoClicker:
    def __init__(self):
        self.running = False
        self.delay_ms = MIN_DELAY_MS
        self._delay_unit = "ms"
        self.click_type = "left"
        self.mode = "autoclick"
        self.hotkey = None
        self.hotkey_repr = ""
        self.capturing = False
        self._listener = None
        self._capture_listener = None
        self._record_keyboard_listener = None
        self._record_mouse_listener = None
        self._pressed_controls = set()
        self._stop_chord_detected = False
        self._record_armed = False
        self._record_filter_at_start = None
        self._closed = False
        self._record_lock = threading.Lock()
        self._record_events = []
        self._record_started_at = None
        self._is_recording = False
        self._stop_event = threading.Event()
        self._playback_thread = None
        self._playing_recording = False
        self.thread = None
        self._action_mouse = False
        self._action_keyboard = False
        self._action_mode = "autoclick"
        self._action_keys = []
        self._keyboard_keys = []
        self._keyboard_controller = None
        self.recordings = []
        self._capture_kind = None
        self._capture_result_pending = False
        self._delay_placeholder_active = True
        self._keyboard_layout_context = None
        self._selected_recording = None
        self._recordings_dir = (
            Path.home() / "Library" / "Application Support" / APP_NAME / "Recordings"
            if IS_MACOS
            else Path.home() / ".local" / "share" / APP_NAME / "Recordings"
        )
        self._recordings_order_path = self._recordings_dir / ".order.json"
        self._recording_drag_start = None
        self._recording_drag_selection = ()
        self._recording_dragging = False
        self._recording_drop_index = None
        self._was_trusted = None
        self._last_input_monitoring_trusted = None
        self._were_listener_permissions_trusted = None
        self._trust_refresh_after_id = None
        self._app_bundle_path = app_bundle_path()

        if _darwin_keycode_context is not None:
            with _darwin_keycode_context() as context:
                self._keyboard_layout_context = context

        self.root = tk.Tk()
        self.root.bind("<FocusIn>", self._schedule_trust_state_refresh, add="+")
        self.root.bind_all("<Button-1>", self._on_window_click, add="+")
        self._build_gui()
        self._refresh_trust_state()
        self._refresh_recordings()
        self.root.after(150, self._clear_widget_focus)

    def _build_gui(self):
        self.root.title(APP_NAME)
        self.root.geometry("640x620")
        self.root.minsize(600, 580)
        self.root.attributes("-topmost", False)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)
        controls_page = ttk.Frame(self.notebook)
        recordings = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(controls_page, text="Controls")
        self.notebook.add(recordings, text="Recordings")

        self.controls_canvas = tk.Canvas(controls_page, highlightthickness=0)
        controls_scrollbar = ttk.Scrollbar(
            controls_page, orient="vertical", command=self.controls_canvas.yview
        )
        self.controls_canvas.configure(yscrollcommand=controls_scrollbar.set)
        self.controls_canvas.pack(side="left", fill="both", expand=True)
        controls_scrollbar.pack(side="right", fill="y")
        controls = ttk.Frame(self.controls_canvas, padding=12)
        self._controls_canvas_window = self.controls_canvas.create_window(
            (0, 0), window=controls, anchor="nw"
        )
        controls.bind("<Configure>", self._update_controls_scroll_region)
        self.controls_canvas.bind("<Configure>", self._resize_controls_content)

        status_style = ttk.Style(self.root)
        status_style.configure("Status.TLabel", font=("TkDefaultFont", 14, "bold"))
        self.status_label = ttk.Label(
            controls,
            text="Stopped",
            style="Status.TLabel",
            foreground="#e57373",
        )
        self.status_label.pack(pady=(0, 8))

        delay_row = ttk.Frame(controls)
        delay_row.pack(fill="x", pady=3)
        ttk.Label(delay_row, text="Delay").pack(side="left", padx=(0, 8))
        entry_style = ttk.Style(self.root)
        entry_style.configure(
            "App.TEntry",
            foreground="white",
            fieldbackground="#666666",
            insertcolor="white",
            padding=(6, 3),
        )
        entry_style.configure(
            "Placeholder.TEntry",
            foreground="#999999",
            fieldbackground="#666666",
            insertcolor="white",
            padding=(6, 3),
        )
        entry_style.configure("KeyChip.TButton", padding=(3, 1))
        entry_style.configure("Compact.TButton", padding=(1, 0))
        self.delay_entry = ttk.Entry(
            delay_row,
            width=10,
            style="Placeholder.TEntry",
            validate="none",
            validatecommand=(self.root.register(self._validate_delay), "%P"),
        )
        self.delay_entry.insert(0, "e.g. 10.0")
        self.delay_entry.pack(side="left", padx=(0, 6))
        self.delay_unit_var = tk.StringVar(value="ms")
        self.delay_unit_menu = ttk.Combobox(
            delay_row,
            textvariable=self.delay_unit_var,
            values=("ms", "s", "min", "hr"),
            state="readonly",
            width=5,
        )
        self.delay_unit_menu.pack(side="left")
        self.delay_unit_menu.bind("<<ComboboxSelected>>", self._on_delay_unit_change)
        self.delay_entry.bind("<Return>", lambda _: self._apply_delay())
        self.delay_entry.bind("<FocusOut>", self._on_delay_focus_out)
        self.delay_entry.bind("<FocusIn>", self._on_delay_focus_in)
        self.delay_entry.bind("<FocusOut>", self._clear_entry_selection, add="+")

        self.mouse_var = tk.BooleanVar(value=True)
        self.keyboard_var = tk.BooleanVar(value=False)
        actions = ttk.LabelFrame(controls, text="Actions", padding=8)
        actions.pack(fill="x", pady=6)
        mouse_row = ttk.Frame(actions)
        mouse_row.pack(fill="x", pady=2)
        self.mouse_check = ttk.Checkbutton(
            mouse_row, variable=self.mouse_var, command=self._update_input_state
        )
        self.mouse_check.pack(side="left", padx=(0, 5))
        ttk.Label(mouse_row, text="Mouse").pack(side="left")
        self.button_var = tk.StringVar(value="left")
        self.left_radio = ttk.Radiobutton(
            mouse_row, text="Left", variable=self.button_var, value="left",
            command=self._on_button_change,
        )
        self.left_radio.pack(side="left", padx=(18, 6))
        self.right_radio = ttk.Radiobutton(
            mouse_row, text="Right", variable=self.button_var, value="right",
            command=self._on_button_change,
        )
        self.right_radio.pack(side="left")

        keyboard_row = ttk.Frame(actions)
        keyboard_row.pack(fill="x", pady=2)
        self.keyboard_check = ttk.Checkbutton(
            keyboard_row, variable=self.keyboard_var,
            command=self._update_input_state,
        )
        self.keyboard_check.pack(side="left", padx=(0, 5))
        ttk.Label(keyboard_row, text="Keyboard").pack(side="left")
        self.keyboard_keys_frame = ttk.Frame(actions)
        self.keyboard_keys_frame.pack(fill="x", padx=(25, 0), pady=(2, 5))
        self.keyboard_keys_frame.bind("<Configure>", self._layout_keyboard_keys)
        self.keyboard_add_btn = ttk.Button(
            keyboard_row, text="+", width=2, style="Compact.TButton",
            command=self._add_keyboard_key,
        )
        self.keyboard_add_btn.pack(side="left", padx=(10, 0))

        mode_row = ttk.Frame(actions)
        mode_row.pack(fill="x", pady=2)
        ttk.Label(mode_row, text="Mode").pack(side="left", padx=(25, 12))
        self.mode_var = tk.StringVar(value="autoclick")
        self.click_radio = ttk.Radiobutton(
            mode_row, text="Autoclick", variable=self.mode_var,
            value="autoclick", command=self._on_mode_change,
        )
        self.click_radio.pack(side="left", padx=(0, 8))
        self.hold_radio = ttk.Radiobutton(
            mode_row, text="Hold", variable=self.mode_var,
            value="hold", command=self._on_mode_change,
        )
        self.hold_radio.pack(side="left")

        recording_frame = ttk.LabelFrame(controls, text="Recording", padding=8)
        recording_frame.pack(fill="x", pady=6)
        record_row = ttk.Frame(recording_frame)
        record_row.pack(fill="x")
        self.record_var = tk.BooleanVar(value=False)
        self.record_check = ttk.Checkbutton(
            record_row, variable=self.record_var,
            command=self._record_checkbox_changed,
        )
        self.record_check.pack(side="left", padx=(0, 5))
        ttk.Label(record_row, text="Record:").pack(side="left")
        self.record_filter = tk.StringVar(value="all")
        self.record_filter_buttons = []
        for text, value in (
            ("All inputs", "all"),
            ("Only mouse", "mouse"),
            ("Only keyboard", "keyboard"),
        ):
            button = ttk.Radiobutton(
                record_row,
                text=text,
                value=value,
                variable=self.record_filter,
                command=self._record_filter_changed,
            )
            button.pack(side="left", padx=(12, 0))
            self.record_filter_buttons.append(button)
        self.record_hint = ttk.Label(
            recording_frame,
            text="Ctrl + (custom key) to start or stop recording.",
            foreground="gray",
        )
        self.record_hint.pack(anchor="w", padx=(24, 0), pady=(5, 0))

        hotkey_frame = ttk.Frame(controls)
        hotkey_frame.pack(fill="x", pady=(8, 2))
        ttk.Label(
            hotkey_frame, text="Custom Start/stop key:", foreground="gray"
        ).pack(pady=(0, 4))
        hotkey_control_row = ttk.Frame(hotkey_frame)
        hotkey_control_row.pack()
        self.set_hotkey_btn = ttk.Button(
            hotkey_control_row,
            text="(click to add key)",
            command=self._set_hotkey_clicked,
        )
        self.set_hotkey_btn.pack(side="left")
        self.hotkey_error = ttk.Label(
            hotkey_control_row, text="", foreground="#e57373"
        )
        self.hotkey_error.pack(side="left", padx=(8, 0))
        self.guide_btn = ttk.Button(
            controls, text="Guide", command=self._open_guide
        )
        self.guide_btn.pack(pady=(4, 0))
        self.request_input_monitoring_btn = ttk.Button(
            controls,
            text="Request Input Monitoring",
            command=self._request_input_monitoring,
        )
        self.request_input_monitoring_btn.pack(pady=(4, 0))

        self._build_recordings_tab(recordings)
        self._update_input_state()
        self._update_record_hint()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_recordings_tab(self, parent):
        self.recordings_list = tk.Listbox(
            parent, activestyle="none", selectmode=tk.EXTENDED
        )
        self.recordings_list.pack(fill="both", expand=True)
        self.recordings_list.bind("<<ListboxSelect>>", self._on_recording_selected)
        self.recordings_list.bind("<Button-1>", self._on_recording_list_click)
        self.recordings_list.bind("<Command-Button-1>", self._toggle_recording_selection)
        self.recordings_list.bind("<Command-a>", self._select_all_recordings)
        self.recordings_list.bind("<Command-A>", self._select_all_recordings)
        self.recordings_list.bind("<ButtonPress-1>", self._recording_drag_press, add="+")
        self.recordings_list.bind("<B1-Motion>", self._recording_drag_motion, add="+")
        self.recordings_list.bind("<ButtonRelease-1>", self._recording_drag_release, add="+")
        self._recording_drop_indicator = tk.Frame(
            self.recordings_list.master, height=2, background="#0A84FF"
        )
        self._recording_drop_indicator.bind(
            "<Button-1>", self._on_recording_indicator_click
        )
        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Play selected", command=self._play_selected).pack(
            side="left"
        )
        ttk.Button(buttons, text="Delete selected", command=self._delete_selected).pack(
            side="left", padx=8
        )
        ttk.Button(buttons, text="Refresh", command=self._refresh_recordings).pack(
            side="right"
        )
        playback_options = ttk.Frame(parent)
        playback_options.pack(fill="x", pady=(8, 0))
        ttk.Label(playback_options, text="Play").pack(side="left")
        self._playback_count_placeholder_active = True
        self.playback_count_var = tk.StringVar(master=self.root, value="e.g. 10")
        validate_count = (self.root.register(self._validate_playback_count), "%P")
        self.playback_count_entry = ttk.Entry(
            playback_options,
            textvariable=self.playback_count_var,
            width=9,
            validate="none",
            validatecommand=validate_count,
            style="Placeholder.TEntry",
        )
        self.playback_count_entry.pack(side="left", padx=(6, 6))
        self.playback_count_entry.bind("<FocusIn>", self._on_playback_count_focus_in)
        self.playback_count_entry.bind("<FocusOut>", self._on_playback_count_focus_out)
        ttk.Label(playback_options, text="times").pack(side="left")
        self.play_forever_var = tk.BooleanVar(master=self.root, value=False)
        ttk.Checkbutton(
            playback_options,
            text="Play forever until stopped",
            variable=self.play_forever_var,
            command=self._update_playback_count_state,
        ).pack(side="left", padx=(16, 0))
        self.recordings_notice = ttk.Label(
            parent, text="", foreground="#e57373", wraplength=560
        )
        self.recordings_notice.pack(anchor="w", pady=(4, 0))
        self._update_playback_count_state()

    @staticmethod
    def _validate_playback_count(proposed):
        return proposed == "" or all(
            "0" <= character <= "9" for character in proposed
        )

    def _update_playback_count_state(self):
        self.playback_count_entry.configure(
            state="disabled" if self.play_forever_var.get() else "normal"
        )

    def _on_playback_count_focus_in(self, _=None):
        if self._playback_count_placeholder_active:
            self.playback_count_var.set("")
            self._playback_count_placeholder_active = False
        else:
            self.playback_count_entry.selection_range(0, tk.END)
        self.playback_count_entry.configure(
            style="App.TEntry",
            validate="key",
        )

    def _on_playback_count_focus_out(self, _=None):
        if self.playback_count_var.get():
            return
        self._playback_count_placeholder_active = True
        self.playback_count_entry.configure(
            style="Placeholder.TEntry",
            validate="none",
        )
        self.playback_count_var.set("e.g. 10")

    def _update_controls_scroll_region(self, event):
        self.controls_canvas.configure(
            scrollregion=(0, 0, event.width, event.height)
        )

    def _resize_controls_content(self, event):
        self.controls_canvas.itemconfigure(
            self._controls_canvas_window, width=event.width
        )

    def _validate_delay(self, proposed):
        if proposed in ("", "."):
            return True
        try:
            return math.isfinite(float(proposed)) and float(proposed) >= 0
        except ValueError:
            return False

    def _delay_entry_value(self, unit=None):
        if self._delay_placeholder_active:
            return self.delay_ms
        text = self.delay_entry.get().strip()
        if not text:
            return self.delay_ms
        try:
            value = float(text)
        except ValueError:
            return self.delay_ms
        if not math.isfinite(value) or value < 0:
            return self.delay_ms
        return delay_to_ms(value, unit or self._delay_unit)

    def _apply_delay_value(self, delay_ms, display_unit):
        if delay_ms < MIN_DELAY_MS:
            LOG.info(
                "Configured delay was below 50 CPS threshold; "
                "auto-corrected to 20 ms (50 CPS max)."
            )
            self.delay_ms = MIN_DELAY_MS
            self._delay_unit = display_unit if display_unit in ("ms", "s") else "ms"
        else:
            self.delay_ms = max(MIN_DELAY_MS, round(delay_ms))
            self._delay_unit = display_unit
        self.delay_unit_var.set(self._delay_unit)
        if not self._delay_placeholder_active:
            self._show_delay_value()

    def _apply_delay(self):
        self._apply_delay_value(
            self._delay_entry_value(),
            self._delay_unit,
        )

    def _show_delay_value(self):
        self._delay_placeholder_active = False
        self.delay_entry.configure(style="App.TEntry")
        self.delay_entry.configure(validate="key")
        self.delay_entry.delete(0, tk.END)
        factor = DELAY_UNIT_MS[self._delay_unit]
        value = self.delay_ms / factor
        self.delay_entry.insert(0, f"{value:.12g}")

    def _on_delay_focus_in(self, _=None):
        if self._delay_placeholder_active:
            self.delay_entry.delete(0, tk.END)
            self._delay_placeholder_active = False
        else:
            self.delay_entry.selection_range(0, tk.END)
        self.delay_entry.configure(validate="key")
        self.delay_entry.configure(style="App.TEntry")

    def _on_delay_focus_out(self, _=None):
        if self.delay_entry.get().strip():
            self._apply_delay()
        else:
            self._delay_placeholder_active = True
            self.delay_entry.configure(style="Placeholder.TEntry")
            self.delay_entry.configure(validate="none")
            self.delay_entry.delete(0, tk.END)
            self.delay_entry.insert(0, "e.g. 10.0")

    def _on_delay_unit_change(self, _=None):
        selected_unit = self.delay_unit_var.get()
        delay_ms = self._delay_entry_value(self._delay_unit)
        self._apply_delay_value(delay_ms, selected_unit)

    def _set_status(self, text):
        color = "#2e8b57" if text in ("Started", "Recording") else "#e57373"
        self.status_label.configure(text=text, foreground=color)

    def _update_record_hint(self):
        key = self.hotkey_repr or "custom key"
        self.record_hint.configure(text=f"Ctrl + ({key}) to start/stop recording.")

    def _render_keyboard_keys(self):
        for child in self.keyboard_keys_frame.winfo_children():
            child.destroy()
        enabled = self.keyboard_var.get()
        for index, key_data in enumerate(self._keyboard_keys):
            ttk.Button(
                self.keyboard_keys_frame,
                text=f"{self._key_display(key_data)} ×",
                style="KeyChip.TButton",
                width=6,
                command=lambda selected=index: self._remove_keyboard_key(selected),
                state="normal" if enabled else "disabled",
            )
        self._layout_keyboard_keys()

    def _layout_keyboard_keys(self, event=None):
        available_width = (
            event.width if event is not None
            else self.keyboard_keys_frame.winfo_width()
        )
        if available_width <= 1:
            return

        buttons = self.keyboard_keys_frame.winfo_children()
        if not buttons:
            return

        chip_width = max(button.winfo_reqwidth() for button in buttons)
        column_count = max(1, (available_width + 5) // (chip_width + 5))
        for index, button in enumerate(buttons):
            button.grid(
                row=index // column_count,
                column=index % column_count,
                sticky="w",
                padx=(0, 5),
            )

    def _key_display(self, key_data):
        if "char" in key_data:
            return key_data["char"]
        return key_data["special"].replace("_", " ").title()

    def _add_keyboard_key(self):
        if self.capturing:
            return
        if self.running:
            self._stop_action()
        self._begin_key_capture("keyboard")

    def _remove_keyboard_key(self, index):
        if self.running:
            self._stop_action()
        del self._keyboard_keys[index]
        self._render_keyboard_keys()

    def _begin_key_capture(self, kind):
        if keyboard is None:
            messagebox.showerror(APP_NAME, "pynput is required to capture keys.")
            return
        self._refresh_trust_state()
        if not self._has_listener_permissions():
            self._show_permissions_required()
            return
        self.capturing = True
        self._capture_kind = kind
        self._capture_result_pending = False
        if kind == "keyboard":
            self.keyboard_add_btn.configure(text="...")
            self.keyboard_add_btn.focus_set()
        else:
            self.set_hotkey_btn.configure(text="...")
            self.set_hotkey_btn.focus_set()
        self._stop_hotkey_listener()
        self._capture_listener = self._new_keyboard_listener(
            on_press=self._capture_key,
        )
        self._capture_listener.daemon = True
        self._capture_listener.start()

    def _new_keyboard_listener(self, **callbacks):
        if not IS_MACOS or _DarwinListenerMixin is None:
            return keyboard.Listener(**callbacks)

        context = self._keyboard_layout_context

        class MainThreadContextListener(keyboard.Listener):
            def _run(self):
                self._context = context
                try:
                    _DarwinListenerMixin._run(self)
                finally:
                    self._context = None
                if not self.IS_TRUSTED:
                    LOG.error(
                        "macOS denied the keyboard event tap; Accessibility "
                        "permission is missing for %s",
                        APP_BUNDLE_ID,
                    )

        return MainThreadContextListener(**callbacks)

    def _clear_widget_focus(self):
        if self.root.winfo_exists():
            self.root.focus_set()

    def _on_window_click(self, event):
        widget = event.widget
        widget_class = widget.winfo_class() if widget is not None else None
        if widget_class in {"Entry", "TEntry"}:
            return
        self._clear_entry_selection()
        if widget_class not in {"TCombobox", "Listbox"}:
            self.root.after_idle(self._clear_widget_focus)

    def _clear_entry_selection(self, _=None):
        try:
            self.delay_entry.selection_clear()
        except tk.TclError:
            pass

    def _on_button_change(self):
        if self.running:
            self._stop_action()
        self.click_type = self.button_var.get()

    def _on_mode_change(self):
        if self.running:
            self._stop_action()
        self.mode = self.mode_var.get()
        self._update_input_state()

    def _update_input_state(self):
        if self.running and (
            self.mouse_var.get() != self._action_mouse
            or self.keyboard_var.get() != self._action_keyboard
        ):
            self._stop_action()
        mouse_enabled = self.mouse_var.get()
        hold_mode = self.mode_var.get() == "hold"
        mouse_state = "normal" if mouse_enabled else "disabled"
        self.left_radio.configure(state=mouse_state)
        self.right_radio.configure(state=mouse_state)
        self.click_radio.configure(state=mouse_state)
        self.hold_radio.configure(state=mouse_state)
        delay_disabled = hold_mode and mouse_enabled
        self.delay_entry.configure(state="disabled" if delay_disabled else "normal")
        self.delay_unit_menu.configure(state="disabled" if delay_disabled else "readonly")
        letters_enabled = self.keyboard_var.get()
        self.keyboard_add_btn.configure(
            state="normal" if letters_enabled else "disabled"
        )
        self._render_keyboard_keys()
        filters_enabled = self.record_var.get() and not self._is_recording
        for button in self.record_filter_buttons:
            button.configure(state="normal" if filters_enabled else "disabled")

    def _refresh_trust_state(self):
        trusted = is_ax_trusted()
        input_monitoring_trusted = is_input_monitoring_trusted()
        listener_permissions_trusted = trusted and input_monitoring_trusted
        previous_trusted = self._was_trusted
        permissions = (trusted, input_monitoring_trusted)
        if permissions != (
            self._was_trusted,
            self._last_input_monitoring_trusted,
        ):
            LOG.info(
                "Permission status changed: bundle_id=%s accessibility=%s "
                "input_monitoring=%s executable=%s",
                APP_BUNDLE_ID,
                trusted,
                input_monitoring_trusted,
                sys.executable,
            )
            if not trusted:
                LOG.warning(
                    "Accessibility permission is missing. Grant it to "
                    "%s in System Settings > Privacy & Security > Accessibility.",
                    APP_NAME,
                )
            if not input_monitoring_trusted:
                LOG.warning(
                    "Input Monitoring permission is missing. Grant it to "
                    "%s in System Settings > Privacy & Security > Input Monitoring.",
                    APP_NAME,
                )
        self._was_trusted = trusted
        self._last_input_monitoring_trusted = input_monitoring_trusted
        self._were_listener_permissions_trusted = listener_permissions_trusted
        if not trusted and (self.running or self._playing_recording):
            self._stop_action()
        if not listener_permissions_trusted:
            self._stop_hotkey_listener()
            if self.capturing:
                capture_kind = self._capture_kind
                self.capturing = False
                self._capture_kind = None
                self._capture_result_pending = False
                self._stop_capture_listener()
                if capture_kind == "keyboard":
                    self.keyboard_add_btn.configure(text="+")
                elif capture_kind == "hotkey":
                    self.set_hotkey_btn.configure(
                        text=self.hotkey_repr or "(click to add key)"
                    )
            if self._is_recording:
                self._stop_recording()
        if trusted and previous_trusted is False and not (
            self.running or self._is_recording or self._playing_recording
        ):
            self._set_status("Stopped")

    def _schedule_trust_state_refresh(self, _=None):
        if self._trust_refresh_after_id is not None:
            self.root.after_cancel(self._trust_refresh_after_id)
        self._trust_refresh_after_id = self.root.after(
            150, self._refresh_trust_state_after_focus
        )

    def _refresh_trust_state_after_focus(self):
        self._trust_refresh_after_id = None
        self._refresh_trust_state()

    def _has_listener_permissions(self):
        return bool(self._were_listener_permissions_trusted)

    def _show_permissions_required(self):
        accessibility = bool(self._was_trusted)
        input_monitoring = bool(self._last_input_monitoring_trusted)
        LOG.warning(
            "Required permissions missing: accessibility=%s input_monitoring=%s",
            accessibility,
            input_monitoring,
        )
        if not accessibility:
            monitoring_status = "granted" if input_monitoring else "not granted"
            if messagebox.askyesno(
                "Permission Required",
                "macOS currently reports Accessibility permission as not granted. "
                f"Input Monitoring is {monitoring_status}. Open Accessibility "
                "settings?",
            ):
                try:
                    open_accessibility_prefs()
                except (OSError, subprocess.CalledProcessError) as error:
                    LOG.exception("Could not open Accessibility settings")
                    messagebox.showerror(
                        APP_NAME, f"Could not open Accessibility settings:\n{error}"
                    )
            return
        if not input_monitoring:
            if messagebox.askyesno(
                "Permission Required",
                "Accessibility is granted, but macOS reports Input Monitoring "
                "as not granted. Key capture requires both permissions. Open "
                "Input Monitoring settings?",
            ):
                try:
                    open_input_monitoring_prefs()
                except (OSError, subprocess.CalledProcessError) as error:
                    LOG.exception("Could not open Input Monitoring settings")
                    messagebox.showerror(
                        APP_NAME,
                        f"Could not open Input Monitoring settings:\n{error}",
                    )

    def _show_accessibility_required(self):
        LOG.warning("Accessibility is required for this action")
        if not messagebox.askyesno(
            "Permission Required",
            "Accessibility permission is required. Open Accessibility settings?",
        ):
            return
        try:
            open_accessibility_prefs()
        except (OSError, subprocess.CalledProcessError) as error:
            LOG.exception("Could not open Accessibility settings")
            messagebox.showerror(
                APP_NAME, f"Could not open Accessibility settings:\n{error}"
            )

    def _open_guide(self):
        try:
            open_guide()
        except (OSError, subprocess.CalledProcessError) as error:
            messagebox.showerror(APP_NAME, f"Could not open the guide:\n{error}")

    def _request_input_monitoring(self):
        granted = request_input_monitoring_access()
        LOG.info(
            "Input Monitoring request button clicked: granted=%s bundle_id=%s",
            granted,
            APP_BUNDLE_ID,
        )
        try:
            open_input_monitoring_prefs()
        except (OSError, subprocess.CalledProcessError) as error:
            LOG.exception("Could not open Input Monitoring settings")
            messagebox.showerror(
                APP_NAME,
                f"Could not open Input Monitoring settings:\n{error}",
            )
        messagebox.showinfo(
            APP_NAME,
            "Input Monitoring access was requested. macOS currently reports "
            f"it as {'granted' if granted else 'not granted'}. If you turn it "
            "On in System Settings, quit and reopen the app before testing "
            "keyboard capture.",
        )

    # Global start/stop key
    def _matches_hotkey(self, key):
        if self.hotkey is None or keyboard is None:
            return False
        if self.hotkey == BACKTICK:
            return _is_backtick(key)
        if isinstance(self.hotkey, keyboard.Key):
            return key == self.hotkey
        return getattr(key, "char", None) == self.hotkey

    def _set_hotkey_clicked(self):
        if self.capturing:
            return
        self.hotkey_error.configure(text="")
        if self.running:
            self._stop_action()
        if self.record_var.get():
            self._stop_recording()
        self._begin_key_capture("hotkey")

    def _capture_key(self, key):
        if self.capturing and not self._capture_result_pending:
            self._capture_result_pending = True
            self.root.after(0, lambda captured=key: self._on_hotkey_captured(captured))

    def _on_hotkey_captured(self, key):
        if not self.capturing:
            return
        capture_kind = self._capture_kind
        self.capturing = False
        self._capture_kind = None
        self._capture_result_pending = False
        self._stop_capture_listener()
        if capture_kind == "keyboard":
            if key != keyboard.Key.esc:
                encoded_key = _serialize_key(key)
                if encoded_key is not None:
                    self._keyboard_keys.append(encoded_key)
                    self._render_keyboard_keys()
            self.keyboard_add_btn.configure(text="+")
            self._start_hotkey_listener()
            return
        if self._is_control_key(key):
            self.hotkey_error.configure(text="Cannot be Ctrl")
            self.set_hotkey_btn.configure(
                text=self.hotkey_repr or "(click to add key)"
            )
            self._start_hotkey_listener()
            return
        self.hotkey_error.configure(text="")
        if key == keyboard.Key.esc:
            self.hotkey = None
            self.hotkey_repr = ""
            self.set_hotkey_btn.configure(text="(click to add key)")
        elif _is_backtick(key):
            self.hotkey = BACKTICK
            self.hotkey_repr = BACKTICK
            self.set_hotkey_btn.configure(text=BACKTICK)
        else:
            char = getattr(key, "char", None)
            self.hotkey = char if char is not None else key
            self.hotkey_repr = _key_repr(key)
            self.set_hotkey_btn.configure(text=self.hotkey_repr)
        self._set_status("Stopped")
        self._update_record_hint()
        self._start_hotkey_listener()

    def _start_hotkey_listener(self):
        if keyboard is None:
            LOG.error("Cannot start hotkey listener: pynput keyboard is unavailable")
            return
        if self.hotkey is None:
            return
        if not self._were_listener_permissions_trusted:
            LOG.warning("Cannot start hotkey listener: required permissions missing")
            return
        if self._listener is not None:
            if self._listener.is_alive():
                return
            self._listener = None

        def on_press(key):
            if self.capturing:
                return
            if self._is_control_key(key):
                self._pressed_controls.add(key)
                return
            if self._matches_record_chord(key):
                if not self._is_recording:
                    self.root.after(0, self._toggle_armed_recording)
                elif self._record_filter_at_start == "mouse":
                    self.root.after(0, self._stop_recording)
                return
            if self._matches_hotkey(key) and not self._is_recording:
                if self._record_armed:
                    return
                self.root.after(0, self.toggle_action)

        self._listener = self._new_keyboard_listener(
            on_press=on_press, on_release=self._on_hotkey_release
        )
        self._listener.daemon = True
        self._listener.start()

    def _stop_hotkey_listener(self):
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def _stop_capture_listener(self):
        if self._capture_listener is not None:
            self._capture_listener.stop()
            self._capture_listener = None

    def _on_hotkey_release(self, key):
        if self._is_control_key(key):
            self._pressed_controls.discard(key)

    @staticmethod
    def _is_control_key(key):
        return keyboard is not None and key in (
            keyboard.Key.ctrl,
            keyboard.Key.ctrl_l,
            keyboard.Key.ctrl_r,
        )

    def _matches_record_chord(self, key):
        return bool(self._pressed_controls) and self._matches_hotkey(key)

    def _toggle_armed_recording(self):
        if not self.record_var.get():
            return
        if self._is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    # Live action loop
    def toggle_action(self):
        self._refresh_trust_state()
        self._start_hotkey_listener()
        LOG.info(
            "[ACTION BUTTON] Start/Stop toggled: running=%s playing_recording=%s "
            "accessibility=%s input_monitoring=%s bundle_id=%s executable=%s "
            "bundle_path=%s",
            self.running,
            self._playing_recording,
            bool(self._was_trusted),
            bool(self._last_input_monitoring_trusted),
            APP_BUNDLE_ID,
            sys.executable,
            self._app_bundle_path,
        )
        if self.running:
            self._stop_action()
        elif self._playing_recording:
            LOG.info("Stop requested for recording playback")
            self._stop_event.set()
        else:
            self._start_action()

    def _start_action(self):
        if self.running:
            return
        self._apply_delay()
        if not self._was_trusted:
            LOG.warning("Cannot start action: Accessibility permission is missing")
            self._show_accessibility_required()
            return
        if not self.mouse_var.get() and not (
            self.keyboard_var.get() and self._keyboard_keys
        ):
            LOG.warning(
                "Cannot start action: no enabled mouse or configured keyboard action"
            )
            return
        self._action_mouse = self.mouse_var.get()
        self._action_keyboard = self.keyboard_var.get()
        self._action_mode = self.mode_var.get()
        self._action_keys = list(self._keyboard_keys)
        LOG.info(
            "Starting action: mouse=%s keyboard=%s mode=%s click_type=%s "
            "delay_ms=%s key_count=%s",
            self._action_mouse,
            self._action_keyboard,
            self._action_mode,
            self.click_type,
            self.delay_ms,
            len(self._action_keys),
        )
        try:
            if self._action_keyboard:
                self._get_keyboard_controller()
            self.running = True
            self._stop_event.clear()
            if self._action_mouse and self._action_mode == "hold":
                pyautogui.mouseDown(button=self.click_type)
            self._set_status("Started")
            if not (
                self._action_mode == "hold"
                and self._action_mouse
                and not self._action_keyboard
            ):
                self.thread = threading.Thread(
                    target=self._run_action_loop, daemon=True
                )
                self.thread.start()
        except Exception as error:
            self.running = False
            self._stop_event.set()
            self.thread = None
            LOG.exception("Failed to initialize or start action")
            self._set_status("Stopped")
            messagebox.showerror(APP_NAME, f"Could not start action:\n{error}")

    def _run_action_loop(self):
        LOG.info("Action worker started")
        try:
            while not self._stop_event.is_set() and self.running:
                if self._action_mouse and self._action_mode == "autoclick":
                    pyautogui.click(button=self.click_type)
                if self._action_keyboard:
                    for key_data in self._action_keys:
                        key = _deserialize_key(key_data)
                        self._keyboard_controller.press(key)
                        self._keyboard_controller.release(key)
                if (
                    self._action_mode == "hold"
                    and self._action_mouse
                    and not self._action_keyboard
                ):
                    self._stop_event.wait(0.05)
                else:
                    effective_interval = max(
                        MIN_INTERVAL_SECONDS,
                        self.delay_ms / 1_000,
                    )
                    self._stop_event.wait(effective_interval)
        except Exception as error:
            LOG.exception(
                "Action worker failed: mouse=%s keyboard=%s mode=%s",
                self._action_mouse,
                self._action_keyboard,
                self._action_mode,
            )
            if not self._closed:
                self.root.after(
                    0, lambda failure=error: self._on_action_error(failure)
                )
        finally:
            LOG.info("Action worker exited")

    def _on_action_error(self, error):
        LOG.error("Action failure reported to UI: %s", error)
        self._stop_action()
        messagebox.showerror(APP_NAME, f"Action failed:\n{error}")

    def _get_keyboard_controller(self):
        if self._keyboard_controller is None:
            from pynput.keyboard import Controller

            self._keyboard_controller = Controller()
        return self._keyboard_controller

    def _stop_action(self):
        was_running = self.running
        LOG.info(
            "Stopping action: was_running=%s mouse=%s keyboard=%s mode=%s",
            was_running,
            self._action_mouse,
            self._action_keyboard,
            self._action_mode,
        )
        self.running = False
        self._stop_event.set()
        if was_running and self._action_mouse and self._action_mode == "hold":
            try:
                pyautogui.mouseUp(button=self.click_type)
            except Exception:
                LOG.exception("Failed to release held mouse button while stopping")
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=1.0)
        self.thread = None
        if not self._playing_recording:
            self._set_status("Stopped")

    # Recording input to a portable JSON event list
    def _record_checkbox_changed(self):
        self._record_armed = self.record_var.get()
        self._update_input_state()
        if not self.record_var.get() and self._is_recording:
            self._stop_recording()
        if not self._is_recording and not self.running and not self._playing_recording:
            self._set_status("Stopped")

    def _record_filter_changed(self):
        self._update_input_state()

    def _start_recording(self):
        if keyboard is None or mouse is None:
            self.record_var.set(False)
            self._record_armed = False
            self._update_input_state()
            messagebox.showerror(APP_NAME, "pynput is required to record input.")
            return
        if not self._has_listener_permissions():
            self.record_var.set(False)
            self._record_armed = False
            self._update_input_state()
            self._show_permissions_required()
            return
        if self.running:
            self._stop_action()
        self._record_events = []
        self._record_started_at = time.monotonic()
        self._is_recording = True
        self._stop_chord_detected = False
        self._pressed_controls.clear()
        selected = self.record_filter.get()
        self._record_filter_at_start = selected
        if selected in ("all", "keyboard"):
            self._record_keyboard_listener = self._new_keyboard_listener(
                on_press=self._record_key_press,
                on_release=self._record_key_release,
            )
            self._record_keyboard_listener.daemon = True
            self._record_keyboard_listener.start()
        if selected in ("all", "mouse"):
            self._record_mouse_listener = mouse.Listener(
                on_move=self._record_mouse_move,
                on_click=self._record_mouse_click,
                on_scroll=self._record_mouse_scroll,
            )
            self._record_mouse_listener.daemon = True
            self._record_mouse_listener.start()
        self._set_status("Recording")

    def _record_event(self, event):
        if self._record_started_at is None:
            return
        event["time"] = time.monotonic() - self._record_started_at
        with self._record_lock:
            self._record_events.append(event)

    def _record_key_press(self, key):
        if self._is_record_stop_chord(key):
            self.root.after(0, self._stop_recording)
            return
        if self._stop_chord_detected and keyboard is not None and key in (
            keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r
        ):
            return
        serialized = _serialize_key(key)
        if serialized is not None:
            self._record_event({"type": "key_down", "key": serialized})

    def _is_record_stop_chord(self, key):
        if self._is_control_key(key):
            self._pressed_controls.add(key)
            return False
        is_stop_chord = self._matches_record_chord(key)
        if is_stop_chord and not self._stop_chord_detected:
            self._stop_chord_detected = True
            with self._record_lock:
                for index in range(len(self._record_events) - 1, -1, -1):
                    event = self._record_events[index]
                    serialized = event.get("key", {})
                    if (
                        event.get("type") == "key_down"
                        and serialized.get("special") in ("ctrl", "ctrl_l", "ctrl_r")
                    ):
                        del self._record_events[index]
                        break
        return is_stop_chord

    def _record_key_release(self, key):
        if self._stop_chord_detected and self._matches_hotkey(key):
            return
        if self._stop_chord_detected and keyboard is not None and key in (
            keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r
        ):
            self._pressed_controls.discard(key)
            return
        if key in self._pressed_controls:
            self._pressed_controls.discard(key)
        serialized = _serialize_key(key)
        if serialized is not None:
            self._record_event({"type": "key_up", "key": serialized})

    def _record_mouse_move(self, x, y):
        self._record_event({"type": "mouse_move", "x": x, "y": y})

    def _record_mouse_click(self, x, y, button, pressed):
        self._record_event({
            "type": "mouse_down" if pressed else "mouse_up",
            "x": x,
            "y": y,
            "button": button.name,
        })

    def _record_mouse_scroll(self, x, y, dx, dy):
        self._record_event({
            "type": "mouse_scroll", "x": x, "y": y, "dx": dx, "dy": dy
        })

    def _stop_recording(self, save=True):
        if self._record_started_at is None:
            self._update_input_state()
            return
        self._record_started_at = None
        self._is_recording = False
        for name in ("_record_keyboard_listener", "_record_mouse_listener"):
            listener = getattr(self, name)
            if listener is not None:
                listener.stop()
                setattr(self, name, None)
        self._record_filter_at_start = None
        self._update_input_state()
        self._set_status("Stopped")
        if not save:
            return
        with self._record_lock:
            events = list(self._record_events)
        self._record_events = []
        if not events:
            return
        events.sort(key=lambda event: event["time"])
        created = datetime.now().astimezone()
        default_name = created.strftime("Recording %Y-%m-%d %H:%M:%S")
        name = self._prompt_recording_name(default_name)
        if name is None:
            return
        recording = {
            "format": 1,
            "name": name,
            "created": created.isoformat(),
            "events": events,
        }
        path = self._recordings_dir / (
            created.strftime("%Y%m%d-%H%M%S-%f") + ".json"
        )
        try:
            self._recordings_dir.mkdir(parents=True, exist_ok=True)
            write_json_atomically(path, recording)
        except OSError as error:
            messagebox.showerror(APP_NAME, "Could not save recording:\n%s" % error)
            return
        self._refresh_recordings()
        self.notebook.select(1)

    def _prompt_recording_name(self, default_name):
        dialog = tk.Toplevel(self.root)
        dialog.title("Save recording")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.grab_set()

        content = ttk.Frame(dialog, padding=16)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="Recording name:").pack(anchor="w")
        name_var = tk.StringVar(value=default_name)
        name_entry = ttk.Entry(content, textvariable=name_var, width=38)
        name_entry.pack(fill="x", pady=(6, 12))
        result = {"name": None}

        def save():
            if result["name"] is not None:
                return
            result["name"] = name_var.get().strip() or default_name
            save_button.configure(state="disabled")
            dialog.destroy()

        def discard():
            dialog.destroy()

        def save_on_return(event):
            if event.widget.winfo_toplevel() is dialog:
                save()

        def discard_on_escape(event):
            if event.widget.winfo_toplevel() is dialog:
                discard()

        buttons = ttk.Frame(content)
        buttons.pack(fill="x")
        save_button = tk.Button(
            buttons,
            text="Save",
            command=save,
            background="#007AFF",
            foreground="white",
            activebackground="#0063CE",
            activeforeground="white",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=14,
            pady=5,
        )
        ttk.Button(buttons, text="Discard", command=discard).pack(side="left")
        save_button.pack(side="right")
        dialog.bind("<Return>", save_on_return)
        dialog.bind("<Escape>", discard_on_escape)
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - dialog.winfo_width()) // 2
        y = (dialog.winfo_screenheight() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")
        name_entry.focus_set()
        name_entry.selection_range(0, tk.END)
        self.root.wait_window(dialog)
        return result["name"]

    def _refresh_recordings(self, selected_paths=None):
        if selected_paths is None and hasattr(self, "recordings_list"):
            selected_paths = [
                self.recordings[index][0].name
                for index in self.recordings_list.curselection()
                if index < len(self.recordings)
            ]
        self.recordings = []
        self.recordings_list.delete(0, tk.END)
        notices = []
        if not self._recordings_dir.exists():
            self._selected_recording = None
            self.recordings_notice.configure(text="")
            return

        paths = {
            path.name: path
            for path in self._recordings_dir.glob("*.json")
            if path != self._recordings_order_path
        }
        saved_names = []
        order = []
        manifest_valid = False
        if self._recordings_order_path.exists():
            try:
                order = json.loads(
                    self._recordings_order_path.read_text(encoding="utf-8")
                )
                if not isinstance(order, list) or not all(
                    isinstance(name, str) for name in order
                ):
                    raise ValueError("recording order must be a list of filenames")
                saved_names = list(
                    dict.fromkeys(name for name in order if name in paths)
                )
                manifest_valid = True
            except (json.JSONDecodeError, ValueError) as error:
                try:
                    backup_path = preserve_invalid_json(self._recordings_order_path)
                except OSError as preserve_error:
                    LOG.exception("Could not preserve invalid recording order file")
                    notices.append(
                        "Invalid recording order file could not be preserved: "
                        f"{preserve_error}"
                    )
                else:
                    LOG.warning(
                        "Invalid recording order JSON in %s: %s; preserved as %s",
                        self._recordings_order_path,
                        error,
                        backup_path,
                    )
                    notices.append(
                        f"Invalid recording order preserved as {backup_path.name}."
                    )
            except OSError as error:
                LOG.exception("Could not read recording order file")
                notices.append(f"Could not read recording order: {error}")
        ordered_names = [
            path.name
            for path in sorted(paths.values(), reverse=True)
            if path.name not in saved_names
        ]
        ordered_names.extend(saved_names)

        for name in ordered_names:
            path = paths[name]
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("recording data must be a JSON object")
                if data.get("format") != 1 or not isinstance(data.get("events"), list):
                    raise ValueError("unsupported recording format")
                self.recordings.append((path, data))
                self.recordings_list.insert(tk.END, data.get("name", path.stem))
            except (json.JSONDecodeError, ValueError) as error:
                try:
                    backup_path = preserve_invalid_json(path)
                except OSError as preserve_error:
                    LOG.exception("Could not preserve invalid recording file %s", path)
                    notices.append(
                        f"{path.name} is invalid and could not be preserved: "
                        f"{preserve_error}"
                    )
                else:
                    LOG.warning(
                        "Invalid recording JSON in %s: %s; preserved as %s",
                        path,
                        error,
                        backup_path,
                    )
                    notices.append(
                        f"Skipped invalid {path.name}; preserved as {backup_path.name}."
                    )
            except OSError as error:
                LOG.exception("Could not read recording file %s", path)
                notices.append(f"Could not read {path.name}: {error}")
        loaded_names = [item[0].name for item in self.recordings]
        if manifest_valid and order != loaded_names:
            try:
                write_json_atomically(self._recordings_order_path, loaded_names)
            except OSError as error:
                LOG.exception("Could not update recording order file")
                notices.append(f"Could not update recording order: {error}")
        if selected_paths:
            selected_names = set(selected_paths)
            for index, item in enumerate(self.recordings):
                path = item[0]
                if path.name in selected_names:
                    self.recordings_list.selection_set(index)
        if not self.recordings:
            self._selected_recording = None
        else:
            selected = self.recordings_list.curselection()
            self._selected_recording = (
                self.recordings[selected[0]][1] if selected else None
            )
        self.recordings_notice.configure(text="\n".join(notices))

    def _on_recording_selected(self, _=None):
        selected = self.recordings_list.curselection()
        if selected:
            self._selected_recording = self.recordings[selected[0]][1]
        else:
            self._selected_recording = None

    def _recording_row_at_y(self, y):
        index = self.recordings_list.nearest(y)
        if not 0 <= index < len(self.recordings):
            return None
        bounds = self.recordings_list.bbox(index)
        if bounds is None:
            return None
        top, height = bounds[1], bounds[3]
        return index if top <= y < top + height else None

    def _on_recording_list_click(self, event):
        if self._recording_row_at_y(event.y) is not None:
            return
        self.recordings_list.selection_clear(0, tk.END)
        self._on_recording_selected()
        self._recording_drag_start = None
        return "break"

    def _on_recording_indicator_click(self, _event):
        if _event.widget is self._recording_drop_indicator:
            self.recordings_list.focus_set()
        self.recordings_list.selection_clear(0, tk.END)
        self._on_recording_selected()
        return "break"

    def _toggle_recording_selection(self, event):
        index = self._recording_row_at_y(event.y)
        if index is None:
            self.recordings_list.selection_clear(0, tk.END)
            self._on_recording_selected()
            return "break"
        if index in self.recordings_list.curselection():
            self.recordings_list.selection_clear(index)
        else:
            self.recordings_list.selection_set(index)
        self.recordings_list.activate(index)
        self._on_recording_selected()
        return "break"

    def _select_all_recordings(self, _=None):
        if self.recordings:
            self.recordings_list.selection_set(0, tk.END)
            self._on_recording_selected()
        return "break"

    def _recording_drag_press(self, event):
        if not self.recordings:
            self._recording_drag_start = None
            return
        index = self._recording_row_at_y(event.y)
        if index is None:
            self._recording_drag_start = None
            return
        self._recording_drag_start = (event.x, event.y, index)
        self._recording_drag_selection = ()
        self._recording_dragging = False

        selected = self.recordings_list.curselection()
        if index in selected:
            self._recording_drag_selection = tuple(selected)
            return

        def capture_new_selection():
            selected = self.recordings_list.curselection()
            self._recording_drag_selection = (
                tuple(selected) if index in selected else (index,)
            )

        self.root.after_idle(capture_new_selection)

    def _recording_drag_motion(self, event):
        if self._recording_dragging:
            self._update_recording_drop_indicator(event.y)
            return "break"
        if self._recording_drag_start is None:
            return
        start_x, start_y = self._recording_drag_start[:2]
        if abs(event.x - start_x) + abs(event.y - start_y) < 5:
            return
        self._recording_dragging = True
        self._update_recording_drop_indicator(event.y)
        return "break"

    def _update_recording_drop_indicator(self, y):
        visible_rows = [
            (index, self.recordings_list.bbox(index))
            for index in range(len(self.recordings))
        ]
        visible_rows = [
            (index, bounds) for index, bounds in visible_rows if bounds is not None
        ]
        if not visible_rows:
            return

        first_index, first_bounds = visible_rows[0]
        insertion = first_index
        indicator_y = first_bounds[1]
        for index, bounds in visible_rows:
            top, height = bounds[1], bounds[3]
            if y < top + height / 2:
                insertion = index
                indicator_y = top
                break
            insertion = index + 1
            indicator_y = top + height

        self._recording_drop_index = insertion
        self._recording_drop_indicator.place(
            in_=self.recordings_list,
            x=4,
            y=max(0, indicator_y - 1),
            relwidth=1,
            width=-8,
            height=2,
        )
        self._recording_drop_indicator.lift()

    def _hide_recording_drop_indicator(self):
        self._recording_drop_indicator.place_forget()
        self._recording_drop_index = None

    def _recording_drag_release(self, _event):
        if self._recording_drag_start is None:
            return
        was_dragging = self._recording_dragging
        self._recording_drag_start = None
        self._recording_dragging = False
        if not was_dragging:
            return

        self._update_recording_drop_indicator(_event.y)
        insertion = self._recording_drop_index
        self._hide_recording_drop_indicator()
        if insertion is None:
            return "break"

        selected = sorted(set(self._recording_drag_selection))
        if not selected:
            return "break"
        selected_set = set(selected)
        moving = [self.recordings[index] for index in selected]
        remaining = [
            item for index, item in enumerate(self.recordings)
            if index not in selected_set
        ]
        insertion -= sum(index < insertion for index in selected)
        insertion = max(0, min(insertion, len(remaining)))
        reordered = remaining[:insertion] + moving + remaining[insertion:]
        if reordered != self.recordings:
            self.recordings = reordered
            selected_names = [item[0].name for item in moving]
            try:
                self._recordings_dir.mkdir(parents=True, exist_ok=True)
                write_json_atomically(
                    self._recordings_order_path,
                    [item[0].name for item in reordered],
                )
            except OSError as error:
                messagebox.showerror(
                    APP_NAME, "Could not save recording order:\n%s" % error
                )
            self._refresh_recordings(selected_names)
        return "break"

    def _play_selected(self):
        self._refresh_trust_state()
        LOG.info(
            "[ACTION BUTTON] Play selected recording: accessibility=%s "
            "input_monitoring=%s bundle_id=%s executable=%s bundle_path=%s",
            bool(self._was_trusted),
            bool(self._last_input_monitoring_trusted),
            APP_BUNDLE_ID,
            sys.executable,
            self._app_bundle_path,
        )
        selected = self.recordings_list.curselection()
        if self.running or self._playing_recording:
            self._stop_action()
            return
        if not selected:
            return
        if len(selected) != 1:
            messagebox.showinfo(APP_NAME, "Select exactly one recording to play.")
            return
        if not self._was_trusted:
            LOG.warning("Cannot play recording: Accessibility permission is missing")
            self._show_accessibility_required()
            return
        if self.record_var.get():
            self._stop_recording()
        recording = self.recordings[selected[0]][1]
        events = list(recording["events"])
        if not events:
            messagebox.showerror(APP_NAME, "This recording contains no events.")
            return
        if self.play_forever_var.get():
            repeat_count = None
        else:
            count_text = (
                ""
                if self._playback_count_placeholder_active
                else self.playback_count_var.get()
            )
            try:
                repeat_count = int(count_text)
            except ValueError:
                repeat_count = 0
            if repeat_count < 1:
                messagebox.showerror(
                    APP_NAME, "Enter a whole number of plays greater than zero."
                )
                return
        keyboard_controller = self._get_keyboard_controller()
        from pynput.mouse import Controller as MouseController

        mouse_controller = MouseController()
        self._playing_recording = True
        self._stop_event.clear()
        self._set_status("Started")
        try:
            self._playback_thread = threading.Thread(
                target=self._play_recording,
                args=(events, keyboard_controller, mouse_controller, repeat_count),
                daemon=True,
            )
            self._playback_thread.start()
            LOG.info(
                "Recording playback worker started: event_count=%s repeat_count=%s",
                len(events),
                repeat_count if repeat_count is not None else "forever",
            )
        except Exception as error:
            self._playing_recording = False
            self._stop_event.set()
            LOG.exception("Failed to initialize or start recording playback")
            self._set_status("Stopped")
            messagebox.showerror(APP_NAME, f"Could not start playback:\n{error}")

    def _play_recording(
        self, events, keyboard_controller, mouse_controller, repeat_count=1
    ):
        pressed_keys = set()
        pressed_buttons = set()
        last_mouse_down_at = None
        try:
            from pynput.mouse import Button

            completed = 0
            while repeat_count is None or completed < repeat_count:
                started = time.monotonic()
                for event in events:
                    deadline = started + max(0, event["time"])
                    if event["type"] == "mouse_down" and last_mouse_down_at is not None:
                        deadline = max(
                            deadline,
                            last_mouse_down_at + MIN_INTERVAL_SECONDS,
                        )
                    wait = deadline - time.monotonic()
                    if self._stop_event.wait(max(0, wait)):
                        return
                    kind = event["type"]
                    if kind == "key_down":
                        key = _deserialize_key(event["key"])
                        keyboard_controller.press(key)
                        pressed_keys.add(key)
                    elif kind == "key_up":
                        key = _deserialize_key(event["key"])
                        keyboard_controller.release(key)
                        pressed_keys.discard(key)
                    elif kind == "mouse_move":
                        mouse_controller.position = (event["x"], event["y"])
                    elif kind in ("mouse_down", "mouse_up"):
                        mouse_controller.position = (event["x"], event["y"])
                        button = getattr(Button, event["button"])
                        if kind == "mouse_down":
                            mouse_controller.press(button)
                            last_mouse_down_at = time.monotonic()
                            pressed_buttons.add(button)
                        else:
                            mouse_controller.release(button)
                            pressed_buttons.discard(button)
                    elif kind == "mouse_scroll":
                        mouse_controller.position = (event["x"], event["y"])
                        mouse_controller.scroll(event["dx"], event["dy"])
                completed += 1
                for key in pressed_keys:
                    keyboard_controller.release(key)
                pressed_keys.clear()
                for button in pressed_buttons:
                    mouse_controller.release(button)
                pressed_buttons.clear()
        except Exception as error:
            LOG.exception("Recording playback failed")
            if not self._closed:
                self.root.after(
                    0, lambda err=error: messagebox.showerror(
                        APP_NAME, "Could not play recording:\n%s" % err
                    )
                )
        finally:
            for key in pressed_keys:
                try:
                    keyboard_controller.release(key)
                except Exception:
                    LOG.exception("Failed to release a key after recording playback")
            for button in pressed_buttons:
                try:
                    mouse_controller.release(button)
                except Exception:
                    LOG.exception(
                        "Failed to release a mouse button after recording playback"
                    )
            self._playing_recording = False
            LOG.info("Recording playback worker exited")
            if not self._closed:
                self.root.after(0, self._set_stopped)

    def _set_stopped(self):
        if not self.running:
            self._set_status("Stopped")

    def _delete_selected(self):
        self._on_recording_selected()
        selected = self.recordings_list.curselection()
        if not selected:
            return
        selected_items = [self.recordings[index] for index in selected]
        count = len(selected_items)
        prompt = (
            "Delete this recording?"
            if count == 1
            else f"Delete these {count} recordings?"
        )
        if not messagebox.askyesno(APP_NAME, prompt):
            return
        for item in selected_items:
            path = item[0]
            try:
                path.unlink()
            except OSError as error:
                messagebox.showerror(
                    APP_NAME, "Could not delete recording:\n%s" % error
                )
                break
        self._selected_recording = None
        self._refresh_recordings()

    def _on_close(self):
        self._closed = True
        if self.record_var.get():
            self._stop_recording()
        self._stop_action()
        self._stop_event.set()
        if self._playback_thread is not None:
            self._playback_thread.join(timeout=1.0)
        self.capturing = False
        self._stop_hotkey_listener()
        self._stop_capture_listener()
        self.root.destroy()
        self.root.quit()


if __name__ == "__main__":
    configure_logging()
    if pyautogui is None:
        print("pyautogui not installed. Run: pip install pyautogui pynput")
        sys.exit(1)
    AutoClicker().root.mainloop()
