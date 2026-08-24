"""
V42.3 - WITH TELEGRAM COMMANDS /status /scan /pnl /balance
"""
import os, time, traceback, threading, requests, ccxt, pandas as pd
from datetime import datetime
from flask import Flask

DRY_RUN = True
RISK_PCT = 0.05
LEVERAGE = 5
SCAN_INTERVAL_SEC = 300
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

app = Flask(__name__)
@app.route('/')
def home():
    return f"V42.3 COMMANDS LIVE DRY_RUN={DRY_RUN} {datetime.utcnow().isoformat()}"
@app.route('/health')
def health():
    return "OK"
def run_flask():
    port = int(os.getenv("PORT", 10000))
    print(f"Flask running on {port}", flush=True)
    app.run(host='0.0.0.0', port=port)

state = {"scans":0, "balance":100.0, "last_scan": "never", "signals_today":0, "pnl":0.0, "positions":[]}

def send_telegram_loud(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram env missing", flush=True)
        return
    try:
        if "LOUD ALERT" not in msg and "SIGNAL" in msg:
            loud_msg = "🔔🔔🔔🔔🔔 LOUD ALERT 🔔🔔🔔🔔🔔\n\n" + msg + "\n\nCHECK NOW!"
        else:
            loud_msg = msg
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": loud_msg, "parse_mode": "Markdown", "disable_notification": False}
        r = requests.post(url, json=payload, timeout=15)
        print(f"Telegram sent: {r.status_code}", flush=True)
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)

# --- COMMAND HANDLER ---
last_update_id = 0
def telegram_command_loop():
    global last_update_id
    print("Telegram command listener started - listening for /status /scan /pnl", flush=True)
    while True:
        try:
            if not TELEGRAM_TOKEN:
                time.sleep(10)
                continue
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={last_update_id+1}&timeout=30"
            r = requests.get(url, timeout=35)
            data = r.json()
            if not data.get("ok"):
                time.sleep(5)
                continue
            for upd in data.get("result", []):
                last_update_id = upd["update_id"]
                msg = upd.get("message", {})
                text = msg.get("text", "").strip().lower()
                chat_id = str(msg.get("chat", {}).get("id", ""))
                # Only respond to owner
                if chat_id != str(TELEGRAM_CHAT_ID):
                    continue
                print(f"Command received: {text}", flush=True)
                if text.startswith("/status"):
                    reply = f"🤖 *V42.3 Status*\nDRY_RUN: {DRY_RUN}\nScans: {state['scans']}\nLast scan: {state['last_scan']}\nBalance: ${state['balance']:.2f}\nSignals today: {state['signals_today']}\nPnL: ${state['pnl']:.2f}\nLive: Yes ✅\nTime: {datetime.utcnow().isoformat()}"
                    send_telegram_loud(reply)
                elif text.startswith("/scan"):
                    reply = f"🔍 Manual scan triggered! Scanning Top100 Bybit now...\nYou will get signals in ~1 min"
                    send_telegram_loud(reply)
                    # trigger scan in separate thread
                    threading.Thread(target=do_one_scan, daemon=True).start()
                elif text.startswith("/pnl") or text.startswith("/balance"):
                    reply = f"💰 *Paper Trading Stats*\nBalance: ${state['balance']:.2f}\nRisk per trade: ${state['balance']*RISK_PCT:.2f} (5%)\nPnL: ${state['pnl']:.2f}\nTotal scans: {state['scans']}\nPositions: {len(state['positions'])}\nDRY_RUN: {DRY_RUN}\nMode: Paper trading - no real money"
                    send_telegram_loud(reply)
                elif text.startswith("/help"):
                    reply = "Commands:\n/status - bot status\n/scan - trigger manual scan now\n/pnl - paper trading stats\n/balance - same as pnl\n/help - this help"
                    send_telegram_loud(reply)
        except Exception as e:
            print(f"Command loop error {e}", flush=True)
            time.sleep(5)

exchange = ccxt.bybit({'enableRateLimit': True, 'options': {'defaultType': 'linear'}})

