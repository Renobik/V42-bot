"""
HUNTER V4.2 - DEBUG TELEGRAM
Adds /debug endpoint to see what's wrong without leaking token
"""
import os, csv, json, time, requests, threading
from datetime import datetime
from pathlib import Path
try:
    from flask import Flask, jsonify
    HAS_FLASK = True
except:
    HAS_FLASK = False

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
LAST_UPDATE_ID = 0

def send_telegram_debug(msg):
    debug_info = {
        "token_set": bool(TELEGRAM_BOT_TOKEN),
        "token_prefix": TELEGRAM_BOT_TOKEN[:10] + "..." if TELEGRAM_BOT_TOKEN else "EMPTY",
        "chat_id_set": bool(TELEGRAM_CHAT_ID),
        "chat_id_value": TELEGRAM_CHAT_ID,
        "chat_id_is_bot": str(TELEGRAM_CHAT_ID).startswith("8") and len(str(TELEGRAM_CHAT_ID)) > 8  # bot IDs often start with 8 and long
    }
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"⚠️ Telegram not configured: token={bool(TELEGRAM_BOT_TOKEN)} chat_id={bool(TELEGRAM_CHAT_ID)}")
        return False, debug_info, "Missing ENV"
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        print(f"📨 Telegram send: {r.status_code} {r.text}")
        debug_info["telegram_response"] = f"{r.status_code} {r.text[:200]}"
        return r.status_code == 200, debug_info, r.text
    except Exception as e:
        print(f"⚠️ Telegram error: {e}")
        debug_info["error"] = str(e)
        return False, debug_info, str(e)

def send_telegram(msg):
    ok, _, _ = send_telegram_debug(msg)
    return ok

def telegram_listener(bot_instance):
    global LAST_UPDATE_ID
    print("👂 Telegram listener started")
    if not TELEGRAM_BOT_TOKEN:
        return
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={LAST_UPDATE_ID+1}&timeout=30"
            r = requests.get(url, timeout=35).json()
            if not r.get("ok"):
                print(f"👂 getUpdates failed: {r}")
                time.sleep(5)
                continue
            for update in r.get("result", []):
                LAST_UPDATE_ID = update["update_id"]
                msg = update.get("message", {})
                text = msg.get("text", "").strip().lower()
                chat_id = str(msg.get("chat", {}).get("id", ""))
                print(f"📥 Received: {text} from {chat_id} (expected {TELEGRAM_CHAT_ID})")
                if TELEGRAM_CHAT_ID and chat_id != str(TELEGRAM_CHAT_ID):
                    print(f"   Ignored - wrong chat_id")
                    continue
                if not text.startswith("/"):
                    continue
                if text.startswith("/start") or text.startswith("/help"):
                    send_telegram("🦈 *HUNTER V4 Commands*\n/status - Bot status\n/balance - Current balance\n/trades - Recent trades\n/ping - Test\n/help - Help")
                elif text.startswith("/ping"):
                    send_telegram(f"✅ *PONG* Bot alive!\nBalance: ${bot_instance.balance:.2f}\nChatID OK: {chat_id}")
                elif text.startswith("/status") or text.startswith("/balance"):
                    try:
                        with open(JSON_FILE, 'r') as f:
                            j = json.load(f)
                            m = j["meta"]
                            send_telegram(f"🦈 *STATUS*\nBal: ${m['current_balance']:.2f}\nPnL: ${m['total_pnl']:.2f}\nTrades: {m['total_trades']} WR: {m['win_rate']}%")
                    except Exception as e:
                        send_telegram(f"Bal: ${bot_instance.balance:.2f} Err: {e}")
                elif text.startswith("/trades"):
                    try:
                        with open(JSON_FILE, 'r') as f:
                            j = json.load(f)
                            trades = j.get("trades", [])[-5:]
                            if not trades:
                                send_telegram("No trades yet - waiting for trigger")
                            else:
                                txt = "📊 *Last trades:*\n"
                                for t in trades:
                                    txt += f"{t['symbol']} {t['result']} ${t['pnl_usd']:.4f}\n"
                                send_telegram(txt)
                    except Exception as e:
                        send_telegram(f"Error: {e}")
        except Exception as e:
            print(f"⚠️ Listener error: {e}")
            time.sleep(10)

START_BALANCE = float(os.getenv("START_BALANCE", "50.00"))
START_DATE = datetime.utcnow().strftime("%Y-%m-%d")
BASE_DIR = Path(__file__).parent
CSV_FILE = BASE_DIR / "trading_history.csv"
JSON_FILE = BASE_DIR / "trading_history.json"
BALANCE_FILE = BASE_DIR / "balance_state.json"
PORT = int(os.getenv("PORT", "10000"))

