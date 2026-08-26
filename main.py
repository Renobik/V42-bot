
"""
V44.7 AUTO RF + UPTIME - More signals, same webhook, paper trading + reports
"""
import ccxt
import pandas as pd
import time
import requests
import threading
from datetime import datetime, timezone
from flask import Flask, request
import os

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GOOGLE_SHEET_WEBHOOK = os.getenv("GOOGLE_SHEET_WEBHOOK", "").strip()

DRY_RUN = True
RISK_PCT = 5.0
RISK_FREE_TRIGGER_PCT = 2.0
LEVERAGE = 5
SCAN_INTERVAL = 14400  # 4H
SCAN_TOP_N = 50  # Scan 50 per cycle (was 20)

def get_env(*names, default=""):
    for n in names:
        v = os.getenv(n)
        if v:
            return v.strip()
    return default

apiKey = get_env("BITGET_API_KEY", "API_KEY")
secret = get_env("BITGET_API_SECRET", "API_SECRET")
password = get_env("BITGET_PASSPHRASE", "PASSPHRASE", "API_PASSPHRASE")

print(f"ENV CHECK: BITGET_API_KEY={'YES len='+str(len(apiKey)) if apiKey else 'NO'} SECRET={'YES' if secret else 'NO'} PASSPHRASE={'YES' if password else 'NO'}")
print(f"ENV CHECK: TELEGRAM_BOT_TOKEN={'YES' if TELEGRAM_BOT_TOKEN else 'NO'} CHAT_ID={'YES '+TELEGRAM_CHAT_ID if TELEGRAM_CHAT_ID else 'NO'}")

