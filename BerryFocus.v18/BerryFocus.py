import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import threading
import time
import sys
import math
import random
import ctypes
import os
import json
import calendar
import datetime
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape
import psutil
from pynput import mouse, keyboard

# =============================================================================
#  BerryFocus v1  -  Deep Work Protocol + Timetable
#
#  Files next to this script at build time (build_berryfocus.bat bakes them in):
#    Berry2.ico            window + exe icon
#    cherry.wav / .mp3     plays when 20s remain before a break (funny message)
#    beep.wav   / .mp3     plays when a break ends / a sprint ends
#
#  Your data (database + your own audio files) lives in a folder called
#  BerryFocusData NEXT TO THE EXE. Copy that folder + the exe to another PC
#  and everything (history, plans, audio) comes with you.
#
#  Test flags:
#    BerryFocus.exe --dry-run                  never shuts down, uses a separate test DB
#    BerryFocus.exe --dry-run --fake-now 23:29 pretend the clock says 23:29
#    BerryFocus.exe --uninstall                remove the Task Scheduler entry + exit
# =============================================================================

APP_NAME = 'BerryFocus'
TASK_NAME = 'BerryFocus'

# pomodoro limits (minutes)
FOCUS_MIN, FOCUS_MAX, DEFAULT_FOCUS = 20, 120, 40
REST_MIN, REST_MAX, DEFAULT_REST = 1, 15, 1
DEFAULT_SPRINT = 25
WARNING_SECONDS = 20
AUTOSTART_SECONDS = 5

# sleep timer (minutes since midnight)
BEDTIME_MIN = 22 * 60            # earliest pick: 10:00 PM
BEDTIME_MAX = 24 * 60            # latest pick: 12:00 AM
DEFAULT_BEDTIME = 23 * 60 + 30   # 11:30 PM if nothing is picked
WAKE_MIN = 5 * 60 + 30           # locked until 5:30 AM
CURFEW_NOTICE_SECONDS = 10
SHUTDOWN_GRACE_SECONDS = 15

WATCHDOG_MINUTES = 2
CREATE_NO_WINDOW = 0x08000000

ICON_NAME = 'Berry2.ico'
WARNING_AUDIO = ('cherry.wav', 'cherry.mp3')
BEEP_AUDIO = ('beep.wav', 'beep.mp3')

BG = '#121212'
PANEL = '#1b1b1b'
BERRY = '#e91e63'


# ----------------------------------------------------------------- assets --
def _asset_dirs():
    dirs = []
    try:
        dirs.append(Path(__file__).resolve().parent)
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
  <RegistrationInfo><Description>BerryFocus Deep Work Protocol</Description></RegistrationInfo>
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
            / 'Startup' / f'{APP_NAME}.vbs')


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
    exe, args = launch_command()
    if _task_ok(exe, args):
        return
    if _create_task(exe, args) and _task_ok(exe, args):
        return
    _write_startup_fallback(exe, args)


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
    global _MUTEX
    try:
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        _MUTEX = k32.CreateMutexW(None, False, 'BerryFocus_SingleInstance')
        return ctypes.get_last_error() != 183   # ERROR_ALREADY_EXISTS
    except Exception:
        return True


# --------------------------------------------------------------- data dir --
def data_dir():
    """BerryFocusData next to the exe (portable). Falls back to %APPDATA%."""
    cands = []
    if globals().get('__compiled__') is not None:
        try:
            cands.append(Path(launch_command()[0]).parent / 'BerryFocusData')
        except Exception:
            pass
    else:
        cands.append(Path(__file__).resolve().parent / 'BerryFocusData')
    appdata = os.environ.get('APPDATA') or str(Path.home())
    cands.append(Path(appdata) / APP_NAME)
    for d in cands:
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / '.probe'
            probe.write_text('ok')
            probe.unlink()
            return d
        except Exception:
            continue
    return Path(tempfile.gettempdir())


# --------------------------------------------------------------- database --
class Db:
    ROW_FIELDS = {'topic', 'goal', 'progress', 'audio', 'done', 'ran', 'done_at', 'pos'}

    def __init__(self, folder, filename='berryfocus.db'):
        self.folder = Path(folder)
        self.audio_dir = self.folder / 'audio'
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(self.folder / filename))
        self.con.row_factory = sqlite3.Row
        self._schema()

    def _schema(self):
        with self.con:
            self.con.executescript("""
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS plans(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS rows(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL, pos INTEGER NOT NULL,
                topic TEXT DEFAULT '', goal TEXT DEFAULT '', progress TEXT DEFAULT '',
                audio TEXT DEFAULT '', done INTEGER DEFAULT 0, ran INTEGER DEFAULT 0,
                done_at TEXT);
            CREATE TABLE IF NOT EXISTS sessions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started TEXT, day TEXT, minutes INTEGER, kind TEXT,
                plan_id INTEGER, row_id INTEGER, topic TEXT DEFAULT '', note TEXT DEFAULT '');
            CREATE INDEX IF NOT EXISTS idx_sessions_day ON sessions(day);
            CREATE INDEX IF NOT EXISTS idx_rows_plan ON rows(plan_id, pos);
            """)

    # settings ------------------------------------------------------------
    def get(self, key, default=None):
        r = self.con.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return r['value'] if r else default

    def set(self, key, value):
        with self.con:
            self.con.execute('INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)',
                             (key, str(value)))

    def get_int(self, key, default, lo=None, hi=None):
        try:
            v = int(float(self.get(key, default)))
        except Exception:
            v = default
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        return v

    # plans ---------------------------------------------------------------
    def plans(self):
        return self.con.execute('SELECT * FROM plans ORDER BY id').fetchall()

    def plan(self, pid):
        return self.con.execute('SELECT * FROM plans WHERE id=?', (pid,)).fetchone()

    def add_plan(self, name):
        with self.con:
            cur = self.con.execute('INSERT INTO plans(name, created) VALUES(?, ?)',
                                   (name.strip() or 'Plan', datetime.datetime.now().isoformat()))
        return cur.lastrowid

    def rename_plan(self, pid, name):
        with self.con:
            self.con.execute('UPDATE plans SET name=? WHERE id=?', (name.strip() or 'Plan', pid))

    def delete_plan(self, pid):
        with self.con:
            self.con.execute('DELETE FROM rows WHERE plan_id=?', (pid,))
            self.con.execute('DELETE FROM plans WHERE id=?', (pid,))

    def duplicate_plan(self, pid, name):
        new = self.add_plan(name)
        with self.con:
            for r in self.rows(pid):
                self.con.execute(
                    'INSERT INTO rows(plan_id, pos, topic, goal, audio) VALUES(?,?,?,?,?)',
                    (new, r['pos'], r['topic'], r['goal'], r['audio']))
        return new

    # rows ----------------------------------------------------------------
    def rows(self, pid):
        return self.con.execute('SELECT * FROM rows WHERE plan_id=? ORDER BY pos, id',
                                (pid,)).fetchall()

    def pending_rows(self, pid):
        return self.con.execute(
            'SELECT * FROM rows WHERE plan_id=? AND done=0 AND ran=0 ORDER BY pos, id',
            (pid,)).fetchall()

    def next_row(self, pid):
        p = self.pending_rows(pid)
        return p[0] if p else None

    def row(self, rid):
        return self.con.execute('SELECT * FROM rows WHERE id=?', (rid,)).fetchone()

    def _next_pos(self, pid):
        r = self.con.execute('SELECT COALESCE(MAX(pos), 0) + 1 AS p FROM rows WHERE plan_id=?',
                             (pid,)).fetchone()
        return r['p']

    def add_row(self, pid, topic='', goal='', audio=''):
        with self.con:
            cur = self.con.execute(
                'INSERT INTO rows(plan_id, pos, topic, goal, audio) VALUES(?,?,?,?,?)',
                (pid, self._next_pos(pid), topic, goal, audio))
        return cur.lastrowid

    def add_block(self, pid, topic, count, goal=''):
        return [self.add_row(pid, topic, goal) for _ in range(max(1, int(count)))]

    def update_row(self, rid, **fields):
        fields = {k: v for k, v in fields.items() if k in self.ROW_FIELDS}
        if not fields:
            return
        sets = ', '.join(f'{k}=?' for k in fields)
        with self.con:
            self.con.execute(f'UPDATE rows SET {sets} WHERE id=?', (*fields.values(), rid))

    def delete_row(self, rid):
        with self.con:
            self.con.execute('DELETE FROM rows WHERE id=?', (rid,))

    # audio ---------------------------------------------------------------
    def import_audio(self, src):
        """Copy an audio file into BerryFocusData/audio so it travels with the app."""
        src = Path(src)
        safe = ''.join(c if (c.isalnum() or c in '-_') else '_' for c in src.stem)[:40]
        name = f'{int(time.time() * 1000)}_{safe}{src.suffix.lower()}'
        shutil.copy2(str(src), str(self.audio_dir / name))
        return f'audio/{name}'

    def audio_path(self, rel):
        if not rel:
            return None
        p = self.folder / rel
        return p if p.exists() else None

    # sessions / stats ----------------------------------------------------
    def log_session(self, started, day, minutes, kind, plan_id=None, row_id=None, topic=''):
        with self.con:
            cur = self.con.execute(
                'INSERT INTO sessions(started, day, minutes, kind, plan_id, row_id, topic)'
                ' VALUES(?,?,?,?,?,?,?)',
                (started, day, minutes, kind, plan_id, row_id, topic))
        return cur.lastrowid

    def set_session_note(self, row_id, note):
        with self.con:
            self.con.execute(
                'UPDATE sessions SET note=? WHERE id=(SELECT MAX(id) FROM sessions WHERE row_id=?)',
                (note, row_id))

    def day_summary(self, day):
        r = self.con.execute(
            'SELECT COUNT(*) AS n, COALESCE(SUM(minutes), 0) AS m FROM sessions WHERE day=?',
            (day,)).fetchone()
        return r['n'], r['m']

    def month_minutes(self, ym):
        r = self.con.execute(
            'SELECT COALESCE(SUM(minutes), 0) AS m FROM sessions WHERE day LIKE ?',
            (ym + '%',)).fetchone()
        return r['m']

    def total_minutes(self):
        return self.con.execute(
            'SELECT COALESCE(SUM(minutes), 0) AS m FROM sessions').fetchone()['m']

    def last_days(self, n, today):
        start = today - datetime.timedelta(days=n - 1)
        got = {r['day']: r['m'] for r in self.con.execute(
            'SELECT day, SUM(minutes) AS m FROM sessions WHERE day>=? GROUP BY day',
            (start.isoformat(),))}
        out = []
        for i in range(n):
            d = start + datetime.timedelta(days=i)
            out.append((d, int(got.get(d.isoformat(), 0))))
        return out

    def streak(self, today):
        days = {r['day'] for r in self.con.execute('SELECT DISTINCT day FROM sessions')}
        d = today
        if d.isoformat() not in days:
            d -= datetime.timedelta(days=1)   # today isn't over yet
        n = 0
        while d.isoformat() in days:
            n += 1
            d -= datetime.timedelta(days=1)
        return n

    def recent_sessions(self, limit=10):
        return self.con.execute(
            'SELECT day, minutes, kind, topic, note FROM sessions ORDER BY id DESC LIMIT ?',
            (limit,)).fetchall()