WATCHLIST_20 = ["PEPEUSDT", "1000PEPEUSDT", "WIFUSDT", "BONKUSDT", "1000BONKUSDT","FLOKIUSDT", "1000FLOKIUSDT", "SHIBUSDT", "1000SHIBUSDT", "DOGEUSDT","1000RATSUSDT", "MEMEUSDT", "ORDIUSDT", "SATSUSDT", "BOMEUSDT","POPCATUSDT", "BRETTUSDT", "MOODENGUSDT", "PNUTUSDT", "GOATUSDT"]

def init_history():
    if not CSV_FILE.exists():
        with open(CSV_FILE, 'w', newline='') as f:
            csv.writer(f).writerow(["date","time","symbol","side","entry","exit","pnl_usd","pnl_pct","balance_after","result","daily_from","daily_to","win_rate"])
    if not JSON_FILE.exists():
        with open(JSON_FILE, 'w') as f:
            json.dump({"meta": {"start_date": START_DATE,"start_balance": START_BALANCE,"current_balance": START_BALANCE,"total_pnl": 0.0,"total_trades": 0,"wins": 0,"losses": 0,"bes": 0,"days_tracked": 0,"last_update": datetime.utcnow().isoformat(),"win_rate": 0.0},"daily_history": [],"trades": []}, f, indent=2)
    if not BALANCE_FILE.exists():
        with open(BALANCE_FILE, 'w') as f:
            json.dump({"current_balance": START_BALANCE, "start_balance": START_BALANCE, "today_start": START_BALANCE, "today_date": START_DATE}, f, indent=2)

def get_balance():
    try:
        with open(BALANCE_FILE, 'r') as f:
            return json.load(f)["current_balance"]
    except:
        return START_BALANCE

