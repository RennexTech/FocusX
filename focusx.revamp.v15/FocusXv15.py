import tkinter as tk
import threading
import time
import sys
import math
import random
import ctypes
from pathlib import Path
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

WARNING_SECONDS = 20
AUTOSTART_SECONDS = 5
DEFAULT_FOCUS = 40
DEFAULT_REST = 1
DEFAULT_SPRINT = 25

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


# -------------------------------------------------------------------- app --
class FocusX:
    def __init__(self):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        self.root = tk.Tk()
        self.root.title('Deep Work Protocol')
        self.root.configure(bg=BG)

        icon = find_asset(ICON_NAME)
        if icon:
            try:
                self.root.iconbitmap(str(icon))
            except Exception:
                pass

        w, h = 420, 620
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

        self.setup_ui()
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self._autostart_tick()

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
        self.ctrl_frame.pack(pady=16)
        self.pause_btn = tk.Button(self.ctrl_frame, text='PAUSE', width=12, command=self.toggle_pause,
                                   state='disabled', bg='#333333', fg='#ffffff', relief='flat',
                                   font=('Segoe UI', 9, 'bold'))
        self.pause_btn.pack(side='left', padx=8)
        self.reset_btn = tk.Button(self.ctrl_frame, text='RESET', width=12, command=self.reset_app,
                                   state='disabled', bg='#c62828', fg='white', relief='flat',
                                   font=('Segoe UI', 9, 'bold'))
        self.reset_btn.pack(side='left', padx=8)

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
        if self.running:
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
        if self.running:
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
        # Warning fired or break running: the door is sealed.
        if self.locked:
            try:
                self.root.bell()
            except Exception:
                pass
            return
        self.running = False
        try:
            self.unblock_everything()
        except Exception:
            pass
        self.root.destroy()

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
            while self.running and self.locked:
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
        if self.overlay is not None:
            try:
                self.overlay.destroy()
            except Exception:
                pass
            self.overlay = None

    def run(self):
        self.root.mainloop()


if __name__ == '__main__':
    FocusX().run()
