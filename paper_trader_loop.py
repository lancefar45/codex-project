from ib_insync import *
import csv
import datetime as dt
import time
from zoneinfo import ZoneInfo

# ===============================
# CONFIG
# ===============================

HOST = "127.0.0.1"
PORT = 7497
CLIENT_ID = 1

CAPITAL_PER_TRADE_USD = 2000
MAX_OPEN_POSITIONS = 35
MAX_NEW_TRADES_PER_LOOP = 3
LOOP_SECONDS = 20

USE_RTH = True
BAR_SIZE = "5 mins"
INTRADAY_DUR = "2 D"

MIN_SCORE = 0.65
MIN_PRICE = 5
MAX_PRICE = 2000

# ATR% filter
MIN_ATR_PCT = 0.002
MAX_ATR_PCT = 0.08

STOP_LOSS_ATR = 1.0
TAKE_PROFIT_ATR = 1.5

# US whitelist
US_SYMBOLS = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL",
    "TSLA","AMD","INTC","AVGO",
    "JPM","GS","COST","NFLX","BA"
]

# EU whitelist file (already scanned)
EU_OK_CSV = "eu_scan_ok_20260217_225214.csv"

NY = ZoneInfo("America/New_York")
CET = ZoneInfo("Europe/Copenhagen")

# ===============================
# CONNECT
# ===============================

def connect_ib():
    ib = IB()
    for i in range(8):
        try:
            print(f"Connecting to IBKR... attempt {i+1}")
            ib.connect(HOST, PORT, clientId=CLIENT_ID)
            if ib.isConnected():
                print("Connected.")
                return ib
        except Exception as e:
            print(f"Connect failed: {e}")
            time.sleep(2)
    raise RuntimeError("Could not connect to IBKR")

# ===============================
# MARKET HOURS (simple)
# ===============================

def us_open():
    t = dt.datetime.now(tz=NY)
    if t.weekday() >= 5:
        return False
    mo = t.replace(hour=9, minute=30, second=0, microsecond=0)
    mc = t.replace(hour=16, minute=0, second=0, microsecond=0)
    return mo <= t < mc

def eu_open():
    # Simple: 09:00-17:30 Copenhagen time (covers most EU cash sessions)
    t = dt.datetime.now(tz=CET)
    if t.weekday() >= 5:
        return False
    mo = t.replace(hour=9, minute=0, second=0, microsecond=0)
    mc = t.replace(hour=17, minute=30, second=0, microsecond=0)
    return mo <= t < mc

# ===============================
# EU WHITELIST LOADER
# ===============================

def load_eu_whitelist(path):
    """
    Expects the columns from your scan:
    symbol,currency,primaryExchange,status,...
    """
    out = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                if row.get("status","").strip() != "OK":
                    continue
                sym = row["symbol"].strip()
                cur = row["currency"].strip()
                prim = row["primaryExchange"].strip()
                out.append((sym, cur, prim))
    except FileNotFoundError:
        print(f"EU whitelist file not found: {path}")
    return out

# ===============================
# HELPERS / INDICATORS
# ===============================

def get_bars(ib, contract, durationStr):
    try:
        return ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=durationStr,
            barSizeSetting=BAR_SIZE,
            whatToShow="TRADES",
            useRTH=USE_RTH,
            formatDate=1
        )
    except Exception:
        return []

def sma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n

def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(-n, 0):
        ch = closes[i] - closes[i-1]
        if ch >= 0:
            gains += ch
        else:
            losses += -ch
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100.0 - (100.0 / (1.0 + rs))

def atr(bars, n=14):
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(-n, 0):
        h = bars[i].high
        l = bars[i].low
        pc = bars[i-1].close
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    return sum(trs) / n

def position_size(price):
    return max(1, int(CAPITAL_PER_TRADE_USD / price))

def has_position(ib, symbol):
    for p in ib.positions():
        if p.contract.symbol == symbol and abs(p.position) > 0:
            return True
    return False

# ===============================
# BLACKLIST (avoid error spam)
# ===============================

BLACKLIST = {}  # key -> until_epoch

def is_blocked(key):
    return time.time() < BLACKLIST.get(key, 0)

def block(key, minutes, reason):
    BLACKLIST[key] = time.time() + minutes * 60
    print(f"BLACKLIST {key} for {minutes}m (reason={reason})")

def attach_error_handler(ib):
    def on_error(reqId, errorCode, errorString, contract):
        sym = getattr(contract, "symbol", None) if contract else None
        cur = getattr(contract, "currency", None) if contract else None
        prim = getattr(contract, "primaryExchange", None) if contract else None
        if sym and cur and prim and errorCode in (200, 162, 10089, 354):
            key = f"{sym}:{cur}:{prim}"
            block(key, 180, f"ib_error_{errorCode}")
        if contract:
            print(f"Error {errorCode}, reqId {reqId}: {errorString}, contract: {contract}")
        else:
            print(f"Error {errorCode}, reqId {reqId}: {errorString}")
    ib.errorEvent += on_error

# ===============================
# SCORE FUNCTION (quality-focused)
# ===============================

