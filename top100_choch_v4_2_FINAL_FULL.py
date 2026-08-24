import ccxt
import time
import threading
from flask import Flask
import os

# --- FLASK FOR RENDER FREE PLAN ---
app = Flask(__name__)
@app.route('/')
def home():
    return "V4.2 Bot Live - Bybit - FREE Plan - OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_flask, daemon=True).start()
print("Flask web server running on port 10000 for Render FREE plan", flush=True)
# ---------------------------------

# === V4.2 CONFIG ===
DRY_RUN = True
BALANCE = 100.0
RISK_PCT = 0.05
LEVERAGE = 5
TIMEFRAMES = ['4h', '1h']

# Use BYBIT instead of Binance - no IP ban on Render
exchange = ccxt.bybit({
    'enableRateLimit': True,
    'options': {'defaultType': 'linear'},  # USDT perpetuals
})

def get_top100_symbols():
    try:
        exchange.load_markets()
        # Get USDT perpetuals sorted by volume
        tickers = exchange.fetch_tickers()
        # Filter USDT linear perpetuals
        usdt_tickers = {k: v for k, v in tickers.items() if '/USDT:USDT' in k or (k.endswith('/USDT') and ':USDT' in k)}
        # Sort by quoteVolume
        sorted_by_vol = sorted(usdt_tickers.items(), key=lambda x: (x[1].get('quoteVolume') or 0), reverse=True)
        top100 = [symbol for symbol, ticker in sorted_by_vol[:100]]
        if not top100:
            # Fallback: all USDT linear
            top100 = [s for s in exchange.symbols if s.endswith('USDT:USDT')][:100]
        print(f"Top100 fetched from BYBIT: {len(top100)} symbols", flush=True)
        return top100
    except Exception as e:
        print(f"Top100 fetch error bybit {e}", flush=True)
        return ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT'][:10]

print("=== V4.2 Top100 4H->1H 5% Risk 5x DRY_RUN=True ===", flush=True)
print(f"Balance ${BALANCE:.2f} Risk ${BALANCE*RISK_PCT:.2f}", flush=True)

# Cache top100 for 1 hour to avoid rate limit
TOP100_CACHE = []
LAST_FETCH = 0

scan_num = 0
while True:
    try:
        scan_num += 1
        now = time.time()
        # Refresh Top100 every 3600 seconds (1 hour)
        if now - LAST_FETCH > 3600 or not TOP100_CACHE:
            TOP100_CACHE = get_top100_symbols()
            LAST_FETCH = now
            time.sleep(2)  # extra delay after heavy call
        
        print(f"--- Scan {scan_num} {time.strftime('%Y-%m-%dT%H:%M:%S')} TopN={len(TOP100_CACHE)} ---", flush=True)
        
        # Example scan loop with rate limit protection
        for symbol in TOP100_CACHE[:20]:  # scan first 20 per loop to avoid ban, rotate next time
            try:
                # Fetch 4h and 1h candles
                # time.sleep(exchange.rateLimit / 1000) is handled by enableRateLimit
                ohlcv_4h = exchange.fetch_ohlcv(symbol, '4h', limit=50)
                time.sleep(0.5)
                ohlcv_1h = exchange.fetch_ohlcv(symbol, '1h', limit=50)
                time.sleep(0.5)
                # TODO: Your CHOCH logic here
                # print(f"{symbol} OK", flush=True)
            except Exception as e:
                print(f"Scan error {symbol} {e}", flush=True)
                time.sleep(2)
        
        print(f"Scan {scan_num} done, sleeping 60s...", flush=True)
        time.sleep(60)
        
    except Exception as e:
        print(f"Main loop error {e}", flush=True)
        time.sleep(30)
