
"""
V44.10 BE+0.5% + TRAILING STOPS - After RF, locks BE+0.5% then trails
"""
import ccxt, pandas as pd, time, requests, threading
from datetime import datetime, timezone
from flask import Flask, request
import os

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN","").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID","").strip()

DRY_RUN = True
RISK_PCT = 5.0
RISK_FREE_TRIGGER_PCT = 2.0
RF_LOCK_PCT = 0.5  # BE + 0.5%
LEVERAGE = 5
SCAN_INTERVAL = 14400
SCAN_TOP_N = 50
TRAILING_ENABLED = True
TRAILING_DISTANCE = 1.5  # Trail 1.5% behind highest

def get_env(*names, default=""):
    for n in names:
        v=os.getenv(n)
        if v: return v.strip()
    return default

apiKey=get_env("BITGET_API_KEY","API_KEY")
secret=get_env("BITGET_API_SECRET","API_SECRET")
password=get_env("BITGET_PASSPHRASE","PASSPHRASE","API_PASSPHRASE")

public_exchange=ccxt.bitget({'enableRateLimit':True,'options':{'defaultType':'swap'}})
private_exchange=ccxt.bitget({'apiKey':apiKey,'secret':secret,'password':password,'enableRateLimit':True,'options':{'defaultType':'swap'}})

state={"positions":{},"balance":100.0,"last_scan":None,"total_pnl":0.0,"scan_count":0,"signals_today":0}

def log(msg):
    print(f"{datetime.now(timezone.utc).isoformat()} {msg}",flush=True)

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        r=requests.post(url,json={"chat_id":TELEGRAM_CHAT_ID,"text":msg},timeout=15)
        log(f"TG {r.status_code}: {msg[:100]}")
    except Exception as e:
        log(f"TG err {e}")

def fetch_ohlcv(symbol,timeframe,limit=100):
    try:
        return public_exchange.fetch_ohlcv(symbol,timeframe,limit=limit)
    except:
        return []

def get_current_price(symbol):
    try:
        return public_exchange.fetch_ticker(symbol)['last']
    except:
        try:
            ohlcv=fetch_ohlcv(symbol,'1m',2)
            if ohlcv:
                return ohlcv[-1][4]
        except:
            pass
    return None

def get_top100():
    try:
        tickers=public_exchange.fetch_tickers()
        usdt=[s for s in tickers if 'USDT' in s and tickers[s].get('quoteVolume') is not None]
        sorted_by_vol=sorted(usdt,key=lambda x: tickers[x].get('quoteVolume',0) or 0,reverse=True)
        top=sorted_by_vol[:100]
        log(f"Top100: {len(top)}")
        return top
    except Exception as e:
        log(f"Top100 err {e}")
        return ["BTC/USDT:USDT","ETH/USDT:USDT","SOL/USDT:USDT","XRP/USDT:USDT"]

def detect_choch_sensitive(symbol):
    try:
        ohlcv_1h=fetch_ohlcv(symbol,'1h',50)
        ohlcv_15m=fetch_ohlcv(symbol,'15m',50)
        if len(ohlcv_1h)<25 or len(ohlcv_15m)<25:
            return None
        df1h=pd.DataFrame(ohlcv_1h,columns=['t','o','h','l','c','v'])
        df15=pd.DataFrame(ohlcv_15m,columns=['t','o','h','l','c','v'])
        ema20=df1h['c'].ewm(span=20).mean().iloc[-1]
        price_1h=df1h['c'].iloc[-1]
        trend="bull" if price_1h>ema20 else "bear"
        last_high_15=df15['h'].iloc[-21:-1].max()
        last_low_15=df15['l'].iloc[-21:-1].min()
        close_15=df15['c'].iloc[-1]
        if trend=="bull" and close_15>last_high_15:
            return "LONG"
        if trend=="bear" and close_15<last_low_15:
            return "SHORT"
        return None
    except Exception as e:
        log(f"CHoCH {symbol} err {e}")
        return None

def can_open_new():
    if not state["positions"]:
        return True,"No open positions"
    not_rf=[s for s,p in state["positions"].items() if not p.get('risk_free')]
    if not_rf:
        return False,f"Waiting RF: {','.join(not_rf)}"
    return True,f"All {len(state['positions'])} RF"