def row_text(r):
    if r is None:
        return ''
    t = (r['topic'] or '').strip() or 'Untitled'
    g = (r['goal'] or '').strip()
    return f'{t}  -  {g}' if g else t


# ------------------------------------------------------------------ style --
def setup_ttk_style(root):
    st = ttk.Style(root)
    try:
        st.theme_use('clam')
    except Exception:
        pass
    st.configure('TNotebook', background=BG, borderwidth=0)
    st.configure('TNotebook.Tab', background='#2a2a2a', foreground='#bbbbbb',
                 padding=(14, 6), font=('Segoe UI', 10, 'bold'), borderwidth=0)
    st.map('TNotebook.Tab', background=[('selected', BERRY)],
           foreground=[('selected', '#ffffff')])
    st.configure('TCombobox', fieldbackground='#1e1e1e', background='#333333',
                 foreground='#ffffff', arrowcolor='#ffffff', bordercolor='#1e1e1e')
    st.map('TCombobox', fieldbackground=[('readonly', '#1e1e1e')],
           foreground=[('readonly', '#ffffff')],
           selectbackground=[('readonly', '#1e1e1e')],
           selectforeground=[('readonly', '#ffffff')])
    st.configure('Vertical.TScrollbar', background='#333333', troughcolor=BG,
                 arrowcolor='#ffffff', bordercolor=BG)
    root.option_add('*TCombobox*Listbox.background', '#1e1e1e')
    root.option_add('*TCombobox*Listbox.foreground', '#ffffff')
    root.option_add('*TCombobox*Listbox.selectBackground', BERRY)


def make_btn(parent, text, cmd, bg='#333333', fg='white', width=None, font_size=9):
    b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, relief='flat',
                  activebackground=bg, activeforeground=fg, cursor='hand2',
                  font=('Segoe UI', font_size, 'bold'), padx=10, pady=4)
    if width:
        b.config(width=width)
    return b


