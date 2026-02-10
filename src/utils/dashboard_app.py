
# -*- coding: utf-8 -*-
import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
import os
import psutil
import platform
from datetime import datetime

# Настройка страницы
st.set_page_config(
    page_title="🦀 Krab v2.0 Dashboard",
    page_icon="🦀",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Стилизация (Glassmorphism & Cyberpunk)
st.markdown("""
    <style>
    .main {
        background-color: #0e1117;
        color: #e0e0e0;
    }
    .stMetric {
        background: rgba(255, 255, 255, 0.05);
        padding: 15px;
        border-radius: 10px;
        border: 1px solid rgba(255, 255, 255, 0.1);
    }
    h1, h2, h3 {
        color: #00ffcc !important;
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
    st.write("v3.0 Intelligence Evolution")
    st.divider()
    
    # Системные метрики
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    
    st.metric("CPU Usage", f"{cpu}%")
    st.metric("RAM Usage", f"{ram}%")
    
    # RAG Stats (Knowledge Base)
    st.divider()
    st.subheader("🧠 Knowledge Base")
    # Простейшая проверка размера коллекции Chromadb без загрузки всего движка
    kb_path = "artifacts/memory/chroma_db"
    if os.path.exists(kb_path):
        kb_size = sum(os.path.getsize(os.path.join(dirpath, filename)) for dirpath, dirnames, filenames in os.walk(kb_path) for filename in filenames)
        st.write(f"Size: {kb_size / (1024*1024):.2f} MB")
    
    st.divider()
    if st.button("🔄 Refresh Data"):
        st.rerun()

# --- MAIN UI ---
st.title("📊 Krab Intelligence Dashboard v3.0")

df = load_data()

if df.empty:
    st.warning("Черный Ящик пуст. Бот еще не получил сообщений.")
else:
    # Метрики
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Messages", len(df))
    with col2:
        incoming = len(df[df['direction'] == 'INCOMING'])
        st.metric("Incoming", incoming)
    with col3:
        outgoing = len(df[df['direction'] == 'OUTGOING'])
        st.metric("Outgoing", outgoing)
    with col4:
        unique_chats = df['chat_id'].nunique()
        st.metric("Active Chats", unique_chats)

    # Графики
    st.divider()
    
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

    # Логи в реальном времени
    st.divider()
    st.subheader("📋 System Logs (Real-time)")
    log_file = "logs/krab.log"
    if os.path.exists(log_file):
        with open(log_file, "r") as f:
            lines = f.readlines()
            # Показываем последние 20 строк в прокручиваемом окне
            st.code("".join(lines[-30:]), language="text")
    else:
        st.info("Лог-файл krab.log пока не создан.")

    # Таблица последних сообщений
    st.divider()
    st.subheader("📝 Recent Activity (Black Box)")
    
    # Фильтрация
    search = st.text_input("Поиск по тексту...", "")
    if search:
        df_display = df[df['text'].str.contains(search, case=False, na=False)]
    else:
        df_display = df

    st.dataframe(
        df_display[['timestamp', 'chat_title', 'sender_name', 'direction', 'text', 'model_used']].head(100),
        use_container_width=True,
        hide_index=True
    )

# События
st.divider()
st.subheader("🔔 System Events")
ev_df = load_events()
if not ev_df.empty:
    st.table(ev_df[['timestamp', 'event_type', 'description']].head(10))
else:
    st.info("Событий не зафиксировано.")

st.caption(f"Backend: {platform.system()} {platform.release()} | Krab v3.0 Intelligence Evolution")
