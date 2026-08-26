"""
V44.11 FIXED - Paper Trading Bot with Persistence
Fixes: High=0 bug, SL trailing down bug, spin-down loss bug
"""
import os, json, time, threading
from flask import Flask, request
import requests

# --- CONFIG ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8977489087:AAEULx0KS8QQM1nA2Q2hU92ALLxStwOUE0g") # PUT IN ENV VAR!
CHAT_ID = os.getenv("CHAT_ID", "200")
BALANCE_FILE = "state.json"

RF_PCT = 2.0      # Risk Free
BE_PCT = 0.5      # BE +0.5%
TRAIL_PCT = 1.5   # Trail 1.5%
TP_PCT = 6.0
INIT_SL_PCT = 2.0 # Initial SL -2% before RF (IMPORTANT)

app = Flask(__name__)

def load_state():
    if os.path.exists(BALANCE_FILE):
        try:
            with open(BALANCE_FILE, 'r') as f:
                return json.load(f)
        except: pass
    return {"balance": 100.0, "positions": {}, "today_signals": 0}

def save_state(state):
    with open(BALANCE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

state = load_state()

def tg_send(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg}, timeout=5)
        print(f"TG {CHAT_ID}: {msg[:100]}")
    except Exception as e:
        print(f"TG Error: {e}")

def open_position(symbol, entry_price):
    # FIX 1: High = entry, not 0
    state["positions"][symbol] = {
        "symbol": symbol,
        "side": "LONG",
        "entry": entry_price,
        "high": entry_price, # CRITICAL FIX
        "low": entry_price,
        "rf_hit": False,
        "sl": entry_price * (1 - INIT_SL_PCT/100), # FIX: Initial SL
        "entry_time": time.time()
    }
    save_state(state)
    tg_send(f"🚀 PAPER {symbol} LONG\nEntry: {entry_price}\nBal ${state['balance']:.2f}\nRF +{RF_PCT}% -> BE+{BE_PCT}% + TRAIL {TRAIL_PCT}% -> TP +{TP_PCT}% / SL -{INIT_SL_PCT}%")

def update_positions(current_prices):
    # current_prices = {"ETH/USDT:USDT": 2478.91, ...}
    to_close = []
    for symbol, pos in state["positions"].items():
        cur = current_prices.get(symbol)
        if not cur: continue

        pnl_pct = (cur - pos["entry"]) / pos["entry"] * 100

        # Update High - FIX
        if cur > pos["high"]:
            pos["high"] = cur
        
        high_pct = (pos["high"] - pos["entry"]) / pos["entry"] * 100

        # 1. RF HIT
        if not pos["rf_hit"] and pnl_pct >= RF_PCT:
            pos["rf_hit"] = True
            pos["sl"] = pos["entry"] * (1 + BE_PCT/100)
            tg_send(f"🔒 {symbol} RF +{RF_PCT}% HIT! SL -> BE+{BE_PCT}%: {pos['sl']:.2f}")

        # 2. TRAILING - Only after RF and only UP
        if pos["rf_hit"]:
            new_sl = pos["high"] * (1 - TRAIL_PCT/100)
            if new_sl > pos["sl"]: # FIX 2: Never move SL down
                pos["sl"] = new_sl
                # tg_send(f"📈 {symbol} TRAIL UP: SL {pos['sl']:.2f} | High +{high_pct:.2f}%")

        # 3. TP
        if pnl_pct >= TP_PCT:
            to_close.append((symbol, f"TP +{TP_PCT}%", pnl_pct))
            continue

        # 4. SL / TRAIL hit
        if cur <= pos["sl"]:
            reason = "TRAIL SL" if pos["rf_hit"] else f"INIT SL -{INIT_SL_PCT}%"
            to_close.append((symbol, reason, pnl_pct))

    for symbol, reason, pnl_pct in to_close:
        pos = state["positions"].pop(symbol)
        profit = state["balance"] * 0.1 * (pnl_pct/100) # Example 10% position size
        state["balance"] += profit
        tg_send(f"{'✅' if pnl_pct>0 else '❌'} CLOSE {symbol} {reason} PnL {pnl_pct:.2f}%\nBal ${state['balance']:.2f}")
    
    if to_close:
        save_state(state)
    else:
        # Still save high updates
        save_state(state)

# --- DUMMY SCANNER - REPLACE WITH YOUR REAL SIGNAL LOGIC ---
def get_signals():
    # TODO: Replace with your real Top100 scan
    # Return dict of prices for open positions + maybe a new signal
    # For demo, just returns fixed price
    prices = {}
    for sym in state["positions"]:
        # In real bot, fetch real price from exchange here
        prices[sym] = state["positions"][sym]["high"] * 1.001 # simulate up
    return prices

def scanner_loop():
    print("--- SCAN START --- V44.11 Fixed Started!")
    tg_send(f"🤖 V44.11 Started! BE+{BE_PCT}% + TRAIL {TRAIL_PCT}% FIXED\nRF +{RF_PCT}% -> Lock +{BE_PCT}% -> Trail {TRAIL_PCT}% behind high -> TP +{TP_PCT}%")
    while True:
        try:
            print(f"SCAN #{state['today_signals']+1} Bal ${state['balance']:.2f} Pos:{len(state['positions'])}")
            prices = get_signals()
            
            # Example: Open a test trade if none open
            # REMOVE THIS IN LIVE - this is just to show logic
            # if len(state["positions"]) == 0 and state["today_signals"] < 1:
            #     open_position("ETH/USDT:USDT", 2478.91)
            #     state["today_signals"] += 1
            #     save_state(state)

            update_positions(prices)
            time.sleep(30)
        except Exception as e:
            print(f"Scanner Error: {e}")
            time.sleep(10)

# --- FLASK KEEPALIVE + TELEGRAM WEBHOOK ---
@app.route('/')
def home():
    return f"V44.11 OK | Bal ${state['balance']:.2f} | Pos {len(state['positions'])}", 200

@app.route(f'/{TELEGRAM_TOKEN}', methods=['POST'])
def webhook():
    data = request.json
    # Simple status command
    if "message" in data:
        text = data["message"].get("text","")
        if "/status" in text or "status" in text.lower():
            msg = f"📈 V44.11 Status\nBal ${state['balance']:.2f} PnL $0.00\nPos {len(state['positions'])} RF {sum(1 for p in state['positions'].values() if p['rf_hit'])}"
            for s,p in state["positions"].items():
                pnl = (p['high']-p['entry'])/p['entry']*100
                msg += f"\n{s}: LONG RF={p['rf_hit']} PnL={pnl:.1f}% High={((p['high']/p['entry']-1)*100):.1f}% SL={p['sl']:.2f}"
            tg_send(msg)
    return "ok", 200

if __name__ == "__main__":
    # Start scanner in background
    t = threading.Thread(target=scanner_loop, daemon=True)
    t.start()
    # Start Flask
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
