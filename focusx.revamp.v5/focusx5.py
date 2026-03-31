import sys
import tkinter as tk
from tkinter import messagebox
import threading
import time
import psutil
from pynput.mouse import Listener as MouseListener
from pynput.keyboard import Listener as KeyboardListener

# --- 1. THEME & VISUALS ---

class Theme:
    BG = "#FDFBF7"          # Warm Cream
    SURFACE = "#F5F2ED"     # Subtle Section Background
    TEXT = "#1A1A1A"        # Deep Charcoal
    ACCENT = "#D4AF37"      # Muted Gold
    DANGER = "#A63D40"      # Muted Red
    BORDER = "#E0DDD5"      
    FONT_MAIN = ("Consolas", 12)
    FONT_BOLD = ("Consolas", 12, "bold")
    FONT_TIMER = ("Consolas", 55, "bold")
    FONT_LABEL = ("Consolas", 9)
    FONT_MSG = ("Consolas", 10, "italic")

# --- 2. SYSTEM ENGINES ---

class InputBlocker:
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

class TaskReaper:
    def __init__(self, app):
        self.app = app
        self.targets = ['taskmgr.exe', 'processhacker.exe', 'procexp.exe', 'perfmon.exe']

    def start(self):
        threading.Thread(target=self._run_loop, daemon=True).start()

    def _run_loop(self):
        while True:
            # Only kill if the session is running, work phase, AND reaper is enabled
            if self.app.is_running and self.app.is_work_phase and self.app.reaper_enabled.get():
                try:
                    for proc in psutil.process_iter(['name']):
                        if proc.info['name'].lower() in self.targets:
                            proc.kill()
                except:
                    pass
            time.sleep(0.8)

# --- 3. MAIN APPLICATION ---

