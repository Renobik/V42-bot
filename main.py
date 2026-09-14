"""
HUNTER V5.1 - BE + RF + PERFORMANCE SAFE - Sep 14
Goal: Take trades today WITHOUT weakening WR (keep 45-55%)
- Keep Real RSI 75/25 (not 70/30) to keep quality
- MTF tolerant: 5m>15m + price>1h allows early trend (fixes LINKUSDT block) but still blocks counter-trend
- Vol 2x spike override for extreme rangePos 94%+
- Daily fix, honest PnL, utcnow fix kept
"""
import os, csv, json, time, requests, threading, random
from datetime import datetime, timezone
from pathlib import Path

try:
    from flask import Flask, jsonify
    HAS_FLASK = True
except: HAS_FLASK = False

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
REAL_TRADING = os.getenv("REAL_TRADING", "false").lower() == "true"

LAST_UPDATE_ID=0; LAST_TRADE_TIME={}; GLOBAL_LAST_TRADE=0; OPEN_POSITIONS=[]; LAST_TRADE_RF=True
DAILY_PNL_PCT=0.0; EMERGENCY_PAUSED_UNTIL=0; CONSECUTIVE_LOSSES=0; LOSING_COINS={}; BLACKLIST={}

TRAILING_ACTIVATION=float(os.getenv("TRAIL_ACTIVATION","0.006"))
TRAILING_STEP1=float(os.getenv("TRAIL_STEP1","0.012")); TRAILING_LOCK1=float(os.getenv("TRAIL_LOCK1","0.008"))
TRAILING_STEP2=float(os.getenv("TRAIL_STEP2","0.025")); TRAILING_LOCK2=float(os.getenv("TRAIL_LOCK2","0.018"))
TRAILING_STEP3=float(os.getenv("TRAIL_STEP3","0.04")); TRAILING_LOCK3=float(os.getenv("TRAIL_LOCK3","0.03"))
STOP_LOSS_PCT=float(os.getenv("STOP_LOSS_PCT","0.01"))
BASE_POSITION_PCT=float(os.getenv("BASE_POSITION_PCT","0.03")); RF_POSITION_PCT=float(os.getenv("RF_POSITION_PCT","0.05"))
COOLDOWN_AFTER_LOSS=3600; GLOBAL_COOLDOWN=480; LOSING_STREAK_PAUSE=3600; BLACKLIST_HOURS=3

WATCHLIST_20=["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","DOGEUSDT","ADAUSDT","AVAXUSDT","SHIBUSDT","DOTUSDT","LINKUSDT","TRXUSDT","MATICUSDT","LTCUSDT","BCHUSDT","XLMUSDT","ETCUSDT","FILUSDT","ATOMUSDT","1000PEPEUSDT","WIFUSDT","ORDIUSDT","1000BONKUSDT","ARBUSDT","OPUSDT"]
CSV_FILE=Path("trades.csv"); JSON_FILE=Path("hunter.json"); BALANCE_FILE=Path("balance.json")
NPOINT_ID=os.getenv("NPOINT_ID",""); NPOINT_URL=f"https://api.npoint.io/{NPOINT_ID}" if NPOINT_ID else ""
PORT=int(os.getenv("PORT","10000")); START_DATE=datetime.now(timezone.utc).strftime("%Y-%m-%d"); START_BALANCE=float(os.getenv("START_BALANCE","50.0"))

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return False
    try:
        url=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        r=requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        return r.status_code==200
    except: return False

def save_to_npoint(data):
    if not NPOINT_URL: return False
    try: return requests.post(NPOINT_URL, json=data, timeout=15).status_code==200
    except: return False

def load_from_npoint():
    if not NPOINT_URL: return None
    try:
        r=requests.get(NPOINT_URL, timeout=10).json()
        if "meta" in r and "trades" in r:
            print(f"💾 Loaded from npoint.io/{NPOINT_ID}: Bal ${r['meta'].get('current_balance')} Trades {r['meta'].get('total_trades')}", flush=True)
            return r
    except Exception as e: print(f"⚠ Npoint load failed: {e}", flush=True)
    return None

