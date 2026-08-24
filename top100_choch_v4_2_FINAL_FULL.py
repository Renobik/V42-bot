"""
V42.1 - BYBIT Top100 4H->1H CHoCH - LOUD TELEGRAM ALERT VERSION
Paper Trading DRY_RUN=True | 5% Risk | 5x | 5min scan | Flask for Render
"""
import os
import time
import traceback
from datetime import datetime
import threading
import ccxt
import pandas as pd
import numpy as np
from flask import Flask

# --- CONFIG ---
DRY_RUN = True  # Keep True for paper trading
RISK_PCT = 0.05
LEVERAGE = 5
SCAN_INTERVAL_SEC = 300  # 5 mins

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")

# Flask for Render keep alive
app = Flask(__name__)

@app.route('/')
def home():
    return f"V42.1 LIVE - DRY_RUN={DRY_RUN} - {datetime.utcnow().isoformat()} - Bybit Top100 Scanner Running"

@app.route('/health')
def health():
    return "OK"

def run_flask():
    port = int(os.getenv("PORT", 10000))
    print(f"Flask web server running on port {port} for Render FREE plan", flush=True)
    app.run(host='0.0.0.0', port=port)

# --- TELEGRAM LOUD ALERT ---
import requests

def send_telegram_loud(msg, image_path=None):
    """LOUD ALERT - Forces notification with bells"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram env missing", flush=True)
        return
    try:
        # Add loud prefix if not already
        if "🔔🔔🔔" not in msg:
            loud_msg = f"🔔🔔🔔🔔🔔 SIGNAL ALERT 🔔🔔🔔🔔🔔\n\n{msg}\n\n👉 CHECK NOW! 👈"
        else:
            loud_msg = msg

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": loud_msg,
            "parse_mode": "Markdown",
            "disable_notification": False,  # FORCE sound!
        }
        r = requests.post(url, json=payload, timeout=15)
        print(f"Telegram sent: {r.status_code}", flush=True)

        # If image exists, send photo with same loud caption
        if image_path and os.path.exists(image_path):
            url_photo = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
            with open(image_path, 'rb') as f:
                files = {'photo': f}
                data = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": loud_msg[:1000],
                    "parse_mode": "Markdown",
                    "disable_notification": False
                }
                r2 = requests.post(url_photo, data=data, files=files, timeout=20)
                print(f"Telegram photo sent: {r2.status_code}", flush=True)

    except Exception as e:
        print(f"Telegram error: {e}", flush=True)

def send_telegram_simple(msg):
    send_telegram_loud(msg)

# --- BYBIT & CHoCH LOGIC (Same as your V42) ---
exchange = ccxt.bybit({
    'enableRateLimit': True,
    'options': {'defaultType': 'linear'}  # USDT perpetual
})

def get_top100_bybit():
    try:
        exchange.load_markets()
        tickers = exchange.fetch_tickers()
        # Filter USDT perpetual linear, sort by quoteVolume
        usdt = []
        for sym, t in tickers.items():
            if '/USDT' in sym and ':USDT' not in sym:  # spot filtered? we want perp
                # For Bybit linear, symbols like BTC/USDT:USDT
                continue
            if 'USDT:USDT' in sym or ':USDT' in sym:
                vol = t.get('quoteVolume') or t.get('baseVolume') or 0
                usdt.append((sym, vol))
        # Actually use linear perp list
        if len(usdt) < 50:
            # Fallback: use all USDT markets
            usdt = [(s, v.get('quoteVolume',0)) for s,v in tickers.items() if '/USDT' in s]
        usdt_sorted = sorted(usdt, key=lambda x: x[1], reverse=True)[:100]
        symbols = [s[0] for s in usdt_sorted]
        print(f"Bybit Top100 by volume: {len(symbols)}", flush=True)
        return symbols
    except Exception as e:
        print(f"Top100 error {e} {traceback.format_exc()}", flush=True)
        return ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT']  # fallback

def fetch_ohlcv(symbol, timeframe, limit=100):
    try:
        return exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    except Exception as e:
        print(f"OHLCV err {symbol} {e}", flush=True)
        return []

def detect_choch(ohlcv_4h, ohlcv_1h):
    """Simplified CHoCH detection - your original logic placeholder"""
    if len(ohlcv_4h) < 20 or len(ohlcv_1h) < 20:
        return None
    try:
        df4 = pd.DataFrame(ohlcv_4h, columns=['t','o','h','l','c','v'])
        df1 = pd.DataFrame(ohlcv_1h, columns=['t','o','h','l','c','v'])
        # Example CHoCH: 4H bullish BOS then 1H CHoCH
        last_4h = df4.iloc[-5:]
        # Simple: higher highs in 4H
        if df4['h'].iloc[-1] > df4['h'].iloc[-10:-1].max():
            # Check 1H break
            if df1['c'].iloc[-1] > df1['h'].iloc[-10:-1].max():
                return {"side": "LONG", "entry": df1['c'].iloc[-1]}
            if df1['c'].iloc[-1] < df1['l'].iloc[-10:-1].min():
                return {"side": "SHORT", "entry": df1['c'].iloc[-1]}
        return None
    except:
        return None

state = {"scans":0, "balance":100.0}

def scanner_loop():
    print(f"=== V4.2 BYBIT Top100 4H->1H 5% Risk 5x DRY_RUN={DRY_RUN} ===", flush=True)
    send_telegram_simple(f"🚀 *V42.1 Bot Started LOUD ALERT Mode*\nDRY_RUN={DRY_RUN}\nTop100 Bybit\nScan every 5min\n{datetime.utcnow().isoformat()}\n\n🔔 You will get LOUD alerts for signals!")
    
    while True:
        try:
            symbols = get_top100_bybit()
            print(f"Balance ${state['balance']:.2f} Risk ${state['balance']*RISK_PCT:.2f}", flush=True)
            print(f"\n--- Scan {state.get('scans',0)+1} {datetime.utcnow().isoformat()} Top{len(symbols)} Bybit ---", flush=True)
            
            signals = 0
            for sym in symbols:
                ohlcv_4h = fetch_ohlcv(sym, '4h', 100)
                ohlcv_1h = fetch_ohlcv(sym, '1h', 100)
                choch = detect_choch(ohlcv_4h, ohlcv_1h)
                if choch:
                    signals += 1
                    side = choch['side']
                    entry = choch['entry']
                    sl = entry * 0.98 if side=="LONG" else entry * 1.02
                    tp = entry * 1.04 if side=="LONG" else entry * 0.96
                    
                    # LOUD MESSAGE
                    msg = f"✅ *{side} SIGNAL: {sym}*\n\nEntry: `{entry}`\nSL: `{sl:.4f}`\nTP: `{tp:.4f}`\nRisk: 5% | Lev: 5x\nTimeframe: 4H→1H CHoCH\nExchange: Bybit\nDRY_RUN: {DRY_RUN}"
                    
                    print(f"{msg}", flush=True)
                    send_telegram_loud(msg)
                    
                time.sleep(0.2)  # rate limit
            
            print(f"Scan done. Signals {signals}. Sleep 5min", flush=True)
            state['scans'] += 1
            
        except Exception as e:
            print(f"Scanner error: {e} {traceback.format_exc()}", flush=True)
        
        time.sleep(SCAN_INTERVAL_SEC)

if __name__ == "__main__":
    # Start Flask in thread
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()
    # Start scanner
    time.sleep(2)
    scanner_loop()
