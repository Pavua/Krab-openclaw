
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
import threading
import time
import subprocess
import queue
import os
import sys
from pathlib import Path
from pynput import keyboard
import pyperclip
from core.engine import AudioEngine

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Krab Ear 4.0 Standalone")
        self.root.geometry("480x700")
        self.engine = AudioEngine()
        
        # State
        self.is_listening = False
        self.is_on_top = tk.BooleanVar(value=True)
        self.use_max_quality = tk.BooleanVar(value=True) # User requested Default High Quality
        self.auto_paste = tk.BooleanVar(value=True)
        self.toggle_mode = tk.BooleanVar(value=True)
        
        # State
        self.is_recording = False
        self.audio_queue = None
        
        self._setup_ui()
        self.load_settings() # Load saved prefs
        self._start_threads()
        
        # Apply On Top & Force Focus (Initial only)
        # self.root.lift() 
        # self.root.attributes('-topmost', True)
        self.root.after(1000, lambda: self.root.attributes('-topmost', self.is_on_top.get()))
        # self.root.focus_force() # Don't steal focus on restart if background
        
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_ui(self):
        # Styles
        style = ttk.Style()
        style.configure("Rec.TButton", font=("Helvetica", 14, "bold"), foreground="red")
        
        # 1. Header (Settings)
        header = ttk.Frame(self.root, padding=10)
        header.pack(fill='x')
        
        ttk.Checkbutton(header, text="📌 On Top", variable=self.is_on_top, command=self.toggle_top).pack(side='left')
        
        self.auto_start = tk.BooleanVar(value=self.check_autostart())
        ttk.Checkbutton(header, text="🚀 Run at Login", variable=self.auto_start, command=self.toggle_autostart).pack(side='left', padx=10)
        
        ttk.Checkbutton(header, text="🧠 Max Quality", variable=self.use_max_quality, command=self.toggle_model).pack(side='right')
        
        # 2. Main Action Area
        action_frame = ttk.LabelFrame(self.root, text="Voice Control", padding=15)
        action_frame.pack(fill='x', padx=15, pady=5)
        
        self.btn_status = ttk.Label(action_frame, text="Hold [Right Option] to Speak\n(Or Click Here)", font=("Helvetica", 12), justify="center")
        self.btn_status.pack(pady=10)
        # Bind Mouse Click to Toggle Recording
        self.btn_status.bind("<Button-1>", lambda e: self.toggle_recording_click())
        
        progress_frame = ttk.Frame(action_frame)
        progress_frame.pack(fill='x', pady=5)
        self.progress = ttk.Progressbar(progress_frame, mode='indeterminate')
        
        ttk.Checkbutton(action_frame, text="✨ Auto-Paste", variable=self.auto_paste).pack(side='left', padx=5)
        self.toggle_mode = tk.BooleanVar(value=True)
        ttk.Checkbutton(action_frame, text="🔄 Toggle Mode (Press vs Hold)", variable=self.toggle_mode).pack(side='left', padx=5)
        
        self.enable_ai = tk.BooleanVar(value=False)
        ttk.Checkbutton(action_frame, text="🤖 AI Assistant", variable=self.enable_ai).pack(side='left', padx=5)

        # 3. File Import (Pseudo-Drag&Drop zone)
        dnd_frame = ttk.LabelFrame(self.root, text="File Transcription", padding=15)
        dnd_frame.pack(fill='x', padx=15, pady=5)
        
        ttk.Label(dnd_frame, text="Drop audio files here\n(or click to browse)", justify='center', foreground="gray").pack(pady=10)
        dnd_frame.bind("<Button-1>", self.browse_file)
        
        # 4. Transcript / Log
        log_frame = ttk.Frame(self.root, padding=10)
        log_frame.pack(fill='both', expand=True)
        
        self.text_area = scrolledtext.ScrolledText(log_frame, font=("Menlo", 12), height=10)
        self.text_area.pack(fill='both', expand=True)
        
        # 5. Footer
        footer = ttk.Frame(self.root, padding=10)
        footer.pack(fill='x')
        ttk.Button(footer, text="Copy Text", command=self.copy_text).pack(side='left')
        ttk.Button(footer, text="Clear", command=self.clear_text).pack(side='right')

    def _start_threads(self):
        # Global Hotkey
        t = threading.Thread(target=self.hotkey_listener, daemon=True)
        t.start()
        # print("⚠️ Hotkey listener DISABLED for debugging.")

    def check_autostart(self):
        plist_path = os.path.expanduser("~/Library/LaunchAgents/com.openclaw.krabear.plist")
        return os.path.exists(plist_path)

    def toggle_autostart(self):
        plist_path = os.path.expanduser("~/Library/LaunchAgents/com.openclaw.krabear.plist")
        if self.auto_start.get():
            # Create Plist
            python_path = os.path.abspath("./openclaw_official/nexus_bridge/venv/bin/python3")
            main_path = os.path.abspath("KrabEar/main.py")
            
            plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.openclaw.krabear</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{main_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>{os.path.abspath(".")}</string>
</dict>
</plist>"""
            try:
                with open(plist_path, "w") as f:
                    f.write(plist_content)
                subprocess.run(["launchctl", "load", plist_path])
                print("Auto-start enabled.")
            except Exception as e:
                print(f"Failed to enable autostart: {e}")
        else:
            # Remove Plist
            try:
                subprocess.run(["launchctl", "unload", plist_path])
                if os.path.exists(plist_path):
                    os.remove(plist_path)
                print("Auto-start disabled.")
            except Exception as e:
                print(f"Failed to disable autostart: {e}")

    def toggle_top(self):
        self.root.attributes('-topmost', self.is_on_top.get())

    def toggle_model(self):
        # Switch model in background
        threading.Thread(target=lambda: self.engine.set_model_quality(self.use_max_quality.get()), daemon=True).start()

    def toggle_recording_click(self):
        if self.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def hotkey_listener(self):
        def on_press(key):
            try:
                if key == keyboard.Key.alt_r:
                    if self.toggle_mode.get():
                        # Toggle Mode
                        if not self.is_recording:
                            self.root.after(0, self.start_recording)
                        else:
                            self.root.after(0, self.stop_recording)
                    else:
                        # Hold Mode (Start)
                        if not self.is_recording:
                            self.root.after(0, self.start_recording)
            except: pass

        def on_release(key):
            try:
                if key == keyboard.Key.alt_r:
                    if not self.toggle_mode.get():
                        # Hold Mode (Stop)
                        if self.is_recording:
                            self.root.after(0, self.stop_recording)
            except: pass

        with keyboard.Listener(on_press=on_press, on_release=on_release) as l:
            l.join() 

    def start_recording(self):
        print(f"DEBUG: start_recording called. State: {self.is_recording}")
        if self.is_recording: return
        self.is_recording = True
        
        # Ducking
        print("DEBUG: Ducking start")
        threading.Thread(target=lambda: self.duck_volume(lower=True), daemon=True).start()
        
        self.audio_queue = queue.Queue() 
        # self.text_area.delete("1.0", tk.END)  <-- REMOVED: Keep history
        self.append_log("--- New Recording ---", "sys") # Visual separator
        self.btn_status.config(text="🔴 Recording...", foreground="red")
        
        # Start Recording Thread
        print("DEBUG: Starting record thread")
        self.record_thread = threading.Thread(target=self._record_thread, daemon=True)
        self.record_thread.start()



    def stop_recording(self):
        print(f"DEBUG: stop_recording called. State: {self.is_recording}")
        if not self.is_recording: return
        self.is_recording = False # Break the loop immediately
        print("DEBUG: Flag set to False")
        
        self.btn_status.config(text="⏳ Transcribing...", foreground="orange")
        
        # 1. Restore Volume (Fire and forget)
        threading.Thread(target=lambda: self.duck_volume(lower=False), daemon=True).start()
        
        # 2. Force stream close REMOVED (Relies on is_recording flag naturally)
        # The _record_thread context manager will handle clean up.
        
        # 3. Trigger Processing (The record loops ends naturally)

    def _record_thread(self):
        import sounddevice as sd
        import numpy as np
        
        fs = 16000
        # Accumulate audio here
        audio_chunks = []
        
        try:
            with sd.InputStream(samplerate=fs, channels=1, dtype='float32') as stream:
                while self.is_recording:
                    # Read chunk (e.g. 0.1s)
                    data, overflow = stream.read(int(fs * 0.1))
                    if overflow:
                        print("Audio Overflow")
                    audio_chunks.append(data)
        except Exception as e:
            self.root.after(0, lambda: self.append_log(f"Audio Error: {e}", "sys"))
            
        # Finished recording loop
        if not audio_chunks:
            self.root.after(0, self.reset_ui)
            return

        # Process in THIS thread (since it's already a background thread)
        try:
            self.root.after(0, lambda: self.btn_status.config(text="⚙️ Processing..."))
            
            # Concat
            audio = np.concatenate(audio_chunks, axis=0)
            if len(audio.shape) > 1:
                audio = audio.flatten()
            
            # Duration Check
            duration = len(audio) / fs
            print(f"DEBUG: Audio Duration: {duration:.2f}s")
            
            # Transcribe
            text = self.engine.transcribe(audio)
            
            # Update UI
            self.root.after(0, lambda: self.handle_result(text))
            
        except Exception as e:
            msg = f"Transcribe Error: {e}"
            self.root.after(0, lambda: self.append_log(msg, "sys"))
            self.root.after(0, self.reset_ui)
            
        except Exception as e:
            msg = f"Transcribe Error: {e}"
            self.root.after(0, lambda: self.append_log(msg, "sys"))
            self.root.after(0, self.reset_ui)

    def duck_volume(self, lower=True):
        try:
            cmd = "true" if lower else "false"
            subprocess.run(["osascript", "-e", f"set volume output muted {cmd}"], capture_output=False)
        except: pass



    def handle_result(self, text):
        self.progress.stop()
        self.reset_ui()
        
        if not text: return
        
        # Store for copy button
        self.last_transcribed_text = text
        
        # Append User Text
        self.append_log(f"🗣️ {text}", "user")
        
        # Paste
        if self.auto_paste.get():
            self.do_paste(text)
            
        # Brain (in background)
        if self.enable_ai.get():
            threading.Thread(target=lambda: self.get_brain_response(text), daemon=True).start()

    def get_brain_response(self, text):
        resp = self.engine.ask_brain(text)
        if resp:
            self.root.after(0, lambda: self.append_log(f"🤖 {resp}", "ai"))

    def do_paste(self, text):
        pyperclip.copy(text)
        time.sleep(0.1)
        
        try:
            # 1. Hide Window completely
            self.root.withdraw()
            self.root.update() 
            time.sleep(0.8) # Wait for focus to transfer
            
            # 2. Use AppleScript (System Events) -> More robust on macOS than pynput
            try:
                # Debug Clipboard
                clip_content = pyperclip.paste()
                if clip_content == text:
                    print("DEBUG: Clipboard verified.")
                else:
                    print(f"DEBUG: Clipboard Mismatch! Expected: {text[:20]}... Got: {clip_content[:20]}...")
                    pyperclip.copy(text) # Retry copy
                
                # AppleScript to send Cmd+V into the Void (Frontmost App)
                script = '''
                delay 0.3
                tell application "System Events"
                    set frontApp to name of first application process whose frontmost is true
                    tell process frontApp
                        -- Use Key Code 9 (Physical 'V' key) instead of "v" char
                        -- This prevents issues with Russian/Non-English layouts where "v" might match "м"
                        key code 9 using command down
                    end tell
                    return frontApp
                end tell
                '''
                res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=True)
                front_app = res.stdout.strip()
                
                print(f"DEBUG: AppleScript Paste Sent to: {front_app}")
                self.root.after(0, lambda: self.append_log(f"📋 Paste Sent to: {front_app}", "sys"))
                
            except Exception as e:
                 print(f"AppleScript Paste Error: {e}")
                 self.root.after(0, lambda: self.append_log(f"⚠️ Paste Err: {e}", "sys"))
                 self.root.after(0, lambda: self.append_log(f"⚠️ Paste Err: {e}", "sys"))

            time.sleep(0.2)
            
            # 3. Restore Window (but don't steal focus)
            self.root.deiconify()
            self.root.attributes('-topmost', self.is_on_top.get())
            
            # 4. Give focus BACK to the target app explicitly (Robust Method)
            if 'front_app' in locals() and front_app:
                # Use System Events to activate the PROCESS (not Application) to avoid "Can't get application" errors
                script_refocus = f'''
                tell application "System Events"
                    set frontmost of process "{front_app}" to true
                end tell
                '''
                subprocess.run(["osascript", "-e", script_refocus], check=False)
                
        except Exception as e:
            print(f"Paste General Error: {e}")
            self.root.deiconify()

    def reset_ui(self):
        status_text = "Press [Right Option] to Start" if self.toggle_mode.get() else "Hold [Right Option] to Speak"
        self.root.after(0, lambda: self.btn_status.config(text=status_text, foreground="black"))

    def load_settings(self):
        try:
            import json
            if os.path.exists("krab_settings.json"):
                with open("krab_settings.json", "r") as f:
                    data = json.load(f)
                    self.is_on_top.set(data.get("is_on_top", True))
                    self.use_max_quality.set(data.get("use_max_quality", True))
                    self.auto_paste.set(data.get("auto_paste", True))
                    self.toggle_mode.set(data.get("toggle_mode", True))
                    self.enable_ai.set(data.get("enable_ai", False))
        except Exception as e:
            print(f"Failed to load settings: {e}")

    def save_settings(self):
        try:
            import json
            data = {
                "is_on_top": self.is_on_top.get(),
                "use_max_quality": self.use_max_quality.get(),
                "auto_paste": self.auto_paste.get(),
                "toggle_mode": self.toggle_mode.get(),
                "enable_ai": self.enable_ai.get()
            }
            with open("krab_settings.json", "w") as f:
                json.dump(data, f)
        except Exception as e:
            print(f"Failed to save settings: {e}")
            
    def _on_close(self):
        self.save_settings()
        self.root.destroy()
        sys.exit(0)

    def append_log(self, text, tag):
        self.text_area.insert(tk.END, text + "\n\n")
        self.text_area.see(tk.END)

    def copy_text(self):
        # Copy only last text if available, else all
        if hasattr(self, 'last_transcribed_text') and self.last_transcribed_text:
            pyperclip.copy(self.last_transcribed_text)
            self.btn_status.config(text="✅ Copied Last!", foreground="green")
        else:
            pyperclip.copy(self.text_area.get("1.0", tk.END))
            self.btn_status.config(text="✅ Copied All!", foreground="green")

    def clear_text(self):
        self.text_area.delete("1.0", tk.END)

    def browse_file(self, event=None):
        filename = filedialog.askopenfilename(filetypes=[("Audio", "*.wav *.mp3 *.m4a")])
        if filename:
            self.append_log(f"📂 File: {filename}", "sys")
            self.btn_status.config(text="Importing & Transcribing...", foreground="blue")
            self.progress.start()
            # Process in thread
            threading.Thread(target=self._process_file, args=(filename,), daemon=True).start()

    def _process_file(self, filename):
        # MLX expects file path directly usually, or we load it
        # mlx_whisper can handle path
        try:
            text = self.engine.transcribe(filename) # Pass path directly
            self.root.after(0, lambda: self.handle_result(text))
        except Exception as e:
            self.root.after(0, lambda: self.append_log(f"Error: {e}", "error"))
            self.root.after(0, self.progress.stop)
            self.root.after(0, self.reset_ui)
