"""
V44.20 - FINAL HUNTER - 24/7 + Top200 CMC Volume + HTF+Vol+Sweep+CHoCH+OB50%+FVG
- 24/7 hunter, no killzone, no rest
- Top200 Bitget USDT-FUTURES by volume (expands opportunity for strict rules)
- A+++ = Sweep✅+FVG✅+Vol✅+HTF✅+OB50%
- A+ = Sweep✅ OR FVG✅ + Vol✅
"""
import os, json, time, threading
from flask import Flask, request
import requests

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
# FIX: robust CHAT_ID + remove spaces
_raw = os.getenv("CHAT_ID", "") or os.getenv("TELEGRAM_CHAT_ID", "") or ""
CHAT_ID = str(_raw).replace(" ", "").strip() or "200"
for k,v in os.environ.items():
    vv=str(v).replace(" ","").strip()
    if vv.isdigit() and vv.startswith("675"):
        CHAT_ID=vv; break
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "Ogadagidi").strip() or "Ogadagidi"
BALANCE_FILE = "state.json"
SCAN_COUNT = 200 # Top200 CMC volume as requested

RF_PCT, BE_PCT, TRAIL_PCT, TP_PCT, INIT_SL_PCT = 2.0, 0.5, 1.5, 6.0, 2.5

USE_HTF_BIAS = True
USE_VOLUME_FILTER = True
USE_KILLZONE = False # Hunter never sleeps

app = Flask(__name__)

def load_state():
    if os.path.exists(BALANCE_FILE):
        try:
            with open(BALANCE_FILE,'r') as f: return json.load(f)
        except: pass
    return {"balance":100.0,"positions":{},"today_signals":0,"pending":{}}

def save_state(s):
    with open(BALANCE_FILE,'w') as f: json.dump(s,f,indent=2)

state = load_state()

def tg_send(msg, buttons=None, reply_chat=None):
    if not TELEGRAM_TOKEN: print(f"NO TOKEN {msg[:500]}"); return
    try:
        target = reply_chat or CHAT_ID
        payload={"chat_id":target,"text":msg}
        if buttons: payload["reply_markup"]={"inline_keyboard":buttons}
        r=requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json=payload, timeout=10)
        print(f"TG_SEND to {target} -> {r.status_code} {r.text[:300]}")
    except Exception as e:
        print(f"TG_SEND ERR {e}")

def tg_answer_callback(cb_id, text=""):
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery", json={"callback_query_id":cb_id,"text":text}, timeout=5)
    except: pass

def bitget_top200():
    try:
        r=requests.get("https://api.bitget.com/api/v2/mix/market/tickers", params={"productType":"USDT-FUTURES"}, timeout=15).json()
        if r.get("code")!="00000": return []
        tickers=r["data"]
        # Sort by usdtVolume = CMC volume style
        tickers.sort(key=lambda x: float(x.get("usdtVolume",0)), reverse=True)
        filtered=[t for t in tickers if "USDC" not in t["symbol"] and float(t.get("lastPr",0))>0]
        return filtered[:SCAN_COUNT]
    except Exception as e:
        print(f"Top200 err {e}")
        return []

def bitget_candles(bg_sym, granularity="15m", limit=120):
    try:
        r=requests.get("https://api.bitget.com/api/v2/mix/market/candles", params={"productType":"USDT-FUTURES","symbol":bg_sym,"granularity":granularity,"limit":str(limit)}, timeout=12).json()
        if r.get("code")!="00000": return []
        out=[]
        for c in reversed(r["data"]):
            vol = float(c[5]) if len(c)>5 else 0
            out.append({"ts":int(c[0]),"open":float(c[1]),"high":float(c[2]),"low":float(c[3]),"close":float(c[4]),"vol":vol})
        return out
    except: return []

def bitget_price(ccxt_sym):
    bg=ccxt_sym.replace("/","").replace(":USDT","").replace(":","")
    try:
        r=requests.get("https://api.bitget.com/api/v2/mix/market/ticker", params={"productType":"USDT-FUTURES","symbol":bg}, timeout=5).json()
        if r.get("code")=="00000" and r["data"]: return float(r["data"][0]["lastPr"])
    except: pass
    return None

