import tkinter as tk
import threading
import time
import random
from pynput.mouse import Listener as MouseListener
from pynput.keyboard import Listener as KeyboardListener

# --- 1. THEME & VISUALS ---

class Theme:
    BG = "#FDFBF7"          # Warm Cream (Easy on the eyes)
    SURFACE = "#F5F2ED"     # Subtle Section Background
    TEXT = "#1A1A1A"        # Deep Charcoal
    ACCENT = "#D4AF37"      # Muted Gold
    DANGER = "#A63D40"      # Muted Red
    BORDER = "#E0DDD5"      
    FONT_MAIN = ("Consolas", 12)
    FONT_BOLD = ("Consolas", 12, "bold")
    FONT_TIMER = ("Consolas", 60, "bold")
    FONT_BREAK_TIMER = ("Consolas", 80, "bold")
    FONT_LABEL = ("Consolas", 9)
    FONT_MSG = ("Consolas", 14, "italic")

# --- 2. INPUT BLOCKER (For Break Phase Only) ---

class InputBlocker:
    def __init__(self):
        self.active = False
        self.m_listener = None
        self.k_listener = None

    def toggle(self, state):
        if state and not self.active:
            self.active = True
            # Block inputs by returning False in the listeners
            self.m_listener = MouseListener(on_move=lambda x,y:False, on_click=lambda x,y,b,p:False, on_scroll=lambda x,y,dx,dy:False)
            self.k_listener = KeyboardListener(on_press=lambda k:False)
            self.m_listener.start()
            self.k_listener.start()
        elif not state and self.active:
            self.active = False
            if self.m_listener: self.m_listener.stop()
            if self.k_listener: self.k_listener.stop()

# --- 3. MAIN APPLICATION ---

