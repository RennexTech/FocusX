import tkinter as tk
import threading
import time
import random
from datetime import datetime
from pynput.mouse import Listener as MouseListener
from pynput.keyboard import Listener as KeyboardListener

# use this to compile it:
# python -m nuitka --standalone --onefile --windows-disable-console --windows-icon-from-ico="detective.ico" --enable-plugin=tk-inter --include-package=pynput "FocusX8.py"

# --- CONFIGURATION ---
WORK_MINUTES = 40
BREAK_MINUTES = 1
APP_OPACITY = 0.90

# Cyber-Noir Palette Upgrade
THEME_BG = "#0A0B10"          # Deep cosmic obsidian
THEME_HEADER = "#12131C"      # Sleek graphite top bar
THEME_CARD = "#161824"        # Embedded framing element
THEME_TEXT = "#F0F2FA"        # Bright crisp stark white
THEME_ACCENT = "#FF2E63"      # Vivid electric crimson
THEME_MUTED = "#4A4E69"       # Muted slate steel
THEME_GLOW = "#00F5D4"        # Neon cyan accent for specific focus

FONT_CLOCK = ("Segoe UI Semibold", 38)
FONT_TEXT = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 9, "bold")

class InputBlocker:
    """Blocks keyboard and mouse input by swallowing events."""
    def __init__(self):
        self.active = False
        self.m_listener = None
        self.k_listener = None

    def toggle(self, state):
        if state and not self.active:
            self.active = True
            self.m_listener = MouseListener(on_move=lambda x,y:False, on_click=lambda x,y,b,p:False, on_scroll=lambda x,y,dx,dy:False)
            self.k_listener = KeyboardListener(on_press=lambda k:False)
            self.m_listener.start()
            self.k_listener.start()
        elif not state and self.active:
            self.active = False
            if self.m_listener: self.m_listener.stop()
            if self.k_listener: self.k_listener.stop()

