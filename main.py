
"""
V44.9 RF +0.5% Lock - After RF hit +2%, SL moves to +0.5% to cover fees
"""
import ccxt, pandas as pd, time, requests, threading
from datetime import datetime, timezone
from flask import Flask, request
import os

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN","").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID","").strip()
GOOGLE_SHEET_WEBHOOK = os.getenv("GOOGLE_SHEET_WEBHOOK","").strip()

DRY_RUN = True
RISK_PCT = 5.0
RISK_FREE_TRIGGER_PCT = 2.0
RF_LOCK_PCT = 0.5  # Lock +0.5% after RF to cover fees
LEVERAGE = 5
SCAN_INTERVAL = 14400
SCAN_TOP_N = 50

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
        log(f"TG {r.status_code}: {msg[:120]}")
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
        return ["BTC/USDT:USDT","ETH/USDT:USDT","SOL/USDT:USDT","XRP/USDT:USDT","DOGE/USDT:USDT"]

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
        atr_1h=(df1h['h'].iloc[-14:]-df1h['l'].iloc[-14:]).mean()
        if atr_1h/price_1h<0.001:
            return None
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
    return True,f"All {len(state['positions'])} RF - can open"

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
                        "entry_time":datetime.now(timezone.utc).isoformat(),
                        "balance_at_entry":state["balance"],
                        "current_price":price,"pnl_pct":0.0
                    }
                    send_telegram(f"🚀 PAPER {sym} {side}\nTrend: 1H EMA20\nEntry: {price}\n{why_now}\nBal ${state['balance']:.2f}\nRF +2% -> Lock +0.5% -> TP +6% / SL -4%")
                else:
                    log(f"Skip {sym} {side} - {why_now}")
                    send_telegram(f"⏳ SIGNAL {sym} {side} skipped - {why_now}")
            if signals>=3:
                break
        state["last_scan"]=datetime.now(timezone.utc).isoformat()
        log(f"--- SCAN DONE checked={checked} signals={signals} pos={len(state['positions'])} ---")
        if state["scan_count"]==1 or state["scan_count"]%6==0:
            send_telegram(f"✅ V44.9 LIVE! Checked={checked} Signals={signals} Today={state['signals_today']} Bal=${state['balance']:.2f} Pos={len(state['positions'])} RF-Lock +0.5%")
        elif signals==0:
            send_telegram(f"📊 Scan #{state['scan_count']} done: No signals (checked {checked}). Bal ${state['balance']:.2f} Pos {len(state['positions'])}")
    except Exception as e:
        log(f"SCAN CRASH {e}")
        import traceback
        traceback.print_exc()

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
                if side=="LONG":
                    pnl_pct=(curr-entry)/entry*100*LEVERAGE
                else:
                    pnl_pct=(entry-curr)/entry*100*LEVERAGE
                pos['current_price']=curr
                pos['pnl_pct']=pnl_pct

                # RF Trigger at +2%
                if not pos.get('risk_free') and pnl_pct>=RISK_FREE_TRIGGER_PCT:
                    pos['risk_free']=True
                    pos['rf_time']=datetime.now(timezone.utc).isoformat()
                    pos['rf_price']=curr
                    log(f"✅ {sym} HIT RF +{pnl_pct:.2f}% -> Lock at +{RF_LOCK_PCT}% + INSTANT SCAN")
                    send_telegram(f"✅ {sym} {side} HIT RISK-FREE!\nEntry {entry:.4f} -> Now {curr:.4f}\nPnL +{pnl_pct:.2f}%\n🔒 Locked at +{RF_LOCK_PCT}% to cover fees\n🔍 Scanning instantly...")
                    def do_scan():
                        time.sleep(10)
                        try:
                            scan()
                        except Exception as e:
                            log(f"Instant scan err {e}")
                    threading.Thread(target=do_scan,daemon=True).start()

                # If already RF, check if price fell back to +0.5% lock
                if pos.get('risk_free'):
                    if pnl_pct<=RF_LOCK_PCT:
                        gain=5*(RF_LOCK_PCT/10)
                        state["balance"]+=gain
                        state["total_pnl"]+=gain
                        log(f"🔒 RF CLOSE {sym} at +{RF_LOCK_PCT}% (was +{pnl_pct:.2f}%)")
                        send_telegram(f"🔒 RF LOCK {sym} {side} +{RF_LOCK_PCT:.2f}% +${gain:.2f} Bal ${state['balance']:.2f} (protected from SL) -> instant scan...")
                        del state["positions"][sym]
                        def do_scan2():
                            time.sleep(10)
                            try:
                                scan()
                            except Exception as e:
                                log(f"Scan err {e}")
                        threading.Thread(target=do_scan2,daemon=True).start()
                        continue

                # Normal TP/SL only if not yet RF, or if RF but still above lock
                if not pos.get('risk_free'):
                    if pnl_pct>=6.0:
                        gain=5*(pnl_pct/10)
                        state["balance"]+=gain
                        state["total_pnl"]+=gain
                        log(f"CLOSE WIN {sym} +{pnl_pct:.2f}%")
                        send_telegram(f"💰 TP {sym} {side} +{pnl_pct:.2f}% +${gain:.2f} Bal ${state['balance']:.2f} PnL ${state['total_pnl']:.2f} -> instant scan...")
                        del state["positions"][sym]
                        def do_scan3():
                            time.sleep(10)
                            try:
                                scan()
                            except Exception as e:
                                log(f"Scan err {e}")
                        threading.Thread(target=do_scan3,daemon=True).start()
                    elif pnl_pct<=-4.0:
                        loss=5*(abs(pnl_pct)/10)
                        state["balance"]-=loss
                        state["total_pnl"]-=loss
                        log(f"CLOSE LOSS {sym} {pnl_pct:.2f}%")
                        send_telegram(f"🛑 SL {sym} {side} {pnl_pct:.2f}% -${loss:.2f} Bal ${state['balance']:.2f} -> scanning...")
                        del state["positions"][sym]
                        def do_scan4():
                            time.sleep(10)
                            try:
                                scan()
                            except Exception as e:
                                log(f"Scan err {e}")
                        threading.Thread(target=do_scan4,daemon=True).start()
                else:
                    # Already RF, check TP still
                    if pnl_pct>=6.0:
                        gain=5*(pnl_pct/10)
                        state["balance"]+=gain
                        state["total_pnl"]+=gain
                        log(f"CLOSE WIN RF {sym} +{pnl_pct:.2f}%")
                        send_telegram(f"💰 TP {sym} {side} +{pnl_pct:.2f}% +${gain:.2f} Bal ${state['balance']:.2f} PnL ${state['total_pnl']:.2f} -> instant scan...")
                        del state["positions"][sym]
                        def do_scan5():
                            time.sleep(10)
                            try:
                                scan()
                            except Exception as e:
                                log(f"Scan err {e}")
                        threading.Thread(target=do_scan5,daemon=True).start()
        except Exception as e:
            log(f"RF checker err {e}")
            time.sleep(60)

