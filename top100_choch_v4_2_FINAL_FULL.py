"""
V4.2 - Top100 4H/1H CHoCH Futures 5x Compound 5%
FIXED FOR RENDER FREE WEB SERVICE - Flask keep-alive added
"""
import threading, os
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "V4.2 Bot is running - Top100 4H->1H CHoCH - Free Plan OK"

@app.route('/health')
def health():
    return "OK", 200

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_web, daemon=True).start()

import ccxt, json, time, math, requests, io
from datetime import datetime, timedelta
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from zoneinfo import ZoneInfo

RISK_PCT = 0.05
LEVERAGE = 5
INITIAL_BALANCE = 100.0
BALANCE_MODE = "compound"
SCAN_INTERVAL_MIN = 5
MAX_POSITIONS = 3
DRY_RUN = True
TIMEFRAME_BIAS = "4h"
TIMEFRAME_EXEC = "1h"
SWING_LEN_4H = 30
SWING_LEN_1H = 30
ATR_MULT = 1.5
VOL_MULT_4H = 1.2
VOL_MULT_1H = 1.2
PULLBACK_DIST_PCT = 0.03
STATE_FILE = "v4_2_state.json"
SIM_FILE = "v4_2_sim_balance.json"
OFFSET_FILE = "telegram_offset.json"
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY","")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET","")
CMC_API_KEY = os.getenv("CMC_API_KEY","")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN","")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID","")
WAT = ZoneInfo("Africa/Lagos")

def load_json(path, default):
    if os.path.exists(path):
        try: return json.loads(open(path).read())
        except: return default
    return default

def save_json(path, data):
    open(path,"w").write(json.dumps(data, indent=2))

def send_tg(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id":TELEGRAM_CHAT_ID,"text":text,"parse_mode":"Markdown"}, timeout=10)
    except Exception as e:
        print(f"TG send error {e}")

def send_chart(fig, caption=""):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="#0e1116")
    buf.seek(0)
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                      data={"chat_id":TELEGRAM_CHAT_ID,"caption":caption},
                      files={"photo": buf}, timeout=15)
    except Exception as e:
        print(f"TG photo error {e}")
    plt.close(fig)

def get_top100_symbols(exchange):
    if CMC_API_KEY:
        try:
            headers={"X-CMC_PRO_API_KEY":CMC_API_KEY}
            r=requests.get("https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest?limit=100&convert=USD", headers=headers, timeout=15)
            data=r.json()
            cmc_syms=[f"{c['symbol']}/USDT" for c in data["data"]]
            markets=exchange.load_markets()
            filtered=[s for s in cmc_syms if s in markets and "UP" not in s and "DOWN" not in s and "BULL" not in s and "BEAR" not in s]
            if len(filtered)>=30:
                print(f"CMC Top100 loaded: {len(filtered)}")
                return filtered[:100]
        except Exception as e:
            print(f"CMC error {e} fallback to Binance")
    try:
        tickers=exchange.fetch_tickers()
        usdt_perp=[(s,t["quoteVolume"]) for s,t in tickers.items() if "/USDT" in s and t.get("quoteVolume")]
        usdt_perp=sorted(usdt_perp, key=lambda x: x[1], reverse=True)
        top=[]
        for s,vol in usdt_perp:
            if vol<5_000_000: continue
            if any(x in s for x in ["UP/","DOWN/","BEAR/","BULL/"]): continue
            top.append(s)
            if len(top)>=100: break
        print(f"Binance Top100 by volume: {len(top)}")
        return top
    except Exception as e:
        print(f"Top100 fetch error {e}")
        return ["BTC/USDT","ETH/USDT","SOL/USDT","BNB/USDT","XRP/USDT","DOGE/USDT","ADA/USDT","AVAX/USDT","LINK/USDT","DOT/USDT"]*10

