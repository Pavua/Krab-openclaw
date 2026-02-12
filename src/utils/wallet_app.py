# -*- coding: utf-8 -*-
"""
Krab Monero Wallet MVP (Dashboard Module)
Premium Cyberpunk Financial Interface.
"""
import streamlit as st
import pandas as pd
from datetime import datetime
import qrcode
from io import BytesIO

# Настройка страницы
st.set_page_config(
    page_title="💰 Krab Financial Portal",
    page_icon="💸",
    layout="wide"
)

# Кастомный CSS для "премиальности"
st.markdown("""
    <style>
    .main {
        background-color: #030303;
        color: #e0e0e0;
    }
    .wallet-card {
        background: linear-gradient(135deg, rgba(255, 133, 0, 0.1) 0%, rgba(255, 133, 0, 0.02) 100%);
        border-radius: 20px;
        padding: 30px;
        border: 1px solid rgba(255, 133, 0, 0.3);
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.8);
        margin-bottom: 20px;
    }
    .balance-text {
        font-size: 3rem;
        font-weight: 800;
        color: #ff8500;
        text-shadow: 0 0 20px rgba(255, 133, 0, 0.4);
    }
    .stTable {
        background-color: transparent !important;
    }
    h1, h2, h3 {
        color: #ff8500 !important;
        text-transform: uppercase;
        letter-spacing: 2px;
    }
    </style>
    """, unsafe_allow_html=True)

st.title("💸 Krab Monero Terminal v1.0")
st.write("Secure. Anonymous. Autonomous.")

col1, col2 = st.columns([2, 1])

with col1:
    st.markdown("""
        <div class="wallet-card">
            <p style="margin-bottom: 5px; color: #888;">CURRENT BALANCE</p>
            <p class="balance-text">124.52 XMR</p>
            <p style="color: #00ffcc;">≈ $21,432.10 USD</p>
        </div>
    """, unsafe_allow_html=True)

    st.subheader("🕸️ Recent Transactions")
    data = {
        "Date": [datetime.now().strftime("%Y-%m-%d %H:%M") for _ in range(5)],
        "Hash": ["0x...a1b2", "0x...c3d4", "0x...e5f6", "0x...g7h8", "0x...i9j0"],
        "Amount": ["+12.5 XMR", "-0.42 XMR", "+1.05 XMR", "-2.20 XMR", "+0.01 XMR"],
        "Status": ["CONFIRMED", "CONFIRMED", "PENDING", "CONFIRMED", "CONFIRMED"]
    }
    df = pd.DataFrame(data)
    st.table(df)

with col2:
    st.subheader("📥 Receive XMR")
    address = "4AdAS9BNDB8hT4J... (Mock Address)"
    st.code(address)
    
    # Генерация QR
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(f"monero:{address}")
    qr.make(fit=True)
    img = qr.make_image(fill_color="#ff8500", back_color="#030303")
    
    buf = BytesIO()
    img.save(buf)
    st.image(buf.getvalue(), caption="Scan to pay XMR", width=250)

    st.divider()
    st.subheader("⚙️ Wallet Secrets")
    st.warning("Keep your Mnemonic phrase offline. Krab never stores private keys in plain text.")
    if st.button("🗝️ View Public Keys"):
        st.info("Public view key: 0x... (Locked)")

st.sidebar.title("💎 Premium Status")
st.sidebar.success("Account: Verified (Owner)")
st.sidebar.write("Node: `xmr-node.krab.internal`")
st.sidebar.divider()
if st.sidebar.button("🔄 Sync with Blockchain"):
    st.sidebar.write("Syncing... 98%")