class FocusXMini:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("FocusX")
        
        self.root.overrideredirect(True) 
        self.root.geometry("260x150") # Added slight padding for cleaner proportions
        self.root.configure(bg=THEME_BG)
        self.root.attributes("-alpha", APP_OPACITY)
        
        self._offsetx = 0
        self._offsety = 0
        
        self.is_paused = False
        self.is_work_phase = True
        self.is_night_lock = False
        self.blocker = InputBlocker()
        
        self.messages = [
            "GO STARE AT A TREE.",
            "DRINK SOME WATER RIGHT NOW.",
            "STRETCH YOUR BACK, SOME COBRA POSE.",
            "TOUCH SOME GRASS LIKE A COW.",
            "LOOK AT SOMETHING 20 FEET AWAY.",
            "REST YOUR EYES.",
            "STAND UP, RIGHT NOW! YOUR CHAIR IS TIRED OF YOU."
        ]

        self.time_var = tk.StringVar(value="37:00")
        self.status_var = tk.StringVar(value="● SYSTEM ENGAGED")
        self.always_on_top = tk.BooleanVar(value=True)
        
        self._build_ui()
        self._apply_float()
        
        threading.Thread(target=self._main_loop, daemon=True).start()
        threading.Thread(target=self._night_lock_monitor, daemon=True).start()

    def _build_ui(self):
        # Header Bar
        self.header = tk.Frame(self.root, bg=THEME_HEADER, height=28)
        self.header.pack(fill='x')
        self.header.bind('<Button-1>', self._click_win)
        self.header.bind('<B1-Motion>', self._drag_win)

        # Mini Branding in Top Left
        brand_lbl = tk.Label(self.header, text="  FOCUS // X", bg=THEME_HEADER, fg=THEME_MUTED, font=FONT_TITLE)
        brand_lbl.pack(side='left')
        brand_lbl.bind('<Button-1>', self._click_win)
        brand_lbl.bind('<B1-Motion>', self._drag_win)

        # Polished Exit Button
        exit_btn = tk.Label(self.header, text="✕ ", bg=THEME_HEADER, fg=THEME_MUTED, 
                            font=("Segoe UI", 10, "bold"), padx=5)
        exit_btn.pack(side='right', fill='y')
        exit_btn.bind('<Button-1>', lambda e: self.root.destroy())
        exit_btn.bind('<Enter>', lambda e: exit_btn.config(fg=THEME_ACCENT))
        exit_btn.bind('<Leave>', lambda e: exit_btn.config(fg=THEME_MUTED))
        
        # Centralized Frame Content Card
        card = tk.Frame(self.root, bg=THEME_BG)
        card.pack(fill='both', expand=True, padx=15, pady=(5, 5))
        
        # Main Timer Display
        timer_label = tk.Label(card, textvariable=self.time_var, font=FONT_CLOCK, 
                               bg=THEME_BG, fg=THEME_TEXT)
        timer_label.pack(pady=(2, 0))
        timer_label.bind('<Button-1>', self._click_win)
        timer_label.bind('<B1-Motion>', self._drag_win)
        
        # Status Label
        self.status_lbl = tk.Label(card, textvariable=self.status_var, font=FONT_TEXT, 
                                   bg=THEME_BG, fg=THEME_GLOW)
        self.status_lbl.pack(pady=(0, 5))

        # Action / Control Section
        ctrl_frame = tk.Frame(card, bg=THEME_BG)
        ctrl_frame.pack(fill='x', side='bottom', pady=(0, 2))
        
        # Styled Float Toggle Button
        self.float_btn = tk.Checkbutton(ctrl_frame, text="PIN WINDOW", variable=self.always_on_top, 
                       command=self._apply_float, font=("Segoe UI", 8, "bold"),
                       bg=THEME_BG, fg=THEME_MUTED, selectcolor=THEME_HEADER,
                       activebackground=THEME_BG, activeforeground=THEME_TEXT,
                       highlightthickness=0, bd=0)
        self.float_btn.pack(side='left', padx=2)
        
        # Sleek Control Button
        self.pause_btn = tk.Button(ctrl_frame, text="PAUSE ENGINE", command=self._toggle_pause,
                                   font=("Segoe UI", 8, "bold"), bg=THEME_CARD, fg=THEME_TEXT, 
                                   activebackground=THEME_HEADER, activeforeground=THEME_ACCENT,
                                   relief="flat", bd=0, padx=12, pady=3)
        self.pause_btn.pack(side='right', padx=2)

    def _click_win(self, event):
        self._offsetx = event.x
        self._offsety = event.y

    def _drag_win(self, event):
        if self.is_night_lock: return
        x = self.root.winfo_x() + event.x - self._offsetx
        y = self.root.winfo_y() + event.y - self._offsety
        self.root.geometry(f"+{x}+{y}")

    def _apply_float(self):
        self.root.attributes("-topmost", self.always_on_top.get())

    def _toggle_pause(self):
        if self.is_night_lock: return
        self.is_paused = not self.is_paused
        self.pause_btn.config(text="RESUME ENGINE" if self.is_paused else "PAUSE ENGINE", 
                              fg=THEME_GLOW if self.is_paused else THEME_TEXT)
        self.status_var.set("⏸ PAUSED" if self.is_paused else "● SYSTEM ENGAGED")
        self.status_lbl.config(fg=THEME_MUTED if self.is_paused else THEME_GLOW)

    def _main_loop(self):
        while True:
            if self.is_night_lock:
                time.sleep(1)
                continue

            self.is_work_phase = True
            self.root.after(0, lambda: self.status_lbl.config(fg=THEME_GLOW))
            self._countdown(WORK_MINUTES * 60)
            
            if self.is_night_lock: continue

            self.is_work_phase = False
            self.root.after(0, self._show_break)
            self._countdown(BREAK_MINUTES * 60)
            self.root.after(0, self._hide_break)

    def _countdown(self, seconds):
        remaining = seconds
        while remaining >= 0:
            if self.is_night_lock:
                break
            if self.is_paused and self.is_work_phase:
                time.sleep(0.5)
                continue
            
            m, s = divmod(remaining, 60)
            self.time_var.set(f"{m:02d}:{s:02d}")
            
            if not self.is_work_phase and hasattr(self, 'break_timer_label'):
                try: self.break_timer_label.config(text=f"{m:02d}:{s:02d}")
                except tk.TclError: pass
            
            time.sleep(1)
            remaining -= 1

    def _show_break(self):
        if self.is_night_lock: return
        self.overlay = tk.Toplevel(self.root)
        self.overlay.attributes("-fullscreen", True, "-topmost", True)
        self.overlay.configure(bg=THEME_BG)
        self.blocker.toggle(True)
        
        msg = random.choice(self.messages)
        container = tk.Frame(self.overlay, bg=THEME_BG)
        container.place(relx=0.5, rely=0.5, anchor='center')
        
        tk.Label(container, text="DISCONNECT NOW", font=("Segoe UI Semibold", 32), 
                 fg=THEME_MUTED, bg=THEME_BG).pack()
        
        self.break_timer_label = tk.Label(container, text="01:00", font=("Segoe UI Semibold", 130), 
                                          fg=THEME_ACCENT, bg=THEME_BG)
        self.break_timer_label.pack(pady=0)
        
        tk.Label(container, text=msg, font=("Segoe UI", 26, "bold"), 
                 fg=THEME_TEXT, bg=THEME_BG, wraplength=1000).pack(pady=10)
        
        tk.Label(container, text="ALL INPUT SYSTEMS OFFLINE", 
                 font=("Segoe UI", 12, "bold"), fg=THEME_CARD, bg=THEME_BG).pack(pady=20)

    def _hide_break(self):
        if hasattr(self, 'overlay'):
            try: self.overlay.destroy()
            except tk.TclError: pass
        if not self.is_night_lock:
            self.blocker.toggle(False)
            self.status_var.set("● SYSTEM ENGAGED")

    def _night_lock_monitor(self):
        while True:
            now = datetime.now().time()
            is_sleep_hours = now >= datetime.strptime("23:00", "%H:%M").time() or now < datetime.strptime("05:00", "%H:%M").time()

            if is_sleep_hours and not self.is_night_lock:
                self.is_night_lock = True
                self.root.after(0, self._activate_night_overlay)
            elif not is_sleep_hours and self.is_night_lock:
                self.is_night_lock = False
                self.root.after(0, self._deactivate_night_overlay)

            time.sleep(5)

    def _activate_night_overlay(self):
        if hasattr(self, 'overlay'):
            try: self.overlay.destroy()
            except tk.TclError: pass
        
        self.status_var.set("🌙 SYSTEM LOCKED")
        self.status_lbl.config(fg=THEME_ACCENT)
        self.pause_btn.config(state="disabled")
        
        self.night_overlay = tk.Toplevel(self.root)
        self.night_overlay.attributes("-fullscreen", True, "-topmost", True)
        self.night_overlay.configure(bg=THEME_BG)
        
        self.night_overlay.protocol("WM_DELETE_WINDOW", lambda: None)
        self.blocker.toggle(True)
        
        container = tk.Frame(self.night_overlay, bg=THEME_BG)
        container.place(relx=0.5, rely=0.5, anchor='center')
        
        tk.Label(container, text="HARD LOCKOUT MODE", font=("Segoe UI HARD", 36, "bold"), 
                 fg=THEME_ACCENT, bg=THEME_BG).pack(pady=10)
        tk.Label(container, text="IT IS PAST 11 PM. STEP AWAY FROM THE DECK.", font=("Segoe UI", 22, "bold"), 
                 fg=THEME_TEXT, bg=THEME_BG).pack(pady=10)
        tk.Label(container, text="Restoring operations automatically at 5:00 AM.", font=("Segoe UI", 13), 
                 fg=THEME_MUTED, bg=THEME_BG).pack(pady=20)

    def _deactivate_night_overlay(self):
        if hasattr(self, 'night_overlay'):
            try: self.night_overlay.destroy()
            except tk.TclError: pass
        self.blocker.toggle(False)
        self.pause_btn.config(state="normal")
        self.status_var.set("● SYSTEM ENGAGED")
        self.status_lbl.config(fg=THEME_GLOW)

if __name__ == "__main__":
    app = FocusXMini()
    app.root.mainloop()