def fetch_df(exchange, symbol, tf, limit=200):
    try:
        ohlcv=exchange.fetch_ohlcv(symbol, tf, limit=limit)
        df=pd.DataFrame(ohlcv, columns=["ts","open","high","low","close","vol"])
        df["ts"]=pd.to_datetime(df["ts"], unit="ms")
        return df
    except Exception as e:
        return None

def calc_atr(df, period=14):
    return (df["high"]-df["low"]).rolling(period).mean().iloc[-1]

def detect_choch(df, swing_len):
    if df is None or len(df)<swing_len+20: return None
    highs=df["high"].rolling(swing_len).max()
    lows=df["low"].rolling(swing_len).min()
    atr=calc_atr(df)
    recent_high=highs.iloc[-swing_len-5]
    recent_low=lows.iloc[-swing_len-5]
    vol_ma=df["vol"].rolling(20).mean().iloc[-1]
    vol_ok=df["vol"].iloc[-1] > vol_ma*VOL_MULT_4H
    swept_low=df["low"].iloc[-10:].min() < recent_low
    broke_high=df["close"].iloc[-1] > recent_high
    swing_size=recent_high-recent_low
    atr_ok=swing_size > atr*ATR_MULT
    if swept_low and broke_high and atr_ok:
        ob_low=df["low"].iloc[-40:-10].min()
        ob_high=df["low"].iloc[-40:-10].max()
        entry=(ob_low+ob_high)/2
        sl=ob_low*0.998
        if abs(entry-sl)/entry<0.001: return None
        return {"dir":"long","entry":entry,"sl":sl,"ob_low":ob_low,"ob_high":ob_high,"atr_ok":atr_ok,"vol_ok":vol_ok,"swing":swing_size,"recent_high":recent_high,"recent_low":recent_low}
    swept_high=df["high"].iloc[-10:].max() > recent_high
    broke_low=df["close"].iloc[-1] < recent_low
    if swept_high and broke_low and atr_ok:
        ob_low=df["high"].iloc[-40:-10].min()
        ob_high=df["high"].iloc[-40:-10].max()
        entry=(ob_low+ob_high)/2
        sl=ob_high*1.002
        if abs(entry-sl)/entry<0.001: return None
        return {"dir":"short","entry":entry,"sl":sl,"ob_low":ob_low,"ob_high":ob_high,"atr_ok":atr_ok,"vol_ok":vol_ok,"swing":swing_size,"recent_high":recent_high,"recent_low":recent_low}
    return None

def check_execution(bias_4h, df_1h, price_now):
    choch_1h=detect_choch(df_1h, SWING_LEN_1H)
    if not choch_1h: return {"type":"NO_TRADE","reason":"No 1H CHoCH"}
    if choch_1h["dir"]!=bias_4h["dir"]:
        return {"type":"NO_TRADE","reason":f"Fake filtered 4H {bias_4h['dir']} vs 1H {choch_1h['dir']}"}
    entry_4h=bias_4h["entry"]
    dist=abs(price_now-entry_4h)/entry_4h
    if dist<=PULLBACK_DIST_PCT:
        return {"type":"PULLBACK","choch_1h":choch_1h,"dist":dist}
    else:
        return {"type":"ALIGNED","choch_1h":choch_1h,"dist":dist}

def build_chart(df_4h, df_1h, symbol, bias, exec_info):
    fig, axes=plt.subplots(2,1, figsize=(10,7), facecolor="#0e1116")
    for ax, df, title in [(axes[0], df_4h, f"{symbol} 4H BIAS"), (axes[1], df_1h, f"{symbol} 1H EXEC - {exec_info['type']}")]:
        ax.set_facecolor("#0e1116")
        ax.plot(df["close"].tail(100).values, color="#e5e7eb", linewidth=1.2)
        if title.startswith(symbol+" 4H"):
            ob_low=bias["ob_low"]; ob_high=bias["ob_high"]
            ax.axhspan(ob_low, ob_high, color="orange", alpha=0.25, label="4H OB")
            ax.axhline(bias["entry"], color="orange", linestyle="--", linewidth=1)
        else:
            if "choch_1h" in exec_info:
                ob=exec_info["choch_1h"]
                ax.axhspan(ob["ob_low"], ob["ob_high"], color="yellow", alpha=0.25, label="1H OB")
                ax.axhline(ob["entry"], color="yellow", linestyle="--")
        ax.set_title(title, color="white", fontsize=11)
        ax.tick_params(colors="gray")
        ax.grid(alpha=0.15)
    fig.tight_layout()
    return fig