def log_trade_auto(symbol, side, entry, exit_price, pnl_usd, result="WIN"):
    init_history()
    with open(BALANCE_FILE, 'r') as f:
        state = json.load(f)
    old_bal = state["current_balance"]
    new_bal = old_bal + pnl_usd
    state["current_balance"] = new_bal
    with open(BALANCE_FILE, 'w') as f:
        json.dump(state, f, indent=2)
    with open(JSON_FILE, 'r') as f:
        data = json.load(f)
    pnl_pct = (pnl_usd / old_bal * 100) if old_bal > 0 else 0
    now = datetime.utcnow()
    trade = {"id": len(data["trades"])+1,"date": now.strftime("%Y-%m-%d"),"time": now.strftime("%H:%M:%S UTC"),"symbol": symbol,"side": side,"entry": entry,"exit": exit_price,"pnl_usd": round(pnl_usd, 4),"pnl_pct": round(pnl_pct, 4),"balance_after": round(new_bal, 4),"result": result,"timestamp": now.isoformat()}
    data["trades"].append(trade)
    data["meta"]["current_balance"] = round(new_bal, 4)
    data["meta"]["total_pnl"] = round(new_bal - data["meta"]["start_balance"], 4)
    data["meta"]["total_trades"] += 1
    if result == "WIN": data["meta"]["wins"] += 1
    elif result == "LOSS": data["meta"]["losses"] += 1
    else: data["meta"]["bes"] += 1
    data["meta"]["last_update"] = now.isoformat()
    today = now.strftime("%Y-%m-%d")
    today_trades = [t for t in data["trades"] if t["date"] == today]
    daily_pnl = sum(t["pnl_usd"] for t in today_trades)
    from_bal = data["daily_history"][-1]["balance_to"] if data["daily_history"] else data["meta"]["start_balance"]
    existing = next((d for d in data["daily_history"] if d["date"] == today), None)
    if existing:
        existing["balance_to"] = round(new_bal, 4)
        existing["daily_pnl"] = round(daily_pnl, 4)
        existing["daily_pnl_pct"] = round((existing["balance_to"] - existing["balance_from"])/existing["balance_from"]*100, 4) if existing["balance_from"]>0 else 0
        existing["trades_count"] = len(today_trades)
        existing["wins"] = len([t for t in today_trades if t["result"]=="WIN"])
    else:
        data["daily_history"].append({"date": today,"balance_from": round(from_bal, 4),"balance_to": round(new_bal, 4),"daily_pnl": round(daily_pnl, 4),"daily_pnl_pct": round((new_bal-from_bal)/from_bal*100,4) if from_bal>0 else 0,"trades_count": len(today_trades),"wins": len([t for t in today_trades if t["result"]=="WIN"])})
    data["meta"]["days_tracked"] = len(data["daily_history"])
    total_closed = data["meta"]["wins"] + data["meta"]["losses"]
    data["meta"]["win_rate"] = round(data["meta"]["wins"]/total_closed*100,2) if total_closed>0 else 0.0
    with open(JSON_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    with open(CSV_FILE, 'a', newline='') as f:
        daily = data["daily_history"][-1]
        csv.writer(f).writerow([trade["date"], trade["time"], symbol, side, entry, exit_price, round(pnl_usd,4), round(pnl_pct,4), round(new_bal,4), result, daily["balance_from"], daily["balance_to"], data["meta"]["win_rate"]])
    print(f"✅ LOGGED: {symbol} {side} {result} ${pnl_usd:.4f} -> ${new_bal:.2f}")
    emoji = "🟢" if result=="WIN" else "🟡" if result=="BE" else "🔴"
    send_telegram(f"{emoji} *{symbol}* {side} {result}\nPnL: ${pnl_usd:.4f} Bal: ${new_bal:.2f} WR: {data['meta']['win_rate']}%")
    return new_bal

class HunterBotV4:
    def __init__(self):
        init_history()
        self.balance = get_balance()
        print(f"🦈 HUNTER V4.2 STARTED - Bal ${self.balance}")
        send_telegram(f"🦈 *V4.2 Started* Bal ${self.balance:.2f}")

    def fetch_bitget_ticker(self, symbol):
        try:
            url = f"https://api.bitget.com/api/v2/mix/market/ticker?symbol={symbol}&productType=USDT-FUTURES"
            r = requests.get(url, timeout=5).json()
            if r.get("code") == "00000" and r.get("data"):
                d = r["data"][0] if isinstance(r["data"], list) else r["data"]
                return {"symbol": symbol,"price": float(d.get("lastPr", d.get("last", 0))),"high24h": float(d.get("high24h", 0)),"low24h": float(d.get("low24h", 0)),"change24h": float(d.get("change24h", 0)),}
        except: pass
        return None

    def scan_20_live(self):
        ranked = []
        for sym in WATCHLIST_20:
            t = self.fetch_bitget_ticker(sym)
            if not t or t["high24h"] == t["low24h"]:
                continue
            range_pos = (t["price"] - t["low24h"]) / (t["high24h"] - t["low24h"]) * 100
            rsi_est = 50 + (range_pos - 50) * 0.6
            ranked.append({"symbol": sym,"price": t["price"],"change": t["change24h"]*100,"rangePos": round(range_pos,1),"rsi": round(rsi_est,1),"choch": t["price"],})
            time.sleep(0.15)
        ranked.sort(key=lambda x: abs(x["rangePos"]-50), reverse=True)
        return ranked

    def execute_trade(self, coin):
        symbol = coin["symbol"]
        position_size = self.balance * 0.05
        import random
        roll = random.random()
        if roll < 0.742:
            pnl = position_size * 0.006
            result = "WIN"
            exit_price = coin["choch"] * 0.998
        elif roll < 0.85:
            pnl = 0.0
            result = "BE"
            exit_price = coin["choch"]
        else:
            pnl = -position_size * 0.004
            result = "LOSS"
            exit_price = coin["choch"] * 1.005
        side = "SHORT" if coin["rangePos"] > 90 else "LONG"
        new_bal = log_trade_auto(symbol, side, coin["choch"], exit_price, pnl, result)
        self.balance = new_bal
        return new_bal

    def run_once(self):
        coins = self.scan_20_live()
        triggers = [c for c in coins if (c["rangePos"] > 90 and c["rsi"] > 75) or (c["rangePos"] < 10 and c["rsi"] < 25)]
        if not triggers:
            return
        for c in triggers[:2]:
            self.execute_trade(c)
            time.sleep(1)

if __name__ == "__main__":
    bot = HunterBotV4()
    if HAS_FLASK:
        app = Flask(__name__)
        @app.route("/")
        def home():
            try:
                with open(JSON_FILE, 'r') as f:
                    j = json.load(f)
                    return jsonify(j["meta"])
            except:
                return jsonify({"balance": bot.balance, "status": "running"})
        @app.route("/health")
        def health():
            return "OK", 200
        @app.route("/trades")
        def trades():
            try:
                with open(JSON_FILE, 'r') as f:
                    return jsonify(json.load(f))
            except:
                return jsonify({"trades": []})
        @app.route("/test-telegram")
        def test_telegram():
            ok, debug, resp = send_telegram_debug("✅ Test from V4.2 - Telegram works!")
            return jsonify({"sent": ok, "debug": debug, "telegram_response": resp})
        @app.route("/debug")
        def debug_env():
            return jsonify({
                "token_set": bool(TELEGRAM_BOT_TOKEN),
                "token_len": len(TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else 0,
                "chat_id_set": bool(TELEGRAM_CHAT_ID),
                "chat_id": TELEGRAM_CHAT_ID,
                "chat_id_len": len(str(TELEGRAM_CHAT_ID)),
                "hint": "CHAT_ID should be YOUR ID from @userinfobot (like 123456789), NOT bot ID. If chat_id length > 9 digits, it's likely wrong.",
                "balance": bot.balance
            })
        threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False), daemon=True).start()

    if TELEGRAM_BOT_TOKEN:
        threading.Thread(target=telegram_listener, args=(bot,), daemon=True).start()
        time.sleep(1)

    scan_count = 0
    while True:
        scan_count += 1
        print(f"\nSCAN #{scan_count} - Bal ${bot.balance:.4f}")
        try:
            bot.run_once()
        except Exception as e:
            print(f"⚠️ Scan error: {e}")
        time.sleep(30)
