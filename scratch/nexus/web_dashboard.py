import streamlit as st
import os
import sys
import psutil
import pandas as pd
import sqlite3
import time

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from config.settings import Config
from src.model_scanner import ModelScanner

st.set_page_config(page_title="Nexus Control Deck", page_icon="🦀", layout="wide")

# --- Sidebar ---
st.sidebar.title("🦀 Nexus Admin")
mode = st.sidebar.radio("Mode", ["Dashboard", "Settings", "Agents", "Logs"])

DB_PATH = Config.DB_PATH

# --- Functions ---
def get_system_metrics():
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    return cpu, ram

def load_logs():
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query("SELECT date, sender_name, message_text FROM messages ORDER BY date DESC LIMIT 50", conn)
        conn.close()
        return df
    except:
        return pd.DataFrame()

# --- Pages ---

if mode == "Dashboard":
    st.header("⚡️ Live Status")
    
    col1, col2, col3 = st.columns(3)
    cpu, ram = get_system_metrics()
    
    col1.metric("CPU Load", f"{cpu}%")
    col2.metric("Memory Usage", f"{ram}%")
    col3.metric("Active Brain", "Gemini 2.0 Flash (Cloud)") # Placeholder, need actual state

    st.subheader("📝 Recent Activity")
    logs = load_logs()
    if not logs.empty:
        st.dataframe(logs, use_container_width=True)
    else:
        st.info("No logs found yet.")

elif mode == "Settings":
    st.header("⚙️ Configuration")
    
    st.subheader("Model Selection")
    
    scanner = ModelScanner()
    local_models = scanner.scan_models()
    
    model_options = ["google/gemini-2.0-flash-exp", "google/gemini-1.5-pro-latest"] + [m['id'] for m in local_models]
    
    current = st.selectbox("Active Brain Model", model_options)
    
    if st.button("Save Configuration"):
        # Logic to update .env or DB would go here
        st.success(f"Updated preference to {current}")

elif mode == "Logs":
    st.header("📜 System Logs")
    with open("nexus.log", "r") as f:
        st.text_area("Console Output", f.read(), height=400)
