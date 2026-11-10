import datetime
import json
import math
import os
import threading
import time
import tkinter as tk
from pathlib import Path

from pynput import keyboard, mouse


# FocusX — intentionally minimal
# 1. Pomodoro: auto-starts on launch -> locked break -> focus, repeating.
# 2. Sleep timer: lock inputs at the chosen bedtime until 5:30 AM.
#    Default bedtime is 12:30 AM and is treated as a hard floor:
#    the user can push bedtime LATER (e.g. 1:00 AM) but never earlier
#    than 12:30 AM. Once bedtime passes, it locks until 5:30 AM.
# No shutdown, Task Scheduler, watchdog, auto-start, Sprint, or Task Manager killer.
# Emergency unlock: Ctrl + Alt + F12.

APP_NAME = "FocusX"
BG = "#121212"
DEFAULT_FOCUS = 40
DEFAULT_BREAK = 5
MIN_BEDTIME = 30            # 12:30 AM, permanent floor (minutes after midnight)
DEFAULT_BEDTIME = MIN_BEDTIME
WAKE_MINUTE = 5 * 60 + 30
LOCK_MESSAGE = "TAKE A BREAK\n\nStep away from the screen."
SLEEP_MESSAGE = "SLEEP TIME\n\nGood night. See you at 5:30 AM."


def settings_path():
    base = Path(os.environ.get("APPDATA") or Path.home())
    folder = base / APP_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "settings.json"


def clock_label(minutes):
    hour, minute = divmod(minutes, 60)
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


