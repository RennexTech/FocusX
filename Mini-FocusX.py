import tkinter as tk
import threading
import time
import random
from pynput.mouse import Listener as MouseListener
from pynput.keyboard import Listener as KeyboardListener

# use this to compile it:

# python -m nuitka --standalone --onefile --windows-disable-console --windows-icon-from-ico="detective.ico" --enable-plugin=tk-inter --include-package=pynput "FocusX8.py"

# pip install nuitka is a must, pyinstaller started refusing to compile it...

# zig compiler will be installed an it takes a while.

# --- CONFIGURATION ---
WORK_MINUTES = 37
BREAK_MINUTES = 1
APP_OPACITY = 0.90
THEME_ACCENT = "#D4AF37"
THEME_BG = "#121212"
THEME_TEXT = "#FFFFFF"
THEME_HEADER = "#1A1A1A"

class InputBlocker:
    """Blocks keyboard and mouse input during the break period."""
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
        
        # Remove the Windows Title Bar (The white bar)
        self.root.overrideredirect(True) 
        
        self.root.geometry("240x130")
        self.root.configure(bg=THEME_BG)
        self.root.attributes("-alpha", APP_OPACITY)
        
        # Variables for dragging the window
        self._offsetx = 0
        self._offsety = 0
        
        self.is_paused = False
        self.is_work_phase = True
        self.blocker = InputBlocker()
        
        self.messages = [
            "GO STARE AT A TREE. IT MISSES YOU.",
            "DRINK SOME WATER RIGHT NOW.",
            "STRETCH YOUR BACK. DO IT.",
            "TOUCH SOME REAL GRASS.",
            "LOOK AT SOMETHING 20 FEET AWAY.",
            "REST YOUR EYES. DARKNESS IS GOOD.",
            "STAND UP. YOUR CHAIR IS TIRED OF YOU."
        ]

        self.time_var = tk.StringVar(value="37:00")
        self.status_var = tk.StringVar(value="● FOCUSING")
        self.always_on_top = tk.BooleanVar(value=True)
        
        self._build_ui()
        self._apply_float()
        
        threading.Thread(target=self._main_loop, daemon=True).start()

    def _build_ui(self):
        # Custom Header (Handle for dragging)
        self.header = tk.Frame(self.root, bg=THEME_HEADER, height=25)
        self.header.pack(fill='x')
        self.header.bind('<Button-1>', self._click_win)
        self.header.bind('<B1-Motion>', self._drag_win)

        # Small Exit Button in Header
        exit_btn = tk.Label(self.header, text="✕", bg=THEME_HEADER, fg="#555555", 
                            font=("Arial", 10, "bold"), padx=5)
        exit_btn.pack(side='right')
        exit_btn.bind('<Button-1>', lambda e: self.root.destroy())
        
        # Main Timer Display
        timer_label = tk.Label(self.root, textvariable=self.time_var, font=("Consolas", 36, "bold"), 
                               bg=THEME_BG, fg=THEME_TEXT)
        timer_label.pack(pady=(5, 0))
        timer_label.bind('<Button-1>', self._click_win) # Allow dragging from clock too
        timer_label.bind('<B1-Motion>', self._drag_win)
        
        # Status Label
        tk.Label(self.root, textvariable=self.status_var, font=("Consolas", 10, "bold"), 
                 bg=THEME_BG, fg=THEME_ACCENT).pack()

        # Bottom Controls
        ctrl_frame = tk.Frame(self.root, bg=THEME_BG)
        ctrl_frame.pack(fill='x', pady=5)
        
        tk.Checkbutton(ctrl_frame, text="FLOAT", variable=self.always_on_top, 
                       command=self._apply_float, font=("Consolas", 8),
                       bg=THEME_BG, fg="#666666", selectcolor="#000000",
                       activebackground=THEME_BG, activeforeground=THEME_TEXT,
                       highlightthickness=0, bd=0).pack(side='left', padx=10)
        
        self.pause_btn = tk.Button(ctrl_frame, text="PAUSE", command=self._toggle_pause,
                                   font=("Consolas", 8, "bold"), bg="#252525", fg="white", 
                                   relief="flat", padx=10)
        self.pause_btn.pack(side='right', padx=10)

    def _click_win(self, event):
        self._offsetx = event.x
        self._offsety = event.y

    def _drag_win(self, event):
        x = self.root.winfo_x() + event.x - self._offsetx
        y = self.root.winfo_y() + event.y - self._offsety
        self.root.geometry(f"+{x}+{y}")

    def _apply_float(self):
        self.root.attributes("-topmost", self.always_on_top.get())

    def _toggle_pause(self):
        self.is_paused = not self.is_paused
        self.pause_btn.config(text="RESUME" if self.is_paused else "PAUSE", 
                              bg=THEME_ACCENT if self.is_paused else "#252525")
        self.status_var.set("⏸ PAUSED" if self.is_paused else "● FOCUSING")

    def _main_loop(self):
        while True:
            self.is_work_phase = True
            self._countdown(WORK_MINUTES * 60)
            
            self.is_work_phase = False
            self.root.after(0, self._show_break)
            self._countdown(BREAK_MINUTES * 60)
            self.root.after(0, self._hide_break)

    def _countdown(self, seconds):
        remaining = seconds
        while remaining >= 0:
            if self.is_paused and self.is_work_phase:
                time.sleep(0.5)
                continue
            
            m, s = divmod(remaining, 60)
            self.time_var.set(f"{m:02d}:{s:02d}")
            
            if not self.is_work_phase and hasattr(self, 'break_timer_label'):
                self.break_timer_label.config(text=f"{m:02d}:{s:02d}")
            
            time.sleep(1)
            remaining -= 1

    def _show_break(self):
        self.overlay = tk.Toplevel(self.root)
        self.overlay.attributes("-fullscreen", True, "-topmost", True)
        self.overlay.configure(bg="black")
        self.blocker.toggle(True)
        
        msg = random.choice(self.messages)
        container = tk.Frame(self.overlay, bg="black")
        container.place(relx=0.5, rely=0.5, anchor='center')
        
        tk.Label(container, text="BREAK TIME", font=("Consolas", 44, "bold"), 
                 fg="#444444", bg="black").pack()
        
        self.break_timer_label = tk.Label(container, text="01:00", font=("Consolas", 140, "bold"), 
                                         fg=THEME_ACCENT, bg="black")
        self.break_timer_label.pack(pady=10)
        
        tk.Label(container, text=msg, font=("Consolas", 28, "bold"), 
                 fg="white", bg="black", wraplength=1000).pack(pady=20)
        
        tk.Label(container, text="INPUTS LOCKED.", 
                 font=("Consolas", 14), fg="#222222", bg="black").pack(pady=30)

    def _hide_break(self):
        if hasattr(self, 'overlay'):
            self.overlay.destroy()
        self.blocker.toggle(False)
        self.status_var.set("● FOCUSING")

if __name__ == "__main__":
    app = FocusXMini()
    app.root.mainloop()