def scan():
    state["scan_count"]+=1
    log(f"--- SCAN #{state['scan_count']} START ---")
    try:
        top=get_top100()
        can,why=can_open_new()
        log(f"Bal ${state['balance']:.2f} Pos:{len(state['positions'])} CanOpen? {can} {why}")
        signals=0
        checked=0
        for sym in top[:SCAN_TOP_N]:
            checked+=1
            if sym in state["positions"]:
                continue
            side=detect_choch_sensitive(sym)
            time.sleep(0.2)
            if side:
                signals+=1
                state["signals_today"]+=1
                log(f"SIGNAL {sym} {side}")
                can_now,why_now=can_open_new()
                if can_now:
                    price=get_current_price(sym)
                    if not price:
                        ohlcv=fetch_ohlcv(sym,'1m',2)
                        price=ohlcv[-1][4] if ohlcv else 0
                    state["positions"][sym]={
                        "entry":price,"side":side,"risk_free":False,
                        "highest_pnl":0.0,"trailing_lock":RF_LOCK_PCT,
                        "entry_time":datetime.now(timezone.utc).isoformat(),
                        "balance_at_entry":state["balance"],
                        "current_price":price,"pnl_pct":0.0
                    }
                    send_telegram(f"🚀 PAPER {sym} {side}\nEntry: {price}\nBal ${state['balance']:.2f}\nRF +2% -> BE+0.5% + TRAIL 1.5% -> TP +6% / SL -4%")
                else:
                    send_telegram(f"⏳ SIGNAL {sym} {side} skipped - {why_now}")
            if signals>=3:
                break
        state["last_scan"]=datetime.now(timezone.utc).isoformat()
        if signals==0:
            send_telegram(f"📊 Scan #{state['scan_count']} done: No signals (checked {checked}). Bal ${state['balance']:.2f}")
        else:
            send_telegram(f"✅ V44.10 LIVE! Checked={checked} Signals={signals} Today={state['signals_today']} Bal=${state['balance']:.2f}")
    except Exception as e:
        log(f"SCAN CRASH {e}")

def check_rf_and_pnl():
    while True:
        try:
            time.sleep(300)
            if not state["positions"]:
                continue
            for sym,pos in list(state["positions"].items()):
                curr=get_current_price(sym)
                if not curr or pos.get('entry',0)==0:
                    continue
                entry=pos['entry']
                side=pos['side']
                pnl_pct=(curr-entry)/entry*100*LEVERAGE if side=="LONG" else (entry-curr)/entry*100*LEVERAGE
                pos['current_price']=curr
                pos['pnl_pct']=pnl_pct

                # Track highest PnL
                if pnl_pct>pos.get('highest_pnl',0):
                    pos['highest_pnl']=pnl_pct
                    if pos.get('risk_free') and TRAILING_ENABLED and pos['highest_pnl']>=3.0:
                        new_lock=pos['highest_pnl']-TRAILING_DISTANCE
                        if new_lock>pos.get('trailing_lock',RF_LOCK_PCT):
                            pos['trailing_lock']=new_lock
                            log(f"🔼 TRAIL {sym} highest {pos['highest_pnl']:.1f}% -> lock {new_lock:.1f}%")
                            send_telegram(f"🔼 TRAIL {sym} {side} PnL +{pnl_pct:.1f}% -> Lock now +{new_lock:.1f}%")

                # RF Trigger at +2%
                if not pos.get('risk_free') and pnl_pct>=RISK_FREE_TRIGGER_PCT:
                    pos['risk_free']=True
                    pos['trailing_lock']=RF_LOCK_PCT
                    pos['highest_pnl']=pnl_pct
                    log(f"✅ {sym} HIT RF +{pnl_pct:.2f}% -> Lock BE+{RF_LOCK_PCT}% + TRAIL + INSTANT SCAN")
                    send_telegram(f"✅ {sym} {side} HIT RF! +{pnl_pct:.2f}%\n🔒 Lock BE+{RF_LOCK_PCT}% + Trail {TRAILING_DISTANCE}%\n🔍 Scanning instantly...")
                    threading.Thread(target=lambda: (time.sleep(10), scan()), daemon=True).start()

                # Check trailing lock exit
                if pos.get('risk_free'):
                    lock=pos.get('trailing_lock',RF_LOCK_PCT)
                    if pnl_pct<=lock:
                        gain=5*(lock/10) if lock>0 else 0
                        if gain>0:
                            state["balance"]+=gain
                            state["total_pnl"]+=gain
                        log(f"🔒 TRAIL CLOSE {sym} at +{lock:.2f}% (PnL was +{pnl_pct:.2f}%, highest {pos.get('highest_pnl',0):.1f}%)")
                        if gain>=0:
                            send_telegram(f"🔒 TRAIL LOCK {sym} {side} +{lock:.2f}% (high +{pos.get('highest_pnl',0):.1f}%) +${gain:.2f} Bal ${state['balance']:.2f} -> scan...")
                        else:
                            send_telegram(f"🔒 BE+0.5% LOCK {sym} {side} +{lock:.2f}% protected -> scan...")
                        del state["positions"][sym]
                        threading.Thread(target=lambda: (time.sleep(10), scan()), daemon=True).start()
                        continue

                # Pre-RF SL/TP
                if not pos.get('risk_free'):
                    if pnl_pct>=6.0:
                        gain=5*(pnl_pct/10)
                        state["balance"]+=gain
                        state["total_pnl"]+=gain
                        send_telegram(f"💰 TP {sym} +{pnl_pct:.2f}% +${gain:.2f} Bal ${state['balance']:.2f} -> scan...")
                        del state["positions"][sym]
                        threading.Thread(target=lambda: (time.sleep(10), scan()), daemon=True).start()
                    elif pnl_pct<=-4.0:
                        loss=5*(abs(pnl_pct)/10)
                        state["balance"]-=loss
                        state["total_pnl"]-=loss
                        send_telegram(f"🛑 SL {sym} {pnl_pct:.2f}% -${loss:.2f} Bal ${state['balance']:.2f} -> scan...")
                        del state["positions"][sym]
                        threading.Thread(target=lambda: (time.sleep(10), scan()), daemon=True).start()
                else:
                    if pnl_pct>=6.0:
                        gain=5*(pnl_pct/10)
                        state["balance"]+=gain
                        state["total_pnl"]+=gain
                        send_telegram(f"💰 TP {sym} +{pnl_pct:.2f}% +${gain:.2f} Bal ${state['balance']:.2f} -> scan...")
                        del state["positions"][sym]
                        threading.Thread(target=lambda: (time.sleep(10), scan()), daemon=True).start()
        except Exception as e:
            log(f"RF checker err {e}")
            time.sleep(60)