def get_balance():
    sim=load_json(SIM_FILE, {"balance":INITIAL_BALANCE,"trades":[]})
    return sim.get("balance",INITIAL_BALANCE), sim

def handle_telegram_commands(exchange, symbols):
    offset_data=load_json(OFFSET_FILE, {"offset":0})
    offset=offset_data.get("offset",0)
    if not TELEGRAM_TOKEN: return False
    try:
        r=requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=5", timeout=10).json()
        if not r.get("ok"): return False
        for upd in r.get("result",[]):
            offset=upd["update_id"]+1
            msg=upd.get("message",{})
            text=msg.get("text","")
            if not text.startswith("/"): continue
            cmd=text.split()[0].lower()
            chat_id=str(msg.get("chat",{}).get("id",""))
            if chat_id!=str(TELEGRAM_CHAT_ID): continue
            if cmd=="/balance":
                bal,_=get_balance()
                send_tg(f"Balance: ${bal:.2f} | Next Risk: ${bal*RISK_PCT:.2f} (5%)")
            elif cmd=="/status":
                bal,_=get_balance()
                send_tg(f"*V4.2 Status*\nMode: {'DRY_RUN' if DRY_RUN else 'LIVE'}\nRisk: 5% | Lev: 5x Iso\nBalance: ${bal:.2f}\nTop100: {len(symbols)}\nCMC: {'ON' if CMC_API_KEY else 'OFF (Binance vol)'}")
            elif cmd=="/dailyreport":
                bal,sim=get_balance()
                trades=sim.get("trades",[])
                wins=len([t for t in trades if t.get("pnl",0)>0])
                losses=len(trades)-wins
                eq=[INITIAL_BALANCE]
                for t in trades: eq.append(eq[-1]+t.get("pnl",0))
                fig=plt.figure(figsize=(8,4), facecolor="#0e1116")
                ax=fig.add_subplot(111)
                ax.set_facecolor("#0e1116")
                ax.plot(eq, color="#10b981", linewidth=2)
                ax.set_title(f"Equity ${eq[-1]:.2f} W{wins} L{losses}", color="white")
                ax.tick_params(colors="gray")
                send_chart(fig, f"Daily Report W{wins} L{losses} Bal ${eq[-1]:.2f}")
            elif cmd=="/scan":
                send_tg("🔍 Forced scan starting Top100...")
                return True
            elif cmd=="/help":
                send_tg("/balance /status /dailyreport /scan /help")
        save_json(OFFSET_FILE, {"offset":offset})
    except Exception as e:
        print(f"TG poll error {e}")
    return False

