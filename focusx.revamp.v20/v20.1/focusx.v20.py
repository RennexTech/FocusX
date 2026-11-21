"""
FocusX - Deep Work Protocol
Pomodoro + analogue clock + sleep lock + NTP time.
Single file. Build with build.bat (Nuitka + Zig).
"""
import sys
import os
import json
import math
import socket
import struct
import random
import threading
import time
import datetime as dt
import tkinter as tk
from tkinter import messagebox

import psutil
from pynput import mouse, keyboard

try:
    import winsound
except ImportError:  # non-Windows (dev only)
    winsound = None

APP_NAME = "FocusX"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
LOCK_PORT = 48653

DEFAULTS = {
    "work_min": 37,
    "break_sec": 120,
    "long_break_sec": 900,
    "warn_seconds": 20,
    "long_every": 4,
    "sleep_enabled": True,
    "sleep_time": "23:00",
    "wake_time": "05:00",
    "use_ntp": True,
    "ntp_server": "pool.ntp.org",
    "manual_offset_min": 0.0,
    "autostart": True,
    "autostart_seconds": 7,
    "sound": True,
    "start_minimized": False,
    "emergency_phrase": "letmeout",
    "stats": {"date": "", "focus_sec": 0.0, "sessions": 0},
}

BG = "#121212"
PANEL = "#1a1a1a"
COL = {"IDLE": "#00e5ff", "FOCUS": "#ff9800", "BREAK": "#ff1744", "SLEEP": "#7c4dff"}

FUNNY = [
    "STEP AWAY FROM THE BOX.",
    "YOUR KEYBOARD IS NOW A PAPERWEIGHT.",
    "BRAIN REBOOT IN PROGRESS...",
    "THE COMPUTER MISSES YOU, BUT I DO NOT CARE.",
    "GO STARE AT A TREE OR SOMETHING.",
    "ACCESS DENIED. REASON: YOU ARE TIRED.",
    "DRINK WATER. STRETCH. BLINK.",
]


# ----------------------------------------------------------------- helpers
def load_settings():
    s = json.loads(json.dumps(DEFAULTS))
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for k, v in saved.items():
            if k in s:
                s[k] = v
        if "break_min" in saved and "break_sec" not in saved:
            s["break_sec"] = int(float(saved["break_min"]) * 60)
        if "long_break_min" in saved and "long_break_sec" not in saved:
            s["long_break_sec"] = int(float(saved["long_break_min"]) * 60)
    except Exception:
        pass
    return s


def save_settings(s):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
    except Exception:
        pass


def parse_hhmm(text):
    return dt.datetime.strptime(text.strip(), "%H:%M").time()


