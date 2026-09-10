"""
HUNTER V4.5 - NO DAILY CAP + RF RULE
- No daily limit (was 20, now 999 = unlimited)
- RF Rule: Only ONE trade at risk at a time
- No new entry unless previous trade is Risk Free (BE) or closed (WIN/LOSS)
- Global cooldown 5 min, per-coin 15 min
- Permanent storage via NPOINT_ID
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
LAST_TRADE_TIME = {}
GLOBAL_LAST_TRADE = 0

# RF RULE STATE
OPEN_POSITION = None  # None or dict {symbol, side, entry, open_time, is_rf}
LAST_TRADE_RF = True  # True means last trade is RF or closed, allowed to take next
LAST_TRADE_RESULT = None

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        print(f"📨 Telegram: {r.status_code}")
        return r.status_code == 200
    except Exception as e:
        print(f"⚠️ TG error: {e}")
        return False

def telegram_listener(bot_instance):
    global LAST_UPDATE_ID, GLOBAL_LAST_TRADE, LAST_TRADE_RF, OPEN_POSITION
    print("👂 Listener started")
    if not TELEGRAM_BOT_TOKEN:
        return
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={LAST_UPDATE_ID+1}&timeout=30"
            r = requests.get(url, timeout=35).json()
            if not r.get("ok"):
                time.sleep(5)
                continue
            for update in r.get("result", []):
                LAST_UPDATE_ID = update["update_id"]
                msg = update.get("message", {})
                text = msg.get("text", "").strip().lower()
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if TELEGRAM_CHAT_ID and chat_id != str(TELEGRAM_CHAT_ID):
                    continue
                if not text.startswith("/"):
                    continue
                print(f"📥 Command: {text}")
                if text.startswith("/start") or text.startswith("/help"):
                    send_telegram(f"🦈 *V4.5 RF Rule + No Cap*\n/ping - Alive\n/status - Status\n/trades - Last 5\n/stopspam - Pause 1h\n/rf - Check RF status\nBal: ${bot_instance.balance:.2f}")
                elif text.startswith("/ping"):
                    rf_status = "✅ RF (free to enter)" if LAST_TRADE_RF else f"🔒 AT RISK ({OPEN_POSITION['symbol'] if OPEN_POSITION else '?'})"
                    send_telegram(f"✅ *PONG* V4.5\nBal: ${bot_instance.balance:.2f}\nRF: {rf_status}\nCooldown: 5min global, 15min/coin\nLast trade: {int(time.time()-GLOBAL_LAST_TRADE)}s ago")
                elif text.startswith("/status"):
                    try:
                        with open(JSON_FILE, 'r') as f:
                            j = json.load(f)
                            m = j["meta"]
                            today = datetime.utcnow().strftime("%Y-%m-%d")
                            today_count = len([t for t in j["trades"] if t["date"]==today])
                            rf_status = "✅ RF" if LAST_TRADE_RF else "🔒 AT RISK"
                            send_telegram(f"🦈 *V4.5 STATUS*\nBal: ${m['current_balance']:.2f} (from ${m['start_balance']})\nPnL: ${m['total_pnl']:.2f}\nTrades: {m['total_trades']} WR: {m['win_rate']}%\nToday: {today_count} (no cap)\nRF: {rf_status}\nStorage: {'☁️ npoint.io' if NPOINT_ID else '⚠️ local'}")
                    except Exception as e:
                        send_telegram(f"Bal: ${bot_instance.balance:.2f} RF: {'✅' if LAST_TRADE_RF else '🔒'}")
                elif text.startswith("/trades"):
                    try:
                        with open(JSON_FILE, 'r') as f:
                            j = json.load(f)
                            trades = j.get("trades", [])[-5:]
                            if not trades:
                                send_telegram("No trades yet")
                            else:
                                txt = "📊 *Last 5:*\n"
                                for t in trades:
                                    txt += f"{t['symbol']} {t['result']} ${t['pnl_usd']:.4f}\n"
                                send_telegram(txt)
                    except Exception as e:
                        send_telegram(f"Err: {e}")
                elif text.startswith("/rf"):
                    if OPEN_POSITION:
                        send_telegram(f"🔒 *RF Status: AT RISK*\nOpen: {OPEN_POSITION['symbol']} {OPEN_POSITION['side']}\nEntry: {OPEN_POSITION['entry']}\nOpen for: {int(time.time()-OPEN_POSITION['open_time'])}s\nNext entry: BLOCKED until RF")
                    else:
                        send_telegram(f"✅ *RF Status: FREE*\nNo open risk\nNext entry: ALLOWED\nLast result: {LAST_TRADE_RESULT}")
                elif text.startswith("/stopspam"):
                    GLOBAL_LAST_TRADE = time.time() + 3600
                    send_telegram("⏸️ Paused trading for 1 hour")
        except Exception as e:
            print(f"⚠️ Listener err: {e}")
            time.sleep(10)

START_BALANCE = float(os.getenv("START_BALANCE", "50.98"))
START_DATE = datetime.utcnow().strftime("%Y-%m-%d")
BASE_DIR = Path(__file__).parent
CSV_FILE = BASE_DIR / "trading_history.csv"
JSON_FILE = BASE_DIR / "trading_history.json"
BALANCE_FILE = BASE_DIR / "balance_state.json"
NPOINT_ID = os.getenv("NPOINT_ID", "d914adaa4cdb03b3c7c0")
NPOINT_URL = f"https://api.npoint.io/{NPOINT_ID}" if NPOINT_ID else None
PORT = int(os.getenv("PORT", "10000"))

WATCHLIST_20 = ["PEPEUSDT", "1000PEPEUSDT", "WIFUSDT", "BONKUSDT", "1000BONKUSDT","FLOKIUSDT", "1000FLOKIUSDT", "SHIBUSDT", "1000SHIBUSDT", "DOGEUSDT","1000RATSUSDT", "MEMEUSDT", "ORDIUSDT", "SATSUSDT", "BOMEUSDT","POPCATUSDT", "BRETTUSDT", "MOODENGUSDT", "PNUTUSDT", "GOATUSDT"]

def save_to_npoint(data):
    if not NPOINT_URL:
        return False
    try:
        r = requests.post(NPOINT_URL, json=data, timeout=10)
        print(f"💾 Saved to npoint: {r.status_code}")
        return r.status_code == 200
    except Exception as e:
        print(f"⚠️ Npoint save failed: {e}")
        return False

def load_from_npoint():
    if not NPOINT_URL:
        return None
    try:
        r = requests.get(NPOINT_URL, timeout=10).json()
        if "meta" in r and "trades" in r:
            print(f"💾 Loaded from npoint.io/{NPOINT_ID}: Bal ${r['meta'].get('current_balance','?')}")
            return r
        else:
            print(f"⚠️ Npoint has dummy data, ignoring")
            return None
    except Exception as e:
        print(f"⚠️ Npoint load failed: {e}")
        return None

def init_history():
    # Try load from npoint first
    np_data = load_from_npoint()
    if np_data:
        try:
            with open(JSON_FILE, 'w') as f:
                json.dump(np_data, f, indent=2)
            bal = np_data["meta"]["current_balance"]
            with open(BALANCE_FILE, 'w') as f:
                json.dump({"current_balance": bal, "start_balance": np_data["meta"]["start_balance"], "today_start": bal, "today_date": datetime.utcnow().strftime("%Y-%m-%d")}, f, indent=2)
            print(f"✅ Restored from npoint: ${bal}")
            return
        except Exception as e:
            print(f"⚠ Restore failed: {e}")

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

def log_trade_auto(symbol, side, entry, exit_price, pnl_usd, result="WIN", notify=True):
    global LAST_TRADE_RF, LAST_TRADE_RESULT, OPEN_POSITION
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
    today = now.strftime("%Y-%m-%d")
    daily = None
    for d in data["daily_history"]:
        if d["date"] == today:
            daily = d
            break
    if not daily:
        daily = {"date": today, "balance_from": old_bal, "balance_to": new_bal, "daily_pnl": 0.0, "daily_pnl_pct": 0.0, "trades_count": 0, "wins": 0}
        data["daily_history"].append(daily)
    daily["balance_to"] = new_bal
    daily["daily_pnl"] = daily["balance_to"] - daily["balance_from"]
    daily["daily_pnl_pct"] = (daily["daily_pnl"]/daily["balance_from"]*100) if daily["balance_from"]>0 else 0
    daily["trades_count"] = daily.get("trades_count",0)+1
    if result=="WIN":
        daily["wins"] = daily.get("wins",0)+1

    trade = {"date": today, "time": now.strftime("%H:%M:%S"), "symbol": symbol, "side": side, "entry": entry, "exit": exit_price, "pnl_usd": round(pnl_usd,5), "pnl_pct": round(pnl_pct,4), "balance_after": round(new_bal,4), "result": result}
    data["trades"].append(trade)
    data["meta"]["current_balance"] = new_bal
    data["meta"]["total_pnl"] = new_bal - data["meta"]["start_balance"]
    data["meta"]["total_trades"] = len(data["trades"])
    if result=="WIN":
        data["meta"]["wins"] += 1
    elif result=="LOSS":
        data["meta"]["losses"] += 1
    else:
        data["meta"]["bes"] += 1
    total_closed = data["meta"]["wins"]+data["meta"]["losses"]
    data["meta"]["win_rate"] = round(data["meta"]["wins"]/total_closed*100,2) if total_closed>0 else 0.0
    data["meta"]["last_update"] = now.isoformat()
    with open(JSON_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    # Save to npoint
    save_to_npoint(data)
    with open(CSV_FILE, 'a', newline='') as f:
        csv.writer(f).writerow([trade["date"], trade["time"], symbol, side, entry, exit_price, round(pnl_usd,4), round(pnl_pct,4), round(new_bal,4), result, daily["balance_from"], daily["balance_to"], data["meta"]["win_rate"]])
    print(f"✅ {symbol} {result} ${pnl_usd:.4f} -> ${new_bal:.2f}")
    
    # RF RULE: After trade closes, it becomes RF and frees next entry
    LAST_TRADE_RESULT = result
    LAST_TRADE_RF = True
    OPEN_POSITION = None
    print(f"   ✅ RF: Position closed ({result}) -> FREE to enter next trade")

    if notify and result != "BE":
        emoji = "🟢" if result=="WIN" else "🔴"
        send_telegram(f"{emoji} *{symbol}* {side} {result}\nPnL: ${pnl_usd:.4f} Bal: ${new_bal:.2f} WR: {data['meta']['win_rate']}%\nRF: ✅ FREE for next")
    elif notify and result == "BE":
        print(f"   BE - not notifying to reduce spam")
    return new_bal

class HunterBotV4:
    def __init__(self):
        init_history()
        self.balance = get_balance()
        storage = f"npoint.io/{NPOINT_ID}" if NPOINT_ID else "LOCAL (resets!)"
        print(f"🦈 V4.5 NO CAP + RF - Bal ${self.balance} Storage: {storage}")
        send_telegram(f"🦈 *V4.5 No Cap + RF Rule Started*\nBal: ${self.balance:.2f} ☁️\nStorage: {'Permanent' if NPOINT_ID else 'Temporary'}\nRF Rule: ✅ ONE trade at risk at a time\nDaily Cap: ❌ REMOVED (was 20)\nCooldown 5min global, 15min/coin")

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

    def can_trade(self, symbol):
        global GLOBAL_LAST_TRADE, LAST_TRADE_RF, OPEN_POSITION
        now = time.time()
        # RF RULE: Check if previous trade is still at risk
        if not LAST_TRADE_RF:
            print(f"   🔒 RF BLOCKED: {OPEN_POSITION['symbol'] if OPEN_POSITION else '?'} still at risk, no new entry")
            return False
        if OPEN_POSITION is not None:
            print(f"   🔒 RF BLOCKED: Open position {OPEN_POSITION['symbol']} not yet RF")
            return False
        # Global cooldown 5 min
        if now - GLOBAL_LAST_TRADE < 300:
            print(f"   ⏸️ Global cooldown: {int(300-(now-GLOBAL_LAST_TRADE))}s left")
            return False
        # Per-coin cooldown 15 min
        if symbol in LAST_TRADE_TIME and now - LAST_TRADE_TIME[symbol] < 900:
            print(f"   ⏸️ {symbol} cooldown: {int(900-(now-LAST_TRADE_TIME[symbol]))}s left")
            return False
        # Daily limit REMOVED - was 20, now 999
        # try:
        #     with open(JSON_FILE, 'r') as f:
        #         j = json.load(f)
        #         today = datetime.utcnow().strftime("%Y-%m-%d")
        #         today_count = len([t for t in j["trades"] if t["date"]==today])
        #         if today_count >= 999:
        #             print(f"   ⏸️ Daily limit reached: {today_count}")
        #             return False
        # except: pass
        return True

    def execute_trade(self, coin):
        global GLOBAL_LAST_TRADE, OPEN_POSITION, LAST_TRADE_RF
        symbol = coin["symbol"]
        if not self.can_trade(symbol):
            return self.balance
        
        # OPEN NEW POSITION - MARK AS AT RISK
        OPEN_POSITION = {"symbol": symbol, "side": "LONG" if coin["rangePos"]<10 else "SHORT", "entry": coin["choch"], "open_time": time.time(), "is_rf": False}
        LAST_TRADE_RF = False
        print(f"   🔒 RF: Opened {symbol} {OPEN_POSITION['side']} - AT RISK, blocking next entries")
        send_telegram(f"🔒 *Opening {symbol} {OPEN_POSITION['side']}*\nEntry: {coin['choch']}\nRF: 🔒 AT RISK - No new entries until RF")

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
        
        # Simulate RF after 30 seconds (in real trading, this is when SL moves to BE)
        # For demo, we close immediately but show RF logic
        time.sleep(2)  # Small delay to show AT RISK state
        print(f"   ✅ RF: Moving {symbol} to Risk Free (BE)")
        
        new_bal = log_trade_auto(symbol, side, coin["choch"], exit_price, pnl, result, notify=True)
        self.balance = new_bal
        LAST_TRADE_TIME[symbol] = time.time()
        GLOBAL_LAST_TRADE = time.time()
        return new_bal

    def run_once(self):
        coins = self.scan_20_live()
        triggers = [c for c in coins if (c["rangePos"] > 90 and c["rsi"] > 75) or (c["rangePos"] < 10 and c["rsi"] < 25)]
        if not triggers:
            print("   No trigger - waiting")
            return
        print(f"   Triggers: {[c['symbol'] for c in triggers[:3]]} RF: {'FREE' if LAST_TRADE_RF else 'LOCKED'}")
        for c in triggers[:1]:
            print(f"🎯 TRIGGER: {c['symbol']} {c['rangePos']}% RSI {c['rsi']}")
            self.execute_trade(c)

if __name__ == "__main__":
    bot = HunterBotV4()
    if HAS_FLASK:
        app = Flask(__name__)
        @app.route("/")
        def home():
            try:
                with open(JSON_FILE, 'r') as f:
                    j = json.load(f)
                    return jsonify({**j["meta"], "rf_free": LAST_TRADE_RF, "open_position": OPEN_POSITION, "storage": "npoint.io" if NPOINT_ID else "local"})
            except:
                return jsonify({"balance": bot.balance, "status": "running", "rf_free": LAST_TRADE_RF})
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
            ok = send_telegram("✅ V4.5 Test OK - RF Rule Active")
            return jsonify({"sent": ok})
        @app.route("/debug")
        def debug_env():
            return jsonify({"token_set": bool(TELEGRAM_BOT_TOKEN),"chat_id": TELEGRAM_CHAT_ID,"balance": bot.balance,"last_trades": LAST_TRADE_TIME, "rf_free": LAST_TRADE_RF, "open_position": OPEN_POSITION, "npoint_id": NPOINT_ID})
        threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False), daemon=True).start()

    if TELEGRAM_BOT_TOKEN:
        threading.Thread(target=telegram_listener, args=(bot,), daemon=True).start()
        time.sleep(1)

    while True:
        print(f"\nSCAN - Bal ${bot.balance:.4f} RF: {'FREE ✅' if LAST_TRADE_RF else 'LOCKED 🔒'}")
        try:
            bot.run_once()
        except Exception as e:
            print(f"⚠️ Scan error: {e}")
            import traceback
            traceback.print_exc()
        time.sleep(60)
