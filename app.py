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

# --- LOGIKA MMPS (TWOJA FUNKCJA Z PRECYZYJNYMI WAGAMI) ---
def calculate_mmps(p_now, p_3m, p_6m, p_12m, rs_now, rs_max_3m, p_max_3m):
    try:
        # Obliczanie zmian procentowych (Momentum)
        d3 = ((p_now - p_3m) / p_3m * 100) if p_3m > 0 else 0
        d6 = ((p_now - p_6m) / p_6m * 100) if p_6m > 0 else 0
        d12 = ((p_now - p_12m) / p_12m * 100) if p_12m > 0 else 0
        
        # Wzór: (4 * 3m) + (2 * 6m) + 12m
        raw_score = (4 * d3) + (2 * d6) + d12
        
        # Normalizacja do skali 100 (dzielnik 4.0 dla topowych liderów)
        normalized_rs = min(100, raw_score / 4.0)
        
        # Warunek Blue Dot: RS Line na szczycie 3m ORAZ Cena poniżej szczytu 3m
        blue_dot = (rs_now >= rs_max_3m) and (p_now < p_max_3m)
        
        # Bonus 20% za Blue Dot
        final_score = normalized_rs * 1.2 if blue_dot else normalized_rs
        
        return round(float(final_score), 1), blue_dot
    except:
        return 0.0, False

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
manual_input = st.sidebar.text_input("Dodaj ręcznie (np. PKO.WA, MKSI):")
if st.sidebar.button("Dodaj do listy", width='stretch'):
    if manual_input:
        new_list = [t.strip().upper() for t in manual_input.replace(' ', ',').split(',') if t.strip()]
        for t in new_list:
            if t not in st.session_state.watchlist_data:
                st.session_state.watchlist_data[t] = {} 
        st.rerun()

if st.sidebar.button("🔍 URUCHOM SKANER S&P 1500", width='stretch'):
    with st.status("Przeszukiwanie 1500 spółek (Logika VCP)...") as status:
        uni = get_universe_sp1500()
        found_data = {}
        batch_size = 50
        for i in range(0, len(uni), batch_size):
            chunk = uni[i:i+batch_size]
            try:
                data = yf.download(chunk, period="1y", group_by='ticker', progress=False)
                for ticker in chunk:
                    try:
                        df = data[ticker].dropna(subset=['Close']) if len(chunk) > 1 else data.dropna(subset=['Close'])
                        if len(df) < 130: continue
                        
                        close = df['Close']
                        curr_close = close.iloc[-1]
                        
                        # VCP Średnie
                        sma10 = close.rolling(10).mean().iloc[-1]
                        ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
                        sma50 = close.rolling(50).mean().iloc[-1]
                        spread = (max(sma10, ema20, sma50) - min(sma10, ema20, sma50)) / min(sma10, ema20, sma50)
                        
                        if spread > (max_vcp_p/100) or curr_close <= sma50: continue
                        
                        # Momentum 6M
                        mom_6m = close.pct_change(126).iloc[-1]
                        if mom_6m < (min_mom/100): continue
                        
                        # Wolumen
                        vol_sma50 = df['Volume'].rolling(50).mean().iloc[-1]
                        vol_ratio = df['Volume'].iloc[-1] / vol_sma50
                        if vol_ratio >= max_vol_r: continue
                        
                        found_data[ticker] = {
                            'Cena': round(curr_close, 2),
                            'VCP %': round(spread * 100, 2),
                            'Vol Ratio': round(vol_ratio, 2),
                            'Mom 6M %': round(mom_6m * 100, 1)
                        }
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
    # Benchmark S&P 500 do RS Line (2 lata historii)
    market_raw = yf.download("^GSPC", period="2y", progress=False)['Close'].squeeze()
    market = market_raw if not isinstance(market_raw, pd.DataFrame) else market_raw.iloc[:, 0]
    
    with st.spinner("Precyzyjne obliczanie MMPS (252 dni sesyjne)..."):
        for t, d in st.session_state.watchlist_data.items():
            try:
                # Pobieramy 2 lata, aby mieć pewność co do p_12m (rok temu)
                h = yf.download(t, period="2y", progress=False)
                if h.empty or len(h) < 252: continue
                
                if isinstance(h.columns, pd.MultiIndex): h.columns = h.columns.get_level_values(0)
                cl = h['Close'].dropna()
                
                # Precyzyjne punkty momentum (dni sesyjne)
                p_now = float(cl.iloc[-1])
                p_3m  = float(cl.iloc[-63])   # ~63 dni sesyjne (3 miesiące)
                p_6m  = float(cl.iloc[-126])  # ~126 dni sesyjne
                p_12m = float(cl.iloc[-252])  # ~252 dni sesyjne (pełny rok)
                p_max_3m = float(h['High'].iloc[-63:].max())
                
                # Relatywna Siła (RS)
                rs_line = cl / market.reindex(h.index).ffill()
                rs_now = float(rs_line.iloc[-1])
                rs_max_3m = float(rs_line.iloc[-63:].max())

                # Wywołanie wzoru MMPS
                mm_score, b_dot = calculate_mmps(p_now, p_3m, p_6m, p_12m, rs_now, rs_max_3m, p_max_3m)
                
                # Jeśli spółka dodana ręcznie, uzupełnij brakujące wskaźniki techniczne
                if 'VCP %' not in d or d.get('VCP %') == 0:
                    s10, e20, s50 = cl.rolling(10).mean().iloc[-1], cl.ewm(span=20).mean().iloc[-1], cl.rolling(50).mean().iloc[-1]
                    d['Cena'] = round(p_now, 2)
                    d['VCP %'] = round(((max(s10,e20,s50)-min(s10,e20,s50))/min(s10,e20,s50))*100, 2)
                    d['Vol Ratio'] = round(h['Volume'].iloc[-1]/h['Volume'].rolling(50).mean().iloc[-1], 2)
                    d['Mom 6M %'] = round(cl.pct_change(126).iloc[-1]*100, 1)

                final_list.append({
                    'Ticker': t, 'Cena': d['Cena'], 'MMPS': mm_score, 'Blue Dot': "🔵 TAK" if b_dot else "",
                    'VCP %': d['VCP %'], 'Vol Ratio': d['Vol Ratio'], 'Mom 6M %': d['Mom 6M %']
                })
            except: continue

    if final_list:
        df = pd.DataFrame(final_list).sort_values(by='MMPS', ascending=False)
        st.subheader(f"📋 Wyniki Analizy ({len(df)} spółek)")
        
        styler = df.style.format({'Cena': '{:.2f}', 'VCP %': '{:.2f}%', 'Vol Ratio': '{:.2f}', 'Mom 6M %': '{:.1f}%'})
        def highlight_dot(v): return 'background-color: #002b36; color: #00d4ff' if v == "🔵 TAK" else ''
        
        if hasattr(styler, 'map'): styler = styler.map(highlight_dot, subset=['Blue Dot'])
        else: styler = styler.applymap(highlight_dot, subset=['Blue Dot'])
        
        st.dataframe(styler, width='stretch', height=500)
        
        st.divider()
        sel = st.selectbox("Wybierz spółkę do wykresu:", df['Ticker'].tolist())
        if sel:
            c_raw = yf.download(sel, period="6mo", progress=False)
            c_plot = c_raw['Close'].squeeze()
            if isinstance(c_plot, pd.DataFrame): c_plot = c_plot.iloc[:, 0]
            st.line_chart(c_plot)
else:
    st.info("Lista pusta. Dodaj spółki ręcznie lub uruchom skaner w panelu bocznym.")