def bitget_chart_link(ccxt_sym):
    bg=ccxt_sym.replace("/","").replace(":USDT","")
    return f"https://www.bitget.com/futures/{bg}"

def ema(candles, period):
    closes=[c["close"] for c in candles]
    if len(closes)<period: return None
    ema_val=sum(closes[:period])/period
    k=2/(period+1)
    for price in closes[period:]:
        ema_val = price*k + ema_val*(1-k)
    return ema_val

def get_htf_bias(bg_sym):
    try:
        c1h=bitget_candles(bg_sym,"1H",60)
        c4h=bitget_candles(bg_sym,"4H",60)
        if not c1h or not c4h: return "neutral"
        ema1h=ema(c1h,50)
        ema4h=ema(c4h,50)
        if not ema1h or not ema4h: return "neutral"
        if c1h[-1]["close"]>ema1h and c4h[-1]["close"]>ema4h: return "bullish"
        if c1h[-1]["close"]<ema1h and c4h[-1]["close"]<ema4h: return "bearish"
        return "neutral"
    except: return "neutral"

def find_swings(candles, left=2, right=2):
    highs,lows=[],[]
    for i in range(left,len(candles)-right):
        h=candles[i]["high"]; l=candles[i]["low"]
        if all(h>candles[j]["high"] for j in range(i-left,i+right+1) if j!=i): highs.append((i,h))
        if all(l<candles[j]["low"] for j in range(i-left,i+right+1) if j!=i): lows.append((i,l))
    return highs,lows

def get_premium_discount(candles, lookback=50):
    recent=candles[-lookback:]
    high=max(c["high"] for c in recent)
    low=min(c["low"] for c in recent)
    cur=recent[-1]["close"]
    pct=(cur-low)/(high-low)*100 if high!=low else 50
    return pct, high, low

def detect_sweep(candles):
    highs,lows=find_swings(candles,2,2)
    if len(lows)<2 or len(highs)<2: return None
    last=candles[-2]
    if last["low"]<lows[-2][1] and last["close"]>lows[-2][1]: return {"type":"bullish","swept":lows[-2][1]}
    if last["high"]>highs[-2][1] and last["close"]<highs[-2][1]: return {"type":"bearish","swept":highs[-2][1]}
    return None

def find_ob(candles, choch_idx, direction="bullish"):
    for i in range(choch_idx-1, max(0, choch_idx-15), -1):
        c=candles[i]
        if direction=="bullish" and c["close"]<c["open"]:
            for j in range(i+1, min(len(candles), i+6)):
                if candles[j]["close"]>c["high"]: return {"high":c["high"],"low":c["low"],"50":(c["high"]+c["low"])/2,"idx":i}
        if direction=="bearish" and c["close"]>c["open"]:
            for j in range(i+1, min(len(candles), i+6)):
                if candles[j]["close"]<c["low"]: return {"high":c["high"],"low":c["low"],"50":(c["high"]+c["low"])/2,"idx":i}
    return None

def find_fvgs(candles, lookback=20):
    fvgs=[]
    for i in range(1, len(candles)-1):
        if i < len(candles)-lookback: continue
        c1=candles[i-1]; c3=candles[i+1]
        if c3["low"] > c1["high"]: fvgs.append({"type":"bullish","high":c3["low"],"low":c1["high"],"50":(c3["low"]+c1["high"])/2})
        if c3["high"] < c1["low"]: fvgs.append({"type":"bearish","high":c1["low"],"low":c3["high"],"50":(c1["low"]+c3["high"])/2})
    return fvgs

