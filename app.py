import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import io
import os
import time
import random
from datetime import datetime

# --- KONFIGURACJA STRONY ---
st.set_page_config(page_title="VCP & MMPS Pro Dashboard", layout="wide")

FAVORITES_FILE = "favorites_v3.csv"
SETUPS = ["Brak", "Cup", "Shakeout", "Pocket Pivot", "Breakout", "Breakout Range Tight", "VCP Tightening"]

# --- FUNKCJE BAZODANOWE ---
def load_favorites():
    if os.path.exists(FAVORITES_FILE):
        df = pd.read_csv(FAVORITES_FILE)
        df['Ticker'] = df['Ticker'].astype(str).str.upper().str.strip()
        df['Alert_Price'] = df['Alert_Price'].astype(float)
        return df
    return pd.DataFrame(columns=['Ticker', 'Alert_Price', 'Setup'])

def save_favorites(df):
    df.to_csv(FAVORITES_FILE, index=False)

def get_data_safe(df, ticker):
    try:
        if isinstance(df.columns, pd.MultiIndex):
            if ticker in df.columns.levels[0]:
                return df[ticker].dropna(subset=['Close'])
        else:
            return df.dropna(subset=['Close'])
    except:
        return pd.DataFrame()
    return pd.DataFrame()

# --- LOGIKA MMPS ---
def calculate_mmps(p_now, p_3m, p_6m, p_12m, rs_now, rs_max_3m, p_max_3m):
    try:
        d3 = ((p_now - p_3m) / p_3m * 100) if p_3m > 0 else 0
        d6 = ((p_now - p_6m) / p_6m * 100) if p_6m > 0 else 0
        d12 = ((p_now - p_12m) / p_12m * 100) if p_12m > 0 else 0
        raw_score = (4 * d3) + (2 * d6) + d12
        norm_rs = min(100, raw_score / 4.0)
        blue_dot = (rs_now >= rs_max_3m) and (p_now < p_max_3m)
        return round(float(norm_rs * 1.2 if blue_dot else norm_rs), 1), blue_dot
    except: return 0.0, False

@st.cache_data(ttl=86400)
def get_universe_sp1500():
    tickers = []
    sources = [("S&P 500", 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'), 
               ("S&P 400", 'https://en.wikipedia.org/wiki/List_of_S%26P_400_companies'), 
               ("S&P 600", 'https://en.wikipedia.org/wiki/List_of_S%26P_600_companies')]
    for _, url in sources:
        try:
            res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            df = pd.read_html(io.StringIO(res.text))[0]
            col = next((c for c in ['Symbol', 'Ticker symbol', 'Ticker'] if c in df.columns), None)
            if col: tickers.extend(df[col].tolist())
        except: continue
    return list(set([str(t).replace('.', '-') for t in tickers]))

if 'scan_results' not in st.session_state:
    st.session_state.scan_results = []

# --- UI BOCZNE ---
st.sidebar.title("⚙️ Narzędzia")
st.sidebar.markdown("### Skaner i Dodawanie")
min_mom_val = st.sidebar.slider("Min. Momentum 6M (%)", 0, 150, 30)
max_vcp_val = st.sidebar.slider("Max. VCP Spread (%)", 1.0, 10.0, 3.0)
max_vol_val = st.sidebar.slider("Max. Volume Ratio", 0.1, 2.0, 0.9)

st.sidebar.divider()
fav_ticker_input = st.sidebar.text_input("Szybkie dodawanie (Ticker):").upper().strip()
if st.sidebar.button("Dodaj do Watchlisty", width='stretch'):
    if fav_ticker_input:
        fav_df = load_favorites()
        if fav_ticker_input not in fav_df['Ticker'].values:
            new_row = pd.DataFrame([{'Ticker': fav_ticker_input, 'Alert_Price': 0.0, 'Setup': 'Brak'}])
            save_favorites(pd.concat([fav_df, new_row], ignore_index=True))
            st.rerun()

st.sidebar.divider()
if st.sidebar.button("🔍 URUCHOM SKANER S&P 1500", width='stretch'):
    with st.status("Analizowanie 1500 spółek...") as status:
        uni = get_universe_sp1500()
        found = []
        for i in range(0, len(uni), 50):
            chunk = uni[i:i+50]
            data = yf.download(chunk, period="1y", group_by='ticker', progress=False)
            for t in chunk:
                try:
                    df = get_data_safe(data, t)
                    if df.empty or len(df) < 130: continue
                    c = df['Close']
                    s10, e20, s50 = c.rolling(10).mean().iloc[-1], c.ewm(span=20, adjust=False).mean().iloc[-1], c.rolling(50).mean().iloc[-1]
                    spread = (max(s10, e20, s50) - min(s10, e20, s50)) / min(s10, e20, s50)
                    vol_r = df['Volume'].iloc[-1] / df['Volume'].rolling(50).mean().iloc[-1]
                    if spread <= (max_vcp_val/100) and float(c.iloc[-1]) > float(s50):
                        if c.pct_change(126).iloc[-1] >= (min_mom_val/100) and vol_r <= max_vol_val:
                            found.append(t)
                except: continue
        st.session_state.scan_results = list(set(st.session_state.scan_results + found))
        status.update(label="Gotowe!", state="complete")

# --- BUDOWANIE TABELI ---
def build_table_data(tickers, is_fav=False):
    if not tickers: return pd.DataFrame()
    final_list = []
    market_df = yf.download("^GSPC", period="2y", progress=False)
    market = market_df['Close'].squeeze()
    if isinstance(market, pd.DataFrame): market = market.iloc[:, 0]
    
    fav_db = load_favorites()
    data = yf.download(tickers, period="2y", group_by='ticker', progress=False)
    
    for t in tickers:
        try:
            h = get_data_safe(data, t)
            if h.empty or len(h) < 252: continue
            cl = h['Close']
            p_now, p_3m, p_6m, p_12m = float(cl.iloc[-1]), float(cl.iloc[-63]), float(cl.iloc[-126]), float(cl.iloc[-252])
            rs_line = cl / market.reindex(h.index).ffill()
            mm_val, b_dot = calculate_mmps(p_now, p_3m, p_6m, p_12m, rs_line.iloc[-1], rs_line.iloc[-63:].max(), float(h['High'].iloc[-63:].max()))
            s10, e20, s50 = cl.rolling(10).mean().iloc[-1], cl.ewm(span=20, adjust=False).mean().iloc[-1], cl.rolling(50).mean().iloc[-1]
            stooq_symbol = t.replace('.WA', '.PL').lower() if '.WA' in t else f"{t.lower()}.us"
            row = {'Ticker': t, 'Stooq': f"https://stooq.pl/q/a2/?s={stooq_symbol}&i=d&t=c&a=lg", 'Cena': p_now, 'MMPS': mm_val, 'Blue Dot': "🔵 TAK" if b_dot else "", 
                   'VCP %': round(((max(s10,e20,s50)-min(s10,e20,s50))/min(s10,e20,s50))*100, 2), 
                   'Vol Ratio': round(h['Volume'].iloc[-1]/h['Volume'].rolling(50).mean().iloc[-1], 2)}
            if is_fav:
                fav_row = fav_db.loc[fav_db['Ticker'] == t]
                row['Setup'] = str(fav_row['Setup'].values[0])
                row['Alert'] = float(fav_row['Alert_Price'].values[0])
                row['Dystans %'] = round(((row['Alert'] - p_now)/p_now) * 100, 2) if row['Alert'] > 0 else 0.0
            final_list.append(row)
        except: continue
    return pd.DataFrame(final_list)

# --- PANEL GŁÓWNY ---
st.title("🚀 VCP & MMPS Pro Dashboard")

fav_data = load_favorites()
all_tickers_pool = []

# --- 1. WATCHLISTA (Z EDYCJĄ) ---
st.subheader("⭐ MOJA STAŁA WATCHLISTA")
if not fav_data.empty:
    df_fav = build_table_data(fav_data['Ticker'].tolist(), is_fav=True)
    if not df_fav.empty:
        # Przełącznik trybu edycji
        edit_mode = st.toggle("✍️ Tryb Edycji (Alert / Setup)")
        
        if edit_mode:
            st.info("Zmień wartości w kolumnach 'Alert' lub 'Setup' i kliknij 'Zapisz zmiany'.")
            # Edytor dla wybranych pól
            edited_fav = st.data_editor(
                df_fav,
                column_config={
                    "Stooq": st.column_config.LinkColumn("📈", display_text="Otwórz"),
                    "Setup": st.column_config.SelectboxColumn("Setup", options=SETUPS),
                    "Alert": st.column_config.NumberColumn("Alert", format="%.2f"),
                    "Ticker": st.column_config.TextColumn("Ticker", disabled=True),
                    "Dystans %": None # Ukrywamy pasek postępu w trybie edycji dla czytelności
                },
                disabled=[c for c in df_fav.columns if c not in ["Alert", "Setup"]],
                hide_index=True, width='stretch'
            )
            if st.button("💾 Zapisz zmiany w Watchliście"):
                new_fav_db = edited_fav[['Ticker', 'Alert', 'Setup']].rename(columns={'Alert': 'Alert_Price'})
                save_favorites(new_fav_db)
                st.success("Zapisano pomyślnie!")
                time.sleep(1)
                st.rerun()
        else:
            # Widok standardowy z paskiem postępu
            st.dataframe(df_fav, column_config={
                "Stooq": st.column_config.LinkColumn("📈", display_text="Otwórz"), 
                "Dystans %": st.column_config.ProgressColumn("Dystans do Alertu", min_value=-20.0, max_value=20.0, format="%.2f%%")
            }, width='stretch', hide_index=True)
            
            # Usuwanie
            to_del = st.multiselect("Zaznacz spółki do usunięcia:", options=fav_data['Ticker'].tolist())
            if st.button("Usuń zaznaczone"):
                save_favorites(fav_data[~fav_data['Ticker'].isin(to_del)])
                st.rerun()
        
        all_tickers_pool.extend(df_fav['Ticker'].tolist())

# --- 2. SKANER ---
if st.session_state.scan_results:
    st.divider()
    st.subheader("🔍 WYNIKI SKANERA RYNKU")
    df_scan = build_table_data(st.session_state.scan_results)
    if not df_scan.empty:
        df_scan.insert(0, "Dodaj", False)
        edited_scan = st.data_editor(
            df_scan.sort_values(by='MMPS', ascending=False),
            column_config={"Dodaj": st.column_config.CheckboxColumn("Dodaj", default=False), "Stooq": st.column_config.LinkColumn("📈", display_text="Otwórz")},
            disabled=[c for c in df_scan.columns if c != "Dodaj"],
            hide_index=True, width='stretch'
        )
        if st.button("✅ Dodaj zaznaczone do Watchlisty"):
            selected = edited_scan[edited_scan["Dodaj"] == True]["Ticker"].tolist()
            if selected:
                fav_df = load_favorites()
                new_data = pd.DataFrame([{'Ticker': t, 'Alert_Price': 0.0, 'Setup': 'Brak'} for t in selected if t not in fav_df['Ticker'].values])
                save_favorites(pd.concat([fav_df, new_data], ignore_index=True))
                st.rerun()
        all_tickers_pool.extend(df_scan['Ticker'].tolist())

# --- 3. WYKRES ---
if all_tickers_pool:
    st.divider()
    sel = st.selectbox("🎯 ANALIZA TECHNICZNA (Wykres 6M):", options=sorted(list(set(all_tickers_pool))))
    if sel:
        h_chart = yf.download(sel, period="1y", progress=False)
        if isinstance(h_chart.columns, pd.MultiIndex): h_chart.columns = h_chart.columns.get_level_values(0)
        h_chart['SMA 10'] = h_chart['Close'].rolling(10).mean()
        h_chart['EMA 20'] = h_chart['Close'].ewm(span=20, adjust=False).mean()
        h_chart['SMA 50'] = h_chart['Close'].rolling(50).mean()
        df_p = h_chart.tail(126)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Cena", f"{df_p['Close'].iloc[-1]:.2f}")
        m2.metric("SMA 10", f"{df_p['SMA 10'].iloc[-1]:.2f}")
        m3.metric("EMA 20", f"{df_p['EMA 20'].iloc[-1]:.2f}")
        m4.metric("SMA 50", f"{df_p['SMA 50'].iloc[-1]:.2f}")
        st.line_chart(df_p[['Close', 'SMA 10', 'EMA 20', 'SMA 50']], color=["#FFFFFF", "#1F77B4", "#FFD700", "#FF0000"])
