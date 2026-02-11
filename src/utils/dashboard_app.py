
import streamlit as st
import sqlite3
import pandas as pd
import time
import os
import sys
from datetime import datetime

# Фикс для импорта если запускается как скрипт
sys.path.append(os.getcwd())

# Page Config
st.set_page_config(
    page_title="Krab v7.0 Control Center",
    page_icon="🦀",
    layout="wide",
)

# Database Path - Используем актуальную базу из проекта
DB_PATH = "artifacts/memory/black_box.db"

def get_connection():
    return sqlite3.connect(DB_PATH)

def load_data(conn):
    try:
        query = "SELECT * FROM messages ORDER BY id DESC LIMIT 500"
        df = pd.read_sql_query(query, conn)
        return df
    except Exception as e:
        return pd.DataFrame()

# UI
st.title("🦀 Krab v7.0 Control Center")

page = st.sidebar.radio("Навигация", ["Чат (Black Box)", "Система"])

conn = get_connection()

if page == "Чат (Black Box)":
    st.header("📂 История сообщений")
    df = load_data(conn)
    
    if not df.empty:
        # Поиск
        search = st.text_input("🔍 Поиск по тексту или пользователю")
        if search:
            df = df[df['text'].str.contains(search, case=False, na=False) | 
                    df['sender_name'].str.contains(search, case=False, na=False)]
        
        st.dataframe(df, use_container_width=True)
    else:
        st.info("Сообщений пока нет.")

elif page == "Система":
    st.header("⚙️ Статус системы")
    
    # Мониторинг логов
    st.subheader("📋 Последние логи")
    if os.path.exists("logs/krab.log"):
        with open("logs/krab.log", "r") as f:
            logs = f.readlines()[-50:]
            st.code("".join(logs))
    else:
        st.write("Лог-файл не найден.")

conn.close()
