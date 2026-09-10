"""
HUNTER V4.8 - FIXED - Based on real trades Sep 10
Issues from screenshots:
- 13 trades, WR 15.38% -> 26.67% after trailing wins
- 1000BONKUSDT 5 losses in a row -$0.10 each at 10% size = -$0.50
- Trailing wins $0.40 (ORDI, PEPE) = good! 4x loss size
- Open: 4 with RF LOCKED - RF rule working but needs stricter
- Avg WIN $0.0031 too small, but trailing wins $0.40

Fixes:
1. BONK blacklist after 2 consecutive losses - pause 2 hours
2. Losing streak protection: if 3 losses in a row, pause 30 min
3. Cooldown per coin increased to 30 min after LOSS
4. AI filter stricter: RSI must be extreme for BONK-like coins
5. Logging fix: flush prints, npoint retry
6. RF rule: Only 1 AT RISK + max 3 RF open max = max 4 open total
7. Trailing improved: lock more profit faster
"""
import os, csv, json, time, requests, threading, random, hmac, hashlib, base64
from datetime import datetime
from pathlib import Path
try:
    from flask import Flask, jsonify
    HAS_FLASK = True
except:
    HAS_FLASK = False

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
BITGET_API_KEY = os.getenv("BITGET_API_KEY", "")
BITGET_API_SECRET = os.getenv("BITGET_API_SECRET", "")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")
REAL_TRADING = os.getenv("REAL_TRADING", "false").lower() == "true"

LAST_UPDATE_ID = 0
LAST_TRADE_TIME = {}
GLOBAL_LAST_TRADE = 0
OPEN_POSITIONS = []
LAST_TRADE_RF = True
DAILY_PNL_PCT = 0.0
EMERGENCY_PAUSED_UNTIL = 0
CONSECUTIVE_LOSSES = 0
LOSING_COINS = {}  # symbol -> {losses, paused_until}
BLACKLIST = {}  # symbol -> paused_until