@app.route('/',methods=['GET'])
def home():
    return f"V44.9 LIVE Bal:{state['balance']:.2f} Pos:{len(state['positions'])} Scans:{state['scan_count']} {datetime.now().isoformat()}"

@app.route('/ping',methods=['GET'])
@app.route('/health',methods=['GET'])
def ping():
    return f"PONG V44.9 {datetime.now().isoformat()} Pos:{len(state['positions'])}",200

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
        log(f"Webhook '{text}' from {chat}")
        if chat!=TELEGRAM_CHAT_ID and TELEGRAM_CHAT_ID:
            return "OK",200
        if text.startswith('/status'):
            rf=sum(1 for p in state["positions"].values() if p.get('risk_free'))
            total=len(state["positions"])
            can,why=can_open_new()
            pos_list="\n".join([f"{s}: {p['side']} RF={p.get('risk_free')} PnL={p.get('pnl_pct',0):.1f}%" for s,p in list(state["positions"].items())[:10]]) or "None"
            send_telegram(f"📈 V44.9 Status\nBal ${state['balance']:.2f} PnL ${state['total_pnl']:.2f}\nPos {total} RF {rf}/{total}\n{pos_list}\nScans:{state['scan_count']} Today:{state['signals_today']}\nCan open? {can} {why}\nRF +2% -> Lock +0.5%")
        elif text.startswith('/scan'):
            send_telegram("🔍 Scan starting top 50...")
            threading.Thread(target=scan).start()
        elif text.startswith('/pnl'):
            send_telegram(f"💰 PnL ${state['total_pnl']:.2f} Bal ${state['balance']:.2f} Pos {len(state['positions'])}")
        elif text.startswith('/positions'):
            if not state["positions"]:
                send_telegram("No open positions")
            else:
                txt="\n".join([f"{k} {v['side']} PnL {v.get('pnl_pct',0):.1f}%" for k,v in state["positions"].items()])
                send_telegram(f"Pos:\n{txt}")
        elif text.startswith('/rf'):
            if state["positions"]:
                for s in state["positions"]:
                    state["positions"][s]['risk_free']=True
                send_telegram(f"✅ Forced {len(state['positions'])} to RF -> Lock +0.5% + instant scan 5 sec...")
                def forced_scan():
                    time.sleep(5)
                    try:
                        scan()
                    except Exception as e:
                        log(f"Forced scan err {e}")
                threading.Thread(target=forced_scan,daemon=True).start()
            else:
                send_telegram("No positions")
        elif text.startswith('/help'):
            send_telegram("Commands: /status /scan /positions /pnl /rf /help")
        return "OK",200
    except Exception as e:
        log(f"Webhook err {e}")
        return "OK",200

def set_webhook():
    time.sleep(4)
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        host=os.getenv("RENDER_EXTERNAL_HOSTNAME","v42-bot-2.onrender.com")
        if "onrender.com" not in host:
            host="v42-bot-2.onrender.com"
        webhook_url=f"https://{host}/{TELEGRAM_BOT_TOKEN}"
        url=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook?url={webhook_url}"
        r=requests.get(url,timeout=15)
        log(f"Set webhook {webhook_url} -> {r.text[:500]}")
        send_telegram(f"🤖 V44.9 Started! Top {SCAN_TOP_N} every 4H + INSTANT after RF\nRF +2% -> Lock +{RF_LOCK_PCT}% (covers fees) -> TP +6% / SL -4%\nSend /status /scan /rf")
    except Exception as e:
        log(f"Webhook err {e}")

if __name__=='__main__':
    threading.Thread(target=set_webhook,daemon=True).start()
    threading.Thread(target=check_rf_and_pnl,daemon=True).start()
    def loop():
        while True:
            try:
                scan()
            except Exception as e:
                log(f"Loop err {e}")
            time.sleep(SCAN_INTERVAL)
    threading.Thread(target=loop,daemon=True).start()
    app.run(host='0.0.0.0',port=int(os.getenv("PORT",10000)))