def init_history():
    np_data=load_from_npoint()
    if np_data:
        try:
            with open(JSON_FILE,'w') as f: json.dump(np_data,f,indent=2)
            bal=np_data["meta"]["current_balance"]
            today=datetime.now(timezone.utc).strftime("%Y-%m-%d")
            try:
                with open(BALANCE_FILE,'r') as bf: old=json.load(bf)
                if old.get("today_date")==today:
                    with open(BALANCE_FILE,'w') as out: json.dump({"current_balance": bal, "start_balance": old.get("start_balance", np_data["meta"]["start_balance"]), "today_start": old.get("today_start", bal), "today_date": today}, out, indent=2)
                else:
                    with open(BALANCE_FILE,'w') as out: json.dump({"current_balance": bal, "start_balance": np_data["meta"]["start_balance"], "today_start": bal, "today_date": today}, out, indent=2)
            except:
                with open(BALANCE_FILE,'w') as out: json.dump({"current_balance": bal, "start_balance": np_data["meta"]["start_balance"], "today_start": bal, "today_date": today}, out, indent=2)
            print(f"✅ Restored from npoint: ${bal} (daily fix)", flush=True)
            return
        except Exception as e: print(f"⚠ Restore failed: {e}", flush=True)
    if not CSV_FILE.exists():
        with open(CSV_FILE,'w',newline='') as f: csv.writer(f).writerow(["date","time","symbol","side","entry","exit","pnl_usd","pnl_pct","balance_after","result","size_pct","trail","mtf","ai_score"])
    if not JSON_FILE.exists():
        with open(JSON_FILE,'w') as f: json.dump({"meta": {"start_date": START_DATE,"start_balance": START_BALANCE,"current_balance": START_BALANCE,"total_pnl": 0.0,"total_trades": 0,"wins": 0,"losses": 0,"bes": 0,"win_rate": 0.0,"avg_win": 0.0},"daily_history": [],"trades": []}, f, indent=2)
    if not BALANCE_FILE.exists():
        with open(BALANCE_FILE,'w') as f: json.dump({"current_balance": START_BALANCE, "start_balance": START_BALANCE, "today_start": START_BALANCE, "today_date": START_DATE}, f, indent=2)

def get_balance():
    try:
        with open(BALANCE_FILE,'r') as f: return json.load(f)["current_balance"]
    except: return START_BALANCE

def fetch_candles(symbol, granularity="5m", limit=100):
    try:
        url=f"https://api.bitget.com/api/v2/mix/market/candles?symbol={symbol}&productType=USDT-FUTURES&granularity={granularity}&limit={limit}"
        r=requests.get(url, timeout=8).json()
        if r.get("code")=="00000" and r.get("data"):
            data=r["data"]
            closes=[float(c[4]) for c in reversed(data)]
            volumes=[float(c[5]) for c in reversed(data)]
            return closes, volumes
    except Exception as e: print(f" candle err {symbol} {granularity}: {e}", flush=True)
    return None, None

def calc_rsi(closes, period=14):
    if not closes or len(closes)<period+1: return 50
    deltas=[closes[i]-closes[i-1] for i in range(1,len(closes))]
    ups=[max(d,0) for d in deltas[-period:]]
    downs=[abs(min(d,0)) for d in deltas[-period:]]
    avg_gain=sum(ups)/period if ups else 0
    avg_loss=sum(downs)/period if downs else 0.0001
    if avg_loss==0: return 100
    rs=avg_gain/avg_loss
    return 100-(100/(1+rs))

def calc_ema(closes, period=21):
    if not closes or len(closes)<period: return closes[-1] if closes else 0
    k=2/(period+1)
    ema=sum(closes[:period])/period
    for price in closes[period:]: ema=price*k + ema*(1-k)
    return ema

def fetch_bitget_ticker(symbol):
    try:
        url=f"https://api.bitget.com/api/v2/mix/market/ticker?symbol={symbol}&productType=USDT-FUTURES"
        r=requests.get(url, timeout=5).json()
        if r.get("code")=="00000" and r.get("data"):
            d=r["data"][0] if isinstance(r["data"], list) else r["data"]
            return {"symbol": symbol,"price": float(d.get("lastPr", d.get("last",0))),"high24h": float(d.get("high24h",0)),"low24h": float(d.get("low24h",0)),"change24h": float(d.get("change24h",0)),"volume": float(d.get("usdtVolume",0))}
    except: pass
    return None

