
import sys
import os
import logging
from ui.window import App
import tkinter as tk

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [KRAB] - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("krab_ear.log")
    ]
)
logger = logging.getLogger("Main")

def main():
    logger.info("🦀 Krab Ear Standalone Launching...")
    
    root = tk.Tk()
    # Basic theme setup could go here
    app = App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
