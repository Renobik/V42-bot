import ccxt, time, requests, threading
from datetime import datetime
import os
from flask import Flask
import traceback

# === CONFIG ===
DRY_RUN = True # Change to False for real money
RISK_PCT = 5
LEVERAGE = 5
RISK_FREE_TRIGGER_PCT = 1.0 # +1% = move SL to breakeven
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_SECRET = os.getenv("BYBIT_SECRET", "")

# State with Risk-Free tracking - UNLIMITED TRADES
state = {
    "balance": 100.0,
    "positions": [], # {"symbol": "SPCX/USDT:USDT", "entry": 136.5, "sl": 133.7, "risk_free": False}
    "last_scan": None,
    "total_signals": 0,
    "risk_free_count": 0
}

app = Flask(__name__)

@app.route("/")
def home():
    rf = sum(1 for p in state["positions"] if p["risk_free"])
    total = len(state["positions"])
    return f"V43.1 LIVE DRY_RUN={DRY_RUN} Positions:{total} RiskFree:{rf}/{total} {datetime.utcnow().isoformat()}"

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg} # NO Markdown to avoid 400
        r = requests.post(url, json=data, timeout=10)
        print(f"Telegram sent: {r.status_code} {r.text[:200]}", flush=True)
        return r.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)
        return False

def get_top100_bybit():
    try:
        ex = ccxt.bybit()
        tickers = ex.fetch_tickers()
        sorted_vol = sorted(tickers.items(), key=lambda x: (x[1].get('quoteVolume') or 0), reverse=True)
        top = [s for s, t in sorted_vol if '/USDT' in s and ':USDT' in s][:100]
        print(f"Bybit Top100 by volume: {len(top)}", flush=True)
        return top if top else ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "SPCX/USDT:USDT"]
    except Exception as e:
        print(f"Top100 error: {e}", flush=True)
        return ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "SPCX/USDT:USDT"]

def check_and_update_risk_free(exchange):
    for pos in state["positions"]:
        if pos["risk_free"]:
            continue
        try:
            ticker = exchange.fetch_ticker(pos["symbol"])
            curr_price = ticker['last']
            profit_pct = (curr_price - pos["entry"]) / pos["entry"] * 100
            if profit_pct >= RISK_FREE_TRIGGER_PCT:
                new_sl = pos["entry"] * 1.001
                pos["sl"] = new_sl
                pos["risk_free"] = True
                state["risk_free_count"] += 1
                msg = f"🔒 RISK-FREE: {pos['symbol']}\nEntry: {pos['entry']}\nNow: {curr_price:.4f} (+{profit_pct:.2f}%)\nSL -> breakeven: {new_sl:.4f}\n✅ Can now open new trade!"
                send_telegram(msg)
                print(f"RISK-FREE UPDATED: {pos['symbol']}", flush=True)
        except Exception as e:
            print(f"Risk-free check error {pos['symbol']}: {e}", flush=True)

def can_open_new_position():
    if len(state["positions"]) == 0:
        return True, "No open positions - can open"
    not_risk_free = [p for p in state["positions"] if not p["risk_free"]]
    if len(not_risk_free) == 0:
        return True, f"All {len(state['positions'])} positions risk-free ✅"
    else:
        symbols = ", ".join([p["symbol"] for p in not_risk_free])
        return False, f"Blocked: {len(not_risk_free)} still at risk: {symbols}"

def open_position(symbol, entry, sl, tp, side="LONG"):
    pos = {
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "risk_free": False,
        "opened_at": datetime.utcnow().isoformat()
    }
    state["positions"].append(pos)
    state["total_signals"] += 1
    msg = f"🔔🔔🔔🔔🔔\n✅ {side} SIGNAL: {symbol}\nEntry: {entry}\nSL: {sl}\nTP: {tp}\nRisk: {RISK_PCT}% Lev: {LEVERAGE}x\n4H->1H CHoCH Bybit DRY_RUN={DRY_RUN}\nPositions: {len(state['positions'])} RiskFree: {sum(1 for p in state['positions'] if p['risk_free'])}\nUnlimited trades - Risk-Free Rule ON!"
    send_telegram(msg)
    print(f"{side} SIGNAL: {symbol} Entry: {entry} SL: {sl} TP: {tp}", flush=True)

def scan_and_signal():
    try:
        exchange = ccxt.bybit({
            'apiKey': BYBIT_API_KEY,
            'secret': BYBIT_SECRET,
            'enableRateLimit': True
        })
        symbols = get_top100_bybit()
        state["last_scan"] = datetime.utcnow().isoformat()
        print(f"--- Scan {state['last_scan']} Top{len(symbols)} ---", flush=True)
        print(f"Balance ${state['balance']:.2f} Risk {RISK_PCT}% Positions: {len(state['positions'])}", flush=True)
        check_and_update_risk_free(exchange)
        can_open, reason = can_open_new_position()
        print(f"Can open new? {can_open} - {reason}", flush=True)
        if not can_open:
            print(f"SKIP NEW TRADES: {reason}", flush=True)
            return 0
        signals = 0
        # === YOUR CHoCH LOGIC HERE ===
        print(f"Scan done. Signals {signals}. {reason}", flush=True)
        return signals
    except Exception as e:
        print(f"Scan error: {e}\n{traceback.format_exc()}", flush=True)
        return 0

