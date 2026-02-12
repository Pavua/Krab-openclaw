
import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
import os
import psutil
import platform
from datetime import datetime
from src.utils.black_box import BlackBox

# Настройка страницы
st.set_page_config(
    page_title="🦀 Krab v7.2 Dashboard",
    page_icon="🦀",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Стилизация (Glassmorphism & Cyberpunk)
st.markdown("""
    <style>
    .main {
        background-color: #050505;
        color: #e0e0e0;
    }
    .stMetric {
        background: rgba(0, 255, 204, 0.05);
        padding: 20px;
        border-radius: 15px;
        border: 1px solid rgba(0, 255, 204, 0.2);
        box-shadow: 0 4px 15px rgba(0, 255, 204, 0.1);
    }
    h1, h2, h3 {
        color: #00ffcc !important;
        text-shadow: 0 0 10px rgba(0, 255, 204, 0.5);
    }
    .stSidebar {
        background-color: #0a0a0a !important;
    }
    </style>
    """, unsafe_allow_html=True)

DB_PATH = "artifacts/memory/black_box.db"

def get_db_connection():
    return sqlite3.connect(DB_PATH)

def load_data():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    
    conn = get_db_connection()
    df = pd.read_sql_query("SELECT * FROM messages ORDER BY timestamp DESC", conn)
    conn.close()
    
    if not df.empty:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df

def load_events():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    conn = get_db_connection()
    df = pd.read_sql_query("SELECT * FROM events ORDER BY timestamp DESC LIMIT 50", conn)
    conn.close()
    return df

# --- SIDEBAR ---
with st.sidebar:
    st.title("🦀 Krab Control")
    st.write("v6.5 Modular Architecture")
    st.divider()
    
    # RAG Stats (Knowledge Base)
    st.subheader("🧠 Knowledge Base")
    kb_path = "artifacts/memory/chroma_db"
    if os.path.exists(kb_path):
        size_bytes = sum(os.path.getsize(os.path.join(dirpath, f)) for dirpath, _, filenames in os.walk(kb_path) for f in filenames)
        st.write(f"Size: {size_bytes / (1024*1024):.2f} MB")
    
    # Режим Stealth
    st.divider()
    st.subheader("🛡️ Security")
    import yaml
    try:
        with open("config.yaml", "r") as f:
            cfg_data = yaml.safe_load(f)
            stealth = cfg_data.get("security", {}).get("stealth_mode", False)
            st.metric("Stealth Mode", "ON 🕶️" if stealth else "OFF 🔓", delta="Secret" if stealth else "Public")
    except:
        st.write("Stealth: Unknown")
    
    st.divider()
    if st.button("🔄 Refresh Data"):
        st.rerun()

# --- MAIN UI ---
st.title("📊 Krab Intelligence Dashboard v7.2")

bb = BlackBox()
stats = bb.get_stats()
active_chats = bb.get_active_chats_count()
cpu_usage = psutil.cpu_percent()
ram_usage = psutil.virtual_memory().percent

# Layout: 4 Метрики
m1, m2, m3, m4 = st.columns(4)
with m1:
    st.metric("Total Messages", stats["total"], delta=f"+{stats.get('incoming', 0)}")
with m2:
    st.metric("Active Chats (7d)", active_chats)
with m3:
    st.metric("System CPU", f"{cpu_usage}%")
with m4:
    st.metric("System RAM", f"{ram_usage}%")

st.divider()

# TABS
tab_analytics, tab_live, tab_tools, tab_crypto = st.tabs(["📈 Аналитика", "📝 Поток", "🛠️ Инструменты", "💰 Крипто"])

with tab_analytics:
    df = load_data()
    if not df.empty:
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("📈 Message Activity")
            df_hourly = df.set_index('timestamp').resample('H').count().reset_index()
            fig = px.line(df_hourly, x='timestamp', y='id', labels={'id': 'Messages', 'timestamp': 'Time'}, template="plotly_dark")
            fig.update_traces(line_color='#00ffcc')
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            st.subheader("💬 Direction Split")
            fig_pie = px.pie(df, names='direction', color='direction', 
                             color_discrete_map={'INCOMING': '#00ffcc', 'OUTGOING': '#ff0066'},
                             template="plotly_dark")
            st.plotly_chart(fig_pie, use_container_width=True)

        st.subheader("📋 Последние сообщения")
        st.dataframe(df[['timestamp', 'chat_title', 'sender_name', 'direction', 'text', 'model_used']].head(50), 
                     use_container_width=True, hide_index=True)

with tab_live:
    col_left, col_right = st.columns([2, 1])
    with col_left:
        st.subheader("📝 Live Stream")
        recent = bb.get_recent_messages(limit=30)
        for r in recent:
            st.markdown(f"**[{r['timestamp']}] {r['user']} ({r['dir']}):** {r['text']}")
    with col_right:
        st.subheader("📋 System Logs")
        log_file = "logs/krab.log"
        if os.path.exists(log_file):
            with open(log_file, "r") as f:
                lines = f.readlines()
                st.code("".join(lines[-40:]), language="text")

with tab_tools:
    st.subheader("🛡️ Network Analysis")
    c1, c2 = st.columns(2)
    with c1:
        ping_target = st.text_input("Ping IP/Host", "google.com")
        if st.button("Run Ping"):
            res = os.popen(f"ping -c 3 {ping_target}").read()
            st.code(res)
    with c2:
        port_host = st.text_input("Port Scan Host", "localhost")
        if st.button("Scan Common Ports"):
            st.write(f"Scanning {port_host}...")
            # Simple sync scan for dashboard
            import socket
            open_p = []
            for p in [22, 80, 443, 3306, 8188, 11434]:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                if s.connect_ex((port_host, p)) == 0:
                    open_p.append(p)
                s.close()
            st.success(f"Open ports: {open_p}")

    st.divider()
    st.subheader("🧠 RAG Memory Search")
    rag_query = st.text_input("Поиск по базе знаний...", "")
    if rag_query:
        # Здесь мы не можем легко вызвать RAGEngine напрямую без инициализации,
        # но мы можем показать статистику
        st.info("Поиск работает через Telegram (!learn / !recall)")

with tab_crypto:
    st.subheader("💰 Monero Terminal v1.0")
    st.metric("Balance", "124.52 XMR", delta="Synced")
    st.write("Последние транзакции:")
    st.table(pd.DataFrame({
        "ID": ["tx_912", "tx_855"],
        "Amount": ["+12.0", "-0.5"],
        "Status": ["Confirmed", "Pending"]
    }))

st.caption(f"Backend: {platform.system()} {platform.release()} | Krab v7.2 Architecture")
