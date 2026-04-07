import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import io
import os
import random
from datetime import datetime

# --- KONFIGURACJA STRONY ---
st.set_page_config(page_title="VCP & MMPS Pro Dashboard", layout="wide")

# Pliki i stałe
FAVORITES_FILE = "favorites_v2.csv"
SETUPS = ["Brak", "Cup", "Shakeout", "Pocket Pivot", "Breakout", "Breakout Range Tight", "VCP Tightening"]

# --- FUNKCJE BAZODANOWE ---
def load_favorites():
    if os.path.exists(FAVORITES_FILE):
        return pd.read_csv(FAVORITES_FILE)
    return pd.DataFrame(columns=['Ticker', 'Alert_Price', 'Setup'])

def save_favorites(df):
    df.to_csv(FAVORITES_FILE, index=False)

# --- FUNKCJE ANALITYCZNE ---
def force_float(val):
    try:
        if isinstance(val, (pd.Series, pd.DataFrame)): val = val.dropna().iloc[-1]
        return float(val)
    except: return 0.0

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
    for name, url in sources:
        try:
            res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            df = pd.read_html(io.StringIO(res.text))[0]
            col = next((c for c in ['Symbol', 'Ticker symbol', 'Ticker'] if c in df.columns), None)
            if col: tickers.extend(df[col].tolist())
        except: continue
    return list(set([str(t).replace('.', '-') for t in tickers]))

# --- UI BOCZNE ---
st.sidebar.title("⚙️ Zarządzanie")

# DODAWANIE DO WATCHLISTY
st.sidebar.subheader("⭐ Dodaj do Watchlisty")
fav_ticker = st.sidebar.text_input("Ticker (np. NVDA):").upper()
alert_val = st.sidebar.number_input("Cena Alertu (Pivot):", value=0.0)
setup_choice = st.sidebar.selectbox("Przyczyna kupna (Setup):", SETUPS)

if st.sidebar.button("Zapisz w Watchliście", width='stretch'):
    if fav_ticker:
        fav_df = load_favorites()
        if fav_ticker in fav_df['Ticker'].values:
            fav_df.loc[fav_df['Ticker'] == fav_ticker, 'Alert_Price'] = alert_val
            fav_df.loc[fav_df['Ticker'] == fav_ticker, 'Setup'] = setup_choice
        else:
            new_row = pd.DataFrame([{'Ticker': fav_ticker, 'Alert_Price': alert_val, 'Setup': setup_choice}])
            fav_df = pd.concat([fav_df, new_row], ignore_index=True)
        save_favorites(fav_df)
        st.sidebar.success(f"Zapisano {fav_ticker}!")
        st.rerun()

st.sidebar.divider()
st.sidebar.subheader("🔍 Skaner Rynku")
min_mom = st.sidebar.slider("Min. Momentum 6M (%)", 0, 150, 30)
max_vcp_p = st.sidebar.slider("Max. VCP Spread (%)", 1.0, 10.0, 3.0)
max_vol_r = st.sidebar.slider("Max. Volume Ratio", 0.1, 2.0, 0.9)

if 'scan_results' not in st.session_state: st.session_state.scan_results = {}

if st.sidebar.button("URUCHOM SKANER S&P 1500", width='stretch'):
    with st.status("Analizowanie 1500 spółek...") as status:
        uni = get_universe_sp1500()
        found = {}
        for i in range(0, len(uni), 50):
            chunk = uni[i:i+50]
            try:
                data = yf.download(chunk, period="1y", group_by='ticker', progress=False)
                for ticker in chunk:
                    try:
                        df = data[ticker].dropna(subset=['Close']) if len(chunk) > 1 else data.dropna(subset=['Close'])
                        if len(df) < 130: continue
                        c = df['Close']
                        s10, e20, s50 = c.rolling(10).mean().iloc[-1], c.ewm(span=20, adjust=False).mean().iloc[-1], c.rolling(50).mean().iloc[-1]
                        spread = (max(s10, e20, s50) - min(s10, e20, s50)) / min(s10, e20, s50)
                        vol_r = df['Volume'].iloc[-1] / df['Volume'].rolling(50).mean().iloc[-1]
                        if spread <= (max_vcp_p/100) and float(c.iloc[-1]) > float(s50):
                            if c.pct_change(126).iloc[-1] >= (min_mom/100) and vol_r <= max_vol_r:
                                found[ticker] = {'Cena': round(c.iloc[-1], 2), 'VCP %': round(spread * 100, 2), 'Vol Ratio': round(vol_r, 2), 'Mom 6M %': round(c.pct_change(126).iloc[-1] * 100, 1)}
                    except: continue
            except: continue
        st.session_state.scan_results.update(found)
        status.update(label=f"Skanowanie zakończone.", state="complete")