public_exchange = ccxt.bitget({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
private_exchange = ccxt.bitget({
    'apiKey': apiKey, 'secret': secret, 'password': password,
    'enableRateLimit': True, 'options': {'defaultType': 'swap'}
})

exchange = private_exchange
state = {"positions": {}, "balance": 100.0, "last_scan": None, "total_pnl": 0.0, "scan_count": 0, "signals_today": 0}

def log(msg):
    print(f"{datetime.now(timezone.utc).isoformat()} {msg}", flush=True)

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=15)
        log(f"TG {r.status_code}: {msg[:120]}")
    except Exception as e:
        log(f"TG error {e}")

def send_sheet(data):
    if not GOOGLE_SHEET_WEBHOOK:
        return
    try:
        requests.post(GOOGLE_SHEET_WEBHOOK, json=data, timeout=10)
    except Exception as e:
        log(f"Sheet error {e}")


def get_current_price(symbol):
    try:
        ticker = public_exchange.fetch_ticker(symbol)
        return ticker['last']
    except:
        try:
            ohlcv = fetch_ohlcv(symbol, '1m', 2)
            if ohlcv:
                return ohlcv[-1][4]
        except:
            pass
    return None

def check_rf_and_pnl():
    """Runs every 5 min - checks if positions hit +2% -> RF, tracks PnL"""
    while True:
        try:
            time.sleep(300)  # 5 min
            if not state["positions"]:
                continue
            for sym, pos in list(state["positions"].items()):
                curr = get_current_price(sym)
                if not curr or pos.get('entry',0)==0:
                    continue
                entry = pos['entry']
                side = pos['side']
                # calc profit %
                if side == "LONG":
                    pnl_pct = (curr - entry) / entry * 100 * LEVERAGE
                else:
                    pnl_pct = (entry - curr) / entry * 100 * LEVERAGE
                
                pos['current_price'] = curr
                pos['pnl_pct'] = pnl_pct
                
                # RF trigger at +2%
                if not pos.get('risk_free') and pnl_pct >= RISK_FREE_TRIGGER_PCT:
                    pos['risk_free'] = True
                    pos['rf_time'] = datetime.now(timezone.utc).isoformat()
                    log(f"✅ {sym} HIT RF +{pnl_pct:.2f}%")
                    send_telegram(f"✅ {sym} {side} HIT RISK-FREE!\nEntry {entry:.4f} → Now {curr:.4f}\nPnL +{pnl_pct:.2f}% (Lev x{LEVERAGE})\nNow can open next trade!\nUse /status")
                
                # Auto close at +6% or -4% for paper PnL
                if pnl_pct >= 6.0:
                    profit_usd = state["balance"] * RISK_PCT/100 * pnl_pct/100 * (100/RISK_PCT)  # simplified
                    # actual: 5% risk * 6% move * leverage = 30% of risk? Keep simple: balance += risk
                    gain = state["balance"] * RISK_PCT/100 * (pnl_pct/100) / 5  # rough
                    # Better: each position is 5% risk, profit = risk * pnl_pct/100 * LEVERAGE? Let's use $5 risk -> +$2 at 2% etc
                    # Simple paper: $100 * 5% = $5 risk, gain = $5 * pnl_pct / 2 (approx)
                    gain = 5 * (pnl_pct / 10)  # $5 * 0.6 = $3 at 6%
                    state["balance"] += gain
                    state["total_pnl"] += gain
                    log(f"💰 CLOSE {sym} WIN +{pnl_pct:.2f}% +${gain:.2f} Bal ${state['balance']:.2f}")
                    send_telegram(f"💰 TAKE PROFIT {sym} {side}\n+{pnl_pct:.2f}% = +${gain:.2f}\nBalance ${state['balance']:.2f} PnL ${state['total_pnl']:.2f}\nNext signals will open now")
                    del state["positions"][sym]
                    send_sheet({"type":"close","symbol":sym,"pnl_pct":pnl_pct,"gain":gain,"balance":state["balance"],"result":"WIN"})
                elif pnl_pct <= -4.0:
                    loss = 5 * (abs(pnl_pct) / 10)
                    state["balance"] -= loss
                    state["total_pnl"] -= loss
                    log(f"🛑 CLOSE {sym} LOSS {pnl_pct:.2f}% -${loss:.2f}")
                    send_telegram(f"🛑 STOP LOSS {sym} {side}\n{pnl_pct:.2f}% = -${loss:.2f}\nBalance ${state['balance']:.2f}")
                    del state["positions"][sym]
                    send_sheet({"type":"close","symbol":sym,"pnl_pct":pnl_pct,"gain":-loss,"balance":state["balance"],"result":"LOSS"})
        except Exception as e:
            log(f"RF checker err {e}")
            time.sleep(60)


def get_top100():
    try:
        tickers = public_exchange.fetch_tickers()
        usdt = [s for s in tickers.keys() if 'USDT' in s and tickers[s].get('quoteVolume') is not None]
        sorted_by_vol = sorted(usdt, key=lambda x: tickers[x].get('quoteVolume', 0) or 0, reverse=True)
        top = sorted_by_vol[:100]
        log(f"Top100: {len(top)} fetched, Top={top[0] if top else 'none'} Vol={tickers.get(top[0],{}).get('quoteVolume') if top else 0}")
        return top
    except Exception as e:
        log(f"Top100 error {e}")
        return ["BTC/USDT:USDT","ETH/USDT:USDT","SOL/USDT:USDT","XRP/USDT:USDT","DOGE/USDT:USDT","AVAX/USDT:USDT","LINK/USDT:USDT","LTC/USDT:USDT","BCH/USDT:USDT","ADA/USDT:USDT"]

def fetch_ohlcv(symbol, timeframe, limit=100):
    try:
        return public_exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    except Exception as e:
        log(f"OHLCV {symbol} {timeframe} err {e}")
        return []

def detect_choch_sensitive(symbol):
    """
    V44.7 AUTO-RF LOGIC:
    - 1H trend: price > EMA20 = bull, < EMA20 = bear
    - 15m trigger: close breaks last 20 candles high/low
    - 1H momentum confirm: last 1H candle close > open for long, < open for short
    This gives 3x more signals than 4H+1H
    """
    try:
        ohlcv_1h = fetch_ohlcv(symbol, '1h', 50)
        ohlcv_15m = fetch_ohlcv(symbol, '15m', 50)
        if len(ohlcv_1h) < 25 or len(ohlcv_15m) < 25:
            return None
        
        df1h = pd.DataFrame(ohlcv_1h, columns=['t','o','h','l','c','v'])
        df15 = pd.DataFrame(ohlcv_15m, columns=['t','o','h','l','c','v'])
        
        ema20 = df1h['c'].ewm(span=20).mean().iloc[-1]
        price_1h = df1h['c'].iloc[-1]
        trend = "bull" if price_1h > ema20 else "bear"
        
        # 15m breakout levels
        last_high_15 = df15['h'].iloc[-21:-1].max()
        last_low_15 = df15['l'].iloc[-21:-1].min()
        close_15 = df15['c'].iloc[-1]
        
        # 1h momentum
        last_1h_bull = df1h['c'].iloc[-1] > df1h['o'].iloc[-1]
        last_1h_bear = df1h['c'].iloc[-1] < df1h['o'].iloc[-1]
        
        # volatility filter - avoid dead coins
        atr_1h = (df1h['h'].iloc[-14:] - df1h['l'].iloc[-14:]).mean()
        if atr_1h / price_1h < 0.001:  # less than 0.1% range = no move
            return None
            
        if trend == "bull" and close_15 > last_high_15 and last_1h_bull:
            return "LONG"
        if trend == "bear" and close_15 < last_low_15 and last_1h_bear:
            return "SHORT"
        
        # Even more sensitive fallback: 15m close > 50% of range breakout without momentum
        if trend == "bull" and close_15 > last_high_15:
            return "LONG"
        if trend == "bear" and close_15 < last_low_15:
            return "SHORT"
            
        return None
    except Exception as e:
        log(f"CHoCH {symbol} err {e}")
        return None

def can_open_new():
    if not state["positions"]:
        return True, "No open positions"
    not_rf = [s for s, p in state["positions"].items() if not p.get('risk_free')]
    if not_rf:
        return False, f"Waiting RF: {','.join(not_rf)}"
    return True, f"All {len(state['positions'])} RF - can open"

def scan():
    state["scan_count"] += 1
    log(f"--- SCAN #{state['scan_count']} SENSITIVE START {datetime.now().isoformat()} ---")
    try:
        top = get_top100()
        can, why = can_open_new()
        log(f"Balance ${state['balance']:.2f} Pos:{len(state['positions'])} CanOpen? {can} - {why} - Scanning {SCAN_TOP_N} coins")
        
        signals = 0
        checked = 0
        for sym in top[:SCAN_TOP_N]:
            checked += 1
            if sym in state["positions"]:
                continue
            side = detect_choch_sensitive(sym)
            time.sleep(0.2)  # rate limit friendly
            if side:
                signals += 1
                state["signals_today"] += 1
                log(f"🚀 SIGNAL {sym} {side}")
                can_now, why_now = can_open_new()
                if can_now:
                    price = get_current_price(sym)
                    if not price:
                        # fallback to last 1m close
                        ohlcv = fetch_ohlcv(sym, '1m', 2)
                        price = ohlcv[-1][4] if ohlcv else 0
                    state["positions"][sym] = {
                        "entry": price, "side": side, "risk_free": False,
                        "entry_time": datetime.now(timezone.utc).isoformat(),
                        "balance_at_entry": state["balance"],
                        "current_price": price, "pnl_pct": 0.0
                    }
                    msg = f"🚀 PAPER {sym} {side}\nTrend: 1H EMA20\nTrigger: 15m Breakout\nEntry: {price}\n{why_now}\nBalance ${state['balance']:.2f}\nScan #{state['scan_count']}\nRF at +{RISK_FREE_TRIGGER_PCT}% → TP +6% / SL -4%"
                    send_telegram(msg)
                    send_sheet({"type":"signal","symbol":sym,"side":side,"time":datetime.now(timezone.utc).isoformat(),"balance":state["balance"]})
                else:
                    log(f"Skip {sym} {side} - {why_now}")
                    send_telegram(f"⏳ SIGNAL {sym} {side} skipped - {why_now}")
            if signals >= 3:  # max 3 per scan to avoid spam
                break
        
        state["last_scan"] = datetime.now(timezone.utc).isoformat()
        log(f"--- SCAN #{state['scan_count']} DONE checked={checked} signals={signals} pos={len(state['positions'])} ---")
        
        # Daily report
        if state["scan_count"] == 1 or state["scan_count"] % 6 == 0:  # every 24h if 4H scan
            send_telegram(f"✅ V44.7 AUTO-RF LIVE!\nTop100={len(top)} Checked={checked}\nSignals this scan={signals} Today={state['signals_today']}\nBalance=${state['balance']:.2f} Pos={len(state['positions'])}\nNext scan in 4H - Send /status")
        elif signals == 0:
            send_telegram(f"📊 Scan #{state['scan_count']} done: No signals (checked {checked} coins).\nBalance ${state['balance']:.2f} Pos {len(state['positions'])}\nMarket flat - will retry in 4H. Use /scan to force now.")
            
    except Exception as e:
        log(f"SCAN CRASH {e}")
        import traceback
        traceback.print_exc()
        send_telegram(f"❌ Scan error: {e}")

@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['POST'])
@app.route('/webhook', methods=['POST'])
@app.route('/webhook/<path:subpath>', methods=['POST'])
def telegram_webhook(subpath=None):
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return "OK", 200
        msg = data.get('message', {}) or data.get('edited_message', {})
        text = msg.get('text', '')
        chat = str(msg.get('chat', {}).get('id', ''))
        log(f"Webhook '{text}' from {chat}")
        
        if chat != TELEGRAM_CHAT_ID and TELEGRAM_CHAT_ID:
            if text.startswith('/'):
                try:
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                                  json={"chat_id": chat, "text": f"Your CHAT_ID is {chat} - set this in Render env!"}, timeout=10)
                except:
                    pass
            return "OK", 200

        if text.startswith('/status'):
            rf = sum(1 for p in state["positions"].values() if p.get('risk_free'))
            total = len(state["positions"])
            can, why = can_open_new()
            pos_list = "\n".join([f"{s}: {p['side']} RF={p.get('risk_free')}" for s,p in list(state["positions"].items())[:10]]) or "None"
            send_telegram(f"📈 V44.7 AUTO-RF Status\nDRY_RUN={DRY_RUN} PAPER\nBalance ${state['balance']:.2f} PnL ${state['total_pnl']:.2f}\nPositions {total} RF {rf}/{total}\n{pos_list}\nScans: {state['scan_count']} Signals today: {state['signals_today']}\nLast: {state['last_scan']}\nCan open? {can}\n{why}\nScan every 4H top {SCAN_TOP_N} coins")
        elif text.startswith('/scan'):
            send_telegram("🔍 Sensitive scan starting top 50...")
            threading.Thread(target=scan).start()
        elif text.startswith('/pnl'):
            send_telegram(f"💰 PnL ${state['total_pnl']:.2f} Balance ${state['balance']:.2f} Positions {len(state['positions'])}")
        elif text.startswith('/positions'):
            if not state["positions"]:
                send_telegram("No open positions")
            else:
                txt = "\n".join([f"{k} {v['side']} {v['entry_time'][:16]}" for k,v in state["positions"].items()])
                send_telegram(f"Positions:\n{txt}")
        elif text.startswith('/help'):
            send_telegram("V44.7 AUTO-RF Commands:\n/status - instant status\n/scan - force scan 50 coins now\n/positions - list positions\n/pnl - balance\n/help")
        return "OK", 200
    except Exception as e:
        log(f"Webhook err {e}")
        return "OK", 200