@app.route('/',methods=['GET'])
def home():
    return f"V44.10 TRAIL LIVE Bal:{state['balance']:.2f} Pos:{len(state['positions'])}"

@app.route('/ping',methods=['GET'])
@app.route('/health',methods=['GET'])
def ping():
    return f"PONG V44.10",200

@app.route('/webhook',methods=['POST'])
@app.route('/<path:subpath>',methods=['POST'])
def webhook(subpath=None):
    try:
        data=request.get_json(force=True,silent=True)
        if not data:
            return "OK",200
        msg=data.get('message',{}) or data.get('edited_message',{})
        text=msg.get('text','')
        chat=str(msg.get('chat',{}).get('id',''))
        if chat!=TELEGRAM_CHAT_ID and TELEGRAM_CHAT_ID:
            return "OK",200
        if text.startswith('/status'):
            rf=sum(1 for p in state["positions"].values() if p.get('risk_free'))
            total=len(state["positions"])
            can,why=can_open_new()
            pos_list="\n".join([f"{s}: {p['side']} RF={p.get('risk_free')} PnL={p.get('pnl_pct',0):.1f}% High={p.get('highest_pnl',0):.1f}% Lock={p.get('trailing_lock',0):.1f}%" for s,p in list(state["positions"].items())[:10]]) or "None"
            send_telegram(f"📈 V44.10 Status\nBal ${state['balance']:.2f} PnL ${state['total_pnl']:.2f}\nPos {total} RF {rf}\n{pos_list}\nCan open? {can}\nRF +2% -> BE+0.5% + Trail {TRAILING_DISTANCE}%")
        elif text.startswith('/scan'):
            send_telegram("🔍 Scan starting top 50...")
            threading.Thread(target=scan).start()
        elif text.startswith('/rf'):
            if state["positions"]:
                for s in state["positions"]:
                    state["positions"][s]['risk_free']=True
                    state["positions"][s]['trailing_lock']=RF_LOCK_PCT
                send_telegram(f"✅ Forced {len(state['positions'])} to RF BE+0.5% + instant scan...")
                threading.Thread(target=lambda: (time.sleep(5), scan()), daemon=True).start()
            else:
                send_telegram("No positions")
        return "OK",200
    except Exception as e:
        return "OK",200

def set_webhook():
    time.sleep(4)
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        host=os.getenv("RENDER_EXTERNAL_HOSTNAME","v42-bot-2.onrender.com")
        webhook_url=f"https://{host}/{TELEGRAM_BOT_TOKEN}"
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook?url={webhook_url}",timeout=15)
        send_telegram(f"🤖 V44.10 Started! BE+0.5% + TRAIL {TRAILING_DISTANCE}% LIVE\nRF +2% -> Lock +0.5% -> Trail 1.5% behind high -> TP +6%")
    except:
        pass

if __name__=='__main__':
    threading.Thread(target=set_webhook,daemon=True).start()
    threading.Thread(target=check_rf_and_pnl,daemon=True).start()
    def loop():
        while True:
            try:
                scan()
            except:
                pass
            time.sleep(SCAN_INTERVAL)
    threading.Thread(target=loop,daemon=True).start()
    app.run(host='0.0.0.0',port=int(os.getenv("PORT",10000)))
