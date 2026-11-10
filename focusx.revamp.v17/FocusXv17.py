import tkinter as tk
import threading
import time
import sys
import math
import random
import ctypes
import os
import json
import datetime
import subprocess
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape
import psutil
from pynput import mouse, keyboard

# --- FocusX v15 : Deep Work Protocol ---
#
# Files next to this script at build time (build_focusx.bat bakes them in):
#   cherry.ico   window + exe icon
#   cherry.wav   (or cherry.mp3)  plays when 20s remain in a pomodoro
#   beep.wav     (or beep.mp3)    optional, plays when a Sprint ends
#                                 (if missing, Windows beeps are used)
#
# POMODORO tab : adjustable focus/rest (default 40/1), auto-starts 5s after
#                launch unless you touch anything. Lock engages at 20s left
#                and holds through the break.
# SPRINT tab   : adjustable minutes (default 25), no lock, beeps at the end.
# SLEEP TIMER  : bedtime 10 PM - 12 AM (default 11:30 PM, armed from launch).
#                10s notice, then the PC shuts down. Launching between 12 AM and
#                5:30 AM = keyboard/mouse taken over + 10s notice + shutdown.
#                If shutdown ever fails, input stays locked until 5:30 AM, then
#                the pomodoro starts.
# AUTOSTART    : registers itself in Task Scheduler as FocusX15 (logon + a
#                watchdog repeat) and re-adds itself on every run and every
#                minute while running. Startup-folder fallback if schtasks fails.
#
# Test flags (so you don't lock yourself out while testing):
#   FocusX15.exe --dry-run                  never shuts down, releases after ~15s
#   FocusX15.exe --dry-run --fake-now 23:29 pretend the clock says 23:29
#   FocusX15.exe --uninstall                remove the Task Scheduler entry + exit

WARNING_SECONDS = 20
AUTOSTART_SECONDS = 5
DEFAULT_FOCUS = 40
DEFAULT_REST = 1
DEFAULT_SPRINT = 25

# sleep timer (all in minutes since midnight)
BEDTIME_MIN = 22 * 60            # earliest bedtime you can pick: 10:00 PM
BEDTIME_MAX = 24 * 60            # latest: 12:00 AM (midnight)
DEFAULT_BEDTIME = 23 * 60 + 30   # 11:30 PM if you don't touch it
WAKE_MIN = 5 * 60 + 30           # inputs stay locked until 5:30 AM
CURFEW_NOTICE_SECONDS = 10
SHUTDOWN_GRACE_SECONDS = 15      # shutdown should have killed us by now; if not, hold the lock

TASK_NAME = 'FocusX15'
WATCHDOG_MINUTES = 5             # Task Scheduler relaunches the app this often if it got killed
CREATE_NO_WINDOW = 0x08000000

ICON_NAME = 'cherry.ico'
WARNING_AUDIO = ('cherry.wav', 'cherry.mp3')
BEEP_AUDIO = ('beep.wav', 'beep.mp3')

BG = '#121212'


# ----------------------------------------------------------------- assets --
def _asset_dirs():
    dirs = []
    try:
        dirs.append(Path(__file__).resolve().parent)  # Nuitka onefile temp dir (embedded files)
    except Exception:
        pass
    comp = globals().get('__compiled__')
    if comp is not None:
        try:
            dirs.append(Path(comp.containing_dir))
        except Exception:
            pass
    try:
        dirs.append(Path(sys.argv[0]).resolve().parent)
    except Exception:
        pass
    try:
        dirs.append(Path.cwd())
    except Exception:
        pass
    return dirs


def find_asset(*names):
    for d in _asset_dirs():
        for name in names:
            f = d / name
            if f.exists():
                return f
    return None


# ------------------------------------------------------------------ audio --
def _mci(cmd):
    try:
        ctypes.windll.winmm.mciSendStringW(cmd, None, 0, None)
    except Exception:
        pass


def play_audio_async(path):
    """wav -> winsound, mp3 -> Windows MCI. No extra packages needed."""
    path = str(path)
    low = path.lower()
    try:
        if low.endswith('.wav'):
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            return True
        if low.endswith('.mp3'):
            _mci('close fxsnd')
            _mci(f'open "{path}" type mpegvideo alias fxsnd')
            _mci('play fxsnd')
            return True
    except Exception:
        pass
    return False


def windows_beeps_async():
    def _run():
        try:
            import winsound
            for freq in (880, 1175, 1568, 1175, 1568):
                winsound.Beep(freq, 220)
                time.sleep(0.05)
        except Exception:
            try:
                import winsound
                winsound.MessageBeep()
            except Exception:
                pass
    threading.Thread(target=_run, daemon=True).start()