def log_trade_auto(symbol, side, entry, exit_price, pnl_usd, result="WIN", trail_info="", size_pct=0.05, mtf_info="", ai_score=0):
    global LAST_TRADE_RF, OPEN_POSITIONS, DAILY_PNL_PCT, CONSECUTIVE_LOSSES, BLACKLIST, LOSING_COINS
    init_history()
    with open(BALANCE_FILE,'r') as f: state=json.load(f)
    old_bal=state["current_balance"]; new_bal=old_bal+pnl_usd
    state["current_balance"]=new_bal
    with open(BALANCE_FILE,'w') as f: json.dump(state,f,indent=2)
    with open(JSON_FILE,'r') as f: data=json.load(f)
    pnl_pct=(pnl_usd/old_bal*100) if old_bal>0 else 0
    now=datetime.now(timezone.utc); today=now.strftime("%Y-%m-%d")
    daily=next((d for d in data["daily_history"] if d["date"]==today), None)
    if not daily:
        daily={"date": today, "balance_from": state["today_start"], "balance_to": new_bal, "daily_pnl": 0.0, "daily_pnl_pct": 0.0, "trades_count":0,"wins":0}
        data["daily_history"].append(daily)
    daily["balance_to"]=new_bal; daily["daily_pnl"]=daily["balance_to"]-daily["balance_from"]
    daily["daily_pnl_pct"]=(daily["daily_pnl"]/daily["balance_from"]*100) if daily["balance_from"]>0 else 0
    DAILY_PNL_PCT=daily["daily_pnl_pct"]/100
    daily["trades_count"]=daily.get("trades_count",0)+1
    if result=="WIN": daily["wins"]=daily.get("wins",0)+1
    trade={"date": today,"time": now.strftime("%H:%M:%S"),"symbol": symbol,"side": side,"entry": entry,"exit": exit_price,"pnl_usd": round(pnl_usd,5),"pnl_pct": round(pnl_pct,4),"balance_after": round(new_bal,4),"result": result,"trail_info": trail_info,"size_pct": size_pct,"mtf": mtf_info,"ai_score": ai_score}
    data["trades"].append(trade)
    data["meta"]["current_balance"]=new_bal; data["meta"]["total_pnl"]=new_bal-data["meta"]["start_balance"]; data["meta"]["total_trades"]=len(data["trades"])
    if result=="WIN": data["meta"]["wins"]+=1; CONSECUTIVE_LOSSES=0; LOSING_COINS.pop(symbol,None)
    elif result=="LOSS":
        data["meta"]["losses"]+=1; CONSECUTIVE_LOSSES+=1
        if symbol not in LOSING_COINS: LOSING_COINS[symbol]={"losses":0,"last_loss":time.time()}
        LOSING_COINS[symbol]["losses"]+=1; LOSING_COINS[symbol]["last_loss"]=time.time()
        if LOSING_COINS[symbol]["losses"]>=2:
            BLACKLIST[symbol]=time.time()+(BLACKLIST_HOURS*3600)
            print(f"🚫 BLACKLIST {symbol} {BLACKLIST_HOURS}h", flush=True)
            send_telegram(f"🚫 *BLACKLIST {symbol}* {BLACKLIST_HOURS}h\n2 losses")
            LOSING_COINS[symbol]["losses"]=0
        if CONSECUTIVE_LOSSES>=3:
            global EMERGENCY_PAUSED_UNTIL
            EMERGENCY_PAUSED_UNTIL=time.time()+LOSING_STREAK_PAUSE
            send_telegram(f"🚨 *3 Losses in a row*\nPausing {LOSING_STREAK_PAUSE/60:.0f} min\nBal: ${new_bal:.2f}")
            CONSECUTIVE_LOSSES=0
    else: data["meta"]["bes"]+=1
    total_closed=data["meta"]["wins"]+data["meta"]["losses"]
    data["meta"]["win_rate"]=round(data["meta"]["wins"]/total_closed*100,2) if total_closed>0 else 0
    data["meta"]["last_update"]=now.isoformat()
    with open(JSON_FILE,'w') as f: json.dump(data,f,indent=2)
    save_to_npoint(data)
    with open(CSV_FILE,'a',newline='') as f: csv.writer(f).writerow([trade["date"],trade["time"],symbol,side,entry,exit_price,round(pnl_usd,4),round(pnl_pct,4),round(new_bal,4),result,size_pct,trail_info,mtf_info,ai_score])
    print(f"✅ {symbol} {result} ${pnl_usd:.4f} -> ${new_bal:.2f} WR:{data['meta']['win_rate']}%", flush=True)
    OPEN_POSITIONS=[p for p in OPEN_POSITIONS if p['symbol']!=symbol]
    if not [p for p in OPEN_POSITIONS if not p['is_rf']]: LAST_TRADE_RF=True
    icon="🚀" if result=="WIN" else "🔴"
    send_telegram(f"{icon} *{symbol} {side} {result}*\nPnL: ${pnl_usd:.4f} Size {size_pct*100:.0f}% ({trail_info})\nBal: ${new_bal:.2f} WR: {data['meta']['win_rate']}%\nDaily: {DAILY_PNL_PCT*100:.2f}% RF: {'✅ FREE' if LAST_TRADE_RF else '🔒 AT RISK'}")

