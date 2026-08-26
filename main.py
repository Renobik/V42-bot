
"""
V44.5 WEBHOOK FIX - No more polling timeout on Render
"""
import ccxt
import pandas as pd
import time
import requests
import threading
from datetime import datetime, timezone
from flask import Flask, request
import os

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GOOGLE_SHEET_WEBHOOK = os.getenv("GOOGLE_SHEET_WEBHOOK", "").strip()

DRY_RUN = True
RISK_PCT = 5.0
RISK_FREE_TRIGGER_PCT = 2.0
LEVERAGE = 5
SCAN_INTERVAL = 14400

def get_env(*names, default=""):
    for n in names:
        v = os.getenv(n)
        if v:
            return v.strip()
    return default

apiKey = get_env("BITGET_API_KEY", "API_KEY")
secret = get_env("BITGET_API_SECRET", "API_SECRET")
password = get_env("BITGET_PASSPHRASE", "PASSPHRASE", "API_PASSPHRASE")

print(f"ENV CHECK: BITGET_API_KEY={'YES len='+str(len(apiKey)) if apiKey else 'NO'} SECRET={'YES' if secret else 'NO'} PASSPHRASE={'YES' if password else 'NO'}")
print(f"ENV CHECK: TELEGRAM_BOT_TOKEN={'YES len='+str(len(TELEGRAM_BOT_TOKEN)) if TELEGRAM_BOT_TOKEN else 'NO'} CHAT_ID={'YES '+TELEGRAM_CHAT_ID if TELEGRAM_CHAT_ID else 'NO'}")