def score_symbol(ib, contract):
    bars = get_bars(ib, contract, INTRADAY_DUR)
    if len(bars) < 60:
        return 0.0, "not_enough_bars", None, None

    closes = [b.close for b in bars]
    price = float(closes[-1])

    if price < MIN_PRICE or price > MAX_PRICE:
        return 0.0, "price_out_of_range", price, None

    a = atr(bars, 14)
    if not a or a <= 0:
        return 0.0, "atr_missing", price, None

    atr_pct = a / price
    if atr_pct < MIN_ATR_PCT or atr_pct > MAX_ATR_PCT:
        return 0.0, "atr_pct_out_of_range", price, a

    s10 = sma(closes, 10)
    s30 = sma(closes, 30)
    if s10 is None or s30 is None:
        return 0.0, "sma_missing", price, a

    mom = 1.0 if s10 > s30 else 0.0

    r = rsi(closes, 14)
    if r is None:
        return 0.0, "rsi_missing", price, a

    # RSI sweet spot around 60
    if r < 40:
        rsi_score = (r / 40) * 0.4
    elif 40 <= r <= 70:
        rsi_score = 1.0 - abs(r - 60.0) / 20.0
    else:
        rsi_score = max(0.0, 1.0 - (r - 70.0) / 30.0)

    rsi_score = max(0.0, min(1.0, rsi_score))

    # Vol score prefers moderate ATR% (around 1.5%)
    target = 0.015
    vol_score = 1.0 - min(1.0, abs(atr_pct - target) / target)

    score = 0.35 * mom + 0.35 * rsi_score + 0.30 * vol_score
    score = max(0.0, min(1.0, score))

    return score, "ok", price, a

# ===============================
# BRACKET ORDER
# ===============================

def place_bracket(ib, contract, qty, entry_price, atr_val):
    tp = round(entry_price + TAKE_PROFIT_ATR * atr_val, 2)
    sl = round(entry_price - STOP_LOSS_ATR * atr_val, 2)
    if sl <= 0:
        return False

    parent = MarketOrder("BUY", qty)
    tp_order = LimitOrder("SELL", qty, tp)
    sl_order = StopOrder("SELL", qty, sl)

    parent.transmit = False
    tp_order.transmit = False
    sl_order.transmit = True

    try:
        parent_trade = ib.placeOrder(contract, parent)
        ib.sleep(0.2)

        parentId = parent_trade.order.orderId
        tp_order.parentId = parentId
        sl_order.parentId = parentId

        ib.placeOrder(contract, tp_order)
        ib.placeOrder(contract, sl_order)
        return True
    except Exception as e:
        print(f"place_bracket failed for {contract.symbol}: {e}")
        return False

# ===============================
# MAIN
# ===============================

def main():
    ib = connect_ib()
    attach_error_handler(ib)

    try:
        acc = ib.managedAccounts()
        if acc:
            print(f"Managed account: {acc[0]}")
    except Exception:
        pass

    # Build US contracts
    us_items = [(s, "USD", "") for s in US_SYMBOLS]
    us_contracts = {}
    for sym, cur, prim in us_items:
        c = Stock(sym, "SMART", cur)
        try:
            ib.qualifyContracts(c)
            us_contracts[sym] = c
        except Exception:
            pass

    # Build EU contracts from whitelist
    eu_whitelist = load_eu_whitelist(EU_OK_CSV)
    eu_contracts = {}
    for sym, cur, prim in eu_whitelist:
        c = Stock(sym, "SMART", cur, primaryExchange=prim)
        try:
            ib.qualifyContracts(c)
            eu_contracts[sym] = c
        except Exception:
            pass

    print(f"Qualified US: {len(us_contracts)}  EU: {len(eu_contracts)}")
    print("WHITELIST LOOP STARTED (US+EU)")

    while True:
        # Gate by sessions
        u_open = us_open()
        e_open = eu_open()

        if not u_open and not e_open:
            print("Markets closed (US+EU). Sleeping...")
            time.sleep(60)
            continue

        # Position limit
        if len(ib.positions()) >= MAX_OPEN_POSITIONS:
            print("Max positions reached.")
            time.sleep(LOOP_SECONDS)
            continue

        candidates = []

        # US scoring
        if u_open:
            for sym, c in us_contracts.items():
                if has_position(ib, sym):
                    continue
                # use a light blacklist key even for US
                key = f"{sym}:{c.currency}:{getattr(c,'primaryExchange','') or ''}"
                if is_blocked(key):
                    continue
                score, reason, price, a = score_symbol(ib, c)
                if reason != "ok":
                    if reason in ("not_enough_bars","atr_missing","sma_missing","rsi_missing"):
                        block(key, 60, reason)
                    continue
                if score >= MIN_SCORE:
                    candidates.append(("US", score, sym, c, price, a))
                ib.sleep(0.15)

        # EU scoring
        if e_open:
            for sym, c in eu_contracts.items():
                if has_position(ib, sym):
                    continue
                key = f"{sym}:{c.currency}:{getattr(c,'primaryExchange','') or ''}"
                if is_blocked(key):
                    continue
                score, reason, price, a = score_symbol(ib, c)
                if reason != "ok":
                    if reason in ("not_enough_bars","atr_missing","sma_missing","rsi_missing"):
                        block(key, 60, reason)
                    continue
                if score >= MIN_SCORE:
                    candidates.append(("EU", score, sym, c, price, a))
                ib.sleep(0.15)

        candidates.sort(key=lambda x: x[1], reverse=True)

        if not candidates:
            print("No ranked candidates. Sleeping.")
            time.sleep(LOOP_SECONDS)
            continue

        trades = 0
        for region, score, sym, c, price, a in candidates:
            if trades >= MAX_NEW_TRADES_PER_LOOP:
                break
            if len(ib.positions()) >= MAX_OPEN_POSITIONS:
                break
            if has_position(ib, sym):
                continue

            qty = position_size(float(price))
            print(f"BUY {sym} ({region}) score={score:.2f} price={price:.2f}")
            ok = place_bracket(ib, c, qty, float(price), float(a))
            if ok:
                trades += 1
            else:
                key = f"{sym}:{c.currency}:{getattr(c,'primaryExchange','') or ''}"
                block(key, 60, "place_failed")

            ib.sleep(0.5)

        time.sleep(LOOP_SECONDS)

if __name__ == "__main__":
    main()
