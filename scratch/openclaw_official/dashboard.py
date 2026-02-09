
import streamlit as st
import sqlite3
import pandas as pd
import time
import os
import requests

# Page Config
st.set_page_config(
    page_title="Nexus Control Center",
    page_icon="🦀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS
st.markdown("""
<style>
    .stApp {
        background-color: #0E1117;
        color: #FAFAFA;
    }
    .stDataFrame {
        border: 1px solid #262730;
    }
</style>
""", unsafe_allow_html=True)

# Database
DB_PATH = "nexus_history.db"

def get_connection():
    return sqlite3.connect(DB_PATH)

def load_data(conn):
    try:
        # Load last 1000 messages descending
        query = "SELECT * FROM messages ORDER BY id DESC LIMIT 1000"
        df = pd.read_sql_query(query, conn)
        
        if not df.empty and 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], format='mixed', utc=True)
            df['date'] = df['date'].dt.tz_convert('Europe/Paris') # User is +1
        return df
    except Exception as e:
        return pd.DataFrame()

def render_settings_page(conn):
    st.header("⚙️ Settings")
    c = conn.cursor()
    
    # 1. Model Selection
    st.subheader("🧠 Brain Model")
    
    # Get current
    current_model = "google/gemini-2.5-flash-preview-09-2025"
    try:
        c.execute("SELECT value FROM settings WHERE key='current_model'")
        row = c.fetchone()
        if row: current_model = row[0]
    except Exception as e:
        # Table might not exist yet if bot hasn't started or created it
        pass

    st.write(f"**Current Active Model ID:** `{current_model}`")

    # UI Options
    model_options = {
        "Gemini 2.0 Flash (Fast)": "google/gemini-2.0-flash-exp",
        "Gemini 1.5 Pro (Deep)": "google/gemini-1.5-pro-latest",
        "Local LM Studio": "local"
    }
    
    # Reverse lookup for selectbox
    current_label = next((k for k, v in model_options.items() if v == current_model), "Custom/Unknown")
    
    selected_label = st.selectbox("Select Active Model", list(model_options.keys()), index=list(model_options.keys()).index(current_label) if current_label in model_options else 0)
    new_model_id = model_options[selected_label]
    
    if new_model_id != current_model:
        if st.button("💾 Save Model Change"):
            try:
                # Ensure table exists (in case dashboard runs before bot)
                c.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
                c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('current_model', ?)", (new_model_id,))
                conn.commit()
                st.success(f"Updated! The Userbot will pick this up immediately (or on next restart).")
                st.info("Tip: If the bot is sleeping, send '!model check' to wake it up.")
                time.sleep(1)
                st.rerun()
            except Exception as e:
                st.error(f"Failed to save: {e}")

    st.divider()
    st.subheader("🏥 System Health")
    
    # Check processes
    nexus_running = os.popen("pgrep -f nexus_bridge.py").read().strip()
    brain_running = os.popen("pgrep -f openclaw").read().strip()
    
    c1, c2 = st.columns(2)
    with c1:
        if nexus_running:
            st.success(f"👤 Nexus Bridge: Online (PID {nexus_running.split()[0]})")
        else:
            st.error("👤 Nexus Bridge: Offline")
            
    with c2:
        if brain_running:
            st.success(f"🧠 Key/Brain: Online")
        else:
            st.error("🧠 Key/Brain: Offline")
            
    st.write("---")
    st.caption("Nexus Control Center v2.1")

def render_messages_page(conn):
    st.header("📂 Message History (Black Box)")
    
    df = load_data(conn)
    
    if df.empty:
        st.info("No logs found yet. Start the system and wait for messages.")
        return

    # FILTERS
    col1, col2 = st.columns(2)
    with col1:
        search = st.text_input("🔍 Search Messages", placeholder="Text, username...")
    with col2:
        chat_filter = st.multiselect("Filter by Chat", options=df['chat_title'].unique())

    # Apply filters
    filtered_df = df.copy()
    if search:
        filtered_df = filtered_df[
            filtered_df['message_text'].str.contains(search, case=False, na=False) | 
            filtered_df['sender_name'].str.contains(search, case=False, na=False)
        ]
    if chat_filter:
        filtered_df = filtered_df[filtered_df['chat_title'].isin(chat_filter)]

    # Metrics
    m1, m2, m3 = st.columns(3)
    m1.metric("Total Messages", len(df))
    m2.metric("Filtered", len(filtered_df))
    
    # Display Data
    show_cols = ['date', 'chat_title', 'sender_name', 'username', 'message_text']
    
    st.dataframe(
        filtered_df[show_cols],
        column_config={
            "date": st.column_config.DatetimeColumn("Time", format="D MMM, HH:mm:ss"),
            "chat_title": "Chat",
            "sender_name": "Sender",
            "username": "Handle",
            "message_text": st.column_config.TextColumn("Message", width="large")
        },
        height=600,
        use_container_width=True,
        hide_index=True
    )
    
    # Auto-refresh
    time.sleep(5)
    st.rerun()

# --- MAIN LAYOUT ---
conn = get_connection()

with st.sidebar:
    st.image("https://em-content.zobj.net/source/apple/391/crab_1f980.png", width=50)
    st.title("Nexus Control")
    
    page = st.radio("Navigation", ["Messages", "Settings"], index=0)
    
    st.divider()
    if st.button("🔄 Refresh"):
        st.rerun()

if page == "Messages":
    render_messages_page(conn)
elif page == "Settings":
    render_settings_page(conn)

conn.close()
