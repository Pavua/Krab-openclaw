
import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import subprocess
import os
import sys
import time
from pynput import keyboard
import pyperclip

# Ensure we can import ear
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from ear import Ear

class EarUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Krab Ear 3.0 Pro")
        self.root.geometry("420x600")
        
        # Configure Grid
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1) # History expands

        # --- Header & Settings ---
        top_frame = tk.Frame(root)
        top_frame.grid(row=0, column=0, sticky='ew', padx=10, pady=5)
        
        self.status_label = tk.Label(top_frame, text="Initializing...", font=("Helvetica", 12))
        self.status_label.pack(side='left')
        
        self.always_on_top = tk.BooleanVar(value=True)
        tk.Checkbutton(top_frame, text="📌 On Top", variable=self.always_on_top, command=self.toggle_top).pack(side='right')

        # --- Main Control ---
        ctrl_frame = tk.Frame(root)
        ctrl_frame.grid(row=1, column=0, sticky='ew', padx=20, pady=10)
        
        self.btn = tk.Button(ctrl_frame, text="🎙️ Hold [Right Option]", font=("Helvetica", 15, "bold"), bg="#f0f0f0", height=2)
        self.btn.pack(fill='x')
        self.btn.bind('<ButtonPress-1>', self.start_listen_manual)
        self.btn.bind('<ButtonRelease-1>', self.stop_listen_manual)

        # Options
        opt_frame = tk.Frame(root)
        opt_frame.grid(row=2, column=0, sticky='ew', padx=20)
        
        self.paste_mode = tk.BooleanVar(value=True)
        tk.Checkbutton(opt_frame, text="✨ Auto-Paste into App", variable=self.paste_mode).pack(side='left')

        # --- History ---
        self.history_box = scrolledtext.ScrolledText(root, height=10, font=("Menlo", 12), wrap='word', borderwidth=0)
        self.history_box.grid(row=3, column=0, sticky='nsew', padx=20, pady=5)
        # Add visual separator
        ttk.Separator(root, orient='horizontal').grid(row=4, column=0, sticky='ew', padx=10)

        # --- Footer Actions ---
        btn_frame = tk.Frame(root)
        btn_frame.grid(row=5, column=0, sticky='ew', padx=20, pady=10)
        
        self.copy_btn = tk.Button(btn_frame, text="📋 Copy", command=self.copy_last, width=10)
        self.copy_btn.pack(side='left', padx=5)
        
        # Debug Button
        tk.Button(btn_frame, text="🐛 Test Paste", command=self.test_paste).pack(side='left', padx=5)
        
        tk.Button(btn_frame, text="🗑️ Clear", command=self.clear_history).pack(side='right', padx=5)

        # State
        self.is_listening = False
        self.ear = None
        self.last_text = ""
        self.toggle_top() # Apply initial state
        
        # Start Backend
        self.thread = threading.Thread(target=self.load_ear)
        self.thread.daemon = True
        self.thread.start()

        # Global Hotkey Listener
        self.hotkey_thread = threading.Thread(target=self.start_global_listener)
        self.hotkey_thread.daemon = True
        self.hotkey_thread.start()

    def toggle_top(self):
        self.root.attributes('-topmost', self.always_on_top.get())

    def start_global_listener(self):
        # Using Right Option (Alt) as PTT
        def on_press(key):
            if key == keyboard.Key.alt_r:
                self.start_listen_manual(None)

        def on_release(key):
            if key == keyboard.Key.alt_r:
                self.stop_listen_manual(None)

        with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
            listener.join()

    def load_ear(self):
        try:
            self.ear = Ear()
            self.root.after(0, lambda: self.status_label.config(text="Ready"))
            self.root.after(0, lambda: self.btn.config(state="normal"))
        except Exception as e:
            self.root.after(0, lambda: self.status_label.config(text="Brain Disconnected", fg="orange"))
            print(f"Ear Init Error: {e}")

    def start_listen_manual(self, event):
        if not self.ear or self.is_listening: return
        self.is_listening = True
        
        # UI Feedback (Thread safe)
        self.btn.config(text="🎙️ Listening...", bg="#ffcccc", fg="#cc0000")
        self.status_label.config(text="Listening...", fg="red")
        self.set_volume(10)
        
        # IMPORTANT: Do NOT force focus to the window here.
        # This allows the user to keep typing/focus in their target app.
        
        # Start recording logic in thread
        self.listen_thread = threading.Thread(target=self.record_and_process)
        self.listen_thread.start()

    def stop_listen_manual(self, event):
        if self.is_listening:
            self.is_listening = False
            self.btn.config(text="⏳ Processing...", bg="#e6e6e6", fg="black")

    def set_volume(self, level):
        try:
            subprocess.run(["osascript", "-e", f"set volume output volume {level}"], capture_output=True)
        except: pass

    def test_paste(self):
        print("Testing Paste...")
        self.paste_text("Test Paste Success! 🦀")

    def paste_text(self, text):
        # 1. Copy to clipboard
        pyperclip.copy(text)
        # 2. Simulate Cmd+V with delay to ensure focus is ready
        time.sleep(0.3) # Increased delay slightly
        
        # We assume the user's focus is ALREADY on the target window since we didn't steal it.
        # Method 1: AppleScript (Reliable on macOS)
        # Using key code 9 (V) + 55 (Cmd)
        script = 'tell application "System Events" to key code 9 using {command down}'
        try:
            subprocess.run(["osascript", "-e", script], capture_output=True)
            print("Paste command sent.")
        except Exception as e:
            print(f"Paste error: {e}")

    def append_history(self, text, is_user=True):
        self.history_box.config(state='normal')
        prefix = "🗣️ " if is_user else "🤖 "
        tag = "user" if is_user else "ai"
        
        self.history_box.tag_config("user", foreground="#0000FF") # Blue
        self.history_box.tag_config("ai", foreground="#008800")   # Green
        
        self.history_box.insert(tk.END, f"{prefix}{text}\n\n", tag)
        self.history_box.see(tk.END)
        self.history_box.config(state='disabled')
        if is_user: self.last_text = text

    def copy_last(self):
        if self.last_text:
            pyperclip.copy(self.last_text)
            self.status_label.config(text="Copied!")
            self.root.after(1500, lambda: self.status_label.config(text="Ready"))

    def clear_history(self):
        self.history_box.config(state='normal')
        self.history_box.delete("1.0", tk.END)
        self.history_box.config(state='disabled')

    def record_and_process(self):
        import sounddevice as sd
        import numpy as np
        
        fs = 16000
        audio_chunks = []
        
        def callback(indata, frames, time, status):
            if self.is_listening:
                audio_chunks.append(indata.copy())
            else:
                raise sd.CallbackStop()

        try:
            with sd.InputStream(samplerate=fs, channels=1, callback=callback):
                while self.is_listening:
                    sd.sleep(50)
        except Exception as e:
            print(f"Mic Error: {e}")
        
        self.set_volume(50)
        
        if not audio_chunks:
            self.reset_ui()
            return

        audio = np.concatenate(audio_chunks, axis=0)
        
        self.root.after(0, lambda: self.status_label.config(text="Transcribing...", fg="blue"))
        
        try:
            audio = audio.flatten().astype(np.float32)
            # Use the new MLX method
            text = self.ear.transcribe_audio(audio)
            print(f"Heard: {text}")
            
            if text:
                self.root.after(0, lambda: self.append_history(text, True))
                
                # Auto-Paste
                if self.paste_mode.get():
                     self.root.after(0, lambda: self.paste_text(text))

                self.root.after(0, lambda: self.status_label.config(text="Thinking...", fg="purple"))
                
                # Check brain connection first?
                try:
                    response = self.ear.ask_brain(text)
                except:
                    response = "⚠️ Brain Offline"
                
                self.root.after(0, lambda: self.append_history(response, False))
                
            else:
                print("Silence.")
                self.root.after(0, lambda: self.status_label.config(text="Empty Audio", fg="orange"))
                
        except Exception as e:
            print(f"Error: {e}")
            self.root.after(0, lambda: self.status_label.config(text="Error", fg="red"))
        
        self.reset_ui()

    def reset_ui(self):
        self.root.after(0, lambda: self.status_label.config(text="Ready", fg="black"))
        self.root.after(0, lambda: self.btn.config(text="🎙️ Hold [Right Option]", bg="#f0f0f0", fg="black", state="normal"))
        self.is_listening = False

if __name__ == "__main__":
    root = tk.Tk()
    app = EarUI(root)
    text_color = "black"
    # Basic system theme detection could go here
    root.mainloop()