class FocusX:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("FocusX Premium")
        self.root.geometry("500x750")
        self.root.configure(bg=Theme.BG)
        
        # State
        self.is_running = False
        self.is_paused = False
        self.is_work_phase = True
        self.pause_clicks = 0
        
        # Core Modules
        self.blocker = InputBlocker()
        self.reaper = TaskReaper(self)
        
        # StringVars
        self.time_display = tk.StringVar(value="00:00:00")
        self.status_display = tk.StringVar(value="Ready to Focus")
        self.pause_msg_var = tk.StringVar(value="")
        
        # Default Settings (40/1/Night-Lock)
        self.work_h, self.work_m, self.work_s = tk.StringVar(value="00"), tk.StringVar(value="40"), tk.StringVar(value="00")
        self.rest_h, self.rest_m, self.rest_s = tk.StringVar(value="00"), tk.StringVar(value="01"), tk.StringVar(value="00")
        self.sleep_start, self.sleep_end = tk.StringVar(value="00:00"), tk.StringVar(value="05:00")
        
        self.reaper_enabled = tk.BooleanVar(value=True)

        self._build_ui()
        self.reaper.start()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_attempt)

    def _build_ui(self):
        # Header
        tk.Label(self.root, text="FOCUS-X", font=("Consolas", 24, "bold"), bg=Theme.BG, fg=Theme.TEXT).pack(pady=(30, 5))
        
        # Reaper Toggle
        toggle_frame = tk.Frame(self.root, bg=Theme.BG)
        toggle_frame.pack()
        tk.Checkbutton(toggle_frame, text="Hardcore Mode (Kill TaskManager)", variable=self.reaper_enabled, 
                       onvalue=True, offvalue=False, bg=Theme.BG, font=("Consolas", 9),
                       command=self._reaper_warning).pack(side='left')

        # Timer
        tk.Label(self.root, textvariable=self.time_display, font=Theme.FONT_TIMER, bg=Theme.BG, fg=Theme.TEXT).pack(pady=10)
        tk.Label(self.root, textvariable=self.status_display, font=("Consolas", 10, "italic"), bg=Theme.BG, fg="#888888").pack()

        # Input Panel
        panel = tk.Frame(self.root, bg=Theme.SURFACE, highlightbackground=Theme.BORDER, highlightthickness=1)
        panel.pack(padx=40, fill='x', pady=20)

        def create_input_block(parent, title, h_v, m_v, s_v):
            frame = tk.Frame(parent, bg=Theme.SURFACE)
            frame.pack(fill='x', padx=20, pady=10)
            tk.Label(frame, text=title, font=Theme.FONT_BOLD, bg=Theme.SURFACE, fg=Theme.TEXT).pack(anchor='w')
            grid = tk.Frame(frame, bg=Theme.SURFACE)
            grid.pack(anchor='w', pady=5)
            sections = [("HRS", h_v), ("MIN", m_v), ("SEC", s_v)]
            for i, (label, var) in enumerate(sections):
                cell = tk.Frame(grid, bg=Theme.SURFACE)
                cell.pack(side='left', padx=2)
                tk.Entry(cell, textvariable=var, width=3, font=("Consolas", 14), bg="white", justify='center').pack()
                tk.Label(cell, text=label, font=Theme.FONT_LABEL, bg=Theme.SURFACE, fg="#999999").pack()
                if i < 2: tk.Label(grid, text=":", font=("Consolas", 14), bg=Theme.SURFACE).pack(side='left', pady=(0, 15))

        create_input_block(panel, "WORK DURATION", self.work_h, self.work_m, self.work_s)
        create_input_block(panel, "REST DURATION", self.rest_h, self.rest_m, self.rest_s)

        # Night Lock
        sleep_f = tk.Frame(panel, bg=Theme.SURFACE)
        sleep_f.pack(fill='x', padx=20, pady=(0, 15))
        tk.Label(sleep_f, text="NIGHT LOCK (24H FORMAT)", font=Theme.FONT_BOLD, bg=Theme.SURFACE, fg=Theme.TEXT).pack(anchor='w')
        r = tk.Frame(sleep_f, bg=Theme.SURFACE)
        r.pack(anchor='w', pady=5)
        tk.Entry(r, textvariable=self.sleep_start, width=7, font=Theme.FONT_MAIN).pack(side='left')
        tk.Label(r, text=" TO ", font=Theme.FONT_LABEL, bg=Theme.SURFACE).pack(side='left', padx=5)
        tk.Entry(r, textvariable=self.sleep_end, width=7, font=Theme.FONT_MAIN).pack(side='left')

        # Main Button
        self.main_btn = tk.Button(self.root, text="INITIATE SESSION", font=("Consolas", 12, "bold"),
                                  bg=Theme.TEXT, fg="white", pady=12, command=self.start_logic)
        self.main_btn.pack(pady=10, padx=40, fill='x')

        # Pause Button & Message Container
        self.pause_container = tk.Frame(self.root, bg=Theme.BG)
        
        self.pause_btn = tk.Button(self.pause_container, text="PAUSE SESSION", font=("Consolas", 10, "bold"),
                                   bg=Theme.ACCENT, fg="white", relief="flat", padx=20, 
                                   command=self._handle_pause_attempt)
        self.pause_btn.pack(pady=(5, 0))
        
        self.pause_msg_label = tk.Label(self.pause_container, textvariable=self.pause_msg_var, 
                                        font=Theme.FONT_MSG, bg=Theme.BG, fg=Theme.DANGER, wraplength=400)
        self.pause_msg_label.pack(pady=5)
        
    def _reaper_warning(self):
        if not self.reaper_enabled.get():
            self.status_display.set("Fine. Disable me. I guess discipline isn't for everyone.")
            self.root.after(3000, lambda: self.status_display.set("Ready to Focus"))

    def start_logic(self):
        if self.is_running: return
        self.is_running = True
        self.main_btn.config(state='disabled', text="HARDCORE MODE ACTIVE")
        self.pause_container.pack(pady=5)
        
        # Calculate seconds
        try:
            self.w_sec = (int(self.work_h.get())*3600) + (int(self.work_m.get())*60) + int(self.work_s.get())
            self.r_sec = (int(self.rest_h.get())*3600) + (int(self.rest_m.get())*60) + int(self.rest_s.get())
        except ValueError:
            self.w_sec, self.r_sec = 2400, 60 # Defaults if user broke the inputs
        
        threading.Thread(target=self._session_loop, daemon=True).start()

    def _handle_pause_attempt(self):
        # We only care about clicks if not already paused
        if self.is_paused:
            self.is_paused = False
            self.pause_clicks = 0
            self.pause_btn.config(text="PAUSE SESSION", bg=Theme.ACCENT)
            self.pause_msg_var.set("")
            self.status_display.set("● FOCUSING")
            return

        self.pause_clicks += 1
        needed = 7
        remaining = needed - self.pause_clicks
        
        if remaining > 0:
            # The instruction you requested
            instruction = f"You have to click {remaining} more times to pause this to force discipline."
            
            # Emotional manipulation messages
            emotions = [
                "Discipline is what separates you from the rest.",
                "Is this really the moment you give up?",
                "Your future self is watching you quit right now.",
                "Imagine if you actually finished what you started.",
                "Winners don't look for the pause button.",
                "Just keep working. The pain of discipline is temporary."
            ]
            
            # Update the message below the button
            current_emotion = emotions[min(self.pause_clicks - 1, len(emotions) - 1)]
            self.pause_msg_var.set(f"{instruction}\n{current_emotion}")
        else:
            # Barrier passed
            self.is_paused = True
            self.pause_clicks = 0
            self.pause_btn.config(text="RESUME SESSION", bg=Theme.TEXT)
            self.pause_msg_var.set("Weakness accepted. Session paused.")
            self.status_display.set("● SESSION PAUSED")

    def _session_loop(self):
        while self.is_running:
            self.is_work_phase = True
            self._run_countdown(self.w_sec, "FOCUSING")
            
            self.is_work_phase = False
            self.root.after(0, self._show_break_screen, "TIME FOR A BREAK")
            self._run_countdown(self.r_sec, "RESTING")
            self.root.after(0, self._hide_break_screen)

    def _run_countdown(self, seconds, label):
        temp_sec = seconds
        while temp_sec >= 0:
            if not self.is_running: break
            if self.is_paused:
                time.sleep(0.5)
                continue
                
            h, rem = divmod(temp_sec, 3600)
            m, s = divmod(rem, 60)
            self.time_display.set(f"{h:02d}:{m:02d}:{s:02d}")
            self.status_display.set(f"● {label}")
            temp_sec -= 1
            time.sleep(1)

    def _show_break_screen(self, msg):
        self.overlay = tk.Toplevel(self.root)
        self.overlay.attributes("-fullscreen", True, "-topmost", True)
        self.overlay.configure(bg=Theme.BG)
        self.blocker.toggle(True)
        container = tk.Frame(self.overlay, bg=Theme.BG)
        container.place(relx=0.5, rely=0.5, anchor='center')
        tk.Label(container, text=msg, font=("Consolas", 48, "bold"), fg=Theme.TEXT, bg=Theme.BG).pack()
        tk.Label(container, text="Input Locked. Move your body.", font=("Consolas", 14), fg="#888888", bg=Theme.BG).pack(pady=30)

    def _hide_break_screen(self):
        if hasattr(self, 'overlay') and self.overlay:
            self.overlay.destroy()
        self.blocker.toggle(False)

    def _on_close_attempt(self):
        if self.is_running:
            self.status_display.set("!! SESSION ACTIVE: YOU CANNOT ESCAPE !!")
        else:
            self.root.destroy()

if __name__ == "__main__":
    FocusX().root.mainloop()