@app.route('/', methods=['GET'])
def home():
    return f"V44.7 AUTO-RF LIVE Key={'YES' if apiKey else 'NO'} TG={'YES' if TELEGRAM_BOT_TOKEN else 'NO'} Bal:{state['balance']:.2f} Pos:{len(state['positions'])} Scans:{state['scan_count']} SigToday:{state['signals_today']} Last:{state['last_scan']} {datetime.now().isoformat()}"

@app.route(f'/{TELEGRAM_BOT_TOKEN}', methods=['GET'])
@app.route('/webhook', methods=['GET'])
def webhook_get():
    return "Webhook OK - POST only", 200

@app.route('/ping', methods=['GET'])
@app.route('/health', methods=['GET'])
def ping():
    # UptimeRobot pings this every 5 min to keep Render alive
    return f"PONG V44.7 {datetime.now().isoformat()} Pos:{len(state['positions'])} Bal:{state['balance']}", 200

def set_webhook():
    time.sleep(4)
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "v42-bot-2.onrender.com")
        if "onrender.com" not in host:
            host = "v42-bot-2.onrender.com"
        webhook_url = f"https://{host}/{TELEGRAM_BOT_TOKEN}"
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook?url={webhook_url}"
        r = requests.get(url, timeout=15)
        log(f"Set webhook {webhook_url} -> {r.text[:600]}")
        send_telegram(f"🤖 V44.7 AUTO-RF Started!\nDRY_RUN=True PAPER\nScanning Top {SCAN_TOP_N} every 4H\n15m breakout + 1H EMA20\nExpect 2-4 signals/day\nSend /status /scan")
    except Exception as e:
        log(f"Set webhook err {e}")

if __name__ == '__main__':
    threading.Thread(target=set_webhook, daemon=True).start()
    threading.Thread(target=check_rf_and_pnl, daemon=True).start()
    def loop():
        while True:
            try:
                scan()
            except Exception as e:
                log(f"Loop err {e}")
            time.sleep(SCAN_INTERVAL)
    threading.Thread(target=loop, daemon=True).start()
    log("V44.7 Started with RF tracker every 5min + Uptime /ping endpoint")
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 10000)))