class FocusX:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("FocusX: 44-4 Flow")
        self.root.geometry("450x650")
        self.root.configure(bg=Theme.BG)
        
        # State
        self.is_running = True
        self.is_paused = False
        self.is_work_phase = True
        
        # Core Modules
        self.blocker = InputBlocker()
        
        # StringVars
        self.time_display = tk.StringVar(value="00:44:00")
        self.status_display = tk.StringVar(value="● FOCUSING")
        self.break_timer_var = tk.StringVar(value="04:00")
        
        # Default Settings (44/4)
        self.work_m = tk.StringVar(value="44")
        self.rest_m = tk.StringVar(value="4")
        
        self.funny_messages = [
            "Go stare at a tree. It misses you.",
            "Hydrate or diedrate. Drink some water!",
            "Do a little dance. No one is watching. Probably.",
            "Touch some grass. Real grass, not Minecraft grass.",
            "Pet a dog. If no dog is available, pet a sturdy rock.",
            "Your chair misses you, but stay away for now.",
            "Error 404: Productivity not found (and that's okay).",
            "Stand up and stretch like a startled cat.",
            "Think about how cool space is for a second."
        ]

        self._build_ui()
        
        # AUTOSTART Logic
        threading.Thread(target=self._session_loop, daemon=True).start()
        
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_attempt)

    def _build_ui(self):
        # Header
        tk.Label(self.root, text="FOCUS-X", font=("Consolas", 28, "bold"), bg=Theme.BG, fg=Theme.TEXT).pack(pady=(40, 10))
        
        # Main Timer Display
        tk.Label(self.root, textvariable=self.time_display, font=Theme.FONT_TIMER, bg=Theme.BG, fg=Theme.TEXT).pack(pady=10)
        tk.Label(self.root, textvariable=self.status_display, font=("Consolas", 11, "bold"), bg=Theme.BG, fg=Theme.ACCENT).pack()

        # Input Panel (Hidden during flow, visible during pause)
        self.settings_frame = tk.Frame(self.root, bg=Theme.SURFACE, highlightbackground=Theme.BORDER, highlightthickness=1)
        self.settings_frame.pack(padx=40, fill='x', pady=30)

        def create_input_block(parent, title, m_v):
            frame = tk.Frame(parent, bg=Theme.SURFACE)
            frame.pack(fill='x', padx=20, pady=10)
            tk.Label(frame, text=title, font=Theme.FONT_BOLD, bg=Theme.SURFACE, fg=Theme.TEXT).pack(anchor='w')
            grid = tk.Frame(frame, bg=Theme.SURFACE)
            grid.pack(anchor='w', pady=5)
            
            tk.Entry(grid, textvariable=m_v, width=5, font=("Consolas", 16), bg="white", justify='center').pack(side='left')
            tk.Label(grid, text="MINUTES", font=Theme.FONT_LABEL, bg=Theme.SURFACE, fg="#999999").pack(side='left', padx=10)

        create_input_block(self.settings_frame, "WORK DURATION", self.work_m)
        create_input_block(self.settings_frame, "REST DURATION", self.rest_m)

        # Pause/Resume Button
        self.action_btn = tk.Button(self.root, text="PAUSE & ADJUST", font=("Consolas", 12, "bold"),
                                   bg=Theme.TEXT, fg="white", pady=12, relief="flat",
                                   command=self.toggle_pause)
        self.action_btn.pack(pady=20, padx=40, fill='x')

    def toggle_pause(self):
        if not self.is_paused:
            # Entering Pause
            self.is_paused = True
            self.action_btn.config(text="RESUME SESSION", bg=Theme.ACCENT)
            self.status_display.set("⏸ SESSION PAUSED")
        else:
            # Resuming
            self.is_paused = False
            self.action_btn.config(text="PAUSE & ADJUST", bg=Theme.TEXT)
            self.status_display.set("● FOCUSING")

    def _session_loop(self):
        while True:
            # 1. WORK PHASE
            self.is_work_phase = True
            try:
                w_sec = int(self.work_m.get()) * 60
            except ValueError:
                w_sec = 44 * 60 # Safety fallback
            
            self._run_countdown(w_sec, "FOCUSING")
            
            # 2. BREAK PHASE
            self.is_work_phase = False
            self.root.after(0, self._show_break_screen)
            
            try:
                r_sec = int(self.rest_m.get()) * 60
            except ValueError:
                r_sec = 4 * 60 # Safety fallback
                
            self._run_countdown(r_sec, "RESTING")
            self.root.after(0, self._hide_break_screen)

    def _run_countdown(self, seconds, label):
        temp_sec = seconds
        while temp_sec >= 0:
            if self.is_paused:
                time.sleep(0.5)
                continue
                
            m, s = divmod(temp_sec, 60)
            time_str = f"{m:02d}:{s:02d}"
            
            # Update UI
            self.time_display.set(f"00:{time_str}")
            if not self.is_work_phase:
                self.break_timer_var.set(time_str)
            
            temp_sec -= 1
            time.sleep(1)

    def _show_break_screen(self):
        self.overlay = tk.Toplevel(self.root)
        self.overlay.attributes("-fullscreen", True, "-topmost", True)
        self.overlay.configure(bg=Theme.BG)
        
        # Lock Input during break
        self.blocker.toggle(True)
        
        container = tk.Frame(self.overlay, bg=Theme.BG)
        container.place(relx=0.5, rely=0.5, anchor='center')
        
        # Random Funny Message
        msg = random.choice(self.funny_messages)
        
        tk.Label(container, text="TIME TO DISCONNECT", font=("Consolas", 32, "bold"), fg=Theme.TEXT, bg=Theme.BG).pack()
        tk.Label(container, textvariable=self.break_timer_var, font=Theme.FONT_BREAK_TIMER, fg=Theme.ACCENT, bg=Theme.BG, pady=20).pack()
        tk.Label(container, text=msg, font=Theme.FONT_MSG, fg=Theme.DANGER, bg=Theme.BG).pack(pady=20)
        tk.Label(container, text="Inputs are locked. Resistance is futile (and bad for your neck).", 
                 font=Theme.FONT_LABEL, fg="#AAAAAA", bg=Theme.BG).pack(pady=40)

    def _hide_break_screen(self):
        if hasattr(self, 'overlay') and self.overlay:
            self.overlay.destroy()
        self.blocker.toggle(False)

    def _on_close_attempt(self):
        # Allow closing since we trust the user's discipline now
        self.root.destroy()

if __name__ == "__main__":
    FocusX().root.mainloop()