class FocusX:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("FocusX — Simple Focus")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        width, height = 410, 570
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{width}x{height}+{(sw-width)//2}+{(sh-height)//2}")

        self.focus_minutes = DEFAULT_FOCUS
        self.break_minutes = DEFAULT_BREAK
        self.running = False
        self.phase = None
        self.deadline = 0.0
        self.tick_job = None
        self.lock_reason = None
        self.overlay = None
        self.overlay_label = None
        self.mouse_listener = None
        self.kb_listener = None
        self.held_keys = set()
        self.bedtime = self.load_bedtime()
        self.sleep_locked = False
        self.sleep_override = False
        self.paused = False
        self.paused_left = 0.0
        self._closing = False

        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close_app)
        self.root.after(200, self.clock_tick)
        # Auto-start the pomodoro as soon as the app opens.
        self.root.after(300, self.auto_start)

    # ---------------------------------------------------------------- settings
    def load_bedtime(self):
        try:
            data = json.loads(settings_path().read_text(encoding="utf-8"))
            value = int(data.get("bedtime", DEFAULT_BEDTIME))
            # Never earlier than 12:30 AM; may be pushed later within the night.
            if MIN_BEDTIME <= value <= 23 * 60 + 45 and value % 15 == 0:
                return value
        except Exception:
            pass
        return DEFAULT_BEDTIME

    def save_bedtime(self):
        try:
            settings_path().write_text(
                json.dumps({"bedtime": self.bedtime}), encoding="utf-8"
            )
        except Exception:
            self.status_label.config(
                text="Couldn't save bedtime setting.", fg="#ff8a80"
            )

    # --------------------------------------------------------------------- UI
    def build_ui(self):
        tk.Label(
            self.root, text="FOCUSX", font=("Segoe UI", 21, "bold"),
            fg="#00e5ff", bg=BG
        ).pack(pady=(22, 3))
        tk.Label(
            self.root, text="JUST THE ESSENTIALS",
            font=("Segoe UI", 8, "bold"), fg="#777777", bg=BG
        ).pack(pady=(0, 20))

        pomo = tk.LabelFrame(
            self.root, text="  POMODORO  ", bg=BG, fg="#00e5ff",
            font=("Segoe UI", 10, "bold"), padx=18, pady=14
        )
        pomo.pack(fill="x", padx=25, pady=7)

        row = tk.Frame(pomo, bg=BG)
        row.pack()
        tk.Label(row, text="Focus (min)", fg="#cccccc", bg=BG).pack(side="left", padx=5)
        self.focus_var = tk.StringVar(value=str(DEFAULT_FOCUS))
        tk.Spinbox(
            row, from_=1, to=240, width=5, textvariable=self.focus_var,
            justify="center", font=("Segoe UI", 12), bg="#222222",
            fg="white", buttonbackground="#444444", insertbackground="white"
        ).pack(side="left", padx=(0, 15))
        tk.Label(row, text="Break (min)", fg="#cccccc", bg=BG).pack(side="left", padx=5)
        self.break_var = tk.StringVar(value=str(DEFAULT_BREAK))
        tk.Spinbox(
            row, from_=1, to=60, width=5, textvariable=self.break_var,
            justify="center", font=("Segoe UI", 12), bg="#222222",
            fg="white", buttonbackground="#444444", insertbackground="white"
        ).pack(side="left")

        self.start_button = tk.Button(
            pomo, text="START POMODORO", command=self.start_pomodoro,
            bg="#2e7d32", fg="white", activebackground="#1b5e20",
            font=("Segoe UI", 10, "bold"), relief="flat", width=23, height=2
        )
        self.start_button.pack(pady=(16, 4))

        self.timer_label = tk.Label(
            self.root, text="00:00", font=("Segoe UI", 48, "bold"),
            fg="#ff9800", bg=BG
        )
        self.timer_label.pack(pady=(17, 0))
        self.phase_label = tk.Label(
            self.root, text="Auto-starting…", font=("Segoe UI", 10),
            fg="#aaaaaa", bg=BG
        )
        self.phase_label.pack(pady=(0, 10))

        controls = tk.Frame(self.root, bg=BG)
        controls.pack(pady=4)
        self.pause_button = tk.Button(
            controls, text="PAUSE", width=11, state="disabled",
            command=self.toggle_pause, bg="#333333", fg="white", relief="flat"
        )
        self.pause_button.pack(side="left", padx=5)
        self.reset_button = tk.Button(
            controls, text="STOP", width=11, state="disabled",
            command=self.reset_pomodoro, bg="#8e2424", fg="white", relief="flat"
        )
        self.reset_button.pack(side="left", padx=5)

        tk.Frame(self.root, bg="#333333", height=1).pack(fill="x", padx=35, pady=18)

        sleep = tk.LabelFrame(
            self.root, text="  SLEEP TIMER  ", bg=BG, fg="#b39ddb",
            font=("Segoe UI", 10, "bold"), padx=18, pady=12
        )
        sleep.pack(fill="x", padx=25, pady=4)
        row2 = tk.Frame(sleep, bg=BG)
        row2.pack()
        tk.Label(row2, text="Bedtime", fg="#cccccc", bg=BG).pack(side="left", padx=8)
        # Floor is 12:30 AM (MIN_BEDTIME); user may push it later.
        choices = [clock_label(m) for m in range(MIN_BEDTIME, 23 * 60 + 46, 15)]
        self.bed_var = tk.StringVar(value=clock_label(self.bedtime))
        self.bed_menu = tk.OptionMenu(row2, self.bed_var, *choices, command=self.pick_bedtime)
        self.bed_menu.config(
            width=10, font=("Segoe UI", 11, "bold"), bg="#222222",
            fg="white", activebackground="#444444", relief="flat",
            highlightthickness=0
        )
        self.bed_menu["menu"].config(
            bg="#222222", fg="white", activebackground="#7e57c2"
        )
        self.bed_menu.pack(side="left", padx=8)

        self.sleep_status = tk.Label(
            sleep, text="", font=("Segoe UI", 9), fg="#b39ddb", bg=BG
        )
        self.sleep_status.pack(pady=(9, 0))

        self.status_label = tk.Label(
            self.root, text="Auto-started. Input locks only during breaks and sleep hours.",
            font=("Segoe UI", 8), fg="#777777", bg=BG
        )
        self.status_label.pack(pady=(13, 3))
        tk.Label(
            self.root, text="Emergency unlock:  Ctrl + Alt + F12",
            font=("Segoe UI", 8, "bold"), fg="#ffb74d", bg=BG
        ).pack(pady=(0, 12))

    # ----------------------------------------------------------------- helpers
    def read_minutes(self, var, low, high, default):
        try:
            return max(low, min(high, int(var.get())))
        except (ValueError, TypeError):
            return default

    # --------------------------------------------------------------- pomodoro
    def auto_start(self):
        # Fire the pomodoro once the window is up unless sleep-lock is active.
        if self.lock_reason or self.sleep_locked:
            return
        self.start_pomodoro()

    def start_pomodoro(self):
        if self.running or self.lock_reason:
            return
        self.focus_minutes = self.read_minutes(self.focus_var, 1, 240, DEFAULT_FOCUS)
        self.break_minutes = self.read_minutes(self.break_var, 1, 60, DEFAULT_BREAK)
        self.focus_var.set(str(self.focus_minutes))
        self.break_var.set(str(self.break_minutes))
        self.running = True
        self.paused = False
        self.phase = "focus"
        self.start_phase(self.focus_minutes * 60)
        self.start_button.config(state="disabled")
        self.pause_button.config(state="normal", text="PAUSE")
        self.reset_button.config(state="normal")
        self.phase_label.config(text="FOCUS — lock stays OFF.")
        self.status_label.config(text="Focus session running.", fg="#00e5ff")

    def start_phase(self, seconds):
        self.deadline = time.monotonic() + seconds
        self.update_timer(seconds)
        if self.tick_job:
            self.root.after_cancel(self.tick_job)
        self.tick_job = self.root.after(200, self.tick)

    def update_timer(self, seconds):
        seconds = max(0, int(math.ceil(seconds)))
        mins, secs = divmod(seconds, 60)
        self.timer_label.config(text=f"{mins:02d}:{secs:02d}")

    def tick(self):
        self.tick_job = None
        if not self.running or self.lock_reason or self.paused:
            return
        left = self.deadline - time.monotonic()
        self.update_timer(left)
        if left <= 0:
            if self.phase == "focus":
                self.begin_break()
                return
            self.end_break()
            return
        self.tick_job = self.root.after(200, self.tick)

    def begin_break(self):
        self.phase = "break"
        self.phase_label.config(text="BREAK — keyboard and mouse locked.")
        self.status_label.config(text="Break time. Step away.", fg="#ff5252")
        self.lock_inputs("break", LOCK_MESSAGE, self.break_minutes * 60)

    def end_break(self):
        self.unlock_inputs()
        if not self.running:
            return
        self.phase = "focus"
        self.phase_label.config(text="FOCUS — lock stays OFF.")
        self.status_label.config(text="Back to focus.", fg="#00e5ff")
        self.start_phase(self.focus_minutes * 60)

    def toggle_pause(self):
        # Pausing is deliberately unavailable while inputs are locked.
        if not self.running or self.lock_reason:
            return
        if self.paused:
            self.paused = False
            self.deadline = time.monotonic() + self.paused_left
            self.pause_button.config(text="PAUSE")
            self.tick_job = self.root.after(200, self.tick)
            self.status_label.config(text="Resumed.", fg="#00e5ff")
        else:
            self.paused_left = max(0.0, self.deadline - time.monotonic())
            self.paused = True
            if self.tick_job:
                self.root.after_cancel(self.tick_job)
                self.tick_job = None
            self.pause_button.config(text="RESUME")
            self.update_timer(self.paused_left)
            self.status_label.config(text="Paused.", fg="#ffb74d")

    def reset_pomodoro(self):
        # "STOP": stop the loop, release any lock, return to editable state.
        if self.lock_reason:
            return
        self.running = False
        self.phase = None
        self.paused = False
        if self.tick_job:
            self.root.after_cancel(self.tick_job)
            self.tick_job = None
        self.unlock_inputs()
        self.timer_label.config(text="00:00", fg="#ff9800")
        self.phase_label.config(text="Stopped. Adjust times and press START.")
        self.start_button.config(state="normal")
        self.pause_button.config(state="disabled", text="PAUSE")
        self.reset_button.config(state="disabled")
        self.status_label.config(text="Pomodoro stopped.", fg="#aaaaaa")

    # ------------------------------------------------------------------- sleep
    def pick_bedtime(self, label):
        for minute in range(MIN_BEDTIME, 23 * 60 + 46, 15):
            if clock_label(minute) == label:
                # Enforce the permanent floor of 12:30 AM.
                self.bedtime = max(MIN_BEDTIME, minute)
                self.save_bedtime()
                self.bed_var.set(clock_label(self.bedtime))
                break

    def is_sleep_time(self, now=None):
        now = now or datetime.datetime.now()
        minute = now.hour * 60 + now.minute
        # Bedtime (>= 12:30 AM) through midnight, or after midnight until 5:30 AM.
        if self.bedtime >= WAKE_MINUTE:
            return minute >= self.bedtime or minute < WAKE_MINUTE
        return self.bedtime <= minute < WAKE_MINUTE

    def clock_tick(self):
        if self._closing:
            return
        now = datetime.datetime.now()
        self.sleep_status.config(
            text=f"Locks at {clock_label(self.bedtime)} · unlocks at 5:30 AM "
                 f"(floor: 12:30 AM)"
        )

        should_lock = self.is_sleep_time(now) and not self.sleep_override
        if not self.is_sleep_time(now):
            self.sleep_override = False
        if should_lock and not self.sleep_locked:
            self.sleep_locked = True
            if self.running:
                self.running = False
                self.phase = None
                if self.tick_job:
                    self.root.after_cancel(self.tick_job)
                    self.tick_job = None
            if self.lock_reason == "break":
                self.unlock_inputs()
            self.start_button.config(state="disabled")
            self.pause_button.config(state="disabled")
            self.reset_button.config(state="disabled")
            self.phase_label.config(text="SLEEP TIME.")
            self.status_label.config(text="Sleep lock active until 5:30 AM.", fg="#ff5252")
            self.lock_inputs("sleep", SLEEP_MESSAGE, None)
        elif not should_lock and self.sleep_locked:
            self.sleep_locked = False
            if self.lock_reason == "sleep":
                self.unlock_inputs()
            self.start_button.config(state="normal")
            self.phase_label.config(text="Good morning. Press START to focus.")
            self.status_label.config(text="Unlocked.", fg="#00e676")

        self.root.after(1000, self.clock_tick)

    # ---------------------------------------------------------------- locking
    def lock_inputs(self, reason, message, duration):
        if self.lock_reason:
            return
        self.lock_reason = reason
        self.lock_until = time.monotonic() + duration if duration else None
        self.show_overlay(message, duration)
        self.mouse_listener = mouse.Listener(suppress=True)
        self.kb_listener = keyboard.Listener(
            suppress=True, on_press=self.on_key_press, on_release=self.on_key_release
        )
        self.mouse_listener.start()
        self.kb_listener.start()

    def on_key_press(self, key):
        self.held_keys.add(key)
        ctrl = keyboard.Key.ctrl_l in self.held_keys or keyboard.Key.ctrl_r in self.held_keys
        alt = keyboard.Key.alt_l in self.held_keys or keyboard.Key.alt_r in self.held_keys
        if ctrl and alt and key == keyboard.Key.f12:
            self.root.after(0, self.emergency_unlock)
            return False

    def on_key_release(self, key):
        self.held_keys.discard(key)

    def show_overlay(self, message, duration):
        self.overlay = tk.Toplevel(self.root)
        self.overlay.attributes("-fullscreen", True)
        self.overlay.attributes("-topmost", True)
        self.overlay.configure(bg="black")
        self.overlay.protocol("WM_DELETE_WINDOW", lambda: None)
        message = f"{message}\n\nCtrl + Alt + F12 = emergency unlock"
        self.overlay_label = tk.Label(
            self.overlay, text=message, fg="white", bg="black",
            font=("Segoe UI", 27, "bold"), justify="center"
        )
        self.overlay_label.pack(expand=True)
        if duration:
            self.update_overlay_countdown()

    def update_overlay_countdown(self):
        if self.lock_reason != "break" or not self.overlay_label:
            return
        left = max(0, math.ceil(self.lock_until - time.monotonic()))
        mins, secs = divmod(left, 60)
        self.overlay_label.config(
            text=f"{LOCK_MESSAGE}\n\n{mins:02d}:{secs:02d}\n\nCtrl + Alt + F12 = emergency unlock"
        )
        if left <= 0:
            self.end_break()
        else:
            self.root.after(250, self.update_overlay_countdown)

    def emergency_unlock(self):
        was_sleep_lock = self.lock_reason == "sleep"
        self.unlock_inputs()
        self.sleep_locked = False
        self.sleep_override = was_sleep_lock
        self.running = False
        self.phase = None
        self.paused = False
        if self.tick_job:
            self.root.after_cancel(self.tick_job)
            self.tick_job = None
        self.start_button.config(state="normal")
        self.pause_button.config(state="disabled", text="PAUSE")
        self.reset_button.config(state="disabled")
        self.timer_label.config(text="00:00", fg="#ff9800")
        self.phase_label.config(text="Emergency unlock used.")
        self.status_label.config(
            text=("Sleep lock bypassed until 5:30 AM." if was_sleep_lock else "Break lock released. Pomodoro stopped."),
            fg="#ffb74d"
        )

    def unlock_inputs(self):
        if self.mouse_listener:
            try:
                self.mouse_listener.stop()
            except Exception:
                pass
            self.mouse_listener = None
        if self.kb_listener:
            try:
                self.kb_listener.stop()
            except Exception:
                pass
            self.kb_listener = None
        self.held_keys.clear()
        self.lock_reason = None
        if self.overlay:
            try:
                self.overlay.destroy()
            except Exception:
                pass
        self.overlay = None
        self.overlay_label = None

    def close_app(self):
        # No hidden watchdog: closing the app always releases input and exits.
        self._closing = True
        self.running = False
        if self.tick_job:
            try:
                self.root.after_cancel(self.tick_job)
            except Exception:
                pass
        self.unlock_inputs()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    FocusX().run()