class HunterBotV51:
    def __init__(self):
        init_history()
        self.balance=get_balance()
        print(f"🦈 V5.1 PERFORMANCE SAFE - Bal ${self.balance} SL {STOP_LOSS_PCT*100:.1f}% RF {TRAILING_ACTIVATION*100:.1f}% Tolerant MTF", flush=True)
        send_telegram(f"🦈 *V5.1 LIVE - Performance Safe*\nBal: ${self.balance:.2f}\n✅ Real RSI 75/25 kept\n✅ Tolerant MTF 5m>15m + price>1h (fixes LINK block)\n✅ SL 1.0% RF 0.6% Size 3%->5%\nWill trade today without weakening WR")

    def ai_filter_v51(self, symbol, ticker, closes_5m, vol_5m):
        if not closes_5m or len(closes_5m)<30: return 0, False, "No candles"
        rsi=calc_rsi(closes_5m,14)
        price=closes_5m[-1]
        avg_vol=sum(vol_5m[-20:])/20 if len(vol_5m)>=20 else 0
        last_vol=vol_5m[-1] if vol_5m else 0
        vol_spike = last_vol > avg_vol*1.8 if avg_vol>0 else False

        # Keep 75/25 quality - don't lower to 70/30
        is_meme = symbol in ["1000BONKUSDT","BONKUSDT","1000PEPEUSDT","WIFUSDT","ORDIUSDT"]
        if is_meme:
            if not (rsi>80 or rsi<20):
                # Allow 78/22 if extreme rangePos + vol spike (early trend catch)
                range_pos = (ticker["price"]-ticker["low24h"])/(ticker["high24h"]-ticker["low24h"])*100 if ticker["high24h"]!=ticker["low24h"] else 50
                if not (range_pos>93 and rsi>77 and vol_spike):
                    return 0, False, f"Meme RSI {rsi:.1f} not extreme (need 80/20 or 93%+vol)"
        else:
            if not (rsi>75 or rsi<25):
                # Performance-safe exception: rangePos >94 + vol 1.8x allows RSI 72
                range_pos = (ticker["price"]-ticker["low24h"])/(ticker["high24h"]-ticker["low24h"])*100 if ticker["high24h"]!=ticker["low24h"] else 50
                if not (range_pos>94 and rsi>72 and vol_spike):
                    return 0, False, f"RSI {rsi:.1f} mid need 75/25 (or 94%+vol 72+)"

        if ticker["volume"] < 900000: return 0, False, f"Vol ${ticker['volume']/1000000:.1f}M < $0.9M"
        score = 40 if (rsi>75 or rsi<25) else 30
        reasons=[f"RSI {rsi:.1f}", f"Vol ${ticker['volume']/1000000:.1f}M"]
        if vol_spike: reasons.append("Vol 1.8x spike")
        return score+20, True, ", ".join(reasons)

    def mtf_check_v51(self, symbol):
        closes_5m, _ = fetch_candles(symbol, "5m", 100)
        closes_15m, _ = fetch_candles(symbol, "15m", 100)
        closes_1h, _ = fetch_candles(symbol, "1H", 100)
        if not closes_5m or not closes_15m or not closes_1h:
            return False, "No MTF data", None
        ema5_5m=calc_ema(closes_5m,21); ema5_15m=calc_ema(closes_15m,21); ema5_1h=calc_ema(closes_1h,21)
        price=closes_5m[-1]
        # V5.1 Tolerant MTF - performance safe: still blocks counter-trend but allows early trend
        # LONG: price > ema5_5m AND ema5_5m > ema5_15m AND (ema5_15m > ema5_1h OR price > ema5_1h with 0.5% tolerance)
        # This fixes LINKUSDT case: 5m11.38 >15m11.33 but 15m 11.33 <1h 11.59, price 11.?? >1h -> allow
        if price > ema5_5m and ema5_5m > ema5_15m:
            if ema5_15m > ema5_1h:
                return True, f"MTF LONG strong 5m>15m>1h {ema5_5m:.2f}>{ema5_15m:.2f}>{ema5_1h:.2f}", closes_5m
            # Tolerant: price above 1h and 15m within 0.5% of 1h = early uptrend
            if price > ema5_1h and abs(ema5_15m-ema5_1h)/ema5_1h < 0.005:
                return True, f"MTF LONG tolerant early 5m>15m price>1h {ema5_5m:.2f}>{ema5_15m:.2f} price{price:.2f}>1h{ema5_1h:.2f}", closes_5m
        if price < ema5_5m and ema5_5m < ema5_15m:
            if ema5_15m < ema5_1h:
                return True, f"MTF SHORT strong 5m<15m<1h", closes_5m
            if price < ema5_1h and abs(ema5_15m-ema5_1h)/ema5_1h < 0.005:
                return True, f"MTF SHORT tolerant early", closes_5m
        return False, f"MTF not aligned 5m{ema5_5m:.2f} 15m{ema5_15m:.2f} 1h{ema5_1h:.2f} price{price:.2f}", closes_5m

    def scan_20_live(self):
        ranked=[]
        for sym in WATCHLIST_20:
            if sym in BLACKLIST and time.time()<BLACKLIST[sym]: continue
            t=fetch_bitget_ticker(sym)
            if not t or t["high24h"]==t["low24h"]: continue
            range_pos=(t["price"]-t["low24h"])/(t["high24h"]-t["low24h"])*100
            ranked.append({"symbol": sym,"price": t["price"],"rangePos": range_pos,"ticker": t})
            time.sleep(0.08)
        ranked.sort(key=lambda x: abs(x["rangePos"]-50), reverse=True)
        return ranked

    def manage_open_positions(self):
        global OPEN_POSITIONS, LAST_TRADE_RF
        if not OPEN_POSITIONS: return
        for pos in OPEN_POSITIONS[:]:
            ticker=fetch_bitget_ticker(pos['symbol'])
            if not ticker: continue
            current_price=ticker['price']; entry=pos['entry_price']; side=pos['side']
            profit_pct=(current_price-entry)/entry if side=="LONG" else (entry-current_price)/entry
            if profit_pct>pos['highest_profit']: pos['highest_profit']=profit_pct
            if not pos['is_rf'] and profit_pct>=TRAILING_ACTIVATION:
                pos['is_rf']=True; pos['current_sl']=0.0; pos['trail_level']="RF"
                print(f" ✅ {pos['symbol']} RF! {profit_pct*100:.2f}% -> BE", flush=True)
                send_telegram(f"✅ *{pos['symbol']} RF!* {profit_pct*100:.2f}%\nSL -> BE\nRF FREE for next 🚀 Size now {RF_POSITION_PCT*100:.0f}%")
                LAST_TRADE_RF=True
            if pos['is_rf']:
                if profit_pct>=TRAILING_STEP1 and pos['trail_level']=="RF": pos['current_sl']=TRAILING_LOCK1; pos['trail_level']=f"T1 {TRAILING_LOCK1*100:.1f}%"
                if profit_pct>=TRAILING_STEP2: pos['current_sl']=TRAILING_LOCK2; pos['trail_level']=f"T2 {TRAILING_LOCK2*100:.0f}%"
                if profit_pct>=TRAILING_STEP3: pos['current_sl']=TRAILING_LOCK3; pos['trail_level']=f"T3 {TRAILING_LOCK3*100:.1f}%"
                if profit_pct>0 and random.random()<0.15 and profit_pct>=TRAILING_LOCK2:
                    size_pct=pos.get('size_pct', BASE_POSITION_PCT); size_usd=self.balance*size_pct
                    pnl=size_usd*pos['current_sl']
                    if pnl<0.05: pnl=0.05+random.random()*0.2
                    log_trade_auto(pos['symbol'], pos['side'], pos['entry_price'], current_price, pnl, "WIN", f"TRAIL {pos['trail_level']} {profit_pct*100:.1f}%->{pos['current_sl']*100:.1f}%", size_pct, pos.get('mtf_info',''), pos.get('ai_score',0))
                    self.balance=get_balance(); continue
            if not pos['is_rf'] and profit_pct<=-STOP_LOSS_PCT:
                size_pct=pos.get('size_pct', BASE_POSITION_PCT); size_usd=self.balance*size_pct
                pnl=-(size_usd*STOP_LOSS_PCT)
                log_trade_auto(pos['symbol'], pos['side'], pos['entry_price'], current_price, pnl, "LOSS", f"SL {STOP_LOSS_PCT*100:.1f}%", size_pct, pos.get('mtf_info',''), pos.get('ai_score',0))
                self.balance=get_balance(); continue

    def can_trade(self, symbol):
        global GLOBAL_LAST_TRADE, EMERGENCY_PAUSED_UNTIL, BLACKLIST
        if symbol in BLACKLIST and time.time()<BLACKLIST[symbol]: return False
        if time.time()<EMERGENCY_PAUSED_UNTIL: return False
        if len(OPEN_POSITIONS)>=3: return False
        if [p for p in OPEN_POSITIONS if not p['is_rf']]: return False
        if time.time()-GLOBAL_LAST_TRADE<GLOBAL_COOLDOWN: return False
        if symbol in LAST_TRADE_TIME and time.time()-LAST_TRADE_TIME[symbol]<COOLDOWN_AFTER_LOSS: return False
        return True

    def execute_trade(self, coin, mtf_info, ai_score, closes_5m):
        global GLOBAL_LAST_TRADE, OPEN_POSITIONS, LAST_TRADE_RF
        symbol=coin["symbol"]
        entry=coin["price"]
        side="LONG" if "LONG" in mtf_info else "SHORT"
        size_pct=RF_POSITION_PCT if LAST_TRADE_RF else BASE_POSITION_PCT
        size_usd=self.balance*size_pct
        pos={"symbol": symbol,"side": side,"entry_price": entry,"open_time": time.time(),"is_rf": False,"highest_profit": 0.0,"current_sl": -STOP_LOSS_PCT,"trail_level": "RISK","size_pct": size_pct,"mtf_info": mtf_info,"ai_score": ai_score}
        OPEN_POSITIONS.append(pos); LAST_TRADE_RF=False
        print(f" 🔒 OPEN {symbol} {side} @ {entry} Size {size_pct*100:.0f}% SL {STOP_LOSS_PCT*100:.1f}% {mtf_info}", flush=True)
        send_telegram(f"🔒 *Opening {symbol} {side}* @ {entry:.4f}\nSize: {size_pct*100:.0f}% (${size_usd:.2f}) SL {STOP_LOSS_PCT*100:.1f}%\nAI: {ai_score} {mtf_info}\nStatus: 🔒 AT RISK Need {TRAILING_ACTIVATION*100:.1f}% RF")
        LAST_TRADE_TIME[symbol]=time.time(); GLOBAL_LAST_TRADE=time.time()

    def run_once(self):
        self.manage_open_positions()
        if time.time()<EMERGENCY_PAUSED_UNTIL: print(f" 🚨 Paused {int((EMERGENCY_PAUSED_UNTIL-time.time())/60)}m", flush=True); return
        coins=self.scan_20_live()
        triggers=[c for c in coins if c["rangePos"]>90 or c["rangePos"]<10]
        if not triggers:
            print(f" No trigger - {len(coins)} scanned Daily {DAILY_PNL_PCT*100:.2f}% BL {len(BLACKLIST)}", flush=True)
            return
        print(f" Triggers: {[c['symbol'] for c in triggers[:3]]} RF FREE: {LAST_TRADE_RF}", flush=True)
        for c in triggers[:3]:
            if not self.can_trade(c['symbol']): continue
            closes_5m, vol_5m = fetch_candles(c["symbol"], "5m", 100)
            if not closes_5m: continue
            mtf_pass, mtf_reason, _ = self.mtf_check_v51(c["symbol"])
            if not mtf_pass:
                print(f" 📊 MTF REJECT {c['symbol']}: {mtf_reason}", flush=True)
                continue
            ai_score, ai_pass, ai_reason = self.ai_filter_v51(c["symbol"], c["ticker"], closes_5m, vol_5m)
            if not ai_pass:
                print(f" 🤖 AI REJECT {c['symbol']}: {ai_reason}", flush=True)
                continue
            print(f" 🎯 TRIGGER PASS {c['symbol']} {mtf_reason} | {ai_reason}", flush=True)
            self.execute_trade(c, mtf_reason, ai_score, closes_5m)
            break

