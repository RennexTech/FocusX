import tkinter as tk
from tkinter import messagebox
import threading
import time
import os
import psutil
from pynput import mouse, keyboard
import random
import math
import datetime
import socket
import struct

# --- Hardcore Single-File Focus App (Looping Edition with Analogue Clock & Sleep) ---

class HardcoreTimer:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('Deep Work Protocol - FocusXv14')
        self.root.geometry('850x550')
        self.root.configure(bg='#121212') 
        
        # Center the window
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = (screen_width // 2) - (425)
        y = (screen_height // 2) - (275)
        self.root.geometry(f'850x550+{x}+{y}')

        # State Variables
        self.remaining_time = 0
        self.work_duration = 37 * 60  
        self.break_duration = 2 * 60 
        self.is_running = False
        self.is_paused = False
        self.is_break = False
        self.is_sleeping = False
        self.time_offset = 0
        self.auto_start_seconds = 7
        self.current_thread = None
        
        # Input Blocker Listeners
        self.mouse_listener = None
        self.kb_listener = None
        
        # Start NTP Sync
        threading.Thread(target=self.sync_ntp, daemon=True).start()

        self.setup_ui()
        self.start_autostart_countdown()
        self.update_clock()
        self.check_sleep_schedule()

    def sync_ntp(self):
        """Fetch network time from pool.ntp.org"""
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            client.settimeout(5.0)
            client.sendto(b'\x1b' + 47 * b'\0', ('pool.ntp.org', 123))
            data, _ = client.recvfrom(1024)
            ntp_time = struct.unpack('!12I', data)[10] - 2208988800
            self.time_offset = ntp_time - time.time()
        except:
            self.time_offset = 0  # Fallback to local system time

    def get_current_time(self):
        return time.time() + self.time_offset

    def setup_ui(self):
        # Split layout into left (Pomodoro) and right (Analogue Clock)
        self.left_frame = tk.Frame(self.root, bg='#121212')
        self.left_frame.pack(side='left', fill='both', expand=True, padx=20)
        
        self.right_frame = tk.Frame(self.root, bg='#121212')
        self.right_frame.pack(side='right', fill='both', expand=True, padx=20)

        # --- LEFT FRAME: POMODORO & CONTROLS ---
        self.header = tk.Label(self.left_frame, text='SYSTEM INITIALIZED', 
                                font=('Segoe UI', 16, 'bold'), fg='#00ff00', bg='#121212')
        self.header.pack(pady=(30, 5))

        self.auto_label = tk.Label(self.left_frame, text=f'Launching in: {self.auto_start_seconds}s', 
                                    font=('Verdana', 11, 'italic'), fg='#aaaaaa', bg='#121212')
        self.auto_label.pack()

        # Configuration Frame
        config_frame = tk.Frame(self.left_frame, bg='#121212')
        config_frame.pack(pady=15)
        
        tk.Label(config_frame, text="Work (min):", bg='#121212', fg='white').grid(row=0, column=0, padx=5, pady=2)
        self.work_entry = tk.Entry(config_frame, width=5, bg='#333333', fg='white', insertbackground='white')
        self.work_entry.insert(0, "37")
        self.work_entry.grid(row=0, column=1, padx=5, pady=2)

        tk.Label(config_frame, text="Break (min):", bg='#121212', fg='white').grid(row=0, column=2, padx=5, pady=2)
        self.break_entry = tk.Entry(config_frame, width=5, bg='#333333', fg='white', insertbackground='white')
        self.break_entry.insert(0, "2")
        self.break_entry.grid(row=0, column=3, padx=5, pady=2)

        tk.Label(config_frame, text="Sleep (HH:MM):", bg='#121212', fg='white').grid(row=1, column=0, padx=5, pady=2)
        self.sleep_entry = tk.Entry(config_frame, width=5, bg='#333333', fg='white', insertbackground='white')
        self.sleep_entry.insert(0, "23:00")
        self.sleep_entry.grid(row=1, column=1, padx=5, pady=2)

        tk.Label(config_frame, text="Wake (HH:MM):", bg='#121212', fg='white').grid(row=1, column=2, padx=5, pady=2)
        self.wake_entry = tk.Entry(config_frame, width=5, bg='#333333', fg='white', insertbackground='white')
        self.wake_entry.insert(0, "05:00")
        self.wake_entry.grid(row=1, column=3, padx=5, pady=2)

        self.start_btn = tk.Button(self.left_frame, text='ENGAGE PROTOCOL', width=20, height=2, 
                                command=self.apply_and_start,
                                bg='#2e7d32', fg='white', font=('Segoe UI', 10, 'bold'),
                                relief='raised', bd=6, activebackground='#1b5e20', cursor='hand2')
        self.start_btn.pack(pady=15)

        self.timer_label = tk.Label(self.left_frame, text='00:00', 
                                    font=('Verdana', 50, 'bold'), fg='#ff9800', bg='#121212')
        self.timer_label.pack(pady=10)

        # Control Buttons
        self.ctrl_frame = tk.Frame(self.left_frame, bg='#121212')
        self.ctrl_frame.pack(pady=15)

        self.pause_btn = tk.Button(self.ctrl_frame, text='PAUSE', width=12, command=self.toggle_pause, 
                                    state='disabled', bg='#333333', fg='#ffffff', relief='flat', 
                                    font=('Segoe UI', 9, 'bold'))
        self.pause_btn.pack(side='left', padx=8)

        self.reset_btn = tk.Button(self.ctrl_frame, text='RESET', width=12, command=self.reset_app, 
                                    state='disabled', bg='#c62828', fg='white', relief='flat',
                                    font=('Segoe UI', 9, 'bold'))
        self.reset_btn.pack(side='left', padx=8)

        # --- RIGHT FRAME: ANALOGUE CLOCK ---
        self.canvas = tk.Canvas(self.right_frame, width=350, height=350, bg='#121212', highlightthickness=0)
        self.canvas.pack(expand=True)
        self.draw_clock_face()

    def draw_clock_face(self):
        self.canvas.create_oval(10, 10, 340, 340, outline='#00e5ff', width=4)
        for i in range(12):
            angle = i * math.pi/6 - math.pi/2
            x1 = 175 + 140 * math.cos(angle)
            y1 = 175 + 140 * math.sin(angle)
            x2 = 175 + 155 * math.cos(angle)
            y2 = 175 + 155 * math.sin(angle)
            self.canvas.create_line(x1, y1, x2, y2, fill='white', width=3)

    def update_clock(self):
        self.canvas.delete("hands")
        now = datetime.datetime.fromtimestamp(self.get_current_time())
        
        # Second hand
        sec_angle = now.second * math.pi/30 - math.pi/2
        s_x = 175 + 130 * math.cos(sec_angle)
        s_y = 175 + 130 * math.sin(sec_angle)
        self.canvas.create_line(175, 175, s_x, s_y, fill='red', width=2, tags="hands")

        # Minute hand
        min_angle = now.minute * math.pi/30 - math.pi/2
        m_x = 175 + 110 * math.cos(min_angle)
        m_y = 175 + 110 * math.sin(min_angle)
        self.canvas.create_line(175, 175, m_x, m_y, fill='#00e5ff', width=4, tags="hands")

        # Hour hand
        hour_angle = (now.hour % 12 + now.minute / 60) * math.pi/6 - math.pi/2
        h_x = 175 + 70 * math.cos(hour_angle)
        h_y = 175 + 70 * math.sin(hour_angle)
        self.canvas.create_line(175, 175, h_x, h_y, fill='white', width=6, tags="hands")
        
        self.root.after(1000, self.update_clock)

    def check_sleep_schedule(self):
        now = datetime.datetime.fromtimestamp(self.get_current_time())
        current_time = now.time()
        
        try:
            sh, sm = map(int, self.sleep_entry.get().split(':'))
            wh, wm = map(int, self.wake_entry.get().split(':'))
            sleep_start = datetime.time(sh, sm)
            wake_end = datetime.time(wh, wm)
            
            if sleep_start <= wake_end:
                in_sleep_window = sleep_start <= current_time <= wake_end
            else:
                in_sleep_window = current_time >= sleep_start or current_time <= wake_end
                
            if in_sleep_window and not self.is_sleeping:
                self.start_sleep()
            elif not in_sleep_window and self.is_sleeping:
                self.end_sleep()
                
        except ValueError:
            pass # Ignore invalid time formats

        self.root.after(5000, self.check_sleep_schedule)

    def start_sleep(self):
        self.is_sleeping = True
        self.is_paused = True
        self.show_blocker_overlay("SYSTEM SLEEPING\nRETURN AT WAKE TIME", True)
        self.block_inputs()

    def end_sleep(self):
        self.is_sleeping = False
        self.unblock_everything()
        if self.is_running:
            self.is_paused = False # Auto-resume pomodoro

    def start_autostart_countdown(self):
        if self.auto_start_seconds > 0 and not self.is_running:
            self.auto_start_seconds -= 1
            self.auto_label.config(text=f'Launching in: {self.auto_start_seconds}s')
            self.root.after(1000, self.start_autostart_countdown)
        elif self.auto_start_seconds == 0 and not self.is_running:
            self.apply_and_start()

    def apply_and_start(self):
        try:
            w = int(self.work_entry.get())
            b = int(self.break_entry.get())
            self.select_mode(w, b)
        except ValueError:
            messagebox.showerror("Error", "Invalid time values.")

    def select_mode(self, work, rest):
        self.work_duration = work * 60
        self.break_duration = rest * 60
        self.remaining_time = self.work_duration
        self.is_running = True
        self.is_break = False
        
        self.auto_label.config(text='PROTOCOL ENGAGED', fg='#00e5ff')
        self.start_btn.config(state='disabled', relief='sunken', bg='#1b5e20')
        self.pause_btn.config(state='normal')
        self.reset_btn.config(state='normal')
        
        if self.current_thread is None or not self.current_thread.is_alive():
            self.current_thread = threading.Thread(target=self.run_timer, daemon=True)
            self.current_thread.start()

    def toggle_pause(self):
        if not self.is_break and not self.is_sleeping:
            self.is_paused = not self.is_paused
            self.pause_btn.config(text='CONTINUE' if self.is_paused else 'PAUSE',
                                  bg='#fb8c00' if self.is_paused else '#333333')

    def reset_app(self):
        self.is_running = False
        self.is_paused = False
        self.is_break = False
        self.unblock_everything()
        self.start_btn.config(state='normal', relief='raised', bg='#2e7d32')
        self.pause_btn.config(text='PAUSE', state='disabled', bg='#333333')
        self.reset_btn.config(state='disabled')
        self.timer_label.config(text='00:00', fg='#ff9800')
        self.auto_start_seconds = 7
        self.auto_label.config(text='System Standby...', fg='#aaaaaa')

    def run_timer(self):
        while self.is_running:
            if not self.is_paused:
                if self.remaining_time > 0:
                    self.remaining_time -= 1
                    mins, secs = divmod(self.remaining_time, 60)
                    self.root.after(0, self.timer_label.config, {'text': f'{mins:02d}:{secs:02d}'})
                else:
                    if not self.is_break:
                        self.start_break()
                    else:
                        self.end_break_and_restart_focus()
            
            time.sleep(1)

    def start_break(self):
        self.is_break = True
        self.remaining_time = self.break_duration
        self.root.after(0, self.timer_label.config, {'fg': '#ff1744'})
        self.root.after(0, self.pause_btn.config, {'state': 'disabled'})
        self.show_blocker_overlay("MANDATORY BREAK", False)
        self.block_inputs()

    def end_break_and_restart_focus(self):
        self.is_break = False
        self.remaining_time = self.work_duration
        self.root.after(0, self.timer_label.config, {'fg': '#ff9800'})
        self.root.after(0, self.pause_btn.config, {'state': 'normal'})
        self.unblock_everything()

    def show_blocker_overlay(self, title_text, is_sleep_mode):
        self.overlay = tk.Toplevel(self.root)
        self.overlay.attributes('-fullscreen', True)
        self.overlay.attributes('-topmost', True)
        self.overlay.configure(bg='black')
        
        msg = tk.Label(self.overlay, text=title_text, fg='white', bg='black', font=('Verdana', 36, 'bold'))
        msg.pack(expand=True, pady=40)
        
        self.overlay_timer = tk.Label(self.overlay, text="", fg='#ff1744', bg='black', font=('Verdana', 24, 'bold'))
        if not is_sleep_mode:
            self.overlay_timer.pack()
            self.update_overlay_timer(self.overlay_timer)

    def update_overlay_timer(self, label_widget):
        if self.is_break and self.is_running and not self.is_sleeping:
            mins, secs = divmod(self.remaining_time, 60)
            label_widget.config(text=f'{mins:02d}:{secs:02d}')
            self.root.after(1000, lambda: self.update_overlay_timer(label_widget))

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
        while (self.is_break and self.is_running) or self.is_sleeping:
            for proc in psutil.process_iter(['name']):
                try:
                    if proc.info['name'] == 'Taskmgr.exe':
                        proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            time.sleep(0.5)

    def run(self):
        self.root.mainloop()

if __name__ == '__main__':
    app = HardcoreTimer()
    app.run()