# --- PANEL GŁÓWNY ---
st.title("🚀 VCP & MMPS Pro Dashboard")

def build_table(tickers_dict, is_fav=False):
    final_list = []
    market = yf.download("^GSPC", period="2y", progress=False)['Close'].squeeze()
    if isinstance(market, pd.DataFrame): market = market.iloc[:, 0]
    
    fav_db = load_favorites()
    
    for t in tickers_dict.keys():
        try:
            h = yf.download(t, period="2y", progress=False)
            if h.empty or len(h) < 252: continue
            if isinstance(h.columns, pd.MultiIndex): h.columns = h.columns.get_level_values(0)
            cl = h['Close'].dropna()
            
            p_now = float(cl.iloc[-1])
            p_3m, p_6m, p_12m = float(cl.iloc[-63]), float(cl.iloc[-126]), float(cl.iloc[-252])
            rs_line = cl / market.reindex(h.index).ffill()
            mm_val, b_dot = calculate_mmps(p_now, p_3m, p_6m, p_12m, rs_line.iloc[-1], rs_line.iloc[-63:].max(), float(h['High'].iloc[-63:].max()))
            
            s10, e20, s50 = cl.rolling(10).mean().iloc[-1], cl.ewm(span=20, adjust=False).mean().iloc[-1], cl.rolling(50).mean().iloc[-1]
            vcp_v = round(((max(s10,e20,s50)-min(s10,e20,s50))/min(s10,e20,s50))*100, 2)
            v_ratio = round(h['Volume'].iloc[-1]/h['Volume'].rolling(50).mean().iloc[-1], 2)

            stooq_url = f"https://stooq.pl/q/a2/?s={t.replace('.WA','').lower()}.pl&i=d&t=c&a=lg" if ".WA" in t.upper() else f"https://stooq.pl/q/a2/?s={t.lower()}.us&i=d&t=c&a=lg"

            row = {'Ticker': t, 'Wykres': stooq_url, 'Setup': "Skaner", 'Cena': p_now, 'MMPS': mm_val, 'Blue Dot': "🔵 TAK" if b_dot else "", 'VCP %': vcp_v, 'Vol Ratio': v_ratio}
            
            if is_fav:
                fav_row = fav_db.loc[fav_db['Ticker'] == t]
                alert = float(fav_row['Alert_Price'].values[0])
                row['Setup'] = str(fav_row['Setup'].values[0])
                row['Alert'] = alert
                row['Do Alertu %'] = round(((alert - p_now)/p_now)*100, 2) if alert > 0 else 0
            
            final_list.append(row)
        except: continue
    return pd.DataFrame(final_list)

# SEKCJA 1: STAŁA WATCHLISTA
st.subheader("⭐ MOJA STAŁA WATCHLISTA")
fav_data = load_favorites()
if not fav_data.empty:
    with st.spinner("Aktualizacja Twojej listy..."):
        df_fav = build_table({row['Ticker']: {} for _, row in fav_data.iterrows()}, is_fav=True)
        if not df_fav.empty:
            st.dataframe(df_fav.style.format({'Cena': '{:.2f}', 'Alert': '{:.2f}', 'VCP %': '{:.2f}%', 'Do Alertu %': '{:.1f}%'}), 
                         column_config={
                             "Wykres": st.column_config.LinkColumn("Stooq", display_text="📈"), 
                             "Do Alertu %": st.column_config.ProgressColumn("Dystans", min_value=-15, max_value=15, format="%.1f%%"),
                         }, 
                         width='stretch', hide_index=True)
            
            # PANEL USUWANIA POSZCZEGÓLNYCH SPÓŁEK
            cols = st.columns([3, 1])
            with cols[0]:
                to_delete = st.multiselect("Wybierz spółki do usunięcia z listy:", options=fav_data['Ticker'].tolist())
            with cols[1]:
                st.write("##") # Spacer
                if st.button("Usuń zaznaczone", type="primary", width='stretch'):
                    if to_delete:
                        fav_data = fav_data[~fav_data['Ticker'].isin(to_delete)]
                        save_favorites(fav_data)
                        st.rerun()
else:
    st.info("Watchlista jest pusta.")

st.divider()

# SEKCJA 2: WYNIKI SKANERA
st.subheader("🔍 WYNIKI OSTATNIEGO SKANOWANIA")
if st.session_state.scan_results:
    df_scan = build_table(st.session_state.scan_results)
    if not df_scan.empty:
        st.dataframe(df_scan.sort_values(by='MMPS', ascending=False), width='stretch', hide_index=True)
        if st.button("Wyczyść wyniki skanera"):
            st.session_state.scan_results = {}
            st.rerun()