def fmt(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_long(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h}h {m:02d}m"


def next_occurrence(t, now):
    c = dt.datetime.combine(now.date(), t)
    if c <= now:
        c += dt.timedelta(days=1)
    return c


def ntp_offset(server, timeout=3.0):
    """Return (server_time - local_time) in seconds using a plain SNTP query."""
    packet = b"\x1b" + 47 * b"\0"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        t0 = time.time()
        s.sendto(packet, (server, 123))
        data, _ = s.recvfrom(48)
        t3 = time.time()
    finally:
        s.close()
    if len(data) < 48:
        raise ValueError("short NTP reply")
    secs, frac = struct.unpack("!II", data[40:48])
    server_time = secs - 2208988800 + frac / 2 ** 32
    rtt = t3 - t0
    return (server_time + rtt / 2) - t3


class TimeSource:
    """Wall clock = system clock + NTP offset + manual adjustment."""

    def __init__(self, settings):
        self.s = settings
        self.offset = 0.0
        self.status = "System clock"
        self.wake = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def now(self):
        return dt.datetime.fromtimestamp(
            time.time() + self.offset + float(self.s["manual_offset_min"]) * 60
        )

    def resync(self):
        self.wake.set()

    def _loop(self):
        while True:
            wait = 5
            if self.s["use_ntp"]:
                server = self.s["ntp_server"].strip() or "pool.ntp.org"
                try:
                    self.offset = ntp_offset(server)
                    self.status = f"NTP {server}  ({self.offset:+.2f}s)"
                    wait = 1800
                except Exception:
                    self.status = "NTP unreachable - using system clock"
                    wait = 60
            else:
                self.offset = 0.0
                self.status = "System clock"
            self.wake.wait(wait)
            self.wake.clear()


# --------------------------------------------------------------------- app
class FocusX:
    def __init__(self):
        self.s = load_settings()
        self.clock = TimeSource(self.s)

        self.root = tk.Tk()
        self.root.title("FocusX - Deep Work Protocol")
        self.root.configure(bg=BG)
        try:
            self.root.iconbitmap(os.path.join(BASE_DIR, "cherry.ico"))
        except Exception:
            pass
        w, h = 1100, 640
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
        self.root.minsize(860, 520)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # state
        self.state = "IDLE"  # IDLE FOCUS BREAK SLEEP
        self.paused = False
        self.remaining = 0.0
        self.phase_total = 1.0
        self.deadline = 0.0
        self.is_long = False
        self.sessions_done = 0
        self.last_tick = time.monotonic()
        self.last_save = time.monotonic()
        self.snooze_until = 0.0
        self.sleep_warned_for = None
        self.warned = set()
        self.autostart_deadline = (
            time.monotonic() + int(self.s["autostart_seconds"]) if self.s["autostart"] else None
        )

        # blocking
        self.blocked = False
        self.mouse_l = None
        self.kb_l = None
        self.key_buf = ""
        self.emergency = threading.Event()
        self.overlay = None
        self.ov_title = None
        self.ov_timer = None
        self.ov_clock = None
        self.break_text = ""

        self.build_ui()
        if self.s["start_minimized"]:
            self.root.iconify()
        self.tick()

    # ------------------------------------------------------------- UI
    def build_ui(self):
        r = self.root
        r.columnconfigure(0, weight=1)
        r.columnconfigure(1, weight=0)
        r.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(r, bg=BG, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        p = tk.Frame(r, bg=PANEL, width=380)
        p.grid(row=0, column=1, sticky="ns")
        p.grid_propagate(False)
        p.pack_propagate(False)

        self.state_lbl = tk.Label(p, text="STANDBY", font=("Segoe UI", 20, "bold"), fg=COL["IDLE"], bg=PANEL)
        self.state_lbl.pack(pady=(30, 0))
        self.time_lbl = tk.Label(p, text="00:00", font=("Verdana", 54, "bold"), fg="#ff9800", bg=PANEL)
        self.time_lbl.pack(pady=(5, 0))
        self.sub_lbl = tk.Label(p, text="", font=("Segoe UI", 11), fg="#aaaaaa", bg=PANEL, wraplength=340)
        self.sub_lbl.pack(pady=(0, 15))

        bf = tk.Frame(p, bg=PANEL)
        bf.pack(pady=5)
        self.main_btn = tk.Button(bf, text="START", width=12, command=self.on_main, bg="#2e7d32", fg="white",
                                  relief="flat", font=("Segoe UI", 10, "bold"), cursor="hand2")
        self.main_btn.grid(row=0, column=0, padx=6, pady=4)
        self.reset_btn = tk.Button(bf, text="RESET", width=12, command=self.reset, bg="#c62828", fg="white",
                                   relief="flat", font=("Segoe UI", 10, "bold"), cursor="hand2")
        self.reset_btn.grid(row=0, column=1, padx=6, pady=4)
        self.set_btn = tk.Button(bf, text="SETTINGS", width=26, command=self.open_settings, bg="#333333",
                                 fg="white", relief="flat", font=("Segoe UI", 10, "bold"), cursor="hand2")
        self.set_btn.grid(row=1, column=0, columnspan=2, pady=4)

        tk.Frame(p, bg="#333333", height=1).pack(fill="x", padx=25, pady=18)

        def info(color="#cccccc", size=10):
            l = tk.Label(p, text="", font=("Segoe UI", size), fg=color, bg=PANEL, wraplength=340, justify="center")
            l.pack(pady=3)
            return l

        self.auto_lbl = info("#00e5ff")
        self.stats_lbl = info("#ffffff", 11)
        self.sleep_lbl = info("#b39ddb")
        self.date_lbl = info("#aaaaaa")
        self.ntp_lbl = info("#777777", 9)

    def draw_clock(self, now):
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        size = min(w, h)
        if size < 100:
            return
        cx, cy = w / 2, h / 2
        R = size * 0.46
        color = COL[self.state]

        # progress ring
        pad = R
        c.create_oval(cx - pad, cy - pad, cx + pad, cy + pad, outline="#262626", width=max(4, int(size / 60)))
        if self.state in ("FOCUS", "BREAK"):
            frac = max(0.0, min(1.0, self.remaining / self.phase_total))
        elif self.state == "SLEEP":
            frac = 1.0
        else:
            frac = 0.0
        if frac >= 0.999:
            c.create_oval(cx - pad, cy - pad, cx + pad, cy + pad, outline=color, width=max(4, int(size / 60)))
        elif frac > 0:
            c.create_arc(cx - pad, cy - pad, cx + pad, cy + pad, start=90, extent=-360 * frac, style="arc",
                         outline=color, width=max(4, int(size / 60)))

        # face
        r = R * 0.93
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#181818", outline="")
        for i in range(60):
            a = math.radians(i * 6)
            major = i % 5 == 0
            r1 = r * (0.90 if major else 0.94)
            c.create_line(cx + r * 0.98 * math.sin(a), cy - r * 0.98 * math.cos(a),
                          cx + r1 * math.sin(a), cy - r1 * math.cos(a),
                          fill="#dddddd" if major else "#555555", width=3 if major else 1)
        nf = ("Segoe UI", max(10, int(size / 24)), "bold")
        for n in range(1, 13):
            a = math.radians(n * 30)
            c.create_text(cx + r * 0.78 * math.sin(a), cy - r * 0.78 * math.cos(a), text=str(n), fill="#eeeeee",
                          font=nf)

        # labels inside face
        c.create_text(cx, cy - r * 0.32, text={"IDLE": "STANDBY", "FOCUS": "FOCUS" if not self.paused else "PAUSED",
                                               "BREAK": "BREAK", "SLEEP": "SLEEPING"}[self.state],
                      fill=color, font=("Segoe UI", max(9, int(size / 32)), "bold"))
        c.create_text(cx, cy + r * 0.40, text=now.strftime("%H:%M:%S"), fill="#ffffff",
                      font=("Verdana", max(10, int(size / 24)), "bold"))
        c.create_text(cx, cy + r * 0.54, text=now.strftime("%a %d %b %Y"), fill="#888888",
                      font=("Segoe UI", max(8, int(size / 40))))

        # hands
        sec = now.second + now.microsecond / 1e6
        mn = now.minute + sec / 60
        hr = (now.hour % 12) + mn / 60

        def hand(deg, length, width, col, tail=0.12):
            a = math.radians(deg)
            sa, ca = math.sin(a), math.cos(a)
            c.create_line(cx - length * tail * sa, cy + length * tail * ca,
                          cx + length * sa, cy - length * ca,
                          width=width, fill=col, capstyle="round")

        hand(hr * 30, r * 0.50, max(5, int(size / 70)), "#ffffff")
        hand(mn * 6, r * 0.75, max(3, int(size / 110)), "#ffffff")
        hand(sec * 6, r * 0.85, max(1, int(size / 300)), color, 0.2)
        d = max(5, int(size / 70))
        c.create_oval(cx - d, cy - d, cx + d, cy + d, fill=color, outline="")

    def update_panel(self, now):
        st = self.state
        col = COL[st]
        self.state_lbl.config(text={"IDLE": "STANDBY", "FOCUS": "PAUSED" if self.paused else "DEEP FOCUS",
                                    "BREAK": "LONG BREAK" if self.is_long else "BREAK",
                                    "SLEEP": "SLEEP MODE"}[st], fg=col)
        if st in ("FOCUS", "BREAK"):
            self.time_lbl.config(text=fmt(self.remaining), fg=col)
        elif st == "SLEEP":
            wake = next_occurrence(parse_hhmm(self.s["wake_time"]), now)
            self.time_lbl.config(text=fmt((wake - now).total_seconds()), fg=col)
        else:
            self.time_lbl.config(text=f"{int(self.s['work_min']):02d}:00", fg="#555555")

        w = int(self.s["warn_seconds"])
        if st == "FOCUS" and not self.paused and w > 0 and self.remaining <= w:
            self.sub_lbl.config(text=f"INPUT LOCK IN {int(math.ceil(self.remaining))}s - SAVE YOUR WORK!")
            self.time_lbl.config(fg=COL["BREAK"])
        elif st == "FOCUS":
            nxt = self.next_break_is_long()
            self.sub_lbl.config(text=f"Session {self.sessions_done + 1}  -  next: {'long ' if nxt else ''}break")
        elif st == "SLEEP":
            self.sub_lbl.config(text=f"Locked until {self.s['wake_time']}")
        elif st == "BREAK":
            self.sub_lbl.config(text="Input locked. Rest your eyes.")
        else:
            self.sub_lbl.config(text="Ready.")

        # buttons
        locked = st in ("BREAK", "SLEEP")
        if st == "IDLE":
            self.main_btn.config(text="START", bg="#2e7d32", state="normal")
        elif st == "FOCUS":
            self.main_btn.config(text="CONTINUE" if self.paused else "PAUSE",
                                 bg="#fb8c00" if self.paused else "#333333", state="normal")
        else:
            self.main_btn.config(state="disabled", bg="#333333")
        self.reset_btn.config(state="disabled" if locked else "normal")
        self.set_btn.config(state="disabled" if locked else "normal")

        # info
        if st == "IDLE" and self.autostart_deadline:
            left = max(0, int(math.ceil(self.autostart_deadline - time.monotonic())))
            self.auto_lbl.config(text=f"Launching in {left}s  (RESET to cancel)")
        else:
            self.auto_lbl.config(text="")
        stt = self.s["stats"]
        self.stats_lbl.config(text=f"Today: {stt['sessions']} sessions  |  {fmt_long(stt['focus_sec'])} focused")
        if self.s["sleep_enabled"]:
            if st == "SLEEP":
                self.sleep_lbl.config(text=f"Wake at {self.s['wake_time']}")
            else:
                ns = next_occurrence(parse_hhmm(self.s["sleep_time"]), now)
                self.sleep_lbl.config(text=f"Sleep at {self.s['sleep_time']}  (in {fmt_long((ns - now).total_seconds())})")
        else:
            self.sleep_lbl.config(text="Sleep lock: off")
        self.date_lbl.config(text=now.strftime("%A, %d %B %Y"))
        self.ntp_lbl.config(text=self.clock.status)

    # ------------------------------------------------------------- loop
    def tick(self):
        try:
            self._tick()
        except Exception:
            pass
        self.root.after(100, self.tick)

    def _tick(self):
        mono = time.monotonic()
        dtm = min(mono - self.last_tick, 2.0)
        self.last_tick = mono
        now = self.clock.now()
        self.rollover_stats(now)

        if self.emergency.is_set():
            self.emergency.clear()
            self.handle_emergency()

        # sleep window logic
        in_sleep = self.in_sleep_window(now)
        if in_sleep and self.state != "SLEEP" and mono >= self.snooze_until:
            self.enter_sleep()
        elif self.state == "SLEEP" and not in_sleep:
            self.exit_sleep()

        # sleep warning (5 min before)
        if self.s["sleep_enabled"] and self.state != "SLEEP":
            ns = next_occurrence(parse_hhmm(self.s["sleep_time"]), now)
            if (ns - now).total_seconds() <= 300 and self.sleep_warned_for != ns:
                self.sleep_warned_for = ns
                self.beep([(900, 200), (700, 200), (900, 200)])

        if self.state == "IDLE":
            if self.autostart_deadline and mono >= self.autostart_deadline:
                self.autostart_deadline = None
                self.start_focus()
        elif self.state in ("FOCUS", "BREAK") and not self.paused:
            self.remaining = self.deadline - mono
            if self.state == "FOCUS":
                self.s["stats"]["focus_sec"] += dtm
                self.check_break_warning()
            if self.remaining <= 0:
                if self.state == "FOCUS":
                    self.finish_focus()
                else:
                    self.finish_break()

        if mono - self.last_save > 60:
            self.last_save = mono
            save_settings(self.s)

        self.update_overlay(now)
        self.update_panel(now)
        self.draw_clock(now)

    # ---------------------------------------------------------- phases
    def in_sleep_window(self, now):
        if not self.s["sleep_enabled"]:
            return False
        st, wt, t = parse_hhmm(self.s["sleep_time"]), parse_hhmm(self.s["wake_time"]), now.time()
        if st == wt:
            return False
        if st > wt:
            return t >= st or t < wt
        return st <= t < wt

    def next_break_is_long(self):
        n = max(1, int(self.s["long_every"]))
        return (self.sessions_done + 1) % n == 0 and int(self.s["long_break_sec"]) > 0

    def start_focus(self):
        self.hide_overlay()
        self.unblock()
        self.state = "FOCUS"
        self.paused = False
        self.phase_total = max(1, int(self.s["work_min"])) * 60.0
        self.remaining = self.phase_total
        self.deadline = time.monotonic() + self.phase_total
        self.warned = set()
        self.beep([(800, 150), (1000, 150)])

    def check_break_warning(self):
        """Tri-beat when the break (input lock) is about to start."""
        w = int(self.s["warn_seconds"])
        if w <= 0 or self.remaining <= 0:
            return
        marks = [w] + ([10] if w > 10 else [])
        for m in marks:
            if self.remaining <= m and m not in self.warned:
                self.warned.add(m)
                self.beep([(1000, 120), (0, 80), (1000, 120), (0, 80), (1000, 120)], force=True)
                break

    def finish_focus(self):
        self.s["stats"]["sessions"] += 1
        self.sessions_done += 1
        save_settings(self.s)
        self.is_long = self.sessions_done % max(1, int(self.s["long_every"])) == 0 and int(self.s["long_break_sec"]) > 0
        secs = int(self.s["long_break_sec"]) if self.is_long else int(self.s["break_sec"])
        self.state = "BREAK"
        self.paused = False
        self.phase_total = float(max(1, secs))
        self.remaining = self.phase_total
        self.deadline = time.monotonic() + self.phase_total
        self.break_text = random.choice(FUNNY)
        self.show_overlay()
        self.block_inputs()
        self.beep([(600, 300), (500, 300)])

    def finish_break(self):
        self.start_focus()

    def enter_sleep(self):
        self.state = "SLEEP"
        self.paused = False
        self.show_overlay()
        self.block_inputs()
        self.beep([(500, 400), (400, 400), (300, 600)])

    def exit_sleep(self):
        self.hide_overlay()
        self.unblock()
        self.sleep_warned_for = None
        self.state = "IDLE"
        self.start_focus()

    def handle_emergency(self):
        if self.state == "BREAK":
            self.start_focus()
        elif self.state == "SLEEP":
            self.hide_overlay()
            self.unblock()
            self.state = "IDLE"
            self.snooze_until = time.monotonic() + 15 * 60  # sleep lock paused 15 min
            self.autostart_deadline = None

    def on_main(self):
        if self.state == "IDLE":
            self.autostart_deadline = None
            self.start_focus()
        elif self.state == "FOCUS":
            if self.paused:
                self.deadline = time.monotonic() + self.remaining
                self.paused = False
            else:
                self.paused = True

    def reset(self):
        if self.state in ("BREAK", "SLEEP"):
            return
        self.autostart_deadline = None
        self.state = "IDLE"
        self.paused = False
        self.remaining = 0
        self.sessions_done = 0

    # ----------------------------------------------------------- stats
    def rollover_stats(self, now):
        today = now.date().isoformat()
        if self.s["stats"].get("date") != today:
            self.s["stats"] = {"date": today, "focus_sec": 0.0, "sessions": 0}

    # --------------------------------------------------------- overlay
    def show_overlay(self):
        self.hide_overlay()
        o = tk.Toplevel(self.root)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        o.configure(bg="black")
        o.overrideredirect(True)
        o.geometry(f"{sw}x{sh}+0+0")
        o.attributes("-topmost", True)
        self.ov_title = tk.Label(o, text="", fg="white", bg="black", font=("Verdana", 28, "bold"))
        self.ov_title.pack(expand=True, side="top", pady=(sh // 5, 0))
        self.ov_timer = tk.Label(o, text="", fg=COL[self.state], bg="black", font=("Verdana", 64, "bold"))
        self.ov_timer.pack(expand=True)
        self.ov_clock = tk.Label(o, text="", fg="#666666", bg="black", font=("Verdana", 20))
        self.ov_clock.pack(side="bottom", pady=40)
        self.overlay = o
        o.focus_force()

    def hide_overlay(self):
        if self.overlay is not None:
            try:
                self.overlay.destroy()
            except Exception:
                pass
        self.overlay = None

    def update_overlay(self, now):
        if self.overlay is None:
            return
        try:
            self.overlay.lift()
            self.overlay.attributes("-topmost", True)
            if self.state == "BREAK":
                self.ov_title.config(text=self.break_text)
                self.ov_timer.config(text=f"MANDATORY BREAK\n{fmt(self.remaining)}")
            elif self.state == "SLEEP":
                wake = next_occurrence(parse_hhmm(self.s["wake_time"]), now)
                self.ov_title.config(text="GO TO SLEEP.")
                self.ov_timer.config(text=f"Unlocks at {self.s['wake_time']}\n{fmt((wake - now).total_seconds())}")
            self.ov_clock.config(text=now.strftime("%H:%M:%S"))
        except Exception:
            pass

    # -------------------------------------------------------- blocking
    def block_inputs(self):
        if self.blocked:
            return
        self.blocked = True
        self.key_buf = ""
        self.mouse_l = mouse.Listener(suppress=True)
        self.kb_l = keyboard.Listener(on_press=self._on_key, suppress=True)
        self.mouse_l.start()
        self.kb_l.start()
        threading.Thread(target=self._kill_taskmgr, daemon=True).start()

    def unblock(self):
        self.blocked = False
        for attr in ("mouse_l", "kb_l"):
            l = getattr(self, attr)
            if l:
                try:
                    l.stop()
                except Exception:
                    pass
                setattr(self, attr, None)

    def _on_key(self, key):
        try:
            phrase = self.s["emergency_phrase"].strip().lower()
            ch = getattr(key, "char", None)
            if phrase and ch:
                self.key_buf = (self.key_buf + ch.lower())[-len(phrase):]
                if self.key_buf == phrase:
                    self.key_buf = ""
                    self.emergency.set()
        except Exception:
            pass

    def _kill_taskmgr(self):
        while self.blocked:
            for proc in psutil.process_iter(["name"]):
                try:
                    if (proc.info["name"] or "").lower() == "taskmgr.exe":
                        proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            time.sleep(0.5)

    # ----------------------------------------------------------- sound
    def beep(self, pattern, force=False):
        if (not self.s["sound"] and not force) or winsound is None:
            return

        def run():
            for f, d in pattern:
                try:
                    if f <= 0:
                        time.sleep(d / 1000.0)
                    else:
                        winsound.Beep(f, d)
                except Exception:
                    pass

        threading.Thread(target=run, daemon=True).start()

    # -------------------------------------------------------- settings
    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("FocusX Settings")
        win.configure(bg=PANEL)
        win.transient(self.root)
        win.resizable(False, False)
        try:
            win.iconbitmap(os.path.join(BASE_DIR, "cherry.ico"))
        except Exception:
            pass

        fields = [
            ("Focus (minutes)", "work_min", int),
            ("Break (SECONDS)", "break_sec", int),
            ("Long break (SECONDS)", "long_break_sec", int),
            ("Beep warning before break (sec, 0=off)", "warn_seconds", "int0"),
            ("Long break every N sessions", "long_every", int),
            ("Sleep time (HH:MM, 24h)", "sleep_time", "time"),
            ("Wake time (HH:MM, 24h)", "wake_time", "time"),
            ("NTP server", "ntp_server", str),
            ("Manual time adjust (minutes)", "manual_offset_min", float),
            ("Emergency unlock phrase", "emergency_phrase", str),
        ]
        entries = {}
        for i, (label, key, typ) in enumerate(fields):
            tk.Label(win, text=label, fg="#cccccc", bg=PANEL, font=("Segoe UI", 10)).grid(
                row=i, column=0, sticky="w", padx=15, pady=5)
            e = tk.Entry(win, width=22, bg="#2a2a2a", fg="white", insertbackground="white", relief="flat")
            e.insert(0, str(self.s[key]))
            e.grid(row=i, column=1, padx=15, pady=5)
            entries[key] = (e, typ)

        checks = [
            ("Enable sleep lock", "sleep_enabled"),
            ("Sync time with NTP", "use_ntp"),
            ("Auto-start focus on launch", "autostart"),
            ("Sounds", "sound"),
            ("Start minimized", "start_minimized"),
        ]
        vars_ = {}
        row = len(fields)
        for label, key in checks:
            v = tk.BooleanVar(value=bool(self.s[key]))
            tk.Checkbutton(win, text=label, variable=v, fg="#cccccc", bg=PANEL, selectcolor="#2a2a2a",
                           activebackground=PANEL, activeforeground="white",
                           font=("Segoe UI", 10)).grid(row=row, column=0, columnspan=2, sticky="w", padx=12)
            vars_[key] = v
            row += 1

        def save():
            new = {}
            try:
                for key, (e, typ) in entries.items():
                    raw = e.get().strip()
                    if typ == "time":
                        parse_hhmm(raw)
                        new[key] = raw
                    elif typ is int:
                        val = int(raw)
                        if val < 1:
                            raise ValueError(key)
                        new[key] = val
                    elif typ == "int0":
                        val = int(raw)
                        if val < 0:
                            raise ValueError(key)
                        new[key] = val
                    elif typ is float:
                        new[key] = float(raw)
                    else:
                        new[key] = raw
            except Exception:
                messagebox.showerror("Invalid value", "Check the numbers and use HH:MM for times.", parent=win)
                return
            for key, v in vars_.items():
                new[key] = bool(v.get())
            self.s.update(new)
            save_settings(self.s)
            self.clock.resync()
            win.destroy()

        tk.Button(win, text="SAVE", command=save, bg="#2e7d32", fg="white", relief="flat", width=14,
                  font=("Segoe UI", 10, "bold")).grid(row=row, column=0, pady=15)
        tk.Button(win, text="CANCEL", command=win.destroy, bg="#333333", fg="white", relief="flat", width=14,
                  font=("Segoe UI", 10, "bold")).grid(row=row, column=1, pady=15)
        tk.Label(win, text="Duration changes apply from the next phase.", fg="#777777", bg=PANEL,
                 font=("Segoe UI", 9)).grid(row=row + 1, column=0, columnspan=2, pady=(0, 10))

    # ------------------------------------------------------------ exit
    def on_close(self):
        if self.state in ("BREAK", "SLEEP"):
            return
        if self.state == "FOCUS" and not messagebox.askyesno("Quit FocusX", "A focus session is running. Quit anyway?"):
            return
        self.unblock()
        self.hide_overlay()
        save_settings(self.s)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def single_instance():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        return s
    except OSError:
        return None


if __name__ == "__main__":
    try:  # crisp text + correct screen size on high-DPI displays
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    lock = single_instance()
    if lock is None:
        sys.exit(0)  # already running (e.g. startup + manual launch)
    FocusX().run()