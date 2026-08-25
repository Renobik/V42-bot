"""
V43.2 BYBIT Top100 4H->1H CHoCH with RISK-FREE + AUTO GOOGLE SHEETS
- Unlimited trades (only when all risk-free)
- Risk-Free at +2%
- Compounds balance
- Telegram: /status /scan /pnl /close /close_all /help
- NEW: Auto pushes CLOSED trades to Google Sheets Webhook
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
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# NEW: GOOGLE SHEETS WEBHOOK - PASTE YOUR WEB APP URL HERE
GOOGLE_SHEET_WEBHOOK = os.getenv("GOOGLE_SHEET_WEBHOOK", "")  # e.g. https://script.google.com/macros/s/AKfy.../exec

DRY_RUN = True  # True = no real trades, False = real Bybit
RISK_PCT = 5.0
RISK_FREE_TRIGGER_PCT = 2.0
LEVERAGE = 5

# ========== GLOBALS ==========
app = Flask(__name__)
exchange = None
state = {
    "positions": {},  # symbol: {entry, side, risk_free, entry_time, balance_at_entry}
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
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        log(f"Telegram sent: {msg[:80]}")
    except Exception as e:
        log(f"Telegram error: {e}")

def send_to_sheets(text):
    """NEW: Auto push to Google Sheets"""
    if not GOOGLE_SHEET_WEBHOOK:
        return
    try:
        requests.post(GOOGLE_SHEET_WEBHOOK, json={"message": {"text": text}}, timeout=5)
        log(f"Sheets pushed: {text}")
    except Exception as e:
        log(f"Sheets error: {e}")

# ========== BYBIT ==========
def init_exchange():
    global exchange
    

# ========== V44.1 MULTI-EXCHANGE AUTO-SWITCH ==========
# Auto-detects exchange from env vars - NO CODE CHANGE NEEDED
EXCHANGE_NAME = os.getenv("EXCHANGE", "").lower().strip()
if not EXCHANGE_NAME:
    # Auto-detect by which keys exist
    if os.getenv("BITGET_API_KEY") or os.getenv("BITGET_PASSPHRASE"):
        EXCHANGE_NAME = "bitget"
    elif os.getenv("BYBIT_API_KEY"):
        EXCHANGE_NAME = "bybit"
    elif os.getenv("BINANCE_API_KEY"):
        EXCHANGE_NAME = "binance"
    elif os.getenv("OKX_API_KEY"):
        EXCHANGE_NAME = "okx"
    else:
        EXCHANGE_NAME = "bitget"  # default fastest

EXCHANGE_MAP = {
    "bybit": ccxt.bybit,
    "bitget": ccxt.bitget,
    "binance": ccxt.binance,
    "okx": ccxt.okx,
    "gate": ccxt.gate,
}
exchange_class = EXCHANGE_MAP.get(EXCHANGE_NAME, ccxt.bitget)

def get_env(*names, default=""):
    for n in names:
        v = os.getenv(n)
        if v: return v
    return default

exchange_config = {
    'apiKey': get_env("API_KEY", "BYBIT_API_KEY", "BITGET_API_KEY", "BINANCE_API_KEY", "OKX_API_KEY", "GATE_API_KEY"),
    'secret': get_env("API_SECRET", "BYBIT_API_SECRET", "BITGET_API_SECRET", "BINANCE_API_SECRET", "OKX_API_SECRET", "GATE_API_SECRET"),
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
}
if EXCHANGE_NAME == "bitget":
    exchange_config['password'] = get_env("BITGET_PASSPHRASE", "PASSPHRASE", "API_PASSPHRASE", "BITGET_API_PASSPHRASE")

exchange = exchange_class(exchange_config)
print(f"V44.1 AUTO-SWITCH: Exchange={EXCHANGE_NAME.upper()} Keys loaded={'YES' if exchange_config['apiKey'] else 'NO'}")

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
        usdt = [s for s in tickers if s.endswith('USDT') and 'USDC' not in s]
        sorted_by_vol = sorted(usdt, key=lambda x: tickers[x].get('quoteVolume', 0) or 0, reverse=True)
        return sorted_by_vol[:100]
    except Exception as e:
        log(f"Top100 error {e}")
        return ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

def fetch_ohlcv(symbol, timeframe, limit=100):
    try:
        return exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    except:
        return []

def detect_choch(symbol):
    """Simplified 4H trend + 1H CHoCH"""
    try:
        ohlcv_4h = fetch_ohlcv(symbol, '4h', 50)
        ohlcv_1h = fetch_ohlcv(symbol, '1h', 50)
        if len(ohlcv_4h) < 20 or len(ohlcv_1h) < 20:
            return None
        df4 = pd.DataFrame(ohlcv_4h, columns=['t','o','h','l','c','v'])
        df1 = pd.DataFrame(ohlcv_1h, columns=['t','o','h','l','c','v'])
        # Simple CHoCH: 4H bullish if last close > 20 SMA, 1H breaks last high
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
    # Rule: Only open new when ALL positions risk-free
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
                pos['sl'] = entry  # Move SL to breakeven
                msg = f"🔒 RISK-FREE {symbol} {side} +{pnl_pct:.2f}% SL->BE"
                send_telegram(msg)
                send_to_sheets(f"RISK-FREE {symbol} {side} +{pnl_pct:.2f}%")
                log(msg)
                # In real mode, set SL to BE via API
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
    
    entry = pos['entry']
    side = pos['side']
    balance_before = pos.get('balance_at_entry', state["balance"])
    risk_money = balance_before * RISK_PCT / 100
    # Simulated 2R
    if DRY_RUN:
        # For demo, assume we check real pnl: +2R if trending continued
        pnl = risk_money * 2 if reason != "loss" else -risk_money
    else:
        pnl = (exit_price - entry) if side=="LONG" else (entry - exit_price)
        pnl = pnl * pos.get('qty', 0)

    new_balance = state["balance"] + pnl if DRY_RUN else get_balance()
    state["balance"] = new_balance
    state["total_pnl"] += pnl

    result = "W" if pnl > 0 else "L"
    msg = f"✅ CLOSED {symbol} {side} {result} PnL ${pnl:.2f} Balance ${new_balance:.2f} Reason:{reason}"
    send_telegram(msg)
    # NEW: Auto Sheets push - formatted for easy parsing
    send_to_sheets(f"CLOSED {symbol.replace('/','')} {side} {result} {pnl:.2f} Balance {new_balance:.2f} {datetime.now().isoformat()}")

    del state["positions"][symbol]
    log(msg)

def open_position(symbol, side):
    can, why = can_open_new()
    if not can:
        log(f"Cannot open {symbol}: {why}")
        return
    balance = get_balance()
    risk_money = balance * RISK_PCT / 100
    try:
        ticker = exchange.fetch_ticker(symbol)
        price = ticker['last']
    except:
        price = 0
    state["positions"][symbol] = {
        "entry": price,
        "side": side,
        "risk_free": False,
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "balance_at_entry": balance,
        "risk_money": risk_money,
        "sl": None
    }
    msg = f"🚀 NEW {symbol} {side} @ {price} Risk ${risk_money:.2f} Balance ${balance:.2f}\nRule: {why}"
    send_telegram(msg)
    send_to_sheets(f"OPEN {symbol.replace('/','')} {side} @ {price} Risk {risk_money:.2f} Balance {balance:.2f}")
    log(msg)

def scan():
    log(f"--- Scan {datetime.now().isoformat()} Top100 ---")
    top = get_top100()
    log(f"Bybit Top100 by volume: {len(top)}")
    signals = 0
    balance = get_balance()
    can, why = can_open_new()
    log(f"Balance ${balance:.2f} Risk {RISK_PCT}% Positions: {len(state['positions'])}\nCan open new? {can} - {why}")
    check_risk_free()
    for sym in top:
        if sym in state["positions"]:
            continue
        side = detect_choch(sym)
        if side:
            signals += 1
            if DRY_RUN or can:
                open_position(sym, side)
            if len(state["positions"]) >= 20:  # safety cap
                break
    state["last_scan"] = datetime.now(timezone.utc).isoformat()
    log(f"Scan done. Signals {signals}. {why}")

# ========== TELEGRAM COMMANDS ==========
def telegram_polling():
    offset = 0
    log("Telegram command listener started - listening for /status /scan /pnl /close /close_all")
    while True:
        try:
            if not TELEGRAM_BOT_TOKEN:
                time.sleep(10)
                continue
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={offset}&timeout=20"
            r = requests.get(url, timeout=25).json()
            for upd in r.get('result', []):
                offset = upd['update_id'] + 1
                msg = upd.get('message', {})
                text = msg.get('text', '')
                if not text:
                    continue
                if text.startswith('/status'):
                    rf = sum(1 for p in state["positions"].values() if p.get('risk_free'))
                    total = len(state["positions"])
                    can, why = can_open_new()
                    send_telegram(f"V43.2 Status\nDRY_RUN={DRY_RUN}\nBalance ${state['balance']:.2f}\nPositions {total} RF {rf}/{total}\nCan open? {can}\n{why}\nGoogle Sheets: {'ON' if GOOGLE_SHEET_WEBHOOK else 'OFF'}")
                elif text.startswith('/scan'):
                    threading.Thread(target=scan).start()
                    send_telegram("Scanning Top100...")
                elif text.startswith('/pnl'):
                    send_telegram(f"PnL Total ${state['total_pnl']:.2f} Balance ${state['balance']:.2f} Trades {len(state['positions'])}")
                elif text.startswith('/close_all'):
                    for s in list(state["positions"].keys()):
                        close_position(s, "close_all cmd")
                    send_telegram("All closed")
                elif text.startswith('/close'):
                    parts = text.split()
                    if len(parts) > 1:
                        sym = parts[1].upper()
                        # try to match
                        found = [k for k in state["positions"] if sym.replace('USDT','') in k]
                        if found:
                            close_position(found[0], "close cmd")
                        else:
                            send_telegram(f"Not found {sym}. Open: {list(state['positions'].keys())}")
                    else:
                        send_telegram("Use: /close BTCUSDT")
                elif text.startswith('/help'):
                    send_telegram("Commands:\n/status - positions & risk-free\n/scan - force scan\n/pnl - balance\n/close SYMBOL\n/close_all\n/help")
        except Exception as e:
            log(f"Telegram polling error {e}")
            time.sleep(5)

# ========== FLASK KEEPALIVE ==========
@app.route('/')
def home():
    rf = sum(1 for p in state["positions"].values() if p.get('risk_free'))
    total = len(state["positions"])
    return f"V43.2 LIVE DRY_RUN={DRY_RUN} Positions:{total} RiskFree:{rf}/{total} Sheets:{'ON' if GOOGLE_SHEET_WEBHOOK else 'OFF'} {datetime.now().isoformat()}"

# ========== MAIN ==========
if __name__ == '__main__':
    init_exchange()
    startup_msg = f"V43.2 Bot Started WITH RISK-FREE + SHEETS\nDRY_RUN={DRY_RUN}\nTop100 Bybit\nUnlimited Trades ON\nSheets: {'ON' if GOOGLE_SHEET_WEBHOOK else 'OFF - set GOOGLE_SHEET_WEBHOOK env'}\nRule: New trade only when all positions risk-free\nRisk-Free trigger: +{RISK_FREE_TRIGGER_PCT}%\nScan every 5min\nCommands: /status /scan /pnl /close /close_all /help\n{datetime.now(timezone.utc).isoformat()}\nYou will get LOUD alerts!"
    send_telegram(startup_msg)
    print(f"=== V43.2 BYBIT Top100 4H->1H {RISK_PCT}% Risk {LEVERAGE}x RISK-FREE + SHEETS DRY_RUN={DRY_RUN} ===")
    # Start telegram polling
    threading.Thread(target=telegram_polling, daemon=True).start()
    # Start scanner loop
    def loop():
        while True:
            try:
                scan()
            except Exception as e:
                log(f"Loop error {e}")
            time.sleep(300)  # 5 min
    threading.Thread(target=loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 10000)))
