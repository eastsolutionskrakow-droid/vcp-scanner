import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import io
import time
import random
from datetime import datetime, timedelta

# --- KONFIGURACJA STRONY ---
st.set_page_config(page_title="VCP & MMPS Pro Dashboard", layout="wide")

# --- FUNKCJE POMOCNICZE ---
def force_float(val):
    try:
        if isinstance(val, (pd.Series, pd.DataFrame)):
            val = val.dropna().iloc[-1]
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
        final_score = norm_rs * 1.2 if blue_dot else norm_rs
        return round(float(final_score), 1), blue_dot
    except: return 0.0, False

@st.cache_data(ttl=86400)
def get_universe_sp1500():
    tickers = []
    sources = [
        ("S&P 500", 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'),
        ("S&P 400", 'https://en.wikipedia.org/wiki/List_of_S%26P_400_companies'),
        ("S&P 600", 'https://en.wikipedia.org/wiki/List_of_S%26P_600_companies')
    ]
    for name, url in sources:
        try:
            res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            df = pd.read_html(io.StringIO(res.text))[0]
            col = next((c for c in ['Symbol', 'Ticker symbol', 'Ticker'] if c in df.columns), None)
            if col: tickers.extend(df[col].tolist())
        except: continue
    return list(set([str(t).replace('.', '-') for t in tickers]))

# --- UI BOCZNE ---
st.sidebar.title("⚙️ Filtry Strategii")
min_mom = st.sidebar.slider("Min. Momentum 6M (%)", 0, 150, 30)
max_vcp_p = st.sidebar.slider("Max. VCP Spread (%)", 1.0, 10.0, 3.0)
max_vol_r = st.sidebar.slider("Max. Volume Ratio", 0.1, 2.0, 0.9)

if 'watchlist_data' not in st.session_state:
    st.session_state.watchlist_data = {}

st.sidebar.divider()
manual_input = st.sidebar.text_input("Dodaj ręcznie (np. PKO.WA, TSLA):")
if st.sidebar.button("Dodaj do listy", width='stretch'):
    if manual_input:
        new_list = [t.strip().upper() for t in manual_input.replace(' ', ',').split(',') if t.strip()]
        for t in new_list:
            if t not in st.session_state.watchlist_data:
                st.session_state.watchlist_data[t] = {} 
        st.rerun()

if st.sidebar.button("🔍 URUCHOM SKANER S&P 1500", width='stretch'):
    with st.status("Skanowanie 1500 spółek...") as status:
        uni = get_universe_sp1500()
        found_data = {}
        for i in range(0, len(uni), 50):
            chunk = uni[i:i+50]
            try:
                data = yf.download(chunk, period="1y", group_by='ticker', progress=False)
                for ticker in chunk:
                    try:
                        df = data[ticker].dropna(subset=['Close']) if len(chunk) > 1 else data.dropna(subset=['Close'])
                        if len(df) < 130: continue
                        close = df['Close']
                        curr_close = close.iloc[-1]
                        s10, e20, s50 = close.rolling(10).mean().iloc[-1], close.ewm(span=20, adjust=False).mean().iloc[-1], close.rolling(50).mean().iloc[-1]
                        spread = (max(s10, e20, s50) - min(s10, e20, s50)) / min(s10, e20, s50)
                        vol_r = df['Volume'].iloc[-1] / df['Volume'].rolling(50).mean().iloc[-1]
                        if spread <= (max_vcp_p/100) and float(curr_close) > float(sma50):
                            if close.pct_change(126).iloc[-1] >= (min_mom/100) and vol_r <= max_vol_r:
                                found_data[ticker] = {'Cena': round(curr_close, 2), 'VCP %': round(spread * 100, 2), 'Vol Ratio': round(vol_r, 2), 'Mom 6M %': round(close.pct_change(126).iloc[-1] * 100, 1)}
                    except: continue
            except: continue
        st.session_state.watchlist_data.update(found_data)
        status.update(label=f"Sukces! Znaleziono {len(found_data)} spółek.", state="complete")

if st.sidebar.button("Wyczyść wszystko", width='stretch'):
    st.session_state.watchlist_data = {}
    st.rerun()

# --- PANEL GŁÓWNY ---
st.title("🚀 VCP & MMPS Pro Dashboard")

if st.session_state.watchlist_data:
    final_list = []
    market_raw = yf.download("^GSPC", period="2y", progress=False)['Close'].squeeze()
    market = market_raw if not isinstance(market_raw, pd.DataFrame) else market_raw.iloc[:, 0]
    
    with st.spinner("Przeliczanie MMPS i Stooq URL..."):
        for t, d in st.session_state.watchlist_data.items():
            try:
                h = yf.download(t, period="2y", progress=False)
                if h.empty or len(h) < 252: continue
                if isinstance(h.columns, pd.MultiIndex): h.columns = h.columns.get_level_values(0)
                cl = h['Close'].dropna()
                p_now, p_3m, p_6m, p_12m = float(cl.iloc[-1]), float(cl.iloc[-63]), float(cl.iloc[-126]), float(cl.iloc[-252])
                p_max_3m = float(h['High'].iloc[-63:].max())
                rs_line = cl / market.reindex(h.index).ffill()
                mm_val, b_dot = calculate_mmps(p_now, p_3m, p_6m, p_12m, rs_line.iloc[-1], rs_line.iloc[-63:].max(), p_max_3m)
                
                if 'VCP %' not in d or d.get('VCP %') == 0:
                    s10, e20, s50 = cl.rolling(10).mean().iloc[-1], cl.ewm(span=20, adjust=False).mean().iloc[-1], cl.rolling(50).mean().iloc[-1]
                    d.update({'Cena': round(p_now, 2), 'VCP %': round(((max(s10,e20,s50)-min(s10,e20,s50))/min(s10,e20,s50))*100, 2), 'Vol Ratio': round(h['Volume'].iloc[-1]/h['Volume'].rolling(50).mean().iloc[-1], 2), 'Mom 6M %': round(cl.pct_change(126).iloc[-1]*100, 1)})

                # Generator linku do STOOQ
                clean_t = t.upper()
                if ".WA" in clean_t: stooq_url = f"https://stooq.pl/q/a2/?s={clean_t.replace('.WA','').lower()}.pl&i=d&t=c&a=lg"
                else: stooq_url = f"https://stooq.pl/q/a2/?s={clean_t.lower()}.us&i=d&t=c&a=lg"

                final_list.append({'Ticker': t, 'Wykres Stooq': stooq_url, 'Cena': d['Cena'], 'MMPS': mm_val, 'Blue Dot': "🔵 TAK" if b_dot else "", 'VCP %': d['VCP %'], 'Vol Ratio': d['Vol Ratio'], 'Mom 6M %': d['Mom 6M %']})
            except: continue

    if final_list:
        df = pd.DataFrame(final_list).sort_values(by='MMPS', ascending=False)
        styler = df.style.format({'Cena': '{:.2f}', 'VCP %': '{:.2f}%', 'Vol Ratio': '{:.2f}', 'Mom 6M %': '{:.1f}%'})
        def highlight_dot(v): return 'background-color: #002b36; color: #00d4ff' if v == "🔵 TAK" else ''
        if hasattr(styler, 'map'): styler = styler.map(highlight_dot, subset=['Blue Dot'])
        else: styler = styler.applymap(highlight_dot, subset=['Blue Dot'])

        st.dataframe(styler, column_config={"Wykres Stooq": st.column_config.LinkColumn("Analiza Stooq", display_text="Otwórz 📈"), "Ticker": st.column_config.TextColumn("Ticker")}, width='stretch', height=600, hide_index=True)
        st.divider()
        sel = st.selectbox("Podgląd wykresu lokalnego:", df['Ticker'].tolist())
        if sel:
            c_raw = yf.download(sel, period="6mo", progress=False)
            c_plot = c_raw['Close'].squeeze()
            if isinstance(c_plot, pd.DataFrame): c_plot = c_plot.iloc[:, 0]
            st.line_chart(c_plot)
else:
    st.info("Lista pusta. Dodaj spółki lub uruchom skaner.")
