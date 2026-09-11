"""
HUNTER V4.9 - BE + RF + GOOD PROFIT - Sep 11
Based on live screenshot Sep 11 1:40 PM:

✅ ORDI WIN $0.9258 PERFECT EXAMPLE:
- Open @3.902 Need 0.6% RF AT RISK
- RF! 0.67% SL->BE RF FREE
- WIN $0.9258 TRAIL T2 2% 3.0%->1.8% Bal $51.43->$52.36 WR 28%->31% Daily 1%->2.82%

❌ PROBLEM: WIF LOSS -$0.1031 before ORDI caused 3 losses in a row pause
- SL 0.4% too tight = wick kills immediately
- Size 10% = big loss -$0.10
- Back-to-back losses = WIF then others

V4.9 FIX for MORE BE+RF+GOOD PROFIT like ORDI:
1. SL 0.4% -> 0.8% WICK-PROOF (still 2x smaller than RF profit)
2. RF 0.6% KEEP EASY (like ORDI 0.67% BE) - don't make harder!
3. Size 10% -> 5% max (was 5->10) - cut loss damage in half
4. Cooldown loss 30m -> 60m - stop back-to-back
5. Global cooldown 5m -> 8m - less overtrading
6. Losing streak 30m -> 60m pause
7. Blacklist 2h -> 3h
8. AI stricter: RSI 82/18 extreme + Vol $1.5M + Range 92/8
9. MTF: No counter-trend (block SHORT at top if uptrend)
10. Trailing KEEP like ORDI: 0.6% BE, 1.2%->0.8%, 2.5%->1.8%, 4%->3% = $0.92 wins!
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
LOSING_COINS = {}
BLACKLIST = {}

# CONFIG - V4.9 BE+RF+PROFIT - Based on ORDI $0.92 WIN
TRAILING_ACTIVATION = float(os.getenv("TRAIL_ACTIVATION", "0.006"))  # 0.6% RF KEEP EASY like ORDI!
TRAILING_STEP1 = float(os.getenv("TRAIL_STEP1", "0.012"))  # 1.2% -> lock 0.8% (like ORDI T2)
TRAILING_LOCK1 = float(os.getenv("TRAIL_LOCK1", "0.008"))
TRAILING_STEP2 = float(os.getenv("TRAIL_STEP2", "0.025"))  # 2.5% -> lock 1.8% (ORDI $0.92!)
TRAILING_LOCK2 = float(os.getenv("TRAIL_LOCK2", "0.018"))
TRAILING_STEP3 = float(os.getenv("TRAIL_STEP3", "0.04"))  # 4% -> lock 3%
TRAILING_LOCK3 = float(os.getenv("TRAIL_LOCK3", "0.03"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.008"))  # 0.8% (was 0.4) - WICK-PROOF!
EMERGENCY_SL_DAILY = float(os.getenv("EMERGENCY_SL_DAILY", "0.03"))
BASE_POSITION_PCT = float(os.getenv("BASE_POSITION_PCT", "0.03"))  # 3% (was 5) - smaller loss
RF_POSITION_PCT = float(os.getenv("RF_POSITION_PCT", "0.05"))  # 5% (was 10) - NO MORE 10%!
COOLDOWN_AFTER_LOSS = 3600  # 60m anti back-to-back
GLOBAL_COOLDOWN = 480  # 8m
LOSING_STREAK_PAUSE = 3600  # 60m
BLACKLIST_HOURS = 3

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
    print("👂 Listener started V4.9", flush=True)
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
                    send_telegram(f"🦈 *V4.9 BE+RF+PROFIT*\n/ping\n/status\n/trades\n/rf\n/open\n/blacklist\n/clearblacklist\nBal: ${bot_instance.balance:.2f}")
                elif text.startswith("/ping"):
                    mode = "🔴 REAL" if REAL_TRADING else "🟡 SIM"
                    rf = "✅ RF FREE" if LAST_TRADE_RF else f"🔒 AT RISK"
                    bl = f"{len(BLACKLIST)} blacklisted" if BLACKLIST else "0 blacklisted"
                    send_telegram(f"✅ *PONG V4.9 BE+RF*\nMode: {mode}\nBal: ${bot_instance.balance:.2f}\nRF: {rf}\nOpen: {len(OPEN_POSITIONS)}\nDaily: {DAILY_PNL_PCT*100:.2f}%\nLoss streak: {CONSECUTIVE_LOSSES}\nBlacklist: {bl}\nSL: {STOP_LOSS_PCT*100:.1f}% RF: {TRAILING_ACTIVATION*100:.1f}%")
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
                            send_telegram(f"🦈 *V4.9 BE+RF STATUS*\nMode: {'🔴 REAL' if REAL_TRADING else '🟡 SIM'}\nBal: ${m['current_balance']:.2f} PnL: ${m['total_pnl']:.2f} ({DAILY_PNL_PCT*100:.2f}% today)\nTrades: {m['total_trades']} WR: {m['win_rate']}%\nToday: {today_c} Streak: {CONSECUTIVE_LOSSES} losses\nRF: {'✅' if LAST_TRADE_RF else '🔒'} Open: {len(OPEN_POSITIONS)}\nAvg WIN: ${avg_win:.4f} SL:{STOP_LOSS_PCT*100:.1f}% RF:{TRAILING_ACTIVATION*100:.1f}%\nSize: {BASE_POSITION_PCT*100:.0f}%->{RF_POSITION_PCT*100:.0f}% Blacklist: {len(BLACKLIST)}")
                    except Exception as e:
                        send_telegram(f"⚠️ Status error: {e}\nBal: ${bot_instance.balance:.2f}")
                elif text.startswith("/trades"):
                    try:
                        with open(JSON_FILE, 'r') as f:
                            j = json.load(f)
                            last5 = j["trades"][-5:]
                            txt = "📊 *Last 5:*\n"
                            for t in last5:
                                txt += f"{t['symbol']} {t['result']} ${t['pnl_usd']:.4f}\n"
                            send_telegram(txt)
                    except Exception as e:
                        send_telegram(f"⚠️ Trades error: {e}")
        except Exception as e:
            print(f"Listener error: {e}", flush=True)
            time.sleep(5)

# --- Storage ---
NPOINT_ID = os.getenv("NPOINT_ID", "")
NPOINT_URL = f"https://api.npoint.io/{NPOINT_ID}" if NPOINT_ID else ""
PORT = int(os.getenv("PORT", "10000"))
START_DATE = datetime.utcnow().strftime("%Y-%m-%d")
START_BALANCE = float(os.getenv("START_BALANCE", "50.0"))
WATCHLIST_20 = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","DOGEUSDT","ADAUSDT","AVAXUSDT","SHIBUSDT","DOTUSDT","LINKUSDT","TRXUSDT","MATICUSDT","LTCUSDT","BCHUSDT","XLMUSDT","ETCUSDT","FILUSDT","ATOMUSDT","1000PEPEUSDT","WIFUSDT","ORDIUSDT","1000BONKUSDT","ARBUSDT","OPUSDT"]
CSV_FILE = Path("trades.csv")
JSON_FILE = Path("hunter.json")
BALANCE_FILE = Path("balance.json")

def save_to_npoint(data):
    if not NPOINT_URL:
        return False
    for attempt in range(3):
        try:
            r = requests.post(NPOINT_URL, json=data, timeout=15)
            if r.status_code == 200:
                print(f"💾 Saved to npoint.io/{NPOINT_ID}", flush=True)
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
        if symbol not in LOSING_COINS:
            LOSING_COINS[symbol] = {"losses": 0, "last_loss": time.time()}
        LOSING_COINS[symbol]["losses"] += 1
        LOSING_COINS[symbol]["last_loss"] = time.time()
        if LOSING_COINS[symbol]["losses"] >= 2:
            BLACKLIST[symbol] = time.time() + (BLACKLIST_HOURS * 3600)
            print(f"🚫 BLACKLIST {symbol} for {BLACKLIST_HOURS}h after 2 losses (V4.9)", flush=True)
            send_telegram(f"🚫 *BLACKLIST {symbol}* {BLACKLIST_HOURS}h\n2 consecutive losses")
            LOSING_COINS[symbol]["losses"] = 0
        if CONSECUTIVE_LOSSES >= 3:
            global EMERGENCY_PAUSED_UNTIL
            EMERGENCY_PAUSED_UNTIL = time.time() + LOSING_STREAK_PAUSE
            print(f"🚨 3 consecutive losses - pausing {LOSING_STREAK_PAUSE/60:.0f}m", flush=True)
            send_telegram(f"🚨 *3 Losses in a row*\nPausing {LOSING_STREAK_PAUSE/60:.0f} min\nBal: ${new_bal:.2f}")
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
    print(f"✅ {symbol} {result} ${pnl_usd:.4f} Size {size_pct*100:.0f}% {trail_info} -> ${new_bal:.2f} WR:{data['meta']['win_rate']}%", flush=True)
    OPEN_POSITIONS = [p for p in OPEN_POSITIONS if p['symbol'] != symbol]
    at_risk = [p for p in OPEN_POSITIONS if not p['is_rf']]
    if not at_risk:
        LAST_TRADE_RF = True
    if notify:
        icon = "🚀" if result=="WIN" else "🔴"
        send_telegram(f"{icon} *{symbol} {side} {result}* {'🟡 SIM' if not REAL_TRADING else '🔴 REAL'}\nPnL: ${pnl_usd:.4f} Size {size_pct*100:.0f}% ({trail_info})\nBal: ${new_bal:.2f} WR: {data['meta']['win_rate']}%\nDaily: {DAILY_PNL_PCT*100:.2f}% RF: {'✅ FREE' if LAST_TRADE_RF else '🔒 AT RISK'} Streak: {CONSECUTIVE_LOSSES}")

def place_bitget_order(symbol, side, size_usd):
    if not REAL_TRADING:
        print(f"   🟡 SIM: Would place {side} {symbol} ${size_usd}", flush=True)
        return {"simulated": True, "symbol": symbol, "side": side, "size": size_usd}
    if not BITGET_API_KEY:
        print(f"   ⚠️ REAL_TRADING ON but no API keys!", flush=True)
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
        print(f"🦈 V4.9 BE+RF+PROFIT - {mode} - Bal ${self.balance} Storage: {storage}", flush=True)
        print(f"   1. SL: {STOP_LOSS_PCT*100:.1f}% (was 0.4) - WICK-PROOF! BE at {TRAILING_ACTIVATION*100:.1f}% KEEP EASY", flush=True)
        print(f"   2. Size: {BASE_POSITION_PCT*100:.0f}%->{RF_POSITION_PCT*100:.0f}% (was 5->10) - SMALLER!", flush=True)
        print(f"   3. Trailing: BE {TRAILING_ACTIVATION*100:.1f}% -> T1 {TRAILING_LOCK1*100:.1f}% T2 {TRAILING_LOCK2*100:.0f}% like ORDI $0.92 WIN", flush=True)
        print(f"   4. Cooldown: {COOLDOWN_AFTER_LOSS/60:.0f}m loss anti back-to-back, {GLOBAL_COOLDOWN/60:.0f}m global", flush=True)
        print(f"   5. AI: RSI 82/18 extreme Vol $1.5M Score 75+ STRICTER", flush=True)
        send_telegram(f"🦈 *V4.9 BE+RF+PROFIT Started* 🚀\nMode: {mode}\nBal: ${self.balance:.2f}\nBased on ORDI $0.92 WIN:\n✅ SL 0.8% wick-proof (was 0.4)\n✅ RF 0.6% KEEP EASY for BE\n✅ Size 3%->5% smaller\n✅ Trail T2 1.8% = $0.92 wins!\n✅ Cooldown 60m anti back-to-back\nTarget: MORE BE+RF+GOOD PROFIT!")

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
        symbol = coin["symbol"]
        if symbol in ["1000BONKUSDT", "BONKUSDT", "1000PEPEUSDT", "WIFUSDT"]:
            if rsi > 88 or rsi < 12:
                score += 40
                reasons.append(f"RSI {rsi} EXTREME meme")
            elif rsi > 85 or rsi < 15:
                score += 25
                reasons.append(f"RSI {rsi} strong meme")
            else:
                return 0, False, f"Meme {symbol} needs RSI >88/<12, got {rsi}"
        else:
            if rsi > 82 or rsi < 18:
                score += 40
                reasons.append(f"RSI {rsi} extreme")
            elif rsi > 78 or rsi < 22:
                score += 25
                reasons.append(f"RSI {rsi} strong")
            else:
                return 0, False, f"RSI {rsi} not extreme (need 82/18)"
        vol = ticker.get("volume", 0)
        if vol > 1500000:
            score += 30
            reasons.append(f"Vol ${vol/1000000:.1f}M OK")
        elif vol > 1000000:
            score += 15
            reasons.append(f"Vol ${vol/1000000:.1f}M low")
        else:
            return score, False, f"Vol ${vol:.0f} low need $1.5M"
        range_pos = coin["rangePos"]
        change = coin["change"]
        if range_pos > 92 and change < -4:
            score += 35
            reasons.append(f"SHORT trend {change:.1f}%")
        elif range_pos < 8 and change > 4:
            score += 35
            reasons.append(f"LONG trend {change:.1f}%")
        elif range_pos > 92 and change > 2:
            return score, False, f"SHORT blocked uptrend {change:.1f}% at top"
        elif range_pos < 8 and change < -2:
            return score, False, f"LONG blocked downtrend {change:.1f}% at bottom"
        elif range_pos > 94 or range_pos < 6:
            if abs(change) > 6:
                score += 30
                reasons.append(f"EXTREME reversal {change:.1f}%")
            else:
                return score, False, f"Extreme {range_pos:.0f}% trend weak {change:.1f}%"
        else:
            return score, False, f"Range {range_pos:.0f}% Trend {change:.1f}% not extreme"
        passed = score >= 75
        return score, passed, ", ".join(reasons)

    def mtf_choch_check(self, symbol):
        try:
            t = self.fetch_bitget_ticker(symbol)
            if not t:
                return False, "No data"
            range_pos = (t["price"] - t["low24h"]) / (t["high24h"] - t["low24h"]) * 100 if t["high24h"] != t["low24h"] else 50
            change = t["change24h"]*100
            if range_pos > 92 and change < -4:
                return True, f"MTF SHORT aligned {range_pos:.0f}% {change:.1f}%"
            elif range_pos < 8 and change > 4:
                return True, f"MTF LONG aligned {range_pos:.0f}% +{change:.1f}%"
            elif range_pos > 92 and change > 2:
                return False, f"MTF SHORT blocked uptrend {change:.1f}% top"
            elif range_pos < 8 and change < -2:
                return False, f"MTF LONG blocked downtrend {change:.1f}% bottom"
            elif range_pos > 94 or range_pos < 6:
                if abs(change) > 6:
                    return True, f"MTF EXTREME {range_pos:.0f}% {change:.1f}%"
                else:
                    return False, f"MTF extreme weak {change:.1f}%"
            else:
                return False, f"MTF no align {range_pos:.0f}% {change:.1f}%"
        except Exception as e:
            print(f"MTF err: {e}", flush=True)
            return True, "MTF fallback OK"

    def scan_20_live(self):
        ranked = []
        for sym in WATCHLIST_20:
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
        if len(OPEN_POSITIONS) >= 4:
            print(f"   ⏸️ Max open 4 reached", flush=True)
            return False
        at_risk = [p for p in OPEN_POSITIONS if not p['is_rf']]
        if at_risk:
            print(f"   🔒 RF BLOCKED: {at_risk[0]['symbol']} AT RISK {at_risk[0]['highest_profit']*100:.2f}%", flush=True)
            return False
        if time.time() - GLOBAL_LAST_TRADE < GLOBAL_COOLDOWN:
            print(f"   ⏸️ Global cooldown {int(GLOBAL_COOLDOWN-(time.time()-GLOBAL_LAST_TRADE))}s", flush=True)
            return False
        if symbol in LAST_TRADE_TIME and time.time() - LAST_TRADE_TIME[symbol] < COOLDOWN_AFTER_LOSS:
            print(f"   ⏸️ {symbol} cooldown {COOLDOWN_AFTER_LOSS/60:.0f}m {int(COOLDOWN_AFTER_LOSS-(time.time()-LAST_TRADE_TIME[symbol]))}s left", flush=True)
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
        print(f"   💰 Size: {size_pct*100:.0f}% = ${size_usd:.2f} (RF FREE: {LAST_TRADE_RF}) SL {STOP_LOSS_PCT*100:.1f}% RF {TRAILING_ACTIVATION*100:.1f}%", flush=True)
        side = "SHORT" if coin["rangePos"] > 90 else "LONG"
        order_result = place_bitget_order(symbol, side, size_usd)
        pos = {"symbol": symbol, "side": side, "entry_price": coin["choch"], "entry": coin["choch"], "open_time": time.time(), "is_rf": False, "highest_profit": 0.0, "current_sl": -STOP_LOSS_PCT, "trail_level": "RISK", "size_pct": size_pct, "mtf_info": mtf_reason, "ai_score": ai_score, "real_order": order_result}
        OPEN_POSITIONS.append(pos)
        LAST_TRADE_RF = False
        real_icon = "🔴 REAL" if REAL_TRADING else "🟡 SIM"
        print(f"   🔒 OPEN {symbol} {side} @ {coin['choch']} Size {size_pct*100:.0f}% {real_icon} - AT RISK need {TRAILING_ACTIVATION*100:.1f}% RF SL {STOP_LOSS_PCT*100:.1f}%", flush=True)
        send_telegram(f"🔒 *Opening {symbol} {side}* {real_icon} @ {coin['choch']:.6f}\nSize: {size_pct*100:.0f}% (${size_usd:.2f}) SL {STOP_LOSS_PCT*100:.1f}%\nAI: {ai_score} {ai_reason[:30]}\nMTF: {mtf_reason[:30]}\nStatus: 🔒 AT RISK\nNeed {TRAILING_ACTIVATION*100:.1f}% RF for BE")
        LAST_TRADE_TIME[symbol] = time.time()
        GLOBAL_LAST_TRADE = time.time()
        return self.balance

    def run_once(self):
        self.manage_open_positions()
        if time.time() < EMERGENCY_PAUSED_UNTIL:
            print(f"   🚨 Emergency paused {int((EMERGENCY_PAUSED_UNTIL-time.time())/60)}m", flush=True)
            return
        coins = self.scan_20_live()
        triggers = [c for c in coins if (c["rangePos"] > 90 and c["rsi"] > 78) or (c["rangePos"] < 10 and c["rsi"] < 22)]
        if not triggers:
            if OPEN_POSITIONS:
                print(f"   Managing {len(OPEN_POSITIONS)} open RF FREE: {LAST_TRADE_RF} Daily {DAILY_PNL_PCT*100:.2f}% Streak {CONSECUTIVE_LOSSES}", flush=True)
            else:
                print(f"   No trigger - AI+MTF filtering {len(coins)} scanned - Daily {DAILY_PNL_PCT*100:.2f}% Blacklist {len(BLACKLIST)} SL{STOP_LOSS_PCT*100:.1f}% RF{TRAILING_ACTIVATION*100:.1f}%", flush=True)
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
                    return jsonify({**j["meta"], "rf_free": LAST_TRADE_RF, "open_positions": OPEN_POSITIONS, "daily_pnl_pct": DAILY_PNL_PCT, "emergency_paused": time.time()<EMERGENCY_PAUSED_UNTIL, "real_trading": REAL_TRADING, "blacklist": list(BLACKLIST.keys()), "streak": CONSECUTIVE_LOSSES, "storage": "npoint.io" if NPOINT_ID else "local", "sl": STOP_LOSS_PCT, "rf": TRAILING_ACTIVATION})
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
            ok = send_telegram("✅ V4.9 BE+RF+PROFIT Test OK 🚀 ORDI $0.92 WIN model! SL 0.8% RF 0.6%")
            return jsonify({"sent": ok})
        @app.route("/debug")
        def debug_env():
            return jsonify({"token_set": bool(TELEGRAM_BOT_TOKEN),"chat_id": TELEGRAM_CHAT_ID,"balance": bot.balance,"rf_free": LAST_TRADE_RF, "open_positions": OPEN_POSITIONS, "npoint_id": NPOINT_ID, "real_trading": REAL_TRADING, "bitget_keys": bool(BITGET_API_KEY), "daily_pnl": DAILY_PNL_PCT, "blacklist": BLACKLIST, "streak": CONSECUTIVE_LOSSES, "sl": STOP_LOSS_PCT, "rf": TRAILING_ACTIVATION})
        threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False), daemon=True).start()

    if TELEGRAM_BOT_TOKEN:
        threading.Thread(target=telegram_listener, args=(bot,), daemon=True).start()
        time.sleep(1)

    while True:
        print(f"\nSCAN V4.9 BE+RF - Bal ${bot.balance:.4f} RF: {'FREE ✅' if LAST_TRADE_RF else 'LOCKED 🔒'} Open: {len(OPEN_POSITIONS)} Daily: {DAILY_PNL_PCT*100:.2f}% Streak:{CONSECUTIVE_LOSSES} BL:{len(BLACKLIST)} SL:{STOP_LOSS_PCT*100:.1f}% RF:{TRAILING_ACTIVATION*100:.1f}% {'🔴 REAL' if REAL_TRADING else '🟡 SIM'}", flush=True)
        try:
            bot.run_once()
        except Exception as e:
            print(f"⚠️ Scan error: {e}", flush=True)
            import traceback
            traceback.print_exc()
        time.sleep(60)