def get_top100_bybit():
    try:
        tickers = exchange.fetch_tickers()
        usdt = []
        for sym, t in tickers.items():
            if ':USDT' in sym:
                vol = t.get('quoteVolume') or 0
                usdt.append((sym, vol))
        usdt_sorted = sorted(usdt, key=lambda x: x[1], reverse=True)[:100]
        symbols = [s[0] for s in usdt_sorted]
        print(f"Bybit Top100 by volume: {len(symbols)}", flush=True)
        return symbols if symbols else ['BTC/USDT:USDT', 'ETH/USDT:USDT']
    except Exception as e:
        print(f"Top100 error {e}", flush=True)
        return ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT']

def fetch_ohlcv(symbol, timeframe, limit=100):
    try:
        return exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    except:
        return []

def detect_choch(ohlcv_4h, ohlcv_1h):
    if len(ohlcv_4h) < 20 or len(ohlcv_1h) < 20:
        return None
    try:
        df4 = pd.DataFrame(ohlcv_4h, columns=['t','o','h','l','c','v'])
        df1 = pd.DataFrame(ohlcv_1h, columns=['t','o','h','l','c','v'])
        if df4['h'].iloc[-1] > df4['h'].iloc[-10:-1].max():
            if df1['c'].iloc[-1] > df1['h'].iloc[-10:-1].max():
                return {"side": "LONG", "entry": float(df1['c'].iloc[-1])}
            if df1['c'].iloc[-1] < df1['l'].iloc[-10:-1].min():
                return {"side": "SHORT", "entry": float(df1['c'].iloc[-1])}
        return None
    except:
        return None

def do_one_scan():
    try:
        symbols = get_top100_bybit()
        state["last_scan"] = datetime.utcnow().isoformat()
        print(f"Balance ${state['balance']:.2f} Risk ${state['balance']*RISK_PCT:.2f}", flush=True)
        print(f"--- Manual Scan {datetime.utcnow().isoformat()} Top{len(symbols)} ---", flush=True)
        signals = 0
        for sym in symbols:
            ohlcv_4h = fetch_ohlcv(sym, '4h', 100)
            ohlcv_1h = fetch_ohlcv(sym, '1h', 100)
            choch = detect_choch(ohlcv_4h, ohlcv_1h)
            if choch:
                signals += 1
                state["signals_today"] += 1
                side = choch['side']
                entry = choch['entry']
                sl = entry * 0.98 if side=="LONG" else entry * 1.02
                tp = entry * 1.04 if side=="LONG" else entry * 0.96
                msg = f"✅ {side} SIGNAL: {sym}\nEntry: {entry}\nSL: {sl:.4f}\nTP: {tp:.4f}\nRisk: 5% Lev: 5x\n4H->1H CHoCH Bybit DRY_RUN={DRY_RUN}"
                print(msg, flush=True)
                send_telegram_loud(msg)
            time.sleep(0.2)
        print(f"Scan done. Signals {signals}.", flush=True)
        state['scans'] += 1
        if signals==0:
            send_telegram_loud(f"Scan finished - No signals this time. Top100 checked. Balance ${state['balance']:.2f}")
    except Exception as e:
        print(f"Scan error: {e} {traceback.format_exc()}", flush=True)

def scanner_loop():
    print(f"=== V4.2 BYBIT Top100 4H->1H 5% Risk 5x DRY_RUN={DRY_RUN} ===", flush=True)
    startup_msg = f"🚀 V42.3 Bot Started WITH COMMANDS\nDRY_RUN={DRY_RUN}\nTop100 Bybit\nScan every 5min\nCommands: /status /scan /pnl /help\n{datetime.utcnow().isoformat()}\nYou will get LOUD alerts!"
    send_telegram_loud(startup_msg)
    # start command listener thread
    threading.Thread(target=telegram_command_loop, daemon=True).start()
    while True:
        do_one_scan()
        time.sleep(SCAN_INTERVAL_SEC)

if __name__ == "__main__":
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()
    time.sleep(2)
    scanner_loop()
