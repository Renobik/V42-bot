"""
V44.3 BITGET Top100 4H->1H CHoCH with RISK-FREE + AUTO GOOGLE SHEETS
- Bitget Futures ONLY - PAPER TRADING
- Unlimited trades (only when all risk-free)
- Risk-Free at +2%
- Scan every 4 HOURS (not 5 mins)
"""
import ccxt
import pandas as pd
import time
import requests
import threading
from datetime import datetime, timezone
from flask import Flask
import os

# ========== CONFIG ==========
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
GOOGLE_SHEET_WEBHOOK = os.getenv("GOOGLE_SHEET_WEBHOOK", "")

DRY_RUN = True # PAPER TRADING - NO REAL MONEY
RISK_PCT = 5.0
RISK_FREE_TRIGGER_PCT = 2.0
LEVERAGE = 5
SCAN_INTERVAL = 14400 # 4 HOURS = 14400 seconds

# ========== GLOBALS ==========
app = Flask(__name__)

# ========== V44.3 BITGET ONLY ==========
def get_env(*names, default=""):
    for n in names:
        v = os.getenv(n)
        if v:
            return v.strip()
    return default

EXCHANGE_NAME = "BITGET"
apiKey = get_env("BITGET_API_KEY", "API_KEY", "BYBIT_API_KEY")
secret = get_env("BITGET_API_SECRET", "API_SECRET", "BYBIT_API_SECRET")
password = get_env("BITGET_PASSPHRASE", "PASSPHRASE", "API_PASSPHRASE")

exchange_config = {
    'apiKey': apiKey,
    'secret': secret,
    'password': password,
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
}

print(f"V44.3 CONFIG: Exchange={EXCHANGE_NAME} Key={'YES' if apiKey else 'NO'} Passphrase={'YES' if password else 'NO'}")

exchange = ccxt.bitget(exchange_config)

state = {
    "positions": {},
    "balance": 100.0,
    "last_scan": None,
    "total_pnl": 0.0
}

def log(msg):
    print(f"{datetime.now(timezone.utc).isoformat()} {msg}")

# ========== TELEGRAM ==========
def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
        log(f"Telegram sent: {msg[:80]}")
    except Exception as e:
        log(f"Telegram error: {e}")

def send_to_sheets(text):
    if not GOOGLE_SHEET_WEBHOOK:
        return
    try:
        requests.post(GOOGLE_SHEET_WEBHOOK, json={"message": {"text": text}}, timeout=5)
    except Exception as e:
        log(f"Sheets error: {e}")

# ========== BALANCE & MARKET ==========
def get_balance():
    if DRY_RUN:
        return state["balance"]
    try:
        bal = exchange.fetch_balance()
        return float(bal['USDT']['free']) if 'USDT' in bal else 100.0
    except:
        return state["balance"]

def get_top100():
    try:
        tickers = exchange.fetch_tickers()
        # Bitget tickers look like BTC/USDT:USDT
        usdt = [s for s in tickers.keys() if 'USDT' in s and tickers[s].get('quoteVolume') is not None]
        sorted_by_vol = sorted(usdt, key=lambda x: tickers[x].get('quoteVolume', 0) or 0, reverse=True)
        top = sorted_by_vol[:100]
        log(f"BITGET Top100 fetched: {len(top)} - Top is {top[0] if top else 'none'}")
        return top
    except Exception as e:
        log(f"Top100 error {e} - using fallback")
        return ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]

def fetch_ohlcv(symbol, timeframe, limit=100):
    try:
        return exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
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
    except:
        return None

# ========== TRADING LOGIC ==========
def can_open_new():
    if not state["positions"]:
        return True, "No open positions - can open"
    not_rf = [s for s, p in state["positions"].items() if not p.get('risk_free')]
    if not_rf:
        return False, f"Waiting risk-free: {','.join(not_rf)}"
    return True, f"All {len(state['positions'])} positions risk-free - can open"

def check_risk_free():
    for symbol, pos in list(state["positions"].items()):
        if pos.get('risk_free'):
            continue
        try:
            ticker = exchange.fetch_ticker(symbol)
            price = ticker['last']
            entry = pos['entry']
            side = pos['side']
            pnl_pct = ((price - entry)/entry*100*LEVERAGE) if side=="LONG" else ((entry-price)/entry*100*LEVERAGE)
            if pnl_pct >= RISK_FREE_TRIGGER_PCT:
                pos['risk_free'] = True
                pos['sl'] = entry
                msg = f"🔒 RISK-FREE {symbol} {side} +{pnl_pct:.2f}% SL->BE"
                send_telegram(msg)
        except Exception as e:
            log(f"RF check error {symbol} {e}")