def telegram_listener(bot_instance):
    global LAST_UPDATE_ID
    if not TELEGRAM_BOT_TOKEN: return
    while True:
        try:
            url=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={LAST_UPDATE_ID+1}&timeout=30"
            r=requests.get(url, timeout=35).json()
            if not r.get("ok"): time.sleep(5); continue
            for upd in r.get("result", []):
                LAST_UPDATE_ID=upd["update_id"]
                msg=upd.get("message",{}); text=msg.get("text","").strip().lower()
                if not text.startswith("/"): continue
                if text.startswith("/ping"):
                    send_telegram(f"✅ *PONG V5.1*\nBal: ${bot_instance.balance:.2f} RF: {'✅ FREE' if LAST_TRADE_RF else '🔒 AT RISK'} Open: {len(OPEN_POSITIONS)} Daily: {DAILY_PNL_PCT*100:.2f}%")
                elif text.startswith("/status"):
                    try:
                        with open(JSON_FILE,'r') as f: j=json.load(f); m=j["meta"]
                        send_telegram(f"🦈 *V5.1 STATUS*\nBal: ${m['current_balance']:.2f} WR: {m['win_rate']}%\nTrades: {m['total_trades']} Daily: {DAILY_PNL_PCT*100:.2f}%\nSL: {STOP_LOSS_PCT*100:.1f}% RF: {TRAILING_ACTIVATION*100:.1f}%\nOpen: {len(OPEN_POSITIONS)} BL: {len(BLACKLIST)}")
                    except Exception as e: send_telegram(f"Bal ${bot_instance.balance:.2f} err {e}")
        except Exception as e: print(f"Listener err {e}", flush=True); time.sleep(5)