def play_warning():
    audio = find_asset(*WARNING_AUDIO)
    if audio:
        play_audio_async(audio)


def play_time_up():
    audio = find_asset(*BEEP_AUDIO)
    if audio and play_audio_async(audio):
        return
    windows_beeps_async()


# ------------------------------------------------------------------ clock --
def fmt_clock(mins):
    mins %= 1440
    h, m = divmod(mins, 60)
    return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


BEDTIME_CHOICES = {fmt_clock(m): m for m in range(BEDTIME_MIN, BEDTIME_MAX + 1, 15)}


def _state_path():
    base = os.environ.get('APPDATA') or str(Path.home())
    d = Path(base) / 'FocusX15'
    d.mkdir(parents=True, exist_ok=True)
    return d / 'bedtime.json'


# -------------------------------------------------- autostart / scheduler --
def _run_hidden(args, timeout=20):
    try:
        return subprocess.run(args, capture_output=True, text=True, errors='ignore',
                              timeout=timeout, stdin=subprocess.DEVNULL,
                              creationflags=CREATE_NO_WINDOW)
    except Exception:
        return None


def launch_command():
    """(exe, args) that Task Scheduler / Startup should run."""
    comp = globals().get('__compiled__')
    if comp is not None:
        cands = [os.environ.get('NUITKA_ONEFILE_BINARY'),
                 getattr(comp, 'original_argv0', None),
                 sys.argv[0]]
        for c in cands:
            if c and str(c).lower().endswith('.exe'):
                try:
                    return str(Path(c).resolve()), ''
                except Exception:
                    pass
        return str(Path(sys.argv[0]).resolve()), ''
    py = Path(sys.executable)
    pyw = py.with_name('pythonw.exe')
    exe = pyw if pyw.exists() else py
    return str(exe), f'"{Path(__file__).resolve()}"'


