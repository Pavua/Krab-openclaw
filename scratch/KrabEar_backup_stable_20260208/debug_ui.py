import tkinter as tk
import sys

print("Python executable:", sys.executable)
print("Starting Minimal UI...")

root = tk.Tk()
root.title("Test Window")
root.geometry("200x200")
label = tk.Label(root, text="Can you see me?")
label.pack(expand=True)

print("Entering mainloop...")
root.lift()
root.attributes('-topmost',True)
root.after_idle(root.attributes,'-topmost',False)
root.mainloop()