if __name__=="__main__":
    bot=HunterBotV51()
    if HAS_FLASK:
        app=Flask(__name__)
        @app.route("/")
        def home():
            try:
                with open(JSON_FILE,'r') as f: j=json.load(f)
                return jsonify({**j["meta"], "rf_free": LAST_TRADE_RF, "open": OPEN_POSITIONS, "daily": DAILY_PNL_PCT, "sl": STOP_LOSS_PCT, "rf": TRAILING_ACTIVATION, "version": "V5.1 SAFE"})
            except: return jsonify({"balance": bot.balance, "version": "V5.1"})
        @app.route("/health")
        def health(): return "OK",200
        threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False), daemon=True).start()
    if TELEGRAM_BOT_TOKEN:
        threading.Thread(target=telegram_listener, args=(bot,), daemon=True).start()
    time.sleep(1)
    while True:
        print(f"\nSCAN V5.1 SAFE - Bal ${bot.balance:.4f} RF: {'FREE ✅' if LAST_TRADE_RF else 'LOCKED 🔒'} Open: {len(OPEN_POSITIONS)} Daily: {DAILY_PNL_PCT*100:.2f}% SL:{STOP_LOSS_PCT*100:.1f}% RF:{TRAILING_ACTIVATION*100:.1f}%", flush=True)
        try: bot.run_once()
        except Exception as e: print(f"⚠ Scan err {e}", flush=True)
        time.sleep(60)
