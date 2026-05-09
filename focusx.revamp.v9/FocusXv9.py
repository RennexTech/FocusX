import tkinter as tk
from tkinter import messagebox
import threading
import time
import os
import psutil
from pynput import mouse, keyboard
import random

# --- Hardcore Single-File Focus App (Looping Edition) ---

class HardcoreTimer:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Deep Work Protocol")
        self.root.geometry("400x520")
        self.root.configure(bg="#121212") 
        
        # Center the window logic
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = (screen_width // 2) - (200)
        y = (screen_height // 2) - (260)
        self.root.geometry(f"400x520+{x}+{y}")

        # State Variables
        self.remaining_time = 0
        self.work_duration = 0  # To remember for looping
        self.break_duration = 0 # To remember for looping
        self.is_running = False
        self.is_paused = False
        self.is_break = False
        self.auto_start_seconds = 20
        self.current_thread = None
        
        # Input Blocker Listeners
        self.mouse_listener = None
        self.kb_listener = None
        
        self.setup_ui()
        self.start_autostart_countdown()

    def setup_ui(self):
        # Header
        self.header = tk.Label(self.root, text="SYSTEM INITIALIZED", 
                                font=("Segoe UI", 16, "bold"), fg="#00ff00", bg="#121212")
        self.header.pack(pady=(30, 5))

        # Autostart Label
        self.auto_label = tk.Label(self.root, text=f"Launching in: {self.auto_start_seconds}s", 
                                    font=("Verdana", 11, "italic"), fg="#aaaaaa", bg="#121212")
        self.auto_label.pack()

        # Mode Buttons
        self.btn_frame = tk.Frame(self.root, bg="#121212")
        self.btn_frame.pack(pady=35)

        self.btn47 = tk.Button(self.btn_frame, text="47m FOCUS\n2m REST", width=14, height=3, 
                                command=lambda: self.select_mode(47, 2),
                                bg="#2e7d32", fg="white", font=("Segoe UI", 10, "bold"),
                                relief="raised", bd=6, activebackground="#1b5e20", cursor="hand2")
        self.btn47.pack(side="left", padx=12)

        self.btn120 = tk.Button(self.btn_frame, text="120m DEEP\n4m REST", width=14, height=3, 
                                 command=lambda: self.select_mode(120, 4),
                                 bg="#1565c0", fg="white", font=("Segoe UI", 10, "bold"),
                                 relief="raised", bd=6, activebackground="#0d47a1", cursor="hand2")
        self.btn120.pack(side="left", padx=12)

        # Timer Display
        self.timer_label = tk.Label(self.root, text="00:00", 
                                    font=("Verdana", 50, "bold"), fg="#ff9800", bg="#121212")
        self.timer_label.pack(pady=10)

        # Control Buttons
        self.ctrl_frame = tk.Frame(self.root, bg="#121212")
        self.ctrl_frame.pack(pady=25)

        self.pause_btn = tk.Button(self.ctrl_frame, text="PAUSE", width=12, command=self.toggle_pause, 
                                    state="disabled", bg="#333333", fg="#ffffff", relief="flat", 
                                    font=("Segoe UI", 9, "bold"))
        self.pause_btn.pack(side="left", padx=8)

        self.reset_btn = tk.Button(self.ctrl_frame, text="RESET", width=12, command=self.reset_app, 
                                    state="disabled", bg="#c62828", fg="white", relief="flat",
                                    font=("Segoe UI", 9, "bold"))
        self.reset_btn.pack(side="left", padx=8)

    def start_autostart_countdown(self):
        if self.auto_start_seconds > 0 and not self.is_running:
            self.auto_start_seconds -= 1
            self.auto_label.config(text=f"Launching in: {self.auto_start_seconds}s")
            self.root.after(1000, self.start_autostart_countdown)
        elif self.auto_start_seconds == 0 and not self.is_running:
            self.select_mode(47, 2)

    def select_mode(self, work, rest):
        self.work_duration = work * 60
        self.break_duration = rest * 60
        self.remaining_time = self.work_duration
        self.is_running = True
        self.is_break = False
        
        self.auto_label.config(text="PROTOCOL ENGAGED", fg="#00e5ff")
        self.btn47.config(state="disabled", relief="sunken", bg="#1b5e20")
        self.btn120.config(state="disabled", relief="sunken", bg="#0d47a1")
        self.pause_btn.config(state="normal")
        self.reset_btn.config(state="normal")
        
        if self.current_thread is None or not self.current_thread.is_alive():
            self.current_thread = threading.Thread(target=self.run_timer, daemon=True)
            self.current_thread.start()

    def toggle_pause(self):
        if not self.is_break:
            self.is_paused = not self.is_paused
            self.pause_btn.config(text="CONTINUE" if self.is_paused else "PAUSE",
                                  bg="#fb8c00" if self.is_paused else "#333333")

    def reset_app(self):
        self.is_running = False
        self.is_paused = False
        self.is_break = False
        self.unblock_everything()
        self.btn47.config(state="normal", relief="raised", bg="#2e7d32")
        self.btn120.config(state="normal", relief="raised", bg="#1565c0")
        self.pause_btn.config(text="PAUSE", state="disabled", bg="#333333")
        self.reset_btn.config(state="disabled")
        self.timer_label.config(text="00:00", fg="#ff9800")
        self.auto_start_seconds = 20
        self.auto_label.config(text="System Standby...", fg="#aaaaaa")

    def run_timer(self):
        while self.is_running:
            if not self.is_paused:
                if self.remaining_time > 0:
                    self.remaining_time -= 1
                    mins, secs = divmod(self.remaining_time, 60)
                    self.root.after(0, self.timer_label.config, {"text": f"{mins:02d}:{secs:02d}"})
                else:
                    # Transition Logic
                    if not self.is_break:
                        # Work ended -> Start Break
                        self.start_break()
                    else:
                        # Break ended -> Back to Work (The Loop)
                        self.end_break_and_restart_focus()
            
            time.sleep(1)

    def start_break(self):
        self.is_break = True
        self.remaining_time = self.break_duration
        self.root.after(0, self.timer_label.config, {"fg": "#ff1744"})
        self.root.after(0, self.pause_btn.config, {"state": "disabled"})
        self.root.after(0, self.show_blocker_overlay)
        self.block_inputs()

    def end_break_and_restart_focus(self):
        self.is_break = False
        self.remaining_time = self.work_duration
        # Update UI back to "Work" state
        self.root.after(0, self.timer_label.config, {"fg": "#ff9800"})
        self.root.after(0, self.pause_btn.config, {"state": "normal"})
        self.unblock_everything()

    def show_blocker_overlay(self):
        self.overlay = tk.Toplevel(self.root)
        self.overlay.attributes("-fullscreen", True)
        self.overlay.attributes("-topmost", True)
        self.overlay.configure(bg="black")
        
        funny_texts = [
            "STEP AWAY FROM THE BOX.",
            "YOUR KEYBOARD IS NOW A PAPERWEIGHT.",
            "BRAIN REBOOT IN PROGRESS...",
            "THE COMPUTER MISSES YOU, BUT I DON'T CARE.",
            "GO STARE AT A TREE OR SOMETHING.",
            "ACCESS DENIED. REASON: YOU ARE TIRED."
        ]
        
        msg = tk.Label(self.overlay, text=random.choice(funny_texts), 
                      fg="white", bg="black", font=("Verdana", 28, "bold"))
        msg.pack(expand=True)
        
        self.update_overlay_timer(msg)

    def update_overlay_timer(self, label_widget):
        if self.is_break and self.is_running:
            mins, secs = divmod(self.remaining_time, 60)
            label_widget.config(text=f"MANDATORY BREAK\n\n{mins:02d}:{secs:02d}")
            self.root.after(1000, lambda: self.update_overlay_timer(label_widget))
        else:
            if hasattr(self, 'overlay'):
                try:
                    self.overlay.destroy()
                except:
                    pass

    def block_inputs(self):
        self.mouse_listener = mouse.Listener(suppress=True)
        self.kb_listener = keyboard.Listener(suppress=True)
        self.mouse_listener.start()
        self.kb_listener.start()
        threading.Thread(target=self.kill_task_manager, daemon=True).start()

    def unblock_everything(self):
        if self.mouse_listener: 
            self.mouse_listener.stop()
            self.mouse_listener = None
        if self.kb_listener: 
            self.kb_listener.stop()
            self.kb_listener = None
        if hasattr(self, 'overlay'): 
            try:
                self.overlay.destroy()
            except:
                pass

    def kill_task_manager(self):
        while self.is_break and self.is_running:
            for proc in psutil.process_iter(['name']):
                try:
                    if proc.info['name'] == "Taskmgr.exe":
                        proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            time.sleep(0.5)

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = HardcoreTimer()
    app.run()