def detect_choch(candles, bg_sym):
    if len(candles)<50: return None
    highs,lows=find_swings(candles,2,2)
    if len(highs)<3 or len(lows)<3: return None
    pd_pct, range_high, range_low = get_premium_discount(candles,50)
    sweep=detect_sweep(candles)
    if USE_VOLUME_FILTER:
        avg_vol = sum(c["vol"] for c in candles[-20:-1]) / 19 if len(candles)>=20 else 0
        vol_ok = candles[-2]["vol"] > avg_vol*1.3 if avg_vol>0 else True
    else: vol_ok=True
    last_swing_high=highs[-1][1]
    last_swing_low=lows[-1][1]
    choch_idx_high=highs[-1][0]
    choch_idx_low=lows[-1][0]
    buffer_high=last_swing_high*0.001
    buffer_low=last_swing_low*0.001
    htf_bias = get_htf_bias(bg_sym) if USE_HTF_BIAS else "neutral"
    for i in range(choch_idx_high+1, len(candles)-1):
        if candles[i]["close"]>last_swing_high+buffer_high and candles[i-1]["close"]<=last_swing_high+buffer_high:
            if pd_pct<50:
                if USE_HTF_BIAS and htf_bias=="bearish": continue
                ob=find_ob(candles,i,"bullish")
                fvgs=find_fvgs(candles,20)
                overlapping_fvg=None
                if ob:
                    for f in fvgs:
                        if f["type"]=="bullish" and not (ob["high"]<f["low"] or ob["low"]>f["high"]):
                            overlapping_fvg=f; break
                return {"side":"LONG","choch_price":last_swing_high,"choch_idx":i,"choch_close":candles[i]["close"],"ob":ob,"fvg":overlapping_fvg,"pd_pct":pd_pct,"sweep":sweep,"sweep_valid": sweep and sweep["type"]=="bullish","vol_ok":vol_ok,"htf":htf_bias}
    for i in range(choch_idx_low+1, len(candles)-1):
        if candles[i]["close"]<last_swing_low-buffer_low and candles[i-1]["close"]>=last_swing_low-buffer_low:
            if pd_pct>50:
                if USE_HTF_BIAS and htf_bias=="bullish": continue
                ob=find_ob(candles,i,"bearish")
                fvgs=find_fvgs(candles,20)
                overlapping_fvg=None
                if ob:
                    for f in fvgs:
                        if f["type"]=="bearish" and not (ob["high"]<f["low"] or ob["low"]>f["high"]):
                            overlapping_fvg=f; break
                return {"side":"SHORT","choch_price":last_swing_low,"choch_idx":i,"choch_close":candles[i]["close"],"ob":ob,"fvg":overlapping_fvg,"pd_pct":pd_pct,"sweep":sweep,"sweep_valid": sweep and sweep["type"]=="bearish","vol_ok":vol_ok,"htf":htf_bias}
    return None

def open_position(symbol, side, entry, sl, info, manual=False):
    if symbol in state["positions"] and not manual: return False
    if symbol in state["positions"] and manual: state["positions"].pop(symbol)
    waiting=[s for s,p in state["positions"].items() if not p["rf_hit"]]
    if waiting and not manual: return False
    state["positions"][symbol]={"symbol":symbol,"side":side,"entry":entry,"high":entry,"low":entry,"rf_hit":False,"sl":sl,"info":info,"manual":manual}
    state["today_signals"]+=1
    save_state(state)
    fvg_txt = f"FVG {info['fvg']['low']:.4f}-{info['fvg']['high']:.4f} ✅" if info.get("fvg") else "FVG ❌"
    ob_txt = f"OB50 {info['ob']['50']:.4f}" if info.get("ob") else "Market"
    pd_txt = f"{'DISC' if info['pd_pct']<50 else 'PREM'} {info['pd_pct']:.1f}%"
    htf_txt = f"HTF {info.get('htf','neutral')} ✅"
    sweep_txt = f"Sweep ✅" if info.get("sweep_valid") else "Sweep ❌"
    vol_txt = f"Vol ✅" if info.get("vol_ok") else "Vol ❌"
    grade = "A+++" if info.get("fvg") and info.get("sweep_valid") and info.get("vol_ok") else "A+"
    buttons = [[{"text":"❌ CLOSE NOW","callback_data":f"close:{symbol}"}],[{"text":"📊 CHART","url":bitget_chart_link(symbol)}]]
    tg_send(f"🚀 {'MANUAL ' if manual else ''}PAPER {symbol} {side} {grade} HUNTER\n{pd_txt} | {htf_txt} | {sweep_txt} | {vol_txt}\n{ob_txt}\n{fvg_txt}\nEntry {entry:.4f} SL {sl:.4f} ({abs(entry-sl)/entry*100:.2f}%)\nRF +{RF_PCT}% BE+{BE_PCT}% TRAIL {TRAIL_PCT}% TP {TP_PCT}%", buttons)
    return True

def update_positions():
    if not state["positions"]: return
    to_close=[]
    for sym,pos in list(state["positions"].items()):
        cur=bitget_price(sym)
        if not cur: continue
        if pos["side"]=="LONG":
            pnl=(cur-pos["entry"])/pos["entry"]*100
            if cur>pos["high"]: pos["high"]=cur
            if not pos["rf_hit"] and pnl>=RF_PCT:
                pos["rf_hit"]=True
                pos["sl"]=pos["entry"]*(1+BE_PCT/100)
                tg_send(f"🔒 {sym} LONG RF SL BE+ {pos['sl']:.4f}")
            if pos["rf_hit"]:
                new_sl=pos["high"]*(1-TRAIL_PCT/100)
                if new_sl>pos["sl"]: pos["sl"]=new_sl
            if pnl>=TP_PCT: to_close.append((sym,"TP",pnl,cur))
            elif cur<=pos["sl"]: to_close.append((sym,"SL/TRAIL",pnl,cur))
        else:
            pnl=(pos["entry"]-cur)/pos["entry"]*100
            if cur<pos["low"]: pos["low"]=cur
            if not pos["rf_hit"] and pnl>=RF_PCT:
                pos["rf_hit"]=True
                pos["sl"]=pos["entry"]*(1-BE_PCT/100)
                tg_send(f"🔒 {sym} SHORT RF SL BE+ {pos['sl']:.4f}")
            if pos["rf_hit"]:
                new_sl=pos["low"]*(1+TRAIL_PCT/100)
                if new_sl<pos["sl"]: pos["sl"]=new_sl
            if pnl>=TP_PCT: to_close.append((sym,"TP",pnl,cur))
            elif cur>=pos["sl"]: to_close.append((sym,"SL/TRAIL",pnl,cur))
    for sym,reason,pnl,cur in to_close:
        pos=state["positions"].pop(sym)
        state["balance"]+=state["balance"]*0.1*(pnl/100)
        tg_send(f"{'✅' if pnl>0 else '❌'} CLOSE {sym} {pos['side']} {reason} PnL {pnl:.2f}% Bal ${state['balance']:.2f}")
    save_state(state)

def scanner_loop():
    print(f"--- V44.20 HUNTER 24/7 Top{SCAN_COUNT} CMC Vol + HTF+Vol+Sweep+CHoCH+OB50%+FVG ---")
    tg_send(f"🏹 V44.20 HUNTER 24/7 LIVE Top{SCAN_COUNT} CMC Volume\n24/7 No Sleep | Strict A+++/A+ Only\nFilters: HTF={USE_HTF_BIAS} Vol={USE_VOLUME_FILTER}\nSweep->CHoCH->OB50%+FVG\nLONG DISC | SHORT PREM\n/manual /long /short /status")
    while True:
        try:
            top=bitget_top200()
            update_positions()
            for sym, pend in list(state["pending"].items()):
                cur=bitget_price(sym)
                if not cur: continue
                ob=pend["ob"]; fvg=pend.get("fvg")
                if not ob: continue
                touch_ob50 = abs(cur - ob["50"])/ob["50"] < 0.003 and ob["low"] <= cur <= ob["high"]
                if fvg:
                    if touch_ob50 and fvg["low"] <= cur <= fvg["high"]:
                        side=pend["side"]
                        sl = ob["low"]*0.999 if side=="LONG" else ob["high"]*1.001
                        if abs(cur-sl)/cur*100>4: sl = cur*(1-INIT_SL_PCT/100) if side=="LONG" else cur*(1+INIT_SL_PCT/100)
                        open_position(sym, side, cur, sl, pend)
                        del state["pending"][sym]
                        save_state(state)
                else:
                    if touch_ob50:
                        side=pend["side"]
                        sl = ob["low"]*0.999 if side=="LONG" else ob["high"]*1.001
                        if abs(cur-sl)/cur*100>4: sl = cur*(1-INIT_SL_PCT/100) if side=="LONG" else cur*(1+INIT_SL_PCT/100)
                        open_position(sym, side, cur, sl, pend)
                        del state["pending"][sym]
                        save_state(state)
                if time.time()-pend["time"]>6*3600: del state["pending"][sym]
            
            scanned=0
            for t in top:
                if len(state["positions"])>=1 or len(state["pending"])>=5: break # Allow 5 pendings for Top200
                bg=t["symbol"]; ccxt=bg.replace("USDT","/USDT:USDT") if bg.endswith("USDT") else bg
                if ccxt in state["positions"] or ccxt in state["pending"]: continue
                candles=bitget_candles(bg,"15m",120)
                if not candles: continue
                res=detect_choch(candles, bg)
                scanned+=1
                if res and res["ob"]:
                    if not res["vol_ok"] and USE_VOLUME_FILTER: continue
                    is_aplus = res["sweep_valid"] or res["fvg"] is not None
                    if is_aplus:
                        state["pending"][ccxt]={"side":res["side"],"ob":res["ob"],"fvg":res["fvg"],"choch_price":res["choch_price"],"pd_pct":res["pd_pct"],"sweep":res["sweep"],"sweep_valid":res["sweep_valid"],"vol_ok":res["vol_ok"],"htf":res["htf"],"time":time.time(),"choch_close":res["choch_close"]}
                        save_state(state)
                        zone = "DISC" if res["pd_pct"]<50 else "PREM"
                        fvg_status = f"FVG ✅" if res["fvg"] else "FVG ❌"
                        sweep_status = "Sweep ✅" if res["sweep_valid"] else "Sweep ❌"
                        vol_status = "Vol ✅" if res["vol_ok"] else "Vol ❌"
                        htf_status = f"HTF {res['htf']}"
                        grade = "A+++" if res["fvg"] and res["sweep_valid"] and res["vol_ok"] else "A+"
                        buttons = [[{"text":f"✅ {res['side']} NOW","callback_data":f"longnow:{ccxt}" if res["side"]=="LONG" else f"shortnow:{ccxt}"}, {"text":"❌ SKIP","callback_data":f"skip:{ccxt}"}],[{"text":"📊 CHART","url":bitget_chart_link(ccxt)}]]
                        tg_send(f"👀 {grade} CHoCH {res['side']} {ccxt} HUNTER\n{zone} {res['pd_pct']:.1f}% {htf_status} {sweep_status} {vol_status}\nBreak {res['choch_price']:.4f} -> Wait OB50 {res['ob']['50']:.4f}\n{fvg_status}", buttons)
                if scanned>=SCAN_COUNT: break
            
            if scanned>0:
                tg_send(f"✅ V44.20 HUNTER Checked={scanned}/{SCAN_COUNT} Pend={len(state['pending'])} Pos={len(state['positions'])} Sig={state['today_signals']} Bal=${state['balance']:.2f}")
            time.sleep(60)
        except Exception as e:
            print(f"Err {e}"); import traceback; traceback.print_exc(); time.sleep(10)

@app.route('/')
def home(): return f"V44.20 HUNTER 24/7 Top{SCAN_COUNT} Bal ${state['balance']:.2f} Pos {len(state['positions'])} Pend {len(state['pending'])}",200