def _task_xml(exe, args):
    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".strip('\\')
    workdir = str(Path(exe).parent)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>FocusX Deep Work Protocol</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{xml_escape(user)}</UserId>
    </LogonTrigger>
    <TimeTrigger>
      <Repetition><Interval>PT{WATCHDOG_MINUTES}M</Interval></Repetition>
      <StartBoundary>2024-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{xml_escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <AllowHardTerminate>true</AllowHardTerminate>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{xml_escape(exe)}</Command>
      <Arguments>{xml_escape(args)}</Arguments>
      <WorkingDirectory>{xml_escape(workdir)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _task_ok(exe, args):
    r = _run_hidden(['schtasks', '/Query', '/TN', TASK_NAME, '/XML'])
    if not r or r.returncode != 0:
        return False
    out = r.stdout.lower()
    if '<enabled>false</enabled>' in out:
        return False
    if xml_escape(exe).lower() not in out:
        return False
    if args and xml_escape(args).lower() not in out:
        return False
    return True


def _create_task(exe, args):
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix='.xml')
        os.close(fd)
        with open(tmp, 'w', encoding='utf-16') as f:
            f.write(_task_xml(exe, args))
        r = _run_hidden(['schtasks', '/Create', '/TN', TASK_NAME, '/XML', tmp, '/F'])
        return bool(r and r.returncode == 0)
    except Exception:
        return False
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except Exception:
                pass


def _startup_vbs_path():
    appdata = os.environ.get('APPDATA')
    if not appdata:
        return None
    return (Path(appdata) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs'
            / 'Startup' / 'FocusX15.vbs')


def _write_startup_fallback(exe, args):
    path = _startup_vbs_path()
    if path is None:
        return
    cmd = f'"{exe}"' + (f' {args}' if args else '')
    body = f'CreateObject("WScript.Shell").Run "{cmd.replace(chr(34), chr(34) * 2)}", 0, False\r\n'
    try:
        if path.exists() and path.read_text(errors='ignore') == body:
            return
        path.write_text(body)
    except Exception:
        pass


def ensure_autostart():
    """Task Scheduler entry named FocusX15; re-created if missing/changed/disabled."""
    exe, args = launch_command()
    if _task_ok(exe, args):
        return
    if _create_task(exe, args) and _task_ok(exe, args):
        return
    _write_startup_fallback(exe, args)   # schtasks refused: Startup folder instead


def remove_autostart():
    _run_hidden(['schtasks', '/Delete', '/TN', TASK_NAME, '/F'])
    path = _startup_vbs_path()
    if path is not None:
        try:
            path.unlink()
        except Exception:
            pass


def start_guardian():
    def _loop():
        while True:
            try:
                ensure_autostart()
            except Exception:
                pass
            time.sleep(60)
    threading.Thread(target=_loop, daemon=True).start()


_MUTEX = None


def acquire_single_instance():
    """Task Scheduler's watchdog may launch us again; only one copy may live."""
    global _MUTEX
    try:
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        _MUTEX = k32.CreateMutexW(None, False, 'FocusX15_SingleInstance')
        return ctypes.get_last_error() != 183   # ERROR_ALREADY_EXISTS
    except Exception:
        return True


# -------------------------------------------------------------------- app --
class FocusX:
    def __init__(self, dry_run=False, fake_now=None):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        self.root = tk.Tk()
        self.root.title('Deep Work Protocol' + (' [DRY RUN]' if dry_run else ''))
        self.root.configure(bg=BG)

        icon = find_asset(ICON_NAME)
        if icon:
            try:
                self.root.iconbitmap(str(icon))
            except Exception:
                pass

        w, h = 420, 740
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f'{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}')
        self.root.resizable(False, False)

        # session state
        self.tab = 'pomodoro'
        self.session = None          # 'pomodoro' | 'sprint' | None
        self.phase = None            # 'work' | 'break' | 'sprint'
        self.running = False
        self.paused = False
        self.deadline = 0.0
        self.paused_left = 0.0
        self.focus_seconds = DEFAULT_FOCUS * 60
        self.rest_seconds = DEFAULT_REST * 60
        self.warning_played = False
        self._tm_killer_active = False
        self._tick_job = None

        # autostart
        self.autostart_active = True
        self.autostart_left = AUTOSTART_SECONDS
        self._auto_job = None

        # input blockers / overlay
        self.mouse_listener = None
        self.kb_listener = None
        self.overlay = None
        self.overlay_label = None

        # sleep timer / curfew
        self.dry_run = dry_run
        self.time_offset = datetime.timedelta(0)
        if fake_now is not None:
            fake = datetime.datetime.now().replace(hour=fake_now[0], minute=fake_now[1],
                                                   second=0, microsecond=0)
            self.time_offset = fake - datetime.datetime.now()
        self.bedtime_min = self._load_bedtime()
        self.curfew = False
        self.curfew_phase = None          # 'countdown' | 'hold'
        self.curfew_deadline = 0.0
        self.curfew_suppressed = False    # dry-run only: don't re-trigger after release
        self.curfew_label = None
        self.curfew_headline = ''
        self._note_until = 0.0
        self._clock_job = None

        self.setup_ui()
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        if self._curfew_due():
            self.autostart_active = False   # night: no pomodoro, straight to lights out
            self._hide_cancel_btn()
        else:
            self._autostart_tick()
        self.root.after(100, self._clock_tick)

    # -------------------------------------------------------------- state --
    @property
    def locked(self):
        return (self.session == 'pomodoro' and self.running
                and (self.phase == 'break' or self.warning_played))

    # ----------------------------------------------------------------- ui --
    def setup_ui(self):
        self.header = tk.Label(self.root, text='DEEP WORK PROTOCOL',
                               font=('Segoe UI', 16, 'bold'), fg='#00ff00', bg=BG)
        self.header.pack(pady=(22, 8))

        # tab bar
        self.tab_bar = tk.Frame(self.root, bg=BG)
        self.tab_bar.pack()
        self.tab_btns = {}
        for key, label in (('pomodoro', 'POMODORO'), ('sprint', 'SPRINT')):
            b = tk.Button(self.tab_bar, text=label, width=14, relief='flat', cursor='hand2',
                          font=('Segoe UI', 10, 'bold'),
                          command=lambda k=key: self.switch_tab(k))
            b.pack(side='left', padx=4)
            self.tab_btns[key] = b

        self.auto_label = tk.Label(self.root, text='', font=('Verdana', 11, 'italic'),
                                   fg='#aaaaaa', bg=BG)
        self.auto_label.pack(pady=(14, 4))

        self.content = tk.Frame(self.root, bg=BG)
        self.content.pack()

        self._build_pomodoro_frame()
        self._build_sprint_frame()

        self.timer_label = tk.Label(self.root, text='00:00',
                                    font=('Verdana', 50, 'bold'), fg='#ff9800', bg=BG)
        self.timer_label.pack(pady=(14, 6))

        self.ctrl_frame = tk.Frame(self.root, bg=BG)
        self.ctrl_frame.pack(pady=10)
        self.pause_btn = tk.Button(self.ctrl_frame, text='PAUSE', width=12, command=self.toggle_pause,
                                   state='disabled', bg='#333333', fg='#ffffff', relief='flat',
                                   font=('Segoe UI', 9, 'bold'))
        self.pause_btn.pack(side='left', padx=8)
        self.reset_btn = tk.Button(self.ctrl_frame, text='RESET', width=12, command=self.reset_app,
                                   state='disabled', bg='#c62828', fg='white', relief='flat',
                                   font=('Segoe UI', 9, 'bold'))
        self.reset_btn.pack(side='left', padx=8)

        self._build_sleep_panel()

        self._show_tab('pomodoro')
        self.auto_label.config(text=f'Launching in: {AUTOSTART_SECONDS}s')

    def _spin(self, parent, var, lo, hi):
        return tk.Spinbox(parent, from_=lo, to=hi, textvariable=var, width=5, justify='center',
                          font=('Segoe UI', 14, 'bold'), bg='#1e1e1e', fg='#ffffff',
                          buttonbackground='#333333', insertbackground='#ffffff',
                          relief='flat', disabledbackground='#181818', disabledforeground='#666666')

    def _row_label(self, parent, text):
        return tk.Label(parent, text=text, font=('Segoe UI', 11), fg='#cccccc', bg=BG)

    def _build_pomodoro_frame(self):
        f = tk.Frame(self.content, bg=BG)
        self.pomo_frame = f

        self.focus_var = tk.StringVar(value=str(DEFAULT_FOCUS))
        self.rest_var = tk.StringVar(value=str(DEFAULT_REST))

        row1 = tk.Frame(f, bg=BG)
        row1.pack(pady=4)
        self._row_label(row1, 'Focus (min)').pack(side='left', padx=(0, 10))
        self.focus_spin = self._spin(row1, self.focus_var, 1, 240)
        self.focus_spin.pack(side='left')

        row2 = tk.Frame(f, bg=BG)
        row2.pack(pady=4)
        self._row_label(row2, 'Rest (min)').pack(side='left', padx=(0, 18))
        self.rest_spin = self._spin(row2, self.rest_var, 1, 60)
        self.rest_spin.pack(side='left')

        self.pomo_start = tk.Button(f, text='START POMODORO', width=22, height=2,
                                    command=self.start_pomodoro, bg='#2e7d32', fg='white',
                                    font=('Segoe UI', 10, 'bold'), relief='raised', bd=4,
                                    activebackground='#1b5e20', cursor='hand2')
        self.pomo_start.pack(pady=(14, 6))

        self.cancel_auto_btn = tk.Button(f, text='CANCEL AUTO-START', width=22,
                                         command=self.cancel_autostart, bg='#333333', fg='white',
                                         relief='flat', font=('Segoe UI', 9, 'bold'), cursor='hand2')
        self.cancel_auto_btn.pack(pady=4)

        tk.Label(f, text='Lock engages 20s before the break and holds until it ends.',
                 font=('Segoe UI', 8), fg='#777777', bg=BG).pack(pady=(8, 0))

        # touching the numbers cancels the auto-start
        self.focus_var.trace_add('write', lambda *a: self.cancel_autostart())
        self.rest_var.trace_add('write', lambda *a: self.cancel_autostart())

    def _build_sprint_frame(self):
        f = tk.Frame(self.content, bg=BG)
        self.sprint_frame = f

        self.sprint_var = tk.StringVar(value=str(DEFAULT_SPRINT))
        row = tk.Frame(f, bg=BG)
        row.pack(pady=(4, 4))
        self._row_label(row, 'Sprint (min)').pack(side='left', padx=(0, 10))
        self.sprint_spin = self._spin(row, self.sprint_var, 1, 240)
        self.sprint_spin.pack(side='left')

        self.sprint_start = tk.Button(f, text='START SPRINT', width=22, height=2,
                                      command=self.start_sprint, bg='#0277bd', fg='white',
                                      font=('Segoe UI', 10, 'bold'), relief='raised', bd=4,
                                      activebackground='#01579b', cursor='hand2')
        self.sprint_start.pack(pady=(14, 6))

        tk.Label(f, text='No lock. Pause/reset anytime. Beeps when time is up.',
                 font=('Segoe UI', 8), fg='#777777', bg=BG).pack(pady=(8, 0))

        # keep layout height equal to the pomodoro tab
        tk.Frame(f, bg=BG, height=44).pack()

    def _show_tab(self, key):
        self.tab = key
        self.pomo_frame.pack_forget()
        self.sprint_frame.pack_forget()
        (self.pomo_frame if key == 'pomodoro' else self.sprint_frame).pack()
        for k, b in self.tab_btns.items():
            if k == key:
                b.config(bg='#00e5ff', fg='#000000')
            else:
                b.config(bg='#2a2a2a', fg='#bbbbbb')

    def switch_tab(self, key):
        if self.running or key == self.tab:
            return
        self.cancel_autostart()
        self._show_tab(key)

    def _set_config_enabled(self, enabled):
        state = 'normal' if enabled else 'disabled'
        for w in (self.focus_spin, self.rest_spin, self.sprint_spin,
                  self.pomo_start, self.sprint_start):
            w.config(state=state)
        for b in self.tab_btns.values():
            b.config(state=state)

    # ---------------------------------------------------------- autostart --
    def _autostart_tick(self):
        self._auto_job = None
        if not self.autostart_active or self.running:
            return
        if self.autostart_left <= 0:
            self.autostart_active = False
            self._hide_cancel_btn()
            self.start_pomodoro()
            return
        self.auto_label.config(text=f'Launching in: {self.autostart_left}s')
        self.autostart_left -= 1
        self._auto_job = self.root.after(1000, self._autostart_tick)

    def _hide_cancel_btn(self):
        try:
            self.cancel_auto_btn.pack_forget()
        except Exception:
            pass

    def cancel_autostart(self):
        if not self.autostart_active:
            return
        self.autostart_active = False
        if self._auto_job is not None:
            try:
                self.root.after_cancel(self._auto_job)
            except Exception:
                pass
            self._auto_job = None
        self._hide_cancel_btn()
        if not self.running:
            self.auto_label.config(text='Auto-start off. Pick your mode.', fg='#aaaaaa')

    # ----------------------------------------------------------- starters --
    @staticmethod
    def _read_int(var, lo, hi, default):
        try:
            val = int(float(var.get()))
        except Exception:
            val = default
        val = max(lo, min(hi, val))
        if var.get() != str(val):
            var.set(str(val))
        return val

    def start_pomodoro(self):
        if self.running or self.curfew:
            return
        self.autostart_active = False
        focus = self._read_int(self.focus_var, 1, 240, DEFAULT_FOCUS)
        rest = self._read_int(self.rest_var, 1, 60, DEFAULT_REST)
        self.focus_seconds = focus * 60
        self.rest_seconds = rest * 60
        self.session = 'pomodoro'
        self.phase = 'work'
        self.warning_played = False
        self._begin(self.focus_seconds)
        self.auto_label.config(text='PROTOCOL ENGAGED', fg='#00e5ff')
        self.timer_label.config(fg='#ff9800')

    def start_sprint(self):
        if self.running or self.curfew:
            return
        self.cancel_autostart()
        minutes = self._read_int(self.sprint_var, 1, 240, DEFAULT_SPRINT)
        self.session = 'sprint'
        self.phase = 'sprint'
        self.warning_played = False
        self._begin(minutes * 60)
        self.auto_label.config(text=f'SPRINT: {minutes} MIN', fg='#00e5ff')
        self.timer_label.config(fg='#29b6f6')

    def _begin(self, seconds):
        self.deadline = time.monotonic() + seconds
        self.running = True
        self.paused = False
        self._set_config_enabled(False)
        self.pause_btn.config(state='normal', text='PAUSE', bg='#333333')
        self.reset_btn.config(state='normal', bg='#c62828')
        self._show(seconds)
        if self._tick_job is not None:
            try:
                self.root.after_cancel(self._tick_job)
            except Exception:
                pass
        self._tick_job = self.root.after(200, self._tick)

    # --------------------------------------------------------------- tick --
    def _show(self, secs):
        mins, s = divmod(int(secs), 60)
        txt = f'{mins:02d}:{s:02d}'
        self.timer_label.config(text=txt)
        if self.overlay_label is not None:
            try:
                self.overlay_label.config(text=f'MANDATORY BREAK\n\n{txt}')
            except Exception:
                pass

    def _tick(self):
        self._tick_job = None
        if not self.running:
            return
        if not self.paused:
            left = self.deadline - time.monotonic()
            self._show(max(0, math.ceil(left)))

            if self.session == 'pomodoro':
                if self.phase == 'work':
                    secs = math.ceil(left)
                    if (not self.warning_played and 0 < secs <= WARNING_SECONDS
                            and self.focus_seconds > WARNING_SECONDS):
                        self.trigger_warning()
                    if left <= 0:
                        self.start_break()
                elif self.phase == 'break':
                    if left <= 0:
                        self.end_break()
            elif self.session == 'sprint':
                if left <= 0:
                    self.finish_sprint()
                    return
        self._tick_job = self.root.after(200, self._tick)

    # ------------------------------------------------------------ controls --
    def toggle_pause(self):
        if not self.running or self.locked:
            return
        if self.paused:
            self.deadline = time.monotonic() + self.paused_left
            self.paused = False
        else:
            self.paused_left = max(0.0, self.deadline - time.monotonic())
            self.paused = True
        self.pause_btn.config(text='CONTINUE' if self.paused else 'PAUSE',
                              bg='#fb8c00' if self.paused else '#333333')

    def reset_app(self):
        if self.locked:
            return
        self._stop_session()
        self.timer_label.config(text='00:00', fg='#ff9800')
        self.auto_label.config(text='System Standby...', fg='#aaaaaa')

    def _stop_session(self):
        self.running = False
        self.paused = False
        self.session = None
        self.phase = None
        self.warning_played = False
        if self._tick_job is not None:
            try:
                self.root.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None
        self.unblock_everything()
        self._set_config_enabled(True)
        self.pause_btn.config(text='PAUSE', state='disabled', bg='#333333')
        self.reset_btn.config(state='disabled', bg='#c62828')

    def _on_close(self):
        # Sealed during a lock / curfew. Otherwise the X just tucks the window
        # away: FocusX doesn't quit (and Task Scheduler brings it back if killed).
        if self.locked or self.curfew:
            try:
                self.root.bell()
            except Exception:
                pass
            return
        try:
            self.root.iconify()
        except Exception:
            pass

    # ------------------------------------------------------------- sprint --
    def finish_sprint(self):
        self._stop_session()
        self.timer_label.config(text='00:00', fg='#00e676')
        self.auto_label.config(text="TIME'S UP!", fg='#00e676')
        play_time_up()
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.bell()
        except Exception:
            pass

    # ------------------------------------------------------ pomodoro lock --
    def trigger_warning(self):
        self.warning_played = True
        self.auto_label.config(text='\u26a0 20 SECONDS TO BREAK \u26a0', fg='#ff1744')
        self.pause_btn.config(state='disabled', bg='#444444')
        self.reset_btn.config(state='disabled', bg='#444444')
        self.timer_label.config(fg='#ff1744')
        try:
            self.root.deiconify()
            self.root.lift()
        except Exception:
            pass
        self._ensure_tm_killer()
        threading.Thread(target=play_warning, daemon=True).start()

    def start_break(self):
        self.phase = 'break'
        self.deadline = time.monotonic() + self.rest_seconds
        self.timer_label.config(fg='#ff1744')
        self.pause_btn.config(state='disabled')
        self.reset_btn.config(state='disabled')
        self._ensure_tm_killer()
        self.show_blocker_overlay()
        self.block_inputs()

    def end_break(self):
        self.phase = 'work'
        self.warning_played = False
        self.deadline = time.monotonic() + self.focus_seconds
        self.unblock_everything()
        self.timer_label.config(fg='#ff9800')
        self.pause_btn.config(state='normal', bg='#333333', text='PAUSE')
        self.reset_btn.config(state='normal', bg='#c62828')
        self.auto_label.config(text='PROTOCOL ENGAGED', fg='#00e5ff')

    def _ensure_tm_killer(self):
        if self._tm_killer_active:
            return
        self._tm_killer_active = True
        threading.Thread(target=self._tm_killer_loop, daemon=True).start()

    def _tm_killer_loop(self):
        try:
            while (self.running and self.locked) or self.curfew:
                for proc in psutil.process_iter(['name']):
                    try:
                        if proc.info['name'] == 'Taskmgr.exe':
                            proc.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                time.sleep(0.5)
        finally:
            self._tm_killer_active = False

    def show_blocker_overlay(self):
        self.overlay = tk.Toplevel(self.root)
        self.overlay.attributes('-fullscreen', True)
        self.overlay.attributes('-topmost', True)
        self.overlay.configure(bg='black')
        self.overlay.protocol('WM_DELETE_WINDOW', lambda: None)

        funny_texts = [
            'STEP AWAY FROM THE BOX.',
            'YOUR KEYBOARD IS NOW A PAPERWEIGHT.',
            'BRAIN REBOOT IN PROGRESS...',
            'THE COMPUTER MISSES YOU, BUT I DO NOT CARE.',
            'GO STARE AT A TREE OR SOMETHING.',
            'ACCESS DENIED. REASON: YOU ARE TIRED.',
        ]
        self.overlay_label = tk.Label(self.overlay, text=random.choice(funny_texts),
                                      fg='white', bg='black', font=('Verdana', 28, 'bold'))
        self.overlay_label.pack(expand=True)
        mins, s = divmod(int(max(0, math.ceil(self.deadline - time.monotonic()))), 60)
        self.overlay_label.config(text=f'MANDATORY BREAK\n\n{mins:02d}:{s:02d}')

    def block_inputs(self):
        if self.mouse_listener or self.kb_listener:
            return
        self.mouse_listener = mouse.Listener(suppress=True)
        self.kb_listener = keyboard.Listener(suppress=True)
        self.mouse_listener.start()
        self.kb_listener.start()

    def unblock_everything(self):
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
        self.overlay_label = None
        self.curfew_label = None
        if self.overlay is not None:
            try:
                self.overlay.destroy()
            except Exception:
                pass
            self.overlay = None

    # ------------------------------------------------------- sleep timer --
    def _now(self):
        return datetime.datetime.now() + self.time_offset

    def _now_min(self):
        n = self._now()
        return n.hour * 60 + n.minute

    def _load_bedtime(self):
        try:
            data = json.loads(_state_path().read_text())
            bt = int(data['bedtime'])
            if data['date'] == self._now().date().isoformat() and BEDTIME_MIN <= bt <= BEDTIME_MAX:
                return bt
        except Exception:
            pass
        return DEFAULT_BEDTIME

    def _save_bedtime(self):
        try:
            _state_path().write_text(json.dumps({
                'date': self._now().date().isoformat(),
                'bedtime': self.bedtime_min}))
        except Exception:
            pass

    def _build_sleep_panel(self):
        tk.Frame(self.root, bg='#2a2a2a', height=1, width=340).pack(pady=(4, 10))
        tk.Label(self.root, text='SLEEP TIMER', font=('Segoe UI', 9, 'bold'),
                 fg='#7e57c2', bg=BG).pack()

        row = tk.Frame(self.root, bg=BG)
        row.pack(pady=(6, 2))
        self._row_label(row, 'Bedtime').pack(side='left', padx=(0, 10))
        self.bed_var = tk.StringVar(value=fmt_clock(self.bedtime_min))
        self.bed_menu = tk.OptionMenu(row, self.bed_var, *BEDTIME_CHOICES.keys(),
                                      command=self._on_bedtime_pick)
        self.bed_menu.config(width=9, font=('Segoe UI', 12, 'bold'), bg='#1e1e1e', fg='#ffffff',
                             activebackground='#333333', activeforeground='#ffffff',
                             relief='flat', bd=0, highlightthickness=0, cursor='hand2')
        self.bed_menu['menu'].config(bg='#1e1e1e', fg='#ffffff', activebackground='#7e57c2',
                                     activeforeground='#ffffff', font=('Segoe UI', 11))
        self.bed_menu.pack(side='left')

        self.sleep_status = tk.Label(self.root, text='', font=('Segoe UI', 10, 'bold'),
                                     fg='#b39ddb', bg=BG)
        self.sleep_status.pack(pady=(4, 0))
        tk.Label(self.root,
                 text=f'Pick 10 PM - 12 AM (default 11:30 PM). Keys + mouse locked till {fmt_clock(WAKE_MIN)}.',
                 font=('Segoe UI', 8), fg='#777777', bg=BG).pack(pady=(2, 0))

    def _on_bedtime_pick(self, label):
        mins = BEDTIME_CHOICES.get(label)
        if mins is None:
            return
        if mins <= self._now_min() + 1:
            # that time is already gone, picking it would shut the PC down right now
            self.bed_var.set(fmt_clock(self.bedtime_min))
            self.sleep_status.config(text='That time already passed. Pick a later one.', fg='#ff5252')
            self._note_until = time.monotonic() + 4
            return
        self.bedtime_min = mins
        self._save_bedtime()

    def _update_sleep_status(self):
        if time.monotonic() < self._note_until:
            return
        if self.curfew:
            self.sleep_status.config(text=f'Lights out. See you at {fmt_clock(WAKE_MIN)}.', fg='#ff1744')
            return
        n = self._now()
        left = self.bedtime_min * 60 - (n.hour * 3600 + n.minute * 60 + n.second)
        left = max(0, left)
        at = fmt_clock(self.bedtime_min)
        if left >= 3600:
            txt, col = f'Shutdown at {at}  \u00b7  in {left // 3600}h {(left % 3600) // 60:02d}m', '#b39ddb'
        elif left >= 600:
            txt, col = f'Shutdown at {at}  \u00b7  in {left // 60} min', '#ffb300'
        else:
            txt, col = f'Shutdown at {at}  \u00b7  in {left // 60:02d}:{left % 60:02d}', '#ff5252'
        self.sleep_status.config(text=txt, fg=col)

    def _curfew_due(self):
        if self.curfew_suppressed:
            return False
        m = self._now_min()
        return m < WAKE_MIN or m >= self.bedtime_min

    def _clock_tick(self):
        try:
            if self.curfew:
                self._curfew_step()
            elif self._curfew_due():
                self._start_curfew()
            self._update_sleep_status()
        except Exception:
            pass
        self._clock_job = self.root.after(250, self._clock_tick)

    # ------------------------------------------------------------- curfew --
    def _start_curfew(self):
        night = self._now_min() < WAKE_MIN
        self.curfew_headline = ('YOU ARE UP PAST CURFEW.\nDISOBEDIENT.' if night
                                else f'IT IS {fmt_clock(self.bedtime_min)}.\nBEDTIME.')
        self.curfew = True
        self.cancel_autostart()
        self.autostart_active = False
        if self.session is not None:
            self._stop_session()
        self.unblock_everything()
        self._set_config_enabled(False)
        self.pause_btn.config(state='disabled', bg='#444444')
        self.reset_btn.config(state='disabled', bg='#444444')
        self.curfew_phase = 'countdown'
        self.curfew_deadline = time.monotonic() + CURFEW_NOTICE_SECONDS
        try:
            self.root.deiconify()
            self.root.lift()
        except Exception:
            pass
        self._show_curfew_overlay()
        self.block_inputs()
        self._ensure_tm_killer()
        self.timer_label.config(text='00:00', fg='#ff1744')
        self.auto_label.config(text='LIGHTS OUT', fg='#ff1744')
        if not self.dry_run:
            self._request_shutdown()

    def _request_shutdown(self):
        # Windows owns the countdown from here, so killing this process doesn't save you.
        try:
            subprocess.Popen(['shutdown', '/s', '/f', '/t', str(CURFEW_NOTICE_SECONDS),
                              '/c', 'FocusX: bedtime. Go to sleep.'],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass

    def _curfew_text(self):
        if self.curfew_phase == 'countdown':
            left = max(0, math.ceil(self.curfew_deadline - time.monotonic()))
            tail = f'SHUTTING DOWN IN {left}' if left > 0 else 'GOODNIGHT.'
            return f'{self.curfew_headline}\n\n{tail}'
        return f'{self.curfew_headline}\n\nLOCKED UNTIL {fmt_clock(WAKE_MIN)}'

    def _show_curfew_overlay(self):
        self.overlay = tk.Toplevel(self.root)
        self.overlay.attributes('-fullscreen', True)
        self.overlay.attributes('-topmost', True)
        self.overlay.configure(bg='black')
        self.overlay.protocol('WM_DELETE_WINDOW', lambda: None)
        self.curfew_label = tk.Label(self.overlay, text=self._curfew_text(), fg='white',
                                     bg='black', font=('Verdana', 28, 'bold'), justify='center')
        self.curfew_label.pack(expand=True)

    def _curfew_step(self):
        now = time.monotonic()
        if self.dry_run and now >= self.curfew_deadline + 5:
            self.curfew_suppressed = True
            self._end_curfew()
            return
        if self.curfew_phase == 'countdown' and now >= self.curfew_deadline + SHUTDOWN_GRACE_SECONDS:
            # still alive after the shutdown should have hit: keep the lock till wake time
            self.curfew_phase = 'hold'
        if self.curfew_phase == 'hold' and not self._curfew_due():
            self._end_curfew()
            return
        if self.curfew_label is not None:
            try:
                self.curfew_label.config(text=self._curfew_text())
                if self.overlay is not None:
                    self.overlay.lift()
                    self.overlay.attributes('-topmost', True)
            except Exception:
                pass

    def _end_curfew(self):
        self.curfew = False
        self.curfew_phase = None
        self.unblock_everything()
        self._set_config_enabled(True)
        self.bedtime_min = self._load_bedtime()          # new day, fresh default
        self.bed_var.set(fmt_clock(self.bedtime_min))
        self._show_tab('pomodoro')
        self.start_pomodoro()

    def run(self):
        self.root.mainloop()


def _parse_fake_now(argv):
    if '--fake-now' in argv:
        try:
            hh, mm = argv[argv.index('--fake-now') + 1].split(':')
            return int(hh), int(mm)
        except Exception:
            pass
    return None


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--uninstall' in argv:
        remove_autostart()
        sys.exit(0)
    dry = '--dry-run' in argv
    if not dry:
        if not acquire_single_instance():
            sys.exit(0)          # already running (e.g. the watchdog fired)
        start_guardian()         # keeps the FocusX15 task alive in Task Scheduler
    FocusX(dry_run=dry, fake_now=_parse_fake_now(argv)).run()