def close_position(symbol, reason="manual"):
    pos = state["positions"].get(symbol)
    if not pos:
        return
    try:
        ticker = exchange.fetch_ticker(symbol)
        exit_price = ticker['last']
    except:
        exit_price = pos['entry']

    balance_before = pos.get('balance_at_entry', state["balance"])
    risk_money = balance_before * RISK_PCT / 100
    pnl = risk_money * 2 if reason!= "loss" else -risk_money

    new_balance = state["balance"] + pnl
    state["balance"] = new_balance
    state["total_pnl"] += pnl

    result = "W" if pnl > 0 else "L"
    side = pos['side']
    msg = f"✅ CLOSED {symbol} {side} {result} PnL ${pnl:.2f} Balance ${new_balance:.2f} Reason:{reason}"
    send_telegram(msg)
    send_to_sheets(f"CLOSED {symbol.replace('/','')} {pos['side']} {result} {pnl:.2f} Balance {new_balance:.2f} {datetime.now().isoformat()}")
    del state["positions"][symbol]

def open_position(symbol, side):
    can, why = can_open_new()
    if not can:
        return
    balance = get_balance()
    risk_money = balance * RISK_PCT / 100
    try:
        ticker = exchange.fetch_ticker(symbol)
        price = ticker['last']
    except:
        price = 0
    state["positions"][symbol] = {
        "entry": price, "side": side, "risk_free": False,
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "balance_at_entry": balance, "risk_money": risk_money
    }
    msg = f"🚀 NEW {symbol} {side} @ {price} Risk ${risk_money:.2f} Balance ${balance:.2f}\n{why}"
    send_telegram(msg)
    send_to_sheets(f"OPEN {symbol.replace('/','')} {side} @ {price} Risk {risk_money:.2f} Balance {balance:.2f}")

def scan():
    log(f"--- Scan {datetime.now().isoformat()} BITGET Top100 ---")
    top = get_top100()
    balance = get_balance()
    can, why = can_open_new()
    log(f"Balance ${balance:.2f} Positions: {len(state['positions'])} Can open? {can} - {why}")
    check_risk_free()
    signals = 0
    for sym in top:
        if sym in state["positions"]:
            continue
        side = detect_choch(sym)
        if side:
            signals += 1
            open_position(sym, side)
            if len(state["positions"]) >= 20:
                break
    state["last_scan"] = datetime.now(timezone.utc).isoformat()
    log(f"Scan done. Signals {signals}")

# ========== TELEGRAM COMMANDS ==========
def telegram_polling():
    offset = 0
    log("Telegram listener started")
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={offset}&timeout=20"
            r = requests.get(url, timeout=25).json()
            for upd in r.get('result', []):
                offset = upd['update_id'] + 1
                text = upd.get('message', {}).get('text', '')
                if text.startswith('/status'):
                    rf = sum(1 for p in state["positions"].values() if p.get('risk_free'))
                    total = len(state["positions"])
                    can, why = can_open_new()
                    send_telegram(f"V44.3 BITGET Status\nDRY_RUN={DRY_RUN} (PAPER)\nBalance ${state['balance']:.2f}\nPositions {total} RF {rf}/{total}\nLast scan: {state['last_scan']}\nCan open? {can}\n{why}\nScan: Every 4H")
                elif text.startswith('/scan'):
                    threading.Thread(target=scan).start()
                    send_telegram("🔍 Manual Bitget scan starting...")
                elif text.startswith('/pnl'):
                    send_telegram(f"PnL Total ${state['total_pnl']:.2f} Balance ${state['balance']:.2f}")
                elif text.startswith('/close_all'):
                    for s in list(state["positions"].keys()):
                        close_position(s, "close_all")
                    send_telegram("All closed")
                elif text.startswith('/help'):
                    send_telegram("Commands: /status /scan /pnl /close_all /help\nV44.3 BITGET PAPER - Scans every 4H")
        except Exception as e:
            log(f"Telegram polling error {e}")
            time.sleep(5)

@app.route('/')
def home():
    return f"V44.3 BITGET LIVE DRY_RUN={DRY_RUN} PAPER Balance:{state['balance']:.2f} Pos:{len(state['positions'])} {datetime.now().isoformat()}"

if __name__ == '__main__':
    startup_msg = f"""V44.3 Bot Started BITGET PAPER TRADING
DRY_RUN=True (No real money)
Top100 Bitget Futures
Rule: New trade only when all positions risk-free
Risk-Free: +{RISK_FREE_TRIGGER_PCT}%
Scan every 4H
Commands: /status /scan /pnl /close_all /help
{datetime.now(timezone.utc).isoformat()}
You will get LOUD alerts!"""
    send_telegram(startup_msg)
    threading.Thread(target=telegram_polling, daemon=True).start()
    def loop():
        while True:
            try:
                scan()
            except Exception as e:
                log(f"Loop error {e}")
            time.sleep(SCAN_INTERVAL)
    threading.Thread(target=loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 10000)))