def telegram_listener():
    print("Telegram command listener started - listening for /status /scan /pnl /close /close_all", flush=True)
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=30"
            r = requests.get(url, timeout=35)
            data = r.json()
            if data.get("ok") and data.get("result"):
                for update in data["result"]:
                    offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    text = msg.get("text", "").strip()
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    if chat_id!= str(TELEGRAM_CHAT_ID):
                        continue
                    if text.startswith("/status"):
                        rf = sum(1 for p in state["positions"] if p["risk_free"])
                        can_open, reason = can_open_new_position()
                        pos_list = "\n".join([f"- {p['symbol']} Entry:{p['entry']} {'🔒RF' if p['risk_free'] else '⚠️RISK'}" for p in state["positions"]]) or "No positions"
                        reply = f"V43.1 Status\nDRY_RUN={DRY_RUN}\nBalance: ${state['balance']}\nOpen: {len(state['positions'])} RiskFree: {rf}\nCan open new: {can_open}\n{reason}\n\nPositions:\n{pos_list}\n\nLast scan: {state['last_scan']}\nTotal signals: {state['total_signals']}"
                        send_telegram(reply)
                    elif text.startswith("/scan"):
                        send_telegram("🔍 Manual scan starting...")
                        scan_and_signal()
                    elif text.startswith("/pnl"):
                        rf = sum(1 for p in state["positions"] if p["risk_free"])
                        reply = f"V43.1 PnL\nBalance: ${state['balance']:.2f}\nOpen: {len(state['positions'])}\nRiskFree: {rf}\nRisk: {RISK_PCT}% Lev: {LEVERAGE}x\nUnlimited: ON"
                        send_telegram(reply)
                    elif text.startswith("/close_all"):
                        count = len(state["positions"])
                        if count == 0:
                            send_telegram("No positions to close")
                        else:
                            symbols = [p['symbol'] for p in state["positions"]]
                            state["positions"].clear()
                            send_telegram(f"✅ CLOSED ALL {count} positions:\n" + "\n".join(symbols) + f"\nNow can open new trades!")
                    elif text.startswith("/close"):
                        parts = text.split()
                        if len(parts) < 2:
                            if len(state["positions"]) == 0:
                                send_telegram("No open positions. Use: /close SYMBOL e.g. /close SPCX/USDT:USDT")
                            else:
                                pos_list = "\n".join([f"/close {p['symbol']}" for p in state["positions"]])
                                send_telegram(f"Open positions - tap to close:\n{pos_list}\n\nOr /close_all to close all")
                        else:
                            target = parts[1].upper()
                            to_close = []
                            for p in state["positions"]:
                                if target in p["symbol"].upper() or p["symbol"].upper() in target:
                                    to_close.append(p)
                            if not to_close:
                                to_close = [p for p in state["positions"] if p["symbol"] == parts[1]]
                            if to_close:
                                for p in to_close:
                                    state["positions"].remove(p)
                                send_telegram(f"✅ CLOSED {len(to_close)} position(s):\n" + "\n".join([p['symbol'] for p in to_close]) + f"\nRemaining: {len(state['positions'])}")
                            else:
                                send_telegram(f"❌ Not found: {target}\nOpen: " + ", ".join([p['symbol'] for p in state["positions"]]))
                    elif text.startswith("/help"):
                        send_telegram("V43.1 Commands:\n/status - show positions & risk-free\n/scan - manual scan\n/pnl - balance & risk\n/close SYMBOL - close one (e.g. /close SPCX)\n/close_all - close everything\n/help - this help\n\nRule: Unlimited trades, but new trade only when all positions are 🔒 Risk-Free (+1% profit SL->breakeven)")
        except Exception as e:
            print(f"Listener error: {e}\n{traceback.format_exc()}", flush=True)
            time.sleep(5)

if __name__ == "__main__":
    startup_msg = f"V43.1 Bot Started WITH RISK-FREE + CLOSE\nDRY_RUN={DRY_RUN}\nTop100 Bybit\nUnlimited Trades ON\nRule: New trade only when all positions risk-free\nRisk-Free trigger: +{RISK_FREE_TRIGGER_PCT}%\nScan every 5min\nCommands: /status /scan /pnl /close /close_all /help\n{datetime.utcnow().isoformat()}\nYou will get LOUD alerts!"
    send_telegram(startup_msg)
    print(f"=== V43.1 BYBIT Top100 4H->1H 5% Risk 5x RISK-FREE RULE DRY_RUN={DRY_RUN} ===", flush=True)
    t = threading.Thread(target=telegram_listener, daemon=True)
    t.start()
    def run_flask():
        app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    while True:
        scan_and_signal()
        time.sleep(300)