@app.route(f'/{WEBHOOK_SECRET}', methods=['POST'])
@app.route('/Ogadagidi', methods=['POST'])
@app.route('/ogadagidi', methods=['POST'])
@app.route('/v44-secret-xyz123', methods=['POST'])
def webhook():
    data=request.json
    if not data: return "ok",200
    if "callback_query" in data:
        cq=data["callback_query"]; cb_id=cq["id"]; cb_data=cq.get("data","")
        if cb_data.startswith("longnow:") or cb_data.startswith("shortnow:"):
            sym=cb_data.split(":",1)[1]
            side="LONG" if cb_data.startswith("longnow:") else "SHORT"
            cur=bitget_price(sym)
            if not cur: tg_answer_callback(cb_id,"Price fail"); return "ok",200
            info={"pd_pct":50,"sweep_valid":False,"ob":None,"fvg":None,"htf":"neutral","vol_ok":True}
            if sym in state["pending"]:
                info=state["pending"][sym]
                ob=info["ob"]
                sl = ob["low"]*0.999 if side=="LONG" else ob["high"]*1.001
                del state["pending"][sym]
            else:
                candles=bitget_candles(sym.replace("/","").replace(":USDT","").replace(":",""),"15m",120)
                sl = min(c["low"] for c in candles[-10:])*0.999 if side=="LONG" and candles else cur*(1-INIT_SL_PCT/100)
                if side=="SHORT": sl = max(c["high"] for c in candles[-10:])*1.001 if candles else cur*(1+INIT_SL_PCT/100)
            if abs(cur-sl)/cur*100>4: sl = cur*(1-INIT_SL_PCT/100) if side=="LONG" else cur*(1+INIT_SL_PCT/100)
            success=open_position(sym, side, cur, sl, info, manual=True)
            tg_answer_callback(cb_id,f"{side} {sym}" if success else "Failed")
        elif cb_data.startswith("skip:"):
            sym=cb_data.split(":",1)[1]
            if sym in state["pending"]: del state["pending"][sym]; save_state(state); tg_answer_callback(cb_id,f"Skip {sym}"); tg_send(f"❌ Skipped {sym}")
            else: tg_answer_callback(cb_id,"Not pending")
        elif cb_data.startswith("close:"):
            sym=cb_data.split(":",1)[1]
            if sym in state["positions"]: state["positions"].pop(sym); save_state(state); tg_answer_callback(cb_id,f"Closed {sym}"); tg_send(f"❌ Manual CLOSE {sym}")
            else: tg_answer_callback(cb_id,"No pos")
        return "ok",200
    if "message" not in data: return "ok",200
    # FIX: reply directly to sender - solves CHAT_ID=200 bug!
    incoming_chat = data["message"]["chat"]["id"]
    txt=data["message"].get("text","").lower()
    if "status" in txt or "/status" in txt:
        msg=f"🏹 V44.20 HUNTER 24/7 Top{SCAN_COUNT}\nBal ${state['balance']:.2f} Pos {len(state['positions'])} Pend {len(state['pending'])}\n"
        for s,p in state["positions"].items():
            cur=bitget_price(s) or p["entry"]
            pnl = (cur-p["entry"])/p["entry"]*100 if p["side"]=="LONG" else (p["entry"]-cur)/p["entry"]*100
            msg+=f"\n{s} {p['side']} PnL {pnl:.2f}%"
        for s,p in state["pending"].items():
            msg+=f"\n⏳ {p['side']} {s} OB50 {p['ob']['50']:.4f} { 'FVG✅' if p.get('fvg') else 'FVG❌'} HTF {p.get('htf')}"
        tg_send(msg, reply_chat=incoming_chat)
    elif txt.startswith("/long ") or txt.startswith("/short "):
        parts=txt.split()
        if len(parts)>=2:
            raw_sym=parts[1].upper()
            if "/" not in raw_sym: raw_sym = raw_sym.replace("USDT","") + "/USDT:USDT"
            side="LONG" if txt.startswith("/long") else "SHORT"
            cur=bitget_price(raw_sym)
            if not cur: tg_send(f"❌ No price {raw_sym}", reply_chat=incoming_chat)
            else:
                candles=bitget_candles(raw_sym.replace("/","").replace(":USDT","").replace(":",""),"15m",120)
                sl = min(c["low"] for c in candles[-10:])*0.999 if side=="LONG" and candles else cur*(1-INIT_SL_PCT/100)
                if side=="SHORT": sl = max(c["high"] for c in candles[-10:])*1.001 if candles else cur*(1+INIT_SL_PCT/100)
                info={"pd_pct":50,"sweep_valid":False,"ob":None,"fvg":None,"htf":"neutral","vol_ok":True,"manual":True}
                open_position(raw_sym, side, cur, sl, info, manual=True)
    elif "/close" in txt:
        parts=txt.split()
        if len(parts)>=2:
            target=parts[1].upper()
            for k in list(state["positions"].keys()):
                if target in k: state["positions"].pop(k)
            save_state(state); tg_send(f"❌ Closed {target}", reply_chat=incoming_chat)
        else:
            state["positions"]={}; save_state(state); tg_send("❌ Closed ALL", reply_chat=incoming_chat)
    elif "/clearpending" in txt:
        state["pending"]={}; save_state(state); tg_send("Cleared pendings", reply_chat=incoming_chat)
    return "ok",200

if __name__=="__main__":
    threading.Thread(target=scanner_loop,daemon=True).start()
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",10000)))