def main():
    print(f"=== V4.2 Top100 4H->1H 5% Risk {LEVERAGE}x DRY_RUN={DRY_RUN} ===")
    print("Flask web server running on port 10000 for Render FREE plan")
    exchange=ccxt.binanceusdm({"apiKey":BINANCE_API_KEY,"secret":BINANCE_API_SECRET,"enableRateLimit":True,"options":{"defaultType":"future"}})
    try: exchange.load_markets()
    except: pass
    symbols=get_top100_symbols(exchange)
    balance,sim=get_balance()
    print(f"Balance ${balance:.2f} Risk ${balance*RISK_PCT:.2f}")
    state=load_json(STATE_FILE, {"open_positions":[],"scans":0})
    last_report_hour=-1
    while True:
        forced=handle_telegram_commands(exchange, symbols)
        now_wat=datetime.now(WAT)
        if now_wat.hour in [8,23] and now_wat.hour!=last_report_hour and now_wat.minute<5:
            last_report_hour=now_wat.hour
            try:
                bal,sim=get_balance()
                trades=sim.get("trades",[])
                eq=[INITIAL_BALANCE]
                for t in trades: eq.append(eq[-1]+t.get("pnl",0))
                send_tg(f"⏰ Auto Report {now_wat.strftime('%H:%M WAT')} Balance ${eq[-1]:.2f} Trades {len(trades)}")
            except: pass
        print(f"\n--- Scan {state.get('scans',0)+1} {datetime.utcnow().isoformat()} Top{len(symbols)} ---")
        signals=[]
        for sym in symbols:
            if len(state.get("open_positions",[]))>=MAX_POSITIONS: break
            df_4h=fetch_df(exchange, sym, TIMEFRAME_BIAS)
            if df_4h is None: continue
            bias=detect_choch(df_4h, SWING_LEN_4H)
            if not bias: continue
            df_1h=fetch_df(exchange, sym, TIMEFRAME_EXEC)
            if df_1h is None: continue
            price_now=df_1h["close"].iloc[-1]
            exec_info=check_execution(bias, df_1h, price_now)
            status_type=exec_info["type"]
            if status_type=="NO_TRADE": continue
            choch_1h=exec_info["choch_1h"]
            entry=choch_1h["entry"]; sl=choch_1h["sl"]
            qty=(balance*RISK_PCT)/abs(entry-sl)
            notional=qty*entry
            margin=notional/LEVERAGE
            dist=exec_info.get("dist",0)*100
            msg=f"*{sym}* {status_type} {bias['dir'].upper()} | 4H->1H aligned\nEntry `{entry:.4f}` SL `{sl:.4f}`\nDist {dist:.2f}% VolOK {bias['vol_ok']} AtrOK {bias['atr_ok']}\nQty `{qty:.4f}` Margin `${margin:.2f}` Risk `${balance*RISK_PCT:.2f}`"
            signals.append((sym,status_type,bias,exec_info,qty,margin))
            if not DRY_RUN:
                try:
                    exchange.set_leverage(LEVERAGE, sym)
                    exchange.set_margin_mode("ISOLATED", sym)
                    side="buy" if bias["dir"]=="long" else "sell"
                    opp="sell" if side=="buy" else "buy"
                    exchange.create_order(sym,"limit",side,qty,entry, params={"positionSide":"BOTH"})
                    exchange.create_order(sym,"STOP_MARKET",opp,qty,None, params={"stopPrice":sl,"reduceOnly":True,"workingType":"MARK_PRICE"})
                    rr2=2; rr3=3
                    if bias["dir"]=="long":
                        tp2=entry+(entry-sl)*rr2
                        tp3=entry+(entry-sl)*rr3
                    else:
                        tp2=entry-(sl-entry)*rr2
                        tp3=entry-(sl-entry)*rr3
                    tp1 = entry*1.05 if bias["dir"]=="long" else entry*0.95
                    for tp in [tp1,tp2,tp3]:
                        exchange.create_order(sym,"limit",opp,qty/3,tp, params={"reduceOnly":True})
                except Exception as e:
                    print(f"Order err {sym} {e}")
                    continue
            fig=build_chart(df_4h, df_1h, sym, bias, exec_info)
            send_tg(msg)
            send_chart(fig, f"{sym} {status_type} {bias['dir']} Qty {qty:.4f}")
            state["open_positions"].append({"symbol":sym,"entry":entry,"sl":sl,"type":status_type,"dir":bias["dir"],"time":datetime.utcnow().isoformat()})
            save_json(STATE_FILE, state)
            time.sleep(1)
        state["scans"]=state.get("scans",0)+1
        save_json(STATE_FILE, state)
        print(f"Scan done. Signals {len(signals)}. Sleep {SCAN_INTERVAL_MIN}min")
        for _ in range(SCAN_INTERVAL_MIN*2):
            time.sleep(30)
            if handle_telegram_commands(exchange, symbols):
                break

if __name__=="__main__":
    main()