# ------------------------------------------------------------ plan window --
class PlanTab:
    def __init__(self, win, parent, plan_id):
        self.win = win
        self.app = win.app
        self.db = win.db
        self.plan_id = plan_id
        self.frame = tk.Frame(parent, bg=BG)

        bar = tk.Frame(self.frame, bg=BG)
        bar.pack(fill='x', padx=12, pady=(10, 6))
        make_btn(bar, '\uff0b ADD ROW', self.add_row, '#2e7d32').pack(side='left', padx=(0, 8))
        make_btn(bar, '\uff0b ADD BLOCK (N sessions)', self.add_block, '#0277bd').pack(side='left', padx=(0, 8))
        make_btn(bar, 'USE FOR POMODORO \u25b6', self.use_for_pomodoro, BERRY).pack(side='left')
        self.summary = tk.Label(bar, text='', font=('Segoe UI', 11, 'bold'), fg='#b39ddb', bg=BG)
        self.summary.pack(side='right')

        body = tk.Frame(self.frame, bg=BG)
        body.pack(fill='both', expand=True, padx=(12, 0))
        self.canvas = tk.Canvas(body, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(body, orient='vertical', command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self.canvas.create_window((0, 0), window=self.inner, anchor='nw', tags='inner')
        self.inner.bind('<Configure>',
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',
                         lambda e: self.canvas.itemconfigure('inner', width=e.width))
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)

        for col, weight, minsize in ((0, 0, 48), (1, 2, 120), (2, 3, 150), (3, 4, 200),
                                     (4, 0, 270), (5, 0, 60), (6, 0, 44)):
            self.inner.grid_columnconfigure(col, weight=weight, minsize=minsize)
        self.rebuild()

    # ---------------------------------------------------------------- build --
    def rebuild(self):
        self.win.flush_all()
        for w in self.inner.winfo_children():
            w.destroy()
        heads = ('#', 'TOPIC', 'GOAL  (pages / questions / output)',
                 'PROGRESS  (what I actually did)', 'AUDIO  (plays after this session)',
                 'DONE', '')
        for c, h in enumerate(heads):
            tk.Label(self.inner, text=h, font=('Segoe UI', 9, 'bold'), fg='#7e57c2',
                     bg=BG, anchor='w').grid(row=0, column=c, sticky='w', padx=4, pady=(2, 6))
        rows = self.db.rows(self.plan_id)
        nxt = self.db.next_row(self.plan_id)
        nid = nxt['id'] if nxt else None
        if not rows:
            tk.Label(self.inner, text='Empty. Press "ADD ROW" or "ADD BLOCK" to plan your session(s).',
                     font=('Segoe UI', 11), fg='#777777', bg=BG).grid(
                row=1, column=0, columnspan=7, pady=40)
        for i, r in enumerate(rows, start=1):
            self._build_row(i, r, nid)
        self._update_summary(rows)

    def _entry(self, var, grid_row, col):
        e = tk.Entry(self.inner, textvariable=var, font=('Segoe UI', 11), bg='#1e1e1e',
                     fg='#ffffff', insertbackground='#ffffff', relief='flat', width=10)
        e.grid(row=grid_row, column=col, sticky='ew', padx=3, pady=3, ipady=5)
        return e

    def _build_row(self, i, r, nid):
        rid = r['id']
        if r['done']:
            mark, col = '\u2714', '#00e676'
        elif rid == nid:
            mark, col = '\u25b6', '#ff9800'
        elif r['ran']:
            mark, col = '\u2026', '#ffb300'
        else:
            mark, col = str(i), '#888888'
        tk.Label(self.inner, text=mark, fg=col, bg=BG,
                 font=('Segoe UI', 12, 'bold')).grid(row=i, column=0, padx=4, pady=3)

        for col_idx, field in ((1, 'topic'), (2, 'goal'), (3, 'progress')):
            var = tk.StringVar(value=r[field] or '')
            self._entry(var, i, col_idx)
            var.trace_add('write', lambda *a, rid=rid, field=field, var=var:
                          self.win.queue_save(rid, field, var))

        af = tk.Frame(self.inner, bg=BG)
        af.grid(row=i, column=4, sticky='w', padx=6)
        has_audio = bool(r['audio'])
        name = Path(r['audio']).name.split('_', 1)[-1] if has_audio else '- none -'
        if len(name) > 22:
            name = name[:19] + '...'
        tk.Label(af, text=name, width=22, anchor='w', font=('Segoe UI', 9),
                 fg='#b39ddb' if has_audio else '#666666', bg=BG).pack(side='left')
        make_btn(af, '\uff0b', lambda rid=rid: self._pick_audio(rid), '#333333').pack(side='left', padx=1)
        make_btn(af, '\u25b6', lambda rid=rid: self._preview(rid), '#333333').pack(side='left', padx=1)
        make_btn(af, '\u2716', lambda rid=rid: self._clear_audio(rid), '#333333').pack(side='left', padx=1)

        dv = tk.BooleanVar(value=bool(r['done']))
        tk.Checkbutton(self.inner, variable=dv, bg=BG, activebackground=BG, selectcolor='#1e1e1e',
                       command=lambda rid=rid, dv=dv: self._toggle_done(rid, dv),
                       cursor='hand2').grid(row=i, column=5)
        make_btn(self.inner, '\u2716', lambda rid=rid: self._delete(rid), '#8e1b1b').grid(
            row=i, column=6, padx=4)

    def _update_summary(self, rows=None):
        rows = rows if rows is not None else self.db.rows(self.plan_id)
        total = len(rows)
        done = sum(1 for r in rows if r['done'])
        pct = int(100 * done / total) if total else 0
        self.summary.config(text=f'{done}/{total} sessions done  \u00b7  {pct}%')

    # -------------------------------------------------------------- actions --
    def add_row(self):
        rows = self.db.rows(self.plan_id)
        topic = rows[-1]['topic'] if rows else ''
        self.db.add_row(self.plan_id, topic=topic)
        self.rebuild()
        self.app.on_plan_changed()
        self.canvas.update_idletasks()
        self.canvas.yview_moveto(1.0)

    def add_block(self):
        top = self.win.top
        topic = simpledialog.askstring('Add block', 'Topic (e.g. Python - functions):', parent=top)
        if topic is None:
            return
        n = simpledialog.askinteger('Add block', 'How many focus sessions?', parent=top,
                                    minvalue=1, maxvalue=12, initialvalue=4)
        if not n:
            return
        goal = simpledialog.askstring(
            'Add block', 'Goal for EACH session (pages / questions / output).\nOptional, edit per row later:',
            parent=top) or ''
        self.db.add_block(self.plan_id, topic.strip(), n, goal.strip())
        self.rebuild()
        self.app.on_plan_changed()

    def use_for_pomodoro(self):
        self.app.set_plan(self.plan_id)
        self.rebuild()

    def _toggle_done(self, rid, dv):
        if dv.get():
            self.db.update_row(rid, done=1, done_at=datetime.datetime.now().isoformat())
        else:
            self.db.update_row(rid, done=0, ran=0, done_at=None)
        self.rebuild()
        self.app.on_plan_changed()

    def _delete(self, rid):
        if messagebox.askyesno('Delete row', 'Delete this row?', parent=self.win.top):
            self.db.delete_row(rid)
            self.rebuild()
            self.app.on_plan_changed()

    def _pick_audio(self, rid):
        path = filedialog.askopenfilename(
            parent=self.win.top, title='Pick the audio for this session',
            filetypes=[('Audio', '*.wav *.mp3'), ('All files', '*.*')])
        if not path:
            return
        try:
            rel = self.db.import_audio(path)
        except Exception as e:
            messagebox.showerror('Audio', f'Could not copy that file:\n{e}', parent=self.win.top)
            return
        self.db.update_row(rid, audio=rel)
        self.rebuild()

    def _preview(self, rid):
        r = self.db.row(rid)
        p = self.db.audio_path(r['audio']) if r else None
        if p:
            play_audio_async(p)

    def _clear_audio(self, rid):
        self.db.update_row(rid, audio='')
        self.rebuild()


class PlanWindow:
    def __init__(self, app):
        self.app = app
        self.db = app.db
        self.jobs = {}
        self.tabs = []
        top = self.top = tk.Toplevel(app.root)
        top.title('Timetable')
        top.configure(bg=BG)
        icon = find_asset(ICON_NAME)
        if icon:
            try:
                top.iconbitmap(str(icon))
            except Exception:
                pass
        top.attributes('-fullscreen', True)
        top.bind('<Escape>', lambda e: self.close())
        top.bind('<MouseWheel>', self._wheel)
        top.protocol('WM_DELETE_WINDOW', self.close)

        bar = tk.Frame(top, bg=BG)
        bar.pack(fill='x', padx=16, pady=(14, 4))
        tk.Label(bar, text='TIMETABLE', font=('Segoe UI', 20, 'bold'), fg=BERRY, bg=BG).pack(side='left')
        make_btn(bar, 'CLOSE  (Esc)', self.close, '#444444').pack(side='right', padx=(8, 0))
        make_btn(bar, 'DELETE TAB', self.delete_tab, '#8e1b1b').pack(side='right', padx=4)
        make_btn(bar, 'DUPLICATE AS NEW DAY', self.duplicate_tab, '#0277bd').pack(side='right', padx=4)
        make_btn(bar, 'RENAME', self.rename_tab, '#444444').pack(side='right', padx=4)
        make_btn(bar, '\uff0b NEW TAB', self.new_tab, '#2e7d32').pack(side='right', padx=4)

        self.nb = ttk.Notebook(top)
        self.nb.pack(fill='both', expand=True, padx=12, pady=(4, 10))
        self._load()

    def _load(self, select_id=None):
        self.flush_all()
        for t in self.nb.tabs():
            self.nb.forget(t)
        for tab in self.tabs:
            try:
                tab.frame.destroy()
            except Exception:
                pass
        self.tabs = []
        plans = self.db.plans()
        want = select_id if select_id is not None else self.app.plan_id
        sel = 0
        for i, p in enumerate(plans):
            tab = PlanTab(self, self.nb, p['id'])
            self.tabs.append(tab)
            self.nb.add(tab.frame, text=f"  {p['name']}  ")
            if p['id'] == want:
                sel = i
        if self.tabs:
            self.nb.select(sel)

    def current_tab(self):
        try:
            return self.tabs[self.nb.index(self.nb.select())]
        except Exception:
            return None

    def _wheel(self, e):
        t = self.current_tab()
        if t is not None:
            try:
                t.canvas.yview_scroll(int(-e.delta / 120), 'units')
            except Exception:
                pass

    # debounced autosave of typed text ---------------------------------------
    def queue_save(self, rid, field, var):
        key = (rid, field)
        old = self.jobs.get(key)
        if old:
            try:
                self.top.after_cancel(old[0])
            except Exception:
                pass
        job = self.top.after(400, lambda k=key: self._flush(k))
        self.jobs[key] = (job, rid, field, var)

    def _flush(self, key):
        item = self.jobs.pop(key, None)
        if item:
            _, rid, field, var = item
            try:
                self.db.update_row(rid, **{field: var.get()})
            except Exception:
                pass

    def flush_all(self):
        for key in list(self.jobs):
            item = self.jobs.get(key)
            if item:
                try:
                    self.top.after_cancel(item[0])
                except Exception:
                    pass
            self._flush(key)

    def refresh(self):
        t = self.current_tab()
        if t is not None:
            t.rebuild()

    # tab management ---------------------------------------------------------
    def new_tab(self):
        name = simpledialog.askstring('New tab', 'Name (e.g. Python script, Day 2):', parent=self.top)
        if not name:
            return
        pid = self.db.add_plan(name)
        self.db.add_row(pid)
        self._load(pid)
        self.app.refresh_plans()

    def rename_tab(self):
        t = self.current_tab()
        if not t:
            return
        p = self.db.plan(t.plan_id)
        name = simpledialog.askstring('Rename', 'New name:', initialvalue=p['name'], parent=self.top)
        if name:
            self.db.rename_plan(t.plan_id, name)
            self._load(t.plan_id)
            self.app.refresh_plans()

    def duplicate_tab(self):
        t = self.current_tab()
        if not t:
            return
        p = self.db.plan(t.plan_id)
        name = simpledialog.askstring(
            'Duplicate', 'Copies topics, goals and audio with progress cleared.\nName for the new tab:',
            initialvalue=f"{p['name']} (new day)", parent=self.top)
        if name:
            self.flush_all()
            pid = self.db.duplicate_plan(t.plan_id, name)
            self._load(pid)
            self.app.refresh_plans()

    def delete_tab(self):
        t = self.current_tab()
        if not t:
            return
        p = self.db.plan(t.plan_id)
        if not messagebox.askyesno('Delete tab', f"Delete \"{p['name']}\" and all its rows?\n"
                                   'Your session history and stats are kept.', parent=self.top):
            return
        self.db.delete_plan(t.plan_id)
        if self.app.plan_id == t.plan_id:
            self.app.plan_id = None
            self.db.set('plan_id', '')
        if not self.db.plans():
            self.db.add_plan('Plan 1')
        self._load()
        self.app.refresh_plans()

    def close(self):
        self.flush_all()
        try:
            self.top.destroy()
        except Exception:
            pass
        self.app.plan_win = None
        self.app.refresh_plans()
        self.app.on_plan_changed()


# -------------------------------------------------------------------- app --
class BerryFocus:
    def __init__(self, dry_run=False, fake_now=None):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        self.dry_run = dry_run
        self.db = Db(data_dir(), 'berryfocus_dryrun.db' if dry_run else 'berryfocus.db')
        self._seed_defaults()

        self.root = tk.Tk()
        self.root.title('BerryFocus' + (' [DRY RUN]' if dry_run else ''))
        self.root.configure(bg=BG)
        setup_ttk_style(self.root)
        icon = find_asset(ICON_NAME)
        if icon:
            try:
                self.root.iconbitmap(str(icon))
            except Exception:
                pass
        w = 480
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        h = min(800, sh - 80)
        self.root.geometry(f'{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 2 - 20)}')
        self.root.resizable(False, False)

        # settings (remembered)
        self.focus_seconds = self.db.get_int('focus_min', DEFAULT_FOCUS, FOCUS_MIN, FOCUS_MAX) * 60
        self.rest_seconds = self.db.get_int('rest_min', DEFAULT_REST, REST_MIN, REST_MAX) * 60
        self.sprint_min = self.db.get_int('sprint_min', DEFAULT_SPRINT, 1, 240)
        self.audio_when = self.db.get('audio_when', 'start')
        self.daily_goal = self.db.get_int('daily_goal', 8, 1, 30)
        self.month_goal = self.db.get_int('month_goal_h', 60, 1, 400)
        pid = self.db.get('plan_id', '')
        self.plan_id = int(pid) if str(pid).isdigit() and self.db.plan(int(pid)) else None
        if self.plan_id is None and self.db.plans():
            self.plan_id = self.db.plans()[0]['id']

        # session state
        self.tab = 'pomodoro'
        self.session = None
        self.phase = None
        self.running = False
        self.paused = False
        self.deadline = 0.0
        self.paused_left = 0.0
        self.warning_played = False
        self._tm_killer_active = False
        self._tick_job = None
        self.cur_row = None
        self.session_started = None
        self.next_up = ''
        self.overlay_funny = ''

        # autostart countdown
        self.autostart_active = True
        self.autostart_left = AUTOSTART_SECONDS
        self._auto_job = None

        # blockers / overlay / popups
        self.mouse_listener = None
        self.kb_listener = None
        self.overlay = None
        self.overlay_label = None
        self.plan_win = None
        self.checkin = None

        # sleep timer / curfew
        self.time_offset = datetime.timedelta(0)
        if fake_now is not None:
            fake = datetime.datetime.now().replace(hour=fake_now[0], minute=fake_now[1],
                                                   second=0, microsecond=0)
            self.time_offset = fake - datetime.datetime.now()
        self.bedtime_min = self._load_bedtime()
        self.bed_picked = self.db.get('bedtime_picked', '') == self._now().date().isoformat()
        self.curfew = False
        self.curfew_phase = None
        self.curfew_deadline = 0.0
        self.curfew_suppressed = False
        self.curfew_label = None
        self.curfew_headline = ''
        self._note_until = 0.0

        self.setup_ui()
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        if self._curfew_due():
            self.autostart_active = False
            self._hide_cancel_btn()
        else:
            self._autostart_tick()
        self.root.after(100, self._clock_tick)

    def _seed_defaults(self):
        if not self.db.plans():
            pid = self.db.add_plan('Python - Day 1')
            self.db.add_block(pid, 'Python', 4, '')

    # -------------------------------------------------------------- state --
    @property
    def locked(self):
        return (self.session == 'pomodoro' and self.running
                and (self.phase == 'break' or self.warning_played))

    def _now(self):
        return datetime.datetime.now() + self.time_offset

    def _now_min(self):
        n = self._now()
        return n.hour * 60 + n.minute

    # ----------------------------------------------------------------- ui --
    def setup_ui(self):
        # bottom first so it keeps its space
        self._build_sleep_panel()

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill='both', expand=True, padx=8, pady=(8, 0))
        self.focus_tab = tk.Frame(self.nb, bg=BG)
        self.stats_tab = tk.Frame(self.nb, bg=BG)
        self.nb.add(self.focus_tab, text='  FOCUS  ')
        self.nb.add(self.stats_tab, text='  PROGRESS  ')
        self.nb.bind('<<NotebookTabChanged>>', lambda e: self.refresh_stats())

        self._build_focus_tab(self.focus_tab)
        self._build_stats_tab(self.stats_tab)
        self._show_mode('pomodoro')
        self.auto_label.config(text=f'Launching in: {AUTOSTART_SECONDS}s')
        self.refresh_plans()
        self._refresh_now_card()
        self.refresh_stats()

    # ---- focus tab ----------------------------------------------------------
    def _spin(self, parent, var, lo, hi):
        return tk.Spinbox(parent, from_=lo, to=hi, textvariable=var, width=5, justify='center',
                          font=('Segoe UI', 14, 'bold'), bg='#1e1e1e', fg='#ffffff',
                          buttonbackground='#333333', insertbackground='#ffffff',
                          relief='flat', disabledbackground='#181818', disabledforeground='#666666')

    def _row_label(self, parent, text):
        return tk.Label(parent, text=text, font=('Segoe UI', 11), fg='#cccccc', bg=BG)

    def _build_focus_tab(self, parent):
        bar = tk.Frame(parent, bg=BG)
        bar.pack(pady=(14, 4))
        self.mode_btns = {}
        for key, label in (('pomodoro', 'POMODORO'), ('sprint', 'SPRINT')):
            b = tk.Button(bar, text=label, width=14, relief='flat', cursor='hand2',
                          font=('Segoe UI', 10, 'bold'),
                          command=lambda k=key: self.switch_mode(k))
            b.pack(side='left', padx=4)
            self.mode_btns[key] = b

        self.auto_label = tk.Label(parent, text='', font=('Verdana', 11, 'italic'),
                                   fg='#aaaaaa', bg=BG)
        self.auto_label.pack(pady=(10, 2))

        self.content = tk.Frame(parent, bg=BG)
        self.content.pack()
        self._build_pomodoro_frame()
        self._build_sprint_frame()

        self.timer_label = tk.Label(parent, text='00:00', font=('Verdana', 48, 'bold'),
                                    fg='#ff9800', bg=BG)
        self.timer_label.pack(pady=(8, 2))

        self.now_label = tk.Label(parent, text='', font=('Segoe UI', 10), fg='#cccccc',
                                  bg=PANEL, justify='left', anchor='w', wraplength=420,
                                  padx=10, pady=6)
        self.now_label.pack(fill='x', padx=18, pady=(4, 4))

        ctrl = tk.Frame(parent, bg=BG)
        ctrl.pack(pady=6)
        self.pause_btn = tk.Button(ctrl, text='PAUSE', width=12, command=self.toggle_pause,
                                   state='disabled', bg='#333333', fg='#ffffff', relief='flat',
                                   font=('Segoe UI', 9, 'bold'))
        self.pause_btn.pack(side='left', padx=8)
        self.reset_btn = tk.Button(ctrl, text='RESET', width=12, command=self.reset_app,
                                   state='disabled', bg='#c62828', fg='white', relief='flat',
                                   font=('Segoe UI', 9, 'bold'))
        self.reset_btn.pack(side='left', padx=8)

        tk.Button(parent, text='OPEN TIMETABLE  (fullscreen)', command=self.open_plan,
                  bg='#7e57c2', fg='white', relief='flat', cursor='hand2',
                  font=('Segoe UI', 10, 'bold'), pady=6).pack(fill='x', padx=18, pady=(6, 8))

    def _build_pomodoro_frame(self):
        f = tk.Frame(self.content, bg=BG)
        self.pomo_frame = f

        self.focus_var = tk.StringVar(value=str(self.focus_seconds // 60))
        self.rest_var = tk.StringVar(value=str(self.rest_seconds // 60))

        r0 = tk.Frame(f, bg=BG)
        r0.pack(pady=4)
        self._row_label(r0, 'Plan').pack(side='left', padx=(0, 22))
        self.plan_combo = ttk.Combobox(r0, state='readonly', width=26, font=('Segoe UI', 10))
        self.plan_combo.pack(side='left')
        self.plan_combo.bind('<<ComboboxSelected>>', self._on_plan_combo)

        r1 = tk.Frame(f, bg=BG)
        r1.pack(pady=4)
        self._row_label(r1, f'Focus ({FOCUS_MIN}-{FOCUS_MAX} min)').pack(side='left', padx=(0, 10))
        self.focus_spin = self._spin(r1, self.focus_var, FOCUS_MIN, FOCUS_MAX)
        self.focus_spin.pack(side='left')

        r2 = tk.Frame(f, bg=BG)
        r2.pack(pady=4)
        self._row_label(r2, f'Rest ({REST_MIN}-{REST_MAX} min)').pack(side='left', padx=(0, 18))
        self.rest_spin = self._spin(r2, self.rest_var, REST_MIN, REST_MAX)
        self.rest_spin.pack(side='left')

        r3 = tk.Frame(f, bg=BG)
        r3.pack(pady=(4, 0))
        tk.Label(r3, text='Session audio:', font=('Segoe UI', 9), fg='#999999', bg=BG).pack(side='left')
        self.audio_var = tk.StringVar(value=self.audio_when)
        for val, txt in (('start', 'when break starts'), ('end', 'when break ends')):
            tk.Radiobutton(r3, text=txt, value=val, variable=self.audio_var, bg=BG, fg='#cccccc',
                           selectcolor='#1e1e1e', activebackground=BG, activeforeground='#ffffff',
                           font=('Segoe UI', 9), command=self._on_audio_when).pack(side='left', padx=6)

        self.pomo_start = tk.Button(f, text='START POMODORO', width=22, height=2,
                                    command=self.start_pomodoro, bg='#2e7d32', fg='white',
                                    font=('Segoe UI', 10, 'bold'), relief='raised', bd=4,
                                    activebackground='#1b5e20', cursor='hand2')
        self.pomo_start.pack(pady=(10, 4))
        self.cancel_auto_btn = tk.Button(f, text='CANCEL AUTO-START', width=22,
                                         command=self.cancel_autostart, bg='#333333', fg='white',
                                         relief='flat', font=('Segoe UI', 9, 'bold'), cursor='hand2')
        self.cancel_auto_btn.pack(pady=2)

        self.focus_var.trace_add('write', lambda *a: self.cancel_autostart())
        self.rest_var.trace_add('write', lambda *a: self.cancel_autostart())

    def _build_sprint_frame(self):
        f = tk.Frame(self.content, bg=BG)
        self.sprint_frame = f
        self.sprint_var = tk.StringVar(value=str(self.sprint_min))
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
                 font=('Segoe UI', 8), fg='#777777', bg=BG).pack(pady=(4, 0))
        tk.Frame(f, bg=BG, height=96).pack()   # keep both modes the same height

    def _show_mode(self, key):
        self.tab = key
        self.pomo_frame.pack_forget()
        self.sprint_frame.pack_forget()
        (self.pomo_frame if key == 'pomodoro' else self.sprint_frame).pack()
        for k, b in self.mode_btns.items():
            if k == key:
                b.config(bg='#00e5ff', fg='#000000')
            else:
                b.config(bg='#2a2a2a', fg='#bbbbbb')

    def switch_mode(self, key):
        if self.running or key == self.tab:
            return
        self.cancel_autostart()
        self._show_mode(key)

    def _set_config_enabled(self, enabled):
        state = 'normal' if enabled else 'disabled'
        for w in (self.focus_spin, self.rest_spin, self.sprint_spin,
                  self.pomo_start, self.sprint_start):
            w.config(state=state)
        for b in self.mode_btns.values():
            b.config(state=state)
        self.plan_combo.config(state='readonly' if enabled else 'disabled')

    # ---- plans --------------------------------------------------------------
    NO_PLAN = '(no plan - free focus)'

    def refresh_plans(self):
        plans = self.db.plans()
        self._combo_ids = [None] + [p['id'] for p in plans]
        self.plan_combo['values'] = [self.NO_PLAN] + [p['name'] for p in plans]
        try:
            self.plan_combo.current(self._combo_ids.index(self.plan_id))
        except ValueError:
            self.plan_id = None
            self.plan_combo.current(0)

    def _on_plan_combo(self, _e=None):
        i = self.plan_combo.current()
        if i < 0:
            return
        self.set_plan(self._combo_ids[i])

    def set_plan(self, pid):
        self.cancel_autostart()
        self.plan_id = pid
        self.db.set('plan_id', pid if pid is not None else '')
        self.refresh_plans()
        self._refresh_now_card()

    def _on_audio_when(self):
        self.audio_when = self.audio_var.get()
        self.db.set('audio_when', self.audio_when)
        self.cancel_autostart()

    def open_plan(self):
        self.cancel_autostart()
        if self.plan_win is not None:
            try:
                if self.plan_win.top.winfo_exists():
                    self.plan_win.top.lift()
                    return
            except Exception:
                pass
        self.plan_win = PlanWindow(self)

    def on_plan_changed(self):
        self._refresh_now_card()
        self.refresh_stats()

    def _refresh_now_card(self):
        if self.plan_id is None:
            txt = 'No plan selected: free focus. Pick a plan above to track sessions.'
        else:
            pend = self.db.pending_rows(self.plan_id)
            if self.running and self.session == 'pomodoro' and self.cur_row is not None:
                now = self.cur_row
                rest = [r for r in pend if r['id'] != now['id']]
            else:
                now = pend[0] if pend else None
                rest = pend[1:]
            nxt = rest[0] if rest else None
            if now is None:
                txt = 'Plan complete. Open the timetable to add rows or duplicate it as a new day.'
            else:
                txt = f'NOW   \u25b6 {row_text(now)}'
                txt += f'\nNEXT   {row_text(nxt)}' if nxt else '\nNEXT   (last one!)'
        try:
            self.now_label.config(text=txt)
        except Exception:
            pass

    # ---- stats tab ----------------------------------------------------------
    def _bar_canvas(self, parent, w=430, h=14):
        c = tk.Canvas(parent, width=w, height=h, bg='#2a2a2a', highlightthickness=0)
        c.pack(padx=18, pady=(2, 4))
        return c

    def _draw_bar(self, cv, frac, color):
        cv.delete('all')
        w, h = int(cv['width']), int(cv['height'])
        cv.create_rectangle(0, 0, w, h, fill='#2a2a2a', width=0)
        cv.create_rectangle(0, 0, int(w * max(0.0, min(1.0, frac))), h, fill=color, width=0)

    def _build_stats_tab(self, parent):
        lab = lambda: tk.Label(parent, text='', font=('Segoe UI', 11, 'bold'), fg='#ffffff',
                               bg=BG, anchor='w')
        self.st_today = lab()
        self.st_today.pack(fill='x', padx=18, pady=(12, 0))
        self.bar_day = self._bar_canvas(parent)

        r = tk.Frame(parent, bg=BG)
        r.pack(anchor='w', padx=18)
        tk.Label(r, text='Daily goal (focus sessions)', font=('Segoe UI', 9), fg='#999999',
                 bg=BG).pack(side='left')
        self.dgoal_var = tk.StringVar(value=str(self.daily_goal))
        tk.Spinbox(r, from_=1, to=30, textvariable=self.dgoal_var, width=4, justify='center',
                   font=('Segoe UI', 10, 'bold'), bg='#1e1e1e', fg='#ffffff', relief='flat',
                   buttonbackground='#333333').pack(side='left', padx=8)

        self.st_month = lab()
        self.st_month.pack(fill='x', padx=18, pady=(10, 0))
        self.bar_month = self._bar_canvas(parent)
        r = tk.Frame(parent, bg=BG)
        r.pack(anchor='w', padx=18)
        tk.Label(r, text='Monthly goal (hours)', font=('Segoe UI', 9), fg='#999999',
                 bg=BG).pack(side='left')
        self.mgoal_var = tk.StringVar(value=str(self.month_goal))
        tk.Spinbox(r, from_=1, to=400, textvariable=self.mgoal_var, width=4, justify='center',
                   font=('Segoe UI', 10, 'bold'), bg='#1e1e1e', fg='#ffffff', relief='flat',
                   buttonbackground='#333333').pack(side='left', padx=8)
        self.st_pace = tk.Label(parent, text='', font=('Segoe UI', 9), fg='#ffb300', bg=BG,
                                anchor='w', wraplength=430, justify='left')
        self.st_pace.pack(fill='x', padx=18, pady=(4, 0))
        self.st_streak = tk.Label(parent, text='', font=('Segoe UI', 10, 'bold'), fg='#b39ddb',
                                  bg=BG, anchor='w')
        self.st_streak.pack(fill='x', padx=18, pady=(6, 0))

        self.chart = tk.Canvas(parent, width=430, height=120, bg=PANEL, highlightthickness=0)
        self.chart.pack(padx=18, pady=(6, 4))

        tk.Label(parent, text='RECENT SESSIONS', font=('Segoe UI', 8, 'bold'), fg='#7e57c2',
                 bg=BG, anchor='w').pack(fill='x', padx=18)
        self.log_text = tk.Text(parent, height=8, bg=PANEL, fg='#cccccc', relief='flat',
                                font=('Segoe UI', 9), wrap='word', state='disabled', padx=8, pady=6)
        self.log_text.pack(fill='both', expand=True, padx=18, pady=(2, 10))

        self.dgoal_var.trace_add('write', lambda *a: self._goal_changed('daily'))
        self.mgoal_var.trace_add('write', lambda *a: self._goal_changed('month'))

    def _goal_changed(self, which):
        try:
            if which == 'daily':
                self.daily_goal = max(1, min(30, int(self.dgoal_var.get())))
                self.db.set('daily_goal', self.daily_goal)
            else:
                self.month_goal = max(1, min(400, int(self.mgoal_var.get())))
                self.db.set('month_goal_h', self.month_goal)
        except Exception:
            return
        self.refresh_stats()

    def refresh_stats(self):
        try:
            self._refresh_stats()
        except Exception:
            pass

    def _refresh_stats(self):
        today = self._now().date()
        n, mins = self.db.day_summary(today.isoformat())
        self.st_today.config(text=f'TODAY   {n}/{self.daily_goal} sessions  \u00b7  {mins} min')
        self._draw_bar(self.bar_day, n / self.daily_goal, '#00e676' if n >= self.daily_goal else BERRY)

        mm = self.db.month_minutes(today.strftime('%Y-%m'))
        self.st_month.config(text=f'{today.strftime("%B").upper()}   {mm / 60:.1f} h of {self.month_goal} h')
        self._draw_bar(self.bar_month, mm / 60 / self.month_goal, '#00e676' if mm / 60 >= self.month_goal else '#7e57c2')
        days_left = calendar.monthrange(today.year, today.month)[1] - today.day + 1
        remaining = self.month_goal - mm / 60
        if remaining <= 0:
            self.st_pace.config(text='Monthly goal reached. Anything more is a bonus.')
        else:
            self.st_pace.config(text=f'To hit {self.month_goal} h you need {remaining / days_left:.1f} h/day '
                                     f'for the remaining {days_left} day(s) (counting today).')
        self.st_streak.config(text=f'Day streak: {self.db.streak(today)}   \u00b7   '
                                   f'All time: {self.db.total_minutes() / 60:.1f} h')

        c = self.chart
        c.delete('all')
        W, H = 430, 120
        days = self.db.last_days(14, today)
        mx = max(60, max(m for _, m in days))
        bw = W / len(days)
        for i, (d, m) in enumerate(days):
            h = int((H - 36) * m / mx)
            x0, x1 = i * bw + 5, (i + 1) * bw - 5
            c.create_rectangle(x0, H - 20 - h, x1, H - 20, fill=BERRY if m else '#2a2a2a', width=0)
            c.create_text((x0 + x1) / 2, H - 9, text=d.strftime('%d'), fill='#888888',
                          font=('Segoe UI', 7))
            if m:
                c.create_text((x0 + x1) / 2, H - 25 - h, text=str(m), fill='#cccccc',
                              font=('Segoe UI', 7))
        c.create_text(6, 8, anchor='w', text='minutes per day, last 14 days', fill='#777777',
                      font=('Segoe UI', 7))

        lines = []
        for r in self.db.recent_sessions(12):
            d = datetime.date.fromisoformat(r['day']).strftime('%b %d')
            line = f"{d}  \u00b7  {r['minutes']}m  \u00b7  {r['topic'] or r['kind']}"
            if r['note']:
                line += f"  -  {r['note']}"
            lines.append(line)
        self.log_text.config(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.insert('1.0', '\n'.join(lines) if lines else 'No sessions yet. Start your first one.')
        self.log_text.config(state='disabled')

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

    def _pick_row(self):
        self.cur_row = self.db.next_row(self.plan_id) if self.plan_id is not None else None
        self._refresh_now_card()

    def start_pomodoro(self):
        if self.running or self.curfew:
            return
        self.autostart_active = False
        focus = self._read_int(self.focus_var, FOCUS_MIN, FOCUS_MAX, DEFAULT_FOCUS)
        rest = self._read_int(self.rest_var, REST_MIN, REST_MAX, DEFAULT_REST)
        self.focus_seconds = focus * 60
        self.rest_seconds = rest * 60
        self.db.set('focus_min', focus)
        self.db.set('rest_min', rest)
        self.session = 'pomodoro'
        self.phase = 'work'
        self.warning_played = False
        self.session_started = self._now().isoformat(timespec='seconds')
        self._pick_row()
        self._begin(self.focus_seconds)
        self.auto_label.config(text='PROTOCOL ENGAGED', fg='#00e5ff')
        self.timer_label.config(fg='#ff9800')

    def start_sprint(self):
        if self.running or self.curfew:
            return
        self.cancel_autostart()
        minutes = self._read_int(self.sprint_var, 1, 240, DEFAULT_SPRINT)
        self.db.set('sprint_min', minutes)
        self.session = 'sprint'
        self.phase = 'sprint'
        self.warning_played = False
        self.sprint_minutes_run = minutes
        self.session_started = self._now().isoformat(timespec='seconds')
        self.cur_row = None
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
    def _break_text(self, txt):
        s = f'{self.overlay_funny}\n\nMANDATORY BREAK\n\n{txt}'
        if self.next_up:
            s += f'\n\nNEXT UP: {self.next_up}'
        return s

    def _show(self, secs):
        mins, s = divmod(int(secs), 60)
        txt = f'{mins:02d}:{s:02d}'
        self.timer_label.config(text=txt)
        if self.overlay_label is not None:
            try:
                self.overlay_label.config(text=self._break_text(txt))
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
                    if not self.warning_played and 0 < secs <= WARNING_SECONDS:
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
        self.cur_row = None
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
        self._refresh_now_card()

    def _on_close(self):
        # Sealed during a lock / curfew. Otherwise X just tucks the window away.
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
        minutes = getattr(self, 'sprint_minutes_run', DEFAULT_SPRINT)
        started = self.session_started or self._now().isoformat(timespec='seconds')
        self.db.log_session(started, self._now().date().isoformat(), minutes, 'sprint',
                            self.plan_id, None, 'Sprint')
        self._stop_session()
        self.timer_label.config(text='00:00', fg='#00e676')
        self.auto_label.config(text="TIME'S UP!", fg='#00e676')
        play_time_up()
        self.refresh_stats()
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

    def _play_row_audio(self, row):
        if row is None:
            return
        p = self.db.audio_path(row['audio'])
        if p:
            play_audio_async(p)

    def start_break(self):
        # --- the focus session is complete: log it ---
        row = self.cur_row
        minutes = self.focus_seconds // 60
        started = self.session_started or self._now().isoformat(timespec='seconds')
        topic = (row['topic'] if row else '') or ('Focus' if row is None else 'Untitled')
        self.db.log_session(started, self._now().date().isoformat(), minutes, 'pomodoro',
                            self.plan_id, row['id'] if row else None, topic)
        if row is not None:
            self.db.update_row(row['id'], ran=1)
        self.pending_row = row
        nxt = self.db.next_row(self.plan_id) if self.plan_id is not None else None
        self.next_up = row_text(nxt) if nxt else ''
        self.overlay_funny = random.choice((
            'STEP AWAY FROM THE BOX.',
            'YOUR KEYBOARD IS NOW A PAPERWEIGHT.',
            'BRAIN REBOOT IN PROGRESS...',
            'THE COMPUTER MISSES YOU, BUT I DO NOT CARE.',
            'GO STARE AT A TREE OR SOMETHING.',
            'ACCESS DENIED. REASON: YOU ARE TIRED.'))

        self.phase = 'break'
        self.deadline = time.monotonic() + self.rest_seconds
        self.timer_label.config(fg='#ff1744')
        self.pause_btn.config(state='disabled')
        self.reset_btn.config(state='disabled')
        self._ensure_tm_killer()
        self.show_blocker_overlay()
        self.block_inputs()
        if self.audio_when == 'start':
            self._play_row_audio(row)
        self.refresh_stats()

    def end_break(self):
        row = getattr(self, 'pending_row', None)
        self.pending_row = None
        self.phase = 'work'
        self.warning_played = False
        self.deadline = time.monotonic() + self.focus_seconds
        self.session_started = self._now().isoformat(timespec='seconds')
        self.unblock_everything()
        self.next_up = ''
        self.timer_label.config(fg='#ff9800')
        self.pause_btn.config(state='normal', bg='#333333', text='PAUSE')
        self.reset_btn.config(state='normal', bg='#c62828')
        self.auto_label.config(text='PROTOCOL ENGAGED', fg='#00e5ff')
        play_time_up()
        if self.audio_when == 'end':
            self.root.after(1800, lambda: self._play_row_audio(row))
        self._pick_row()
        if row is not None:
            self.open_checkin(self.db.row(row['id']))
        if self.plan_win is not None:
            try:
                self.plan_win.refresh()
            except Exception:
                pass

    # ----------------------------------------------------- session check-in --
    def open_checkin(self, row):
        if row is None:
            return
        if self.checkin is not None:
            try:
                self.checkin.destroy()
            except Exception:
                pass
        top = self.checkin = tk.Toplevel(self.root)
        top.title('Session check-in')
        top.configure(bg=BG)
        top.attributes('-topmost', True)
        icon = find_asset(ICON_NAME)
        if icon:
            try:
                top.iconbitmap(str(icon))
            except Exception:
                pass
        w, h = 540, 300
        sw = self.root.winfo_screenwidth()
        top.geometry(f'{w}x{h}+{(sw - w) // 2}+80')
        tk.Label(top, text='SESSION DONE. LOG IT.', font=('Segoe UI', 15, 'bold'),
                 fg=BERRY, bg=BG).pack(pady=(16, 4))
        tk.Label(top, text=row_text(row), font=('Segoe UI', 11), fg='#ffffff', bg=BG,
                 wraplength=500).pack(padx=20)
        tk.Label(top, text='What did you actually cover / finish?', font=('Segoe UI', 10),
                 fg='#aaaaaa', bg=BG).pack(pady=(14, 4))
        var = tk.StringVar(value=row['progress'] or '')
        e = tk.Entry(top, textvariable=var, font=('Segoe UI', 12), bg='#1e1e1e', fg='#ffffff',
                     insertbackground='#ffffff', relief='flat')
        e.pack(fill='x', padx=24, ipady=7)
        okv = tk.BooleanVar(value=True)
        tk.Checkbutton(top, text='Goal reached  (untick = repeat this block in a later session)',
                       variable=okv, bg=BG, fg='#cccccc', selectcolor='#1e1e1e',
                       activebackground=BG, activeforeground='#ffffff',
                       font=('Segoe UI', 9)).pack(pady=(10, 4))

        def save(_e=None):
            note = var.get().strip()
            if okv.get():
                self.db.update_row(row['id'], progress=note, done=1, ran=1,
                                   done_at=self._now().isoformat(timespec='seconds'))
            else:
                self.db.update_row(row['id'], progress=note, done=0, ran=0, done_at=None)
            self.db.set_session_note(row['id'], note)
            try:
                top.destroy()
            except Exception:
                pass
            self.checkin = None
            self._pick_row()
            self.refresh_stats()
            if self.plan_win is not None:
                try:
                    self.plan_win.refresh()
                except Exception:
                    pass

        tk.Button(top, text='SAVE', command=save, bg='#2e7d32', fg='white', relief='flat',
                  font=('Segoe UI', 10, 'bold'), width=16, pady=4, cursor='hand2').pack(pady=10)
        e.bind('<Return>', save)
        top.protocol('WM_DELETE_WINDOW', lambda: (top.destroy(), setattr(self, 'checkin', None)))
        try:
            top.focus_force()
            e.focus_set()
        except Exception:
            pass

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
        sw = self.overlay.winfo_screenwidth()
        self.overlay_label = tk.Label(self.overlay, text='', fg='white', bg='black',
                                      font=('Verdana', 26, 'bold'), justify='center',
                                      wraplength=int(sw * 0.85))
        self.overlay_label.pack(expand=True)
        secs = int(max(0, math.ceil(self.deadline - time.monotonic())))
        self._show(secs)

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
    def _load_bedtime(self):
        try:
            bt = int(self.db.get('bedtime'))
            if (self.db.get('bedtime_date') == self._now().date().isoformat()
                    and BEDTIME_MIN <= bt <= BEDTIME_MAX):
                return bt
        except Exception:
            pass
        return DEFAULT_BEDTIME

    def _save_bedtime(self):
        today = self._now().date().isoformat()
        self.db.set('bedtime', self.bedtime_min)
        self.db.set('bedtime_date', today)
        self.db.set('bedtime_picked', today)

    def _build_sleep_panel(self):
        p = tk.Frame(self.root, bg=BG)
        p.pack(side='bottom', fill='x', pady=(0, 8))
        tk.Frame(p, bg='#2a2a2a', height=1).pack(fill='x', padx=30, pady=(4, 6))
        row = tk.Frame(p, bg=BG)
        row.pack()
        tk.Label(row, text='SLEEP TIMER', font=('Segoe UI', 9, 'bold'), fg='#7e57c2',
                 bg=BG).pack(side='left', padx=(0, 14))
        self._row_label(row, 'Bedtime').pack(side='left', padx=(0, 8))
        self.bed_var = tk.StringVar(value=fmt_clock(self.bedtime_min))
        self.bed_menu = tk.OptionMenu(row, self.bed_var, *BEDTIME_CHOICES.keys(),
                                      command=self._on_bedtime_pick)
        self.bed_menu.config(width=9, font=('Segoe UI', 11, 'bold'), bg='#1e1e1e', fg='#ffffff',
                             activebackground='#333333', activeforeground='#ffffff',
                             relief='flat', bd=0, highlightthickness=0, cursor='hand2')
        self.bed_menu['menu'].config(bg='#1e1e1e', fg='#ffffff', activebackground='#7e57c2',
                                     activeforeground='#ffffff', font=('Segoe UI', 11))
        self.bed_menu.pack(side='left')
        self.sleep_status = tk.Label(p, text='', font=('Segoe UI', 10, 'bold'), fg='#b39ddb', bg=BG)
        self.sleep_status.pack(pady=(4, 0))
        tk.Label(p, text=f'Pick 10 PM - 12 AM. Once you pick, you can only move it EARLIER. '
                         f'Locked till {fmt_clock(WAKE_MIN)}.',
                 font=('Segoe UI', 8), fg='#777777', bg=BG).pack()

    def _note(self, text, color='#ff5252', secs=4):
        self.sleep_status.config(text=text, fg=color)
        self._note_until = time.monotonic() + secs

    def _on_bedtime_pick(self, label):
        mins = BEDTIME_CHOICES.get(label)
        if mins is None:
            return
        revert = lambda: self.bed_var.set(fmt_clock(self.bedtime_min))
        if mins <= self._now_min() + 1:
            revert()
            self._note('That time already passed. Pick a later one.')
            return
        if self.bed_picked and mins > self.bedtime_min:
            revert()
            self._note('Locked in. Bedtime can only move earlier today.')
            return
        self.bedtime_min = mins
        self.bed_picked = True
        self._save_bedtime()

    def _update_sleep_status(self):
        if time.monotonic() < self._note_until:
            return
        if self.curfew:
            self.sleep_status.config(text=f'Lights out. See you at {fmt_clock(WAKE_MIN)}.', fg='#ff1744')
            return
        n = self._now()
        left = max(0, self.bedtime_min * 60 - (n.hour * 3600 + n.minute * 60 + n.second))
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
        self.root.after(250, self._clock_tick)

    # ------------------------------------------------------------- curfew --
    def _start_curfew(self):
        night = self._now_min() < WAKE_MIN
        self.curfew_headline = ('YOU ARE UP PAST CURFEW.\nDISOBEDIENT.' if night
                                else f'IT IS {fmt_clock(self.bedtime_min)}.\nBEDTIME.')
        self.curfew = True
        self.cancel_autostart()
        self.autostart_active = False
        if self.plan_win is not None:
            try:
                self.plan_win.close()
            except Exception:
                pass
        if self.checkin is not None:
            try:
                self.checkin.destroy()
            except Exception:
                pass
            self.checkin = None
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
                              '/c', 'BerryFocus: bedtime. Go to sleep.'],
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
            self.curfew_phase = 'hold'     # shutdown didn't take: hold the lock till wake time
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
        self.bedtime_min = self._load_bedtime()
        self.bed_picked = self.db.get('bedtime_picked', '') == self._now().date().isoformat()
        self.bed_var.set(fmt_clock(self.bedtime_min))
        self._show_mode('pomodoro')
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
        start_guardian()         # keeps the BerryFocus task alive in Task Scheduler
    BerryFocus(dry_run=dry, fake_now=_parse_fake_now(argv)).run()