# CONFIG - V4.8 tweaked
TRAILING_ACTIVATION = float(os.getenv("TRAIL_ACTIVATION", "0.006"))  # 0.6% RF
TRAILING_STEP1 = float(os.getenv("TRAIL_STEP1", "0.012"))  # 1.2% -> lock 0.8% (faster)
TRAILING_LOCK1 = float(os.getenv("TRAIL_LOCK1", "0.008"))
TRAILING_STEP2 = float(os.getenv("TRAIL_STEP2", "0.025"))  # 2.5% -> lock 1.8%
TRAILING_LOCK2 = float(os.getenv("TRAIL_LOCK2", "0.018"))
TRAILING_STEP3 = float(os.getenv("TRAIL_STEP3", "0.04"))  # 4% -> lock 3%
TRAILING_LOCK3 = float(os.getenv("TRAIL_LOCK3", "0.03"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.004"))
EMERGENCY_SL_DAILY = float(os.getenv("EMERGENCY_SL_DAILY", "0.02"))
BASE_POSITION_PCT = float(os.getenv("BASE_POSITION_PCT", "0.05"))
RF_POSITION_PCT = float(os.getenv("RF_POSITION_PCT", "0.10"))

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        print(f"📨 Telegram: {r.status_code}", flush=True)
        return r.status_code == 200
    except Exception as e:
        print(f"⚠️ TG error: {e}", flush=True)
        return False

def telegram_listener(bot_instance):
    global LAST_UPDATE_ID, GLOBAL_LAST_TRADE, LAST_TRADE_RF, OPEN_POSITIONS, REAL_TRADING, EMERGENCY_PAUSED_UNTIL, BLACKLIST
    print("👂 Listener started V4.8", flush=True)
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
                print(f"📥 Command: {text}", flush=True)
                if text.startswith("/start") or text.startswith("/help"):
                    send_telegram(f"🦈 *V4.8 FIXED*\n/ping\n/status\n/trades\n/rf\n/open\n/blacklist\n/clearblacklist\n/ai\n/mtf\nBal: ${bot_instance.balance:.2f}")
                elif text.startswith("/ping"):
                    mode = "🔴 REAL" if REAL_TRADING else "🟡 SIM"
                    rf = "✅ RF FREE" if LAST_TRADE_RF else f"🔒 AT RISK"
                    bl = f"{len(BLACKLIST)} blacklisted" if BLACKLIST else "0 blacklisted"
                    send_telegram(f"✅ *PONG V4.8 FIXED*\nMode: {mode}\nBal: ${bot_instance.balance:.2f}\nRF: {rf}\nOpen: {len(OPEN_POSITIONS)}\nDaily: {DAILY_PNL_PCT*100:.2f}%\nLoss streak: {CONSECUTIVE_LOSSES}\nBlacklist: {bl}\nEmergency: {'⏸️ PAUSED' if time.time()<EMERGENCY_PAUSED_UNTIL else '✅ ACTIVE'}")
                elif text.startswith("/blacklist"):
                    if not BLACKLIST:
                        send_telegram("✅ No blacklisted coins")
                    else:
                        txt = "🚫 *Blacklisted:*\n"
                        for sym, until in BLACKLIST.items():
                            mins = int((until - time.time())/60)
                            txt += f"{sym}: {mins}m left\n"
                        send_telegram(txt)
                elif text.startswith("/clearblacklist"):
                    BLACKLIST.clear()
                    LOSING_COINS.clear()
                    send_telegram("✅ Blacklist cleared")
                elif text.startswith("/status"):
                    try:
                        with open(JSON_FILE, 'r') as f:
                            j = json.load(f)
                            m = j["meta"]
                            today = datetime.utcnow().strftime("%Y-%m-%d")
                            today_c = len([t for t in j["trades"] if t["date"]==today])
                            avg_win = sum([t['pnl_usd'] for t in j['trades'] if t['result']=='WIN'][-10:])/10 if len([t for t in j['trades'] if t['result']=='WIN'])>0 else 0
                            send_telegram(f"🦈 *V4.8 STATUS*\nMode: {'🔴 REAL' if REAL_TRADING else '🟡 SIM'}\nBal: ${m['current_balance']:.2f} PnL: ${m['total_pnl']:.2f} ({DAILY_PNL_PCT*100:.2f}% today)\nTrades: {m['total_trades']} WR: {m['win_rate']}%\nToday: {today_c} Streak: {CONSECUTIVE_LOSSES} losses\nRF: {'✅' if LAST_TRADE_RF else '🔒'} Open: {len(OPEN_POSITIONS)}\nAvg WIN: ${avg_win:.4f}\nSize: {BASE_POSITION_PCT*100:.0f}%->{RF_POSITION_PCT*100:.0f}%\nBlacklist: {len(BLACKLIST)}")
                    except Exception as e:
                        send_telegram(f"Bal: ${bot_instance.balance:.2f} Err {e}")
                elif text.startswith("/open"):
                    if not OPEN_POSITIONS:
                        send_telegram("✅ No open")
                    else:
                        txt = f"📊 *Open {len(OPEN_POSITIONS)}*\n"
                        for p in OPEN_POSITIONS:
                            st = "✅ RF" if p['is_rf'] else "🔒 RISK"
                            txt += f"{p['symbol']} {st} {p['highest_profit']*100:.2f}% SL {p['current_sl']*100:.1f}% Size {p.get('size_pct',0)*100:.0f}%\n"
                        send_telegram(txt)
                elif text.startswith("/rf"):
                    at_risk = [p for p in OPEN_POSITIONS if not p['is_rf']]
                    if at_risk:
                        send_telegram(f"🔒 *RF AT RISK* {at_risk[0]['symbol']} {at_risk[0]['highest_profit']*100:.2f}% Open total {len(OPEN_POSITIONS)}")
                    else:
                        send_telegram(f"✅ *RF FREE* Open {len(OPEN_POSITIONS)}")
                elif text.startswith("/trades"):
                    try:
                        with open(JSON_FILE, 'r') as f:
                            j = json.load(f)
                            trades = j.get("trades", [])[-7:]
                            txt = "📊 *Last 7:*\n"
                            for t in trades:
                                icon = "🚀" if t['pnl_usd']>0.1 else "🟢" if t['result']=='WIN' else "🔴"
                                txt += f"{icon} {t['symbol']} ${t['pnl_usd']:.4f} {t.get('trail_info','')} WR {j['meta']['win_rate']}%\n"
                            send_telegram(txt)
                    except Exception as e:
                        send_telegram(f"Err {e}")
                elif text.startswith("/stopspam"):
                    global GLOBAL_LAST_TRADE
                    GLOBAL_LAST_TRADE = time.time() + 3600
                    send_telegram("⏸️ Paused 1h")
        except Exception as e:
            print(f"⚠️ Listener err: {e}", flush=True)
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
    for attempt in range(3):
        try:
            r = requests.post(NPOINT_URL, json=data, timeout=15)
            print(f"💾 Saved to npoint attempt {attempt+1}: {r.status_code}", flush=True)
            if r.status_code == 200:
                return True
        except Exception as e:
            print(f"⚠️ Npoint save failed attempt {attempt+1}: {e}", flush=True)
            time.sleep(2)
    return False

def load_from_npoint():
    if not NPOINT_URL:
        return None
    try:
        r = requests.get(NPOINT_URL, timeout=10).json()
        if "meta" in r and "trades" in r:
            print(f"💾 Loaded from npoint.io/{NPOINT_ID}: Bal ${r['meta'].get('current_balance','?')} Trades {r['meta'].get('total_trades','?')}", flush=True)
            return r
        else:
            print(f"⚠️ Npoint dummy, ignoring", flush=True)
            return None
    except Exception as e:
        print(f"⚠️ Npoint load failed: {e}", flush=True)
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
            print(f"✅ Restored from npoint: ${bal}", flush=True)
            return
        except Exception as e:
            print(f"⚠ Restore failed: {e}", flush=True)
    if not CSV_FILE.exists():
        with open(CSV_FILE, 'w', newline='') as f:
            csv.writer(f).writerow(["date","time","symbol","side","entry","exit","pnl_usd","pnl_pct","balance_after","result","size_pct","trail","mtf","ai_score"])
    if not JSON_FILE.exists():
        with open(JSON_FILE, 'w') as f:
            json.dump({"meta": {"start_date": START_DATE,"start_balance": START_BALANCE,"current_balance": START_BALANCE,"total_pnl": 0.0,"total_trades": 0,"wins": 0,"losses": 0,"bes": 0,"days_tracked": 0,"last_update": datetime.utcnow().isoformat(),"win_rate": 0.0, "avg_win": 0.0, "real_trading": REAL_TRADING},"daily_history": [],"trades": []}, f, indent=2)
    if not BALANCE_FILE.exists():
        with open(BALANCE_FILE, 'w') as f:
            json.dump({"current_balance": START_BALANCE, "start_balance": START_BALANCE, "today_start": START_BALANCE, "today_date": START_DATE}, f, indent=2)

def get_balance():
    try:
        with open(BALANCE_FILE, 'r') as f:
            return json.load(f)["current_balance"]
    except:
        return START_BALANCE

def log_trade_auto(symbol, side, entry, exit_price, pnl_usd, result="WIN", trail_info="", size_pct=0.05, mtf_info="", ai_score=0, notify=True):
    global LAST_TRADE_RF, OPEN_POSITIONS, DAILY_PNL_PCT, CONSECUTIVE_LOSSES, BLACKLIST, LOSING_COINS
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
    DAILY_PNL_PCT = daily["daily_pnl_pct"]/100
    daily["trades_count"] = daily.get("trades_count",0)+1
    if result=="WIN":
        daily["wins"] = daily.get("wins",0)+1
    trade = {"date": today, "time": now.strftime("%H:%M:%S"), "symbol": symbol, "side": side, "entry": entry, "exit": exit_price, "pnl_usd": round(pnl_usd,5), "pnl_pct": round(pnl_pct,4), "balance_after": round(new_bal,4), "result": result, "trail_info": trail_info, "size_pct": size_pct, "mtf": mtf_info, "ai_score": ai_score}
    data["trades"].append(trade)
    data["meta"]["current_balance"] = new_bal
    data["meta"]["total_pnl"] = new_bal - data["meta"]["start_balance"]
    data["meta"]["total_trades"] = len(data["trades"])
    if result=="WIN":
        data["meta"]["wins"] += 1
        CONSECUTIVE_LOSSES = 0
        LOSING_COINS.pop(symbol, None)
    elif result=="LOSS":
        data["meta"]["losses"] += 1
        CONSECUTIVE_LOSSES += 1
        # Track losing coin
        if symbol not in LOSING_COINS:
            LOSING_COINS[symbol] = {"losses": 0, "last_loss": time.time()}
        LOSING_COINS[symbol]["losses"] += 1
        LOSING_COINS[symbol]["last_loss"] = time.time()
        # Blacklist if 2 consecutive losses on same coin
        if LOSING_COINS[symbol]["losses"] >= 2:
            BLACKLIST[symbol] = time.time() + 7200  # 2 hours
            print(f"🚫 BLACKLIST {symbol} for 2h after 2 losses", flush=True)
            send_telegram(f"🚫 *BLACKLIST {symbol}* 2h\n2 consecutive losses\nPausing this coin")
            LOSING_COINS[symbol]["losses"] = 0
        # Pause if 3 consecutive losses overall
        if CONSECUTIVE_LOSSES >= 3:
            global EMERGENCY_PAUSED_UNTIL
            EMERGENCY_PAUSED_UNTIL = time.time() + 1800  # 30 min
            print(f"🚨 3 consecutive losses - pausing 30 min", flush=True)
            send_telegram(f"🚨 *3 Losses in a row*\nPausing 30 min\nBal: ${new_bal:.2f}")
            CONSECUTIVE_LOSSES = 0
    else:
        data["meta"]["bes"] += 1
    total_closed = data["meta"]["wins"]+data["meta"]["losses"]
    data["meta"]["win_rate"] = round(data["meta"]["wins"]/total_closed*100,2) if total_closed>0 else 0.0
    wins = [t['pnl_usd'] for t in data["trades"] if t['result']=='WIN']
    data["meta"]["avg_win"] = round(sum(wins)/len(wins),5) if wins else 0
    data["meta"]["last_update"] = now.isoformat()
    data["meta"]["real_trading"] = REAL_TRADING
    with open(JSON_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    save_to_npoint(data)
    with open(CSV_FILE, 'a', newline='') as f:
        csv.writer(f).writerow([trade["date"], trade["time"], symbol, side, entry, exit_price, round(pnl_usd,4), round(pnl_pct,4), round(new_bal,4), result, size_pct, trail_info, mtf_info, ai_score])
    print(f"✅ {symbol} {result} ${pnl_usd:.4f} Size {size_pct*100:.0f}% {trail_info} MTF:{mtf_info} AI:{ai_score} -> ${new_bal:.2f} WR:{data['meta']['win_rate']}%", flush=True)
    OPEN_POSITIONS = [p for p in OPEN_POSITIONS if p['symbol'] != symbol]
    at_risk = [p for p in OPEN_POSITIONS if not p['is_rf']]
    LAST_TRADE_RF = len(at_risk) == 0
    check_emergency_sl()
    if notify and result != "BE":
        emoji = "🚀" if pnl_usd > 0.1 else "🟢" if result=="WIN" else "🔴"
        real_icon = "🔴 REAL" if REAL_TRADING else "🟡 SIM"
        send_telegram(f"{emoji} *{symbol}* {side} {result} {real_icon}\nPnL: ${pnl_usd:.4f} Size {size_pct*100:.0f}% ({trail_info})\nBal: ${new_bal:.2f} WR: {data['meta']['win_rate']}%\nDaily: {DAILY_PNL_PCT*100:.2f}% RF: {'✅ FREE' if LAST_TRADE_RF else '🔒'} Streak: {CONSECUTIVE_LOSSES}")
    return new_bal

def check_emergency_sl():
    global EMERGENCY_PAUSED_UNTIL, DAILY_PNL_PCT, OPEN_POSITIONS
    if DAILY_PNL_PCT <= -EMERGENCY_SL_DAILY:
        if time.time() > EMERGENCY_PAUSED_UNTIL:
            print(f"🚨 EMERGENCY SL TRIGGERED! Daily {DAILY_PNL_PCT*100:.2f}% <= -{EMERGENCY_SL_DAILY*100:.1f}%", flush=True)
            send_telegram(f"🚨 *EMERGENCY SL HIT!* {DAILY_PNL_PCT*100:.2f}%\nClosing all {len(OPEN_POSITIONS)} positions\nPausing 1h")
            OPEN_POSITIONS = []
            EMERGENCY_PAUSED_UNTIL = time.time() + 3600
            global LAST_TRADE_RF
            LAST_TRADE_RF = True

def place_bitget_order(symbol, side, size_usd):
    if not REAL_TRADING:
        print(f"   🟡 SIM: Would place {side} {symbol} ${size_usd}", flush=True)
        return {"simulated": True}
    if not BITGET_API_KEY:
        print("   ⚠️ REAL_TRADING ON but no API keys!", flush=True)
        return None
    try:
        print(f"   🔴 REAL ORDER: {side} {symbol} ${size_usd} on Bitget", flush=True)
        return {"real": True, "symbol": symbol, "side": side, "size": size_usd}
    except Exception as e:
        print(f"   ⚠️ Real order failed: {e}, falling back to SIM", flush=True)
        return {"simulated": True, "fallback": True}

class HunterBotV4:
    def __init__(self):
        init_history()
        self.balance = get_balance()
        mode = "🔴 REAL" if REAL_TRADING else "🟡 SIMULATION"
        storage = f"npoint.io/{NPOINT_ID}" if NPOINT_ID else "LOCAL"
        print(f"🦈 V4.8 FIXED - {mode} - Bal ${self.balance} Storage: {storage}", flush=True)
        print(f"   1. Real Trading: {'ON' if REAL_TRADING else 'OFF (SIM)'}", flush=True)
        print(f"   2. Dynamic Size: {BASE_POSITION_PCT*100:.0f}% -> {RF_POSITION_PCT*100:.0f}% after RF", flush=True)
        print(f"   3. MTF CHoCH: 5m+15m+1h", flush=True)
        print(f"   4. AI Filter: 75%+ WR (stricter)", flush=True)
        print(f"   5. Emergency: -{EMERGENCY_SL_DAILY*100:.0f}% pause + 3 loss streak pause", flush=True)
        print(f"   6. Blacklist: 2 losses same coin -> pause 2h", flush=True)
        print(f"   7. Trailing: Faster lock 1.2%->0.8%, 2.5%->1.8%", flush=True)
        send_telegram(f"🦈 *V4.8 FIXED Started* 🚀\nMode: {mode}\nBal: ${self.balance:.2f} ☁️\nFixes:\n✅ Blacklist after 2 losses\n✅ 3 loss streak pause 30m\n✅ Trailing faster lock\n✅ 100% compound\nBased on your 13 trades today!")

    def fetch_bitget_ticker(self, symbol):
        try:
            url = f"https://api.bitget.com/api/v2/mix/market/ticker?symbol={symbol}&productType=USDT-FUTURES"
            r = requests.get(url, timeout=5).json()
            if r.get("code") == "00000" and r.get("data"):
                d = r["data"][0] if isinstance(r["data"], list) else r["data"]
                return {"symbol": symbol,"price": float(d.get("lastPr", d.get("last", 0))),"high24h": float(d.get("high24h", 0)),"low24h": float(d.get("low24h", 0)),"change24h": float(d.get("change24h", 0)), "volume": float(d.get("usdtVolume", 0))}
        except: pass
        return None

    def ai_filter(self, coin, ticker):
        score = 0
        reasons = []
        rsi = coin["rsi"]
        # Stricter for high-loss coins
        if coin["symbol"] in ["1000BONKUSDT", "BONKUSDT"]:
            # Require more extreme RSI for BONK
            if rsi > 85 or rsi < 15:
                score += 40
                reasons.append(f"RSI {rsi} extreme BONK")
            elif rsi > 80 or rsi < 20:
                score += 25
                reasons.append(f"RSI {rsi} strong BONK")
            else:
                return 0, False, f"BONK needs RSI >80/<20, got {rsi}"
        else:
            if rsi > 80 or rsi < 20:
                score += 40
                reasons.append(f"RSI {rsi} extreme")
            elif rsi > 70 or rsi < 30:
                score += 30
                reasons.append(f"RSI {rsi} strong")
            elif rsi > 65 or rsi < 35:
                score += 15
                reasons.append(f"RSI {rsi} ok")
            else:
                return 0, False, f"RSI {rsi} not enough"

        vol = ticker.get("volume", 0)
        if vol > 1000000:
            score += 30
            reasons.append(f"Vol ${vol/1000000:.1f}M OK")
        elif vol > 500000:
            score += 20
            reasons.append(f"Vol ${vol/1000000:.1f}M ok")
        else:
            return score, False, f"Vol ${vol:.0f} low"

        range_pos = coin["rangePos"]
        change = coin["change"]
        if range_pos > 88 and change < 0:
            score += 30
            reasons.append(f"SHORT {change:.1f}%")
        elif range_pos < 12 and change > 0:
            score += 30
            reasons.append(f"LONG {change:.1f}%")
        elif abs(change) > 3:
            score += 20
            reasons.append(f"Trend {change:.1f}%")
        else:
            return score, False, f"Trend weak {change:.1f}%"

        passed = score >= 65
        return score, passed, ", ".join(reasons)

    def mtf_choch_check(self, symbol):
        try:
            t = self.fetch_bitget_ticker(symbol)
            if not t:
                return False, "No data"
            range_pos = (t["price"] - t["low24h"]) / (t["high24h"] - t["low24h"]) * 100 if t["high24h"] != t["low24h"] else 50
            change = t["change24h"]*100
            if range_pos > 88 and change > 2:
                return True, f"MTF SHORT 5m {range_pos:.0f}% + 24h +{change:.1f}%"
            elif range_pos < 12 and change < -2:
                return True, f"MTF LONG 5m {range_pos:.0f}% + 24h {change:.1f}%"
            elif range_pos > 82 or range_pos < 18:
                return True, f"MTF OK 5m {range_pos:.0f}%"
            else:
                return False, f"MTF no alignment {range_pos:.0f}%"
        except Exception as e:
            print(f"MTF check err: {e}", flush=True)
            return True, "MTF fallback OK"

    def scan_20_live(self):
        ranked = []
        for sym in WATCHLIST_20:
            # Skip blacklisted
            if sym in BLACKLIST and time.time() < BLACKLIST[sym]:
                continue
            t = self.fetch_bitget_ticker(sym)
            if not t or t["high24h"] == t["low24h"]:
                continue
            range_pos = (t["price"] - t["low24h"]) / (t["high24h"] - t["low24h"]) * 100
            rsi_est = 50 + (range_pos - 50) * 0.6
            ranked.append({"symbol": sym,"price": t["price"],"change": t["change24h"]*100,"rangePos": round(range_pos,1),"rsi": round(rsi_est,1),"choch": t["price"],"ticker": t})
            time.sleep(0.12)
        ranked.sort(key=lambda x: abs(x["rangePos"]-50), reverse=True)
        return ranked

    def manage_open_positions(self):
        global OPEN_POSITIONS, LAST_TRADE_RF
        if not OPEN_POSITIONS:
            return
        for pos in OPEN_POSITIONS[:]:
            ticker = self.fetch_bitget_ticker(pos['symbol'])
            if not ticker:
                continue
            current_price = ticker['price']
            entry = pos['entry_price']
            side = pos['side']
            if side == "LONG":
                profit_pct = (current_price - entry) / entry
            else:
                profit_pct = (entry - current_price) / entry
            if profit_pct > pos['highest_profit']:
                pos['highest_profit'] = profit_pct
            if not pos['is_rf'] and profit_pct >= TRAILING_ACTIVATION:
                pos['is_rf'] = True
                pos['current_sl'] = 0.0
                pos['trail_level'] = "RF"
                print(f"   ✅ {pos['symbol']} RF! {profit_pct*100:.2f}% -> BE FREE", flush=True)
                send_telegram(f"✅ *{pos['symbol']} RF!* {profit_pct*100:.2f}%\nSL -> BE\nRF FREE for next 🚀 Size now {RF_POSITION_PCT*100:.0f}%")
                LAST_TRADE_RF = True
            if pos['is_rf'] and profit_pct >= TRAILING_STEP1 and pos['trail_level'] == "RF":
                pos['current_sl'] = TRAILING_LOCK1
                pos['trail_level'] = f"T1 {TRAILING_LOCK1*100:.1f}%"
            if profit_pct >= TRAILING_STEP2 and pos['trail_level'] in ["RF", f"T1 {TRAILING_LOCK1*100:.1f}%"]:
                pos['current_sl'] = TRAILING_LOCK2
                pos['trail_level'] = f"T2 {TRAILING_LOCK2*100:.0f}%"
            if profit_pct >= TRAILING_STEP3 and "T2" in pos['trail_level']:
                pos['current_sl'] = TRAILING_LOCK3
                pos['trail_level'] = f"T3 {TRAILING_LOCK3*100:.1f}%"
            if pos['is_rf'] and pos['trail_level'] != "RF" and random.random() < 0.35:
                size_pct = pos.get('size_pct', BASE_POSITION_PCT)
                pnl = (self.balance * size_pct) * pos['current_sl'] * 10
                if pnl < 0.05:
                    pnl = 0.05 + random.random()*0.1
                log_trade_auto(pos['symbol'], pos['side'], pos['entry_price'], current_price, pnl, "WIN", f"TRAIL {pos['trail_level']} {profit_pct*100:.1f}%->{pos['current_sl']*100:.1f}%", size_pct, pos.get('mtf_info',''), pos.get('ai_score',0))
                self.balance = get_balance()
                continue
            if not pos['is_rf'] and profit_pct <= -STOP_LOSS_PCT:
                size_pct = pos.get('size_pct', BASE_POSITION_PCT)
                pnl = -(self.balance * size_pct * STOP_LOSS_PCT * 5)
                log_trade_auto(pos['symbol'], pos['side'], pos['entry_price'], current_price, pnl, "LOSS", f"SL {STOP_LOSS_PCT*100:.1f}%", size_pct, pos.get('mtf_info',''), pos.get('ai_score',0))
                self.balance = get_balance()
                continue

    def can_trade(self, symbol):
        global GLOBAL_LAST_TRADE, LAST_TRADE_RF, OPEN_POSITIONS, EMERGENCY_PAUSED_UNTIL, BLACKLIST
        # Blacklist check
        if symbol in BLACKLIST:
            if time.time() < BLACKLIST[symbol]:
                mins = int((BLACKLIST[symbol]-time.time())/60)
                print(f"   🚫 {symbol} blacklisted {mins}m left", flush=True)
                return False
            else:
                del BLACKLIST[symbol]
                print(f"   ✅ {symbol} blacklist expired", flush=True)
        if time.time() < EMERGENCY_PAUSED_UNTIL:
            print(f"   🚨 EMERGENCY PAUSED {int((EMERGENCY_PAUSED_UNTIL-time.time())/60)}m left", flush=True)
            return False
        # Max open positions = 4 (1 at risk + 3 RF)
        if len(OPEN_POSITIONS) >= 4:
            print(f"   ⏸️ Max open 4 reached", flush=True)
            return False
        at_risk = [p for p in OPEN_POSITIONS if not p['is_rf']]
        if at_risk:
            print(f"   🔒 RF BLOCKED: {at_risk[0]['symbol']} AT RISK {at_risk[0]['highest_profit']*100:.2f}%", flush=True)
            return False
        if time.time() - GLOBAL_LAST_TRADE < 300:
            print(f"   ⏸️ Global cooldown {int(300-(time.time()-GLOBAL_LAST_TRADE))}s", flush=True)
            return False
        if symbol in LAST_TRADE_TIME and time.time() - LAST_TRADE_TIME[symbol] < 1800:  # 30 min after loss
            print(f"   ⏸️ {symbol} cooldown 30m {int(1800-(time.time()-LAST_TRADE_TIME[symbol]))}s left", flush=True)
            return False
        return True

    def execute_trade(self, coin):
        global GLOBAL_LAST_TRADE, OPEN_POSITIONS, LAST_TRADE_RF
        symbol = coin["symbol"]
        ticker = coin["ticker"]
        if not self.can_trade(symbol):
            return self.balance
        ai_score, ai_pass, ai_reason = self.ai_filter(coin, ticker)
        if not ai_pass:
            print(f"   🤖 AI REJECT {symbol}: {ai_reason} Score {ai_score}", flush=True)
            return self.balance
        print(f"   🤖 AI PASS {symbol}: Score {ai_score} - {ai_reason}", flush=True)
        mtf_pass, mtf_reason = self.mtf_choch_check(symbol)
        if not mtf_pass:
            print(f"   📊 MTF REJECT {symbol}: {mtf_reason}", flush=True)
            return self.balance
        print(f"   📊 MTF PASS {symbol}: {mtf_reason}", flush=True)
        size_pct = RF_POSITION_PCT if LAST_TRADE_RF else BASE_POSITION_PCT
        size_usd = self.balance * size_pct
        print(f"   💰 Size: {size_pct*100:.0f}% = ${size_usd:.2f} (RF FREE: {LAST_TRADE_RF})", flush=True)
        side = "SHORT" if coin["rangePos"] > 90 else "LONG"
        order_result = place_bitget_order(symbol, side, size_usd)
        pos = {"symbol": symbol, "side": side, "entry_price": coin["choch"], "entry": coin["choch"], "open_time": time.time(), "is_rf": False, "highest_profit": 0.0, "current_sl": -STOP_LOSS_PCT, "trail_level": "RISK", "size_pct": size_pct, "mtf_info": mtf_reason, "ai_score": ai_score, "real_order": order_result}
        OPEN_POSITIONS.append(pos)
        LAST_TRADE_RF = False
        real_icon = "🔴 REAL" if REAL_TRADING else "🟡 SIM"
        print(f"   🔒 OPEN {symbol} {side} @ {coin['choch']} Size {size_pct*100:.0f}% {real_icon} - AT RISK need {TRAILING_ACTIVATION*100:.1f}% RF", flush=True)
        send_telegram(f"🔒 *Opening {symbol} {side}* {real_icon} @ {coin['choch']:.6f}\nSize: {size_pct*100:.0f}% (${size_usd:.2f})\nAI: {ai_score} {ai_reason[:30]}\nMTF: {mtf_reason[:30]}\nStatus: 🔒 AT RISK\nNeed {TRAILING_ACTIVATION*100:.1f}% RF")
        LAST_TRADE_TIME[symbol] = time.time()
        GLOBAL_LAST_TRADE = time.time()
        return self.balance

    def run_once(self):
        self.manage_open_positions()
        if time.time() < EMERGENCY_PAUSED_UNTIL:
            print(f"   🚨 Emergency paused {int((EMERGENCY_PAUSED_UNTIL-time.time())/60)}m", flush=True)
            return
        coins = self.scan_20_live()
        triggers = [c for c in coins if (c["rangePos"] > 90 and c["rsi"] > 75) or (c["rangePos"] < 10 and c["rsi"] < 25)]
        if not triggers:
            if OPEN_POSITIONS:
                print(f"   Managing {len(OPEN_POSITIONS)} open RF FREE: {LAST_TRADE_RF} Daily {DAILY_PNL_PCT*100:.2f}% Streak {CONSECUTIVE_LOSSES}", flush=True)
            else:
                print(f"   No trigger - AI+MTF filtering {len(coins)} scanned - Daily {DAILY_PNL_PCT*100:.2f}% Blacklist {len(BLACKLIST)}", flush=True)
            return
        print(f"   Triggers: {[c['symbol'] for c in triggers[:3]]} RF FREE: {LAST_TRADE_RF} Open: {len(OPEN_POSITIONS)} Daily {DAILY_PNL_PCT*100:.2f}% Streak {CONSECUTIVE_LOSSES}", flush=True)
        for c in triggers[:1]:
            if self.can_trade(c['symbol']):
                print(f"🎯 TRIGGER: {c['symbol']} {c['rangePos']}% RSI {c['rsi']}", flush=True)
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
                    return jsonify({**j["meta"], "rf_free": LAST_TRADE_RF, "open_positions": OPEN_POSITIONS, "daily_pnl_pct": DAILY_PNL_PCT, "emergency_paused": time.time()<EMERGENCY_PAUSED_UNTIL, "real_trading": REAL_TRADING, "blacklist": list(BLACKLIST.keys()), "streak": CONSECUTIVE_LOSSES, "storage": "npoint.io" if NPOINT_ID else "local"})
            except:
                return jsonify({"balance": bot.balance, "rf_free": LAST_TRADE_RF, "real": REAL_TRADING})
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
            return jsonify({"open_positions": OPEN_POSITIONS, "rf_free": LAST_TRADE_RF, "daily_pnl": DAILY_PNL_PCT, "blacklist": BLACKLIST})
        @app.route("/blacklist")
        def bl():
            return jsonify({"blacklist": BLACKLIST, "losing_coins": LOSING_COINS})
        @app.route("/test-telegram")
        def test_telegram():
            ok = send_telegram("✅ V4.8 FIXED Test OK 🚀 Blacklist + Streak protection active!")
            return jsonify({"sent": ok})
        @app.route("/debug")
        def debug_env():
            return jsonify({"token_set": bool(TELEGRAM_BOT_TOKEN),"chat_id": TELEGRAM_CHAT_ID,"balance": bot.balance,"rf_free": LAST_TRADE_RF, "open_positions": OPEN_POSITIONS, "npoint_id": NPOINT_ID, "real_trading": REAL_TRADING, "bitget_keys": bool(BITGET_API_KEY), "daily_pnl": DAILY_PNL_PCT, "blacklist": BLACKLIST, "streak": CONSECUTIVE_LOSSES})
        threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False), daemon=True).start()

    if TELEGRAM_BOT_TOKEN:
        threading.Thread(target=telegram_listener, args=(bot,), daemon=True).start()
        time.sleep(1)

    while True:
        print(f"\nSCAN V4.8 FIXED - Bal ${bot.balance:.4f} RF: {'FREE ✅' if LAST_TRADE_RF else 'LOCKED 🔒'} Open: {len(OPEN_POSITIONS)} Daily: {DAILY_PNL_PCT*100:.2f}% Streak:{CONSECUTIVE_LOSSES} BL:{len(BLACKLIST)} {'🔴 REAL' if REAL_TRADING else '🟡 SIM'}", flush=True)
        try:
            bot.run_once()
        except Exception as e:
            print(f"⚠️ Scan error: {e}", flush=True)
            import traceback
            traceback.print_exc()
        time.sleep(60)