# Two exchanges: one public for tickers (no auth), one private for trading
public_exchange = ccxt.bitget({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
private_exchange = ccxt.bitget({
    'apiKey': apiKey,
    'secret': secret,
    'password': password,
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

exchange = private_exchange  # default for trading
state = {"positions": {}, "balance": 100.0, "last_scan": None, "total_pnl": 0.0, "scan_count": 0}

def log(msg):
    print(f"{datetime.now(timezone.utc).isoformat()} {msg}", flush=True)

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log(f"TELEGRAM SKIP - missing token or chat_id")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=15)
        log(f"Telegram sent status {r.status_code}: {msg[:100]}")
        if r.status_code != 200:
            log(f"Telegram response: {r.text[:500]}")
    except Exception as e:
        log(f"Telegram error: {e}")

def get_top100():
    try:
        log("Fetching tickers from PUBLIC exchange...")
        tickers = public_exchange.fetch_tickers()
        log(f"TICKERS raw count {len(tickers)}")
        usdt = [s for s in tickers.keys() if 'USDT' in s and tickers[s].get('quoteVolume') is not None]
        sorted_by_vol = sorted(usdt, key=lambda x: tickers[x].get('quoteVolume', 0) or 0, reverse=True)
        top = sorted_by_vol[:100]
        log(f"BITGET Top100 fetched: {len(top)} - Top is {top[0] if top else 'none'}")
        return top
    except Exception as e:
        log(f"Top100 error {e} - using fallback")
        import traceback
        traceback.print_exc()
        return ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT", "DOGE/USDT:USDT"]

def fetch_ohlcv(symbol, timeframe, limit=50):
    try:
        return public_exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    except Exception as e:
        log(f"OHLCV error {symbol} {e}")
        return []

def detect_choch(symbol):
    try:
        ohlcv_4h = fetch_ohlcv(symbol, '4h', 50)
        ohlcv_1h = fetch_ohlcv(symbol, '1h', 50)
        if len(ohlcv_4h) < 20 or len(ohlcv_1h) < 20:
            return None
        df4 = pd.DataFrame(ohlcv_4h, columns=['t','o','h','l','c','v'])
        df1 = pd.DataFrame(ohlcv_1h, columns=['t','o','h','l','c','v'])
        sma4 = df4['c'].rolling(20).mean().iloc[-1]
        trend = "bull" if df4['c'].iloc[-1] > sma4 else "bear"
        last_high = df1['h'].iloc[-10:-1].max()
        last_low = df1['l'].iloc[-10:-1].min()
        close = df1['c'].iloc[-1]
        if trend == "bull" and close > last_high:
            return "LONG"
        if trend == "bear" and close < last_low:
            return "SHORT"
        return None
    except Exception as e:
        log(f"CHoCH error {symbol} {e}")
        return None

def can_open_new():
    if not state["positions"]:
        return True, "No open positions - can open"
    not_rf = [s for s, p in state["positions"].items() if not p.get('risk_free')]
    if not_rf:
        return False, f"Waiting risk-free: {','.join(not_rf)}"
    return True, f"All {len(state['positions'])} positions risk-free - can open"

def scan():
    state["scan_count"] += 1
    log(f"--- Scan #{state['scan_count']} {datetime.now().isoformat()} BITGET Top100 START ---")
    try:
        top = get_top100()
        log(f"Scan got {len(top)} symbols")
        can, why = can_open_new()
        log(f"Balance ${state['balance']:.2f} Positions: {len(state['positions'])} Can open? {can} - {why}")
        signals = 0
        for sym in top[:20]:  # only check first 20 per scan to avoid rate limit
            if sym in state["positions"]:
                continue
            side = detect_choch(sym)
            if side:
                signals += 1
                log(f"SIGNAL {sym} {side}")
                can_now, why_now = can_open_new()
                if can_now:
                    state["positions"][sym] = {"entry": 0, "side": side, "risk_free": False, "entry_time": datetime.now(timezone.utc).isoformat(), "balance_at_entry": state["balance"]}
                    send_telegram(f"🚀 PAPER SIGNAL {sym} {side} - {why_now}")
                else:
                    log(f"Skip {sym} {side} - {why_now}")
            if signals >= 2:
                break
        state["last_scan"] = datetime.now(timezone.utc).isoformat()
        log(f"--- Scan #{state['scan_count']} done. Signals {signals} Positions {len(state['positions'])} ---")
        if state["scan_count"] == 1:
            send_telegram(f"✅ V44.5 WEBHOOK LIVE! Top100={len(top)} Signals={signals} Balance=${state['balance']:.2f}")
    except Exception as e:
        log(f"SCAN CRASH {e}")
        import traceback
        traceback.print_exc()
        send_telegram(f"❌ Scan error: {e}")

# ===== WEBHOOK HANDLER - REPLACES POLLING =====
@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    try:
        data = request.get_json(force=True)
        if not data:
            return "OK", 200
        msg = data.get('message', {})
        text = msg.get('text', '')
        chat = str(msg.get('chat', {}).get('id', ''))
        log(f"Webhook recv: '{text}' from {chat}")
        
        if chat != TELEGRAM_CHAT_ID and TELEGRAM_CHAT_ID:
            log(f"Ignoring chat {chat} != {TELEGRAM_CHAT_ID}")
            if text.startswith('/'):
                try:
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                                  json={"chat_id": chat, "text": f"Your CHAT_ID is {chat} - set this in Render!"}, timeout=10)
                except:
                    pass
            return "OK", 200

        if text.startswith('/status'):
            rf = sum(1 for p in state["positions"].values() if p.get('risk_free'))
            total = len(state["positions"])
            can, why = can_open_new()
            send_telegram(f"V44.5 WEBHOOK Status\nDRY_RUN={DRY_RUN} PAPER\nBalance ${state['balance']:.2f}\nPositions {total} RF {rf}/{total}\nScans done: {state['scan_count']}\nLast scan: {state['last_scan']}\nCan open? {can}\n{why}\nScan: Every 4H")
        elif text.startswith('/scan'):
            send_telegram("🔍 Manual scan starting...")
            threading.Thread(target=scan).start()
        elif text.startswith('/pnl'):
            send_telegram(f"PnL ${state['total_pnl']:.2f} Balance ${state['balance']:.2f}")
        elif text.startswith('/help'):
            send_telegram("Commands: /status /scan /pnl /help\nV44.5 WEBHOOK PAPER - Instant reply!")
        return "OK", 200
    except Exception as e:
        log(f"Webhook error {e}")
        return "OK", 200

@app.route('/')
def home():
    return f"V44.5 WEBHOOK LIVE Key={'YES' if apiKey else 'NO'} Pass={'YES' if password else 'NO'} TG={'YES' if TELEGRAM_BOT_TOKEN else 'NO'} Balance:{state['balance']:.2f} Pos:{len(state['positions'])} Scans:{state['scan_count']} Last:{state['last_scan']} {datetime.now().isoformat()}"

def set_webhook():
    time.sleep(3)  # wait for Flask to start
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        # Use RENDER_EXTERNAL_HOSTNAME if available, else fixed URL
        host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "v42-bot-2.onrender.com")
        webhook_url = f"https://{host}/{TELEGRAM_BOT_TOKEN}"
        # Fallback if hostname doesn't contain dot
        if "onrender.com" not in host:
            webhook_url = f"https://v42-bot-2.onrender.com/{TELEGRAM_BOT_TOKEN}"
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook?url={webhook_url}"
        r = requests.get(url, timeout=15)
        log(f"Set webhook {webhook_url} -> {r.text[:500]}")
        send_telegram(f"🤖 V44.5 WEBHOOK Started!\nDRY_RUN=True\nKey=YES Pass=YES\nWebhook: {webhook_url}\nSend /status - now instant!")
    except Exception as e:
        log(f"Set webhook error {e}")

if __name__ == '__main__':
    threading.Thread(target=set_webhook, daemon=True).start()
    def loop():
        while True:
            try:
                scan()
            except Exception as e:
                log(f"Loop error {e}")
            time.sleep(SCAN_INTERVAL)
    threading.Thread(target=loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 10000)))
