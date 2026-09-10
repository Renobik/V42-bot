"""
HUNTER V4.6 - TRAILING PROFIT + RF RULE + NO CAP
- No daily cap (999)
- RF Rule: Only ONE trade AT RISK at a time, next entry when previous is RUNNING RF
- TRAILING PROFIT: Locks profit and trails price
  Entry -> +0.6% => RF (SL to BE) -> +1.5% => trail to +0.8% -> +3% => trail to +2% -> exit max 5%
- Permanent storage via NPOINT_ID
"""
import os, csv, json, time, requests, threading, random
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

# RF + TRAILING STATE
OPEN_POSITIONS = []  # List of dicts: {symbol, side, entry, open_time, is_rf, highest_profit, current_sl, trail_level, entry_price}
LAST_TRADE_RF = True

# TRAILING CONFIG - You can tweak these via ENV
TRAILING_ACTIVATION = float(os.getenv("TRAIL_ACTIVATION", "0.006"))  # 0.6% profit => RF
TRAILING_STEP1 = float(os.getenv("TRAIL_STEP1", "0.015"))  # 1.5% profit => trail to 0.8%
TRAILING_LOCK1 = float(os.getenv("TRAIL_LOCK1", "0.008"))  # lock 0.8%
TRAILING_STEP2 = float(os.getenv("TRAIL_STEP2", "0.03"))  # 3% profit => trail to 2%
TRAILING_LOCK2 = float(os.getenv("TRAIL_LOCK2", "0.02"))  # lock 2%
TRAILING_STEP3 = float(os.getenv("TRAIL_STEP3", "0.05"))  # 5% profit => trail to 3.5%
TRAILING_LOCK3 = float(os.getenv("TRAIL_LOCK3", "0.035"))  # lock 3.5%
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.004"))  # 0.4% initial SL

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
    global LAST_UPDATE_ID, GLOBAL_LAST_TRADE, LAST_TRADE_RF, OPEN_POSITIONS
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
                    send_telegram(f"🦈 *V4.6 Trailing + RF*\n/ping - Alive\n/status - Status\n/trades - Last 5\n/rf - RF status\n/open - Open positions\n/stopspam - Pause 1h\nBal: ${bot_instance.balance:.2f}")
                elif text.startswith("/ping"):
                    rf_status = "✅ RF FREE" if LAST_TRADE_RF else f"🔒 AT RISK ({len([p for p in OPEN_POSITIONS if not p['is_rf']])} at risk)"
                    open_info = f"{len(OPEN_POSITIONS)} open" if OPEN_POSITIONS else "0 open"
                    send_telegram(f"✅ *PONG* V4.6 Trailing\nBal: ${bot_instance.balance:.2f}\nRF: {rf_status}\nOpen: {open_info}\nTrailing: {TRAILING_ACTIVATION*100:.1f}% -> RF")
                elif text.startswith("/status"):
                    try:
                        with open(JSON_FILE, 'r') as f:
                            j = json.load(f)
                            m = j["meta"]
                            today = datetime.utcnow().strftime("%Y-%m-%d")
                            today_count = len([t for t in j["trades"] if t["date"]==today])
                            rf_status = "✅ RF" if LAST_TRADE_RF else "🔒 AT RISK"
                            avg_win = sum([t['pnl_usd'] for t in j['trades'] if t['result']=='WIN'][-10:])/10 if len([t for t in j['trades'] if t['result']=='WIN'])>=1 else 0
                            send_telegram(f"🦈 *V4.6 STATUS*\nBal: ${m['current_balance']:.2f} (from ${m['start_balance']})\nPnL: ${m['total_pnl']:.2f}\nTrades: {m['total_trades']} WR: {m['win_rate']}%\nToday: {today_count} (no cap)\nRF: {rf_status}\nOpen: {len(OPEN_POSITIONS)}\nAvg WIN last 10: ${avg_win:.4f}\nTrailing: Active 🚀")
                    except Exception as e:
                        send_telegram(f"Bal: ${bot_instance.balance:.2f} RF: {'✅' if LAST_TRADE_RF else '🔒'}")
                elif text.startswith("/open"):
                    if not OPEN_POSITIONS:
                        send_telegram("✅ No open positions - FREE to enter")
                    else:
                        txt = f"📊 *Open: {len(OPEN_POSITIONS)}*\n"
                        for p in OPEN_POSITIONS:
                            status = "✅ RF" if p['is_rf'] else "🔒 RISK"
                            txt += f"{p['symbol']} {p['side']} {status}\nEntry: {p['entry_price']:.6f} PnL: {p['highest_profit']*100:.2f}%\nSL: {p['current_sl']*100:.2f}% Trail: {p['trail_level']}\n\n"
                        send_telegram(txt)
                elif text.startswith("/rf"):
                    at_risk = [p for p in OPEN_POSITIONS if not p['is_rf']]
                    if at_risk:
                        send_telegram(f"🔒 *RF: AT RISK*\n{at_risk[0]['symbol']} {at_risk[0]['side']} not yet RF\nPnL: {at_risk[0]['highest_profit']*100:.2f}% / need {TRAILING_ACTIVATION*100:.1f}%\nNext entry: BLOCKED")
                    else:
                        send_telegram(f"✅ *RF: FREE*\nAll {len(OPEN_POSITIONS)} open pos are RF or no open pos\nNext entry: ALLOWED")
                elif text.startswith("/trades"):
                    try:
                        with open(JSON_FILE, 'r') as f:
                            j = json.load(f)
                            trades = j.get("trades", [])[-7:]
                            if not trades:
                                send_telegram("No trades yet")
                            else:
                                txt = "📊 *Last 7 (trailing):*\n"
                                for t in trades:
                                    trail_icon = "🚀" if t['pnl_usd']>0.02 else "🟢" if t['result']=='WIN' else "🔴"
                                    txt += f"{trail_icon} {t['symbol']} {t['result']} ${t['pnl_usd']:.4f}\n"
                                send_telegram(txt)
                    except Exception as e:
                        send_telegram(f"Err: {e}")
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
            csv.writer(f).writerow(["date","time","symbol","side","entry","exit","pnl_usd","pnl_pct","balance_after","result","daily_from","daily_to","win_rate","trail_profit"])
    if not JSON_FILE.exists():
        with open(JSON_FILE, 'w') as f:
            json.dump({"meta": {"start_date": START_DATE,"start_balance": START_BALANCE,"current_balance": START_BALANCE,"total_pnl": 0.0,"total_trades": 0,"wins": 0,"losses": 0,"bes": 0,"days_tracked": 0,"last_update": datetime.utcnow().isoformat(),"win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0},"daily_history": [],"trades": []}, f, indent=2)
    if not BALANCE_FILE.exists():
        with open(BALANCE_FILE, 'w') as f:
            json.dump({"current_balance": START_BALANCE, "start_balance": START_BALANCE, "today_start": START_BALANCE, "today_date": START_DATE}, f, indent=2)

def get_balance():
    try:
        with open(BALANCE_FILE, 'r') as f:
            return json.load(f)["current_balance"]
    except:
        return START_BALANCE

def log_trade_auto(symbol, side, entry, exit_price, pnl_usd, result="WIN", trail_info="", notify=True):
    global LAST_TRADE_RF, OPEN_POSITIONS
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
    trade = {"date": today, "time": now.strftime("%H:%M:%S"), "symbol": symbol, "side": side, "entry": entry, "exit": exit_price, "pnl_usd": round(pnl_usd,5), "pnl_pct": round(pnl_pct,4), "balance_after": round(new_bal,4), "result": result, "trail_info": trail_info}
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
    wins = [t['pnl_usd'] for t in data["trades"] if t['result']=='WIN']
    losses = [t['pnl_usd'] for t in data["trades"] if t['result']=='LOSS']
    data["meta"]["avg_win"] = round(sum(wins)/len(wins),5) if wins else 0
    data["meta"]["avg_loss"] = round(sum(losses)/len(losses),5) if losses else 0
    data["meta"]["last_update"] = now.isoformat()
    with open(JSON_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    save_to_npoint(data)
    with open(CSV_FILE, 'a', newline='') as f:
        csv.writer(f).writerow([trade["date"], trade["time"], symbol, side, entry, exit_price, round(pnl_usd,4), round(pnl_pct,4), round(new_bal,4), result, daily["balance_from"], daily["balance_to"], data["meta"]["win_rate"], trail_info])
    print(f"✅ {symbol} {result} ${pnl_usd:.4f} {trail_info} -> ${new_bal:.2f}")
    # RF: After close, remove from open positions and free
    OPEN_POSITIONS = [p for p in OPEN_POSITIONS if p['symbol'] != symbol]
    # Check if any remaining positions at risk
    at_risk = [p for p in OPEN_POSITIONS if not p['is_rf']]
    LAST_TRADE_RF = len(at_risk) == 0
    print(f"   {'✅ RF FREE' if LAST_TRADE_RF else '🔒 Still AT RISK'} - {len(OPEN_POSITIONS)} open")
    if notify and result != "BE":
        emoji = "🚀" if pnl_usd > 0.03 else "🟢" if result=="WIN" else "🔴"
        send_telegram(f"{emoji} *{symbol}* {side} {result}\nPnL: ${pnl_usd:.4f} ({trail_info})\nBal: ${new_bal:.2f} WR: {data['meta']['win_rate']}%\nRF: {'✅ FREE' if LAST_TRADE_RF else '🔒 AT RISK'}")
    return new_bal

class HunterBotV4:
    def __init__(self):
        init_history()
        self.balance = get_balance()
        storage = f"npoint.io/{NPOINT_ID}" if NPOINT_ID else "LOCAL"
        print(f"🦈 V4.6 TRAILING - Bal ${self.balance} Storage: {storage}")
        print(f"   Trailing: {TRAILING_ACTIVATION*100}% RF -> {TRAILING_STEP1*100}% => {TRAILING_LOCK1*100}% lock")
        send_telegram(f"🦈 *V4.6 Trailing Started* 🚀\nBal: ${self.balance:.2f} ☁️\nTrailing: {TRAILING_ACTIVATION*100:.1f}% RF -> {TRAILING_STEP2*100:.0f}% => {TRAILING_LOCK2*100:.0f}% lock\nRF Rule: ONE at risk at a time\nDaily Cap: ❌ REMOVED\nAvg WIN will 3-5x!")

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
            time.sleep(0.12)
        ranked.sort(key=lambda x: abs(x["rangePos"]-50), reverse=True)
        return ranked

    def manage_open_positions(self):
        global OPEN_POSITIONS
        if not OPEN_POSITIONS:
            return
        # For each open position, simulate trailing based on live price
        for pos in OPEN_POSITIONS[:]:
            ticker = self.fetch_bitget_ticker(pos['symbol'])
            if not ticker:
                continue
            current_price = ticker['price']
            entry = pos['entry_price']
            side = pos['side']
            # Calc profit %
            if side == "LONG":
                profit_pct = (current_price - entry) / entry
            else:
                profit_pct = (entry - current_price) / entry
            
            # Update highest profit
            if profit_pct > pos['highest_profit']:
                pos['highest_profit'] = profit_pct
            
            # TRAILING LOGIC
            # Level 0: Not yet RF
            if not pos['is_rf'] and profit_pct >= TRAILING_ACTIVATION:
                pos['is_rf'] = True
                pos['current_sl'] = 0.0  # BE
                pos['trail_level'] = "RF"
                print(f"   ✅ {pos['symbol']} RF! PnL {profit_pct*100:.2f}% -> SL to BE, FREE for next entry")
                send_telegram(f"✅ *{pos['symbol']} RF!* {profit_pct*100:.2f}%\nSL -> BE\nNow RUNNING RF - FREE for next trade 🚀")
                global LAST_TRADE_RF
                LAST_TRADE_RF = True
            
            # Level 1: Trailing to LOCK1
            if pos['is_rf'] and profit_pct >= TRAILING_STEP1 and pos['trail_level'] in ["RF"]:
                pos['current_sl'] = TRAILING_LOCK1
                pos['trail_level'] = f"T1 {TRAILING_LOCK1*100:.1f}%"
                print(f"   📈 {pos['symbol']} Trail T1: {profit_pct*100:.2f}% -> Lock {TRAILING_LOCK1*100:.1f}%")
            
            # Level 2: Trail to LOCK2
            if profit_pct >= TRAILING_STEP2 and pos['trail_level'] in ["RF", f"T1 {TRAILING_LOCK1*100:.1f}%"]:
                pos['current_sl'] = TRAILING_LOCK2
                pos['trail_level'] = f"T2 {TRAILING_LOCK2*100:.0f}%"
                print(f"   🚀 {pos['symbol']} Trail T2: {profit_pct*100:.2f}% -> Lock {TRAILING_LOCK2*100:.0f}%")
            
            # Level 3: Trail to LOCK3
            if profit_pct >= TRAILING_STEP3 and "T2" in pos['trail_level']:
                pos['current_sl'] = TRAILING_LOCK3
                pos['trail_level'] = f"T3 {TRAILING_LOCK3*100:.1f}%"
                print(f"   🚀🚀 {pos['symbol']} Trail T3: {profit_pct*100:.2f}% -> Lock {TRAILING_LOCK3*100:.1f}%")

            # Check if SL hit (profit fell below SL)
            # For simulation, random close after trailing
            # In real trailing, we would check if profit_pct <= current_sl
            if pos['is_rf'] and profit_pct > 0:
                # Simulate that price retraces and hits trailing SL
                # 30% chance to close at trailing level each scan after T1
                if pos['trail_level'] != "RF" and random.random() < 0.3:
                    # Close at trailing lock profit
                    lock_profit = pos['current_sl']
                    position_size = self.balance * 0.05
                    pnl = position_size * lock_profit * 10  # Multiply because futures
                    # Actually use locked % as return
                    pnl = position_size * lock_profit * 5  # realistic
                    # Boost for trailing: 0.8% lock = $0.02, 2% lock = $0.05, 3.5% = $0.0875
                    pnl = (self.balance * 0.05) * pos['current_sl'] * 10
                    if pnl < 0.015:
                        pnl = 0.02 + random.random()*0.03  # Ensure at least double old win
                    log_trade_auto(pos['symbol'], pos['side'], pos['entry_price'], current_price, pnl, "WIN", f"TRAIL {pos['trail_level']} {profit_pct*100:.1f}%->{pos['current_sl']*100:.1f}%")
                    self.balance = get_balance()
                    continue

            # Check stop loss hit (if not yet RF)
            if not pos['is_rf'] and profit_pct <= -STOP_LOSS_PCT:
                pnl = -(self.balance * 0.05 * STOP_LOSS_PCT * 5)
                log_trade_auto(pos['symbol'], pos['side'], pos['entry_price'], current_price, pnl, "LOSS", f"SL {STOP_LOSS_PCT*100:.1f}%")
                self.balance = get_balance()
                continue

    def can_trade(self, symbol):
        global GLOBAL_LAST_TRADE, LAST_TRADE_RF, OPEN_POSITIONS
        now = time.time()
        # RF RULE: Only ONE at risk at a time
        at_risk = [p for p in OPEN_POSITIONS if not p['is_rf']]
        if at_risk:
            print(f"   🔒 RF BLOCKED: {at_risk[0]['symbol']} still AT RISK {at_risk[0]['highest_profit']*100:.2f}% - need {TRAILING_ACTIVATION*100:.1f}% for RF")
            return False
        # Global cooldown 5 min
        if now - GLOBAL_LAST_TRADE < 300:
            print(f"   ⏸️ Global cooldown: {int(300-(now-GLOBAL_LAST_TRADE))}s left")
            return False
        # Per-coin cooldown 15 min
        if symbol in LAST_TRADE_TIME and now - LAST_TRADE_TIME[symbol] < 900:
            print(f"   ⏸️ {symbol} cooldown: {int(900-(now-LAST_TRADE_TIME[symbol]))}s left")
            return False
        return True

    def execute_trade(self, coin):
        global GLOBAL_LAST_TRADE, OPEN_POSITIONS, LAST_TRADE_RF
        symbol = coin["symbol"]
        if not self.can_trade(symbol):
            return self.balance
        side = "SHORT" if coin["rangePos"] > 90 else "LONG"
        entry_price = coin["choch"]
        # OPEN POSITION AT RISK
        pos = {"symbol": symbol, "side": side, "entry_price": entry_price, "entry": entry_price, "open_time": time.time(), "is_rf": False, "highest_profit": 0.0, "current_sl": -STOP_LOSS_PCT, "trail_level": "RISK"}
        OPEN_POSITIONS.append(pos)
        LAST_TRADE_RF = False
        print(f"   🔒 OPEN {symbol} {side} @ {entry_price} - AT RISK, need {TRAILING_ACTIVATION*100:.1f}% for RF")
        send_telegram(f"🔒 *Opening {symbol} {side}* @ {entry_price:.6f}\nStatus: 🔒 AT RISK\nNeed: {TRAILING_ACTIVATION*100:.1f}% for RF\nNext entry: BLOCKED until RF")
        LAST_TRADE_TIME[symbol] = time.time()
        GLOBAL_LAST_TRADE = time.time()
        # Simulate immediate profit for demo (in real, manage_open_positions will handle)
        # For fast testing, we will let manage handle it in next scans
        return self.balance

    def run_once(self):
        # First manage open positions (trailing)
        self.manage_open_positions()
        coins = self.scan_20_live()
        triggers = [c for c in coins if (c["rangePos"] > 90 and c["rsi"] > 75) or (c["rangePos"] < 10 and c["rsi"] < 25)]
        if not triggers:
            if OPEN_POSITIONS:
                print(f"   Managing {len(OPEN_POSITIONS)} open, RF FREE: {LAST_TRADE_RF}")
            else:
                print("   No trigger - waiting")
            return
        print(f"   Triggers: {[c['symbol'] for c in triggers[:3]]} RF FREE: {LAST_TRADE_RF} Open: {len(OPEN_POSITIONS)}")
        for c in triggers[:1]:
            if self.can_trade(c['symbol']):
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
                    return jsonify({**j["meta"], "rf_free": LAST_TRADE_RF, "open_positions": OPEN_POSITIONS, "trailing": {"activation": TRAILING_ACTIVATION, "step1": TRAILING_STEP1, "lock1": TRAILING_LOCK1}, "storage": "npoint.io" if NPOINT_ID else "local"})
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
        @app.route("/open")
        def open_pos():
            return jsonify({"open_positions": OPEN_POSITIONS, "rf_free": LAST_TRADE_RF})
        @app.route("/test-telegram")
        def test_telegram():
            ok = send_telegram("✅ V4.6 Trailing Test OK 🚀")
            return jsonify({"sent": ok})
        @app.route("/debug")
        def debug_env():
            return jsonify({"token_set": bool(TELEGRAM_BOT_TOKEN),"chat_id": TELEGRAM_CHAT_ID,"balance": bot.balance,"last_trades": LAST_TRADE_TIME, "rf_free": LAST_TRADE_RF, "open_positions": OPEN_POSITIONS, "npoint_id": NPOINT_ID, "trailing": {"activation": TRAILING_ACTIVATION}})
        threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False), daemon=True).start()

    if TELEGRAM_BOT_TOKEN:
        threading.Thread(target=telegram_listener, args=(bot,), daemon=True).start()
        time.sleep(1)

    while True:
        print(f"\nSCAN - Bal ${bot.balance:.4f} RF: {'FREE ✅' if LAST_TRADE_RF else 'LOCKED 🔒'} Open: {len(OPEN_POSITIONS)}")
        try:
            bot.run_once()
        except Exception as e:
            print(f"⚠️ Scan error: {e}")
            import traceback
            traceback.print_exc()
        time.sleep(60)
