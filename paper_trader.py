from __future__ import annotations

from datetime import datetime, timezone
import os
import csv
import json
import math
import time

from ib_insync import IB, Stock, util, MarketOrder, LimitOrder, StopOrder


# ========= CONFIG =========
HOST = "127.0.0.1"
PORT = 7497                 # IB Gateway Paper (hos dig)
CLIENT_ID = 55              # vælg et fast tal

TICKERS = [
    "SPY", "QQQ", "IWM", "DIA",
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA",
    "TSLA", "AMD", "INTC", "AVGO", "CRM",
    "NFLX", "ORCL", "ADBE", "CSCO", "QCOM",
    "JPM", "BAC", "GS", "V",
    "XOM", "CVX",
    "UNH", "JNJ",
    "KO", "WMT"
]

ACCOUNT_EQUITY_DKK = 5000.0
RISK_PER_TRADE_PCT = 0.01
STOP_LOSS_PCT = 0.007
TAKE_PROFIT_PCT = 0.012

MAX_TRADES_PER_DAY = 2

USE_RTH = True
BAR_DURATION = "2 D"
BAR_SIZE = "5 mins"

# Market data: 3 = delayed (det bruger du)
MARKET_DATA_TYPE = 3

ENTRY_LOG = "trade_log.csv"
CLOSE_LOG = "trade_close_log.csv"
STATE_FILE = "bot_state.json"


# ========= UTILS =========
def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def utc_now_iso() -> str:
    return utc_now().isoformat()

def today_utc_str() -> str:
    return str(utc_now().date())

def ensure_csv_header(path: str, header: list[str]) -> None:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(header)

def append_csv(path: str, row: list) -> None:
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)

def read_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"date": today_utc_str(), "trades_today": 0, "open_position": None, "last_close_time": None}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        state.setdefault("date", today_utc_str())
        state.setdefault("trades_today", 0)
        state.setdefault("open_position", None)
        state.setdefault("last_close_time", None)
        return state
    except Exception:
        return {"date": today_utc_str(), "trades_today": 0, "open_position": None, "last_close_time": None}

def write_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

def reset_state_if_new_day(state: dict) -> dict:
    if state.get("date") != today_utc_str():
        state["date"] = today_utc_str()
        state["trades_today"] = 0
        write_state(state)
    return state

def pick_price(ticker_obj):
    last = ticker_obj.last
    close = ticker_obj.close
    if last is not None and last == last and last > 0:
        return float(last), "last"
    if close is not None and close == close and close == close and close > 0:
        return float(close), "close"
    return None, "none"


# ========= SIGNAL =========
def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def get_signal(df):
    df = df.copy()
    df["EMA20"] = df["close"].ewm(span=20).mean()
    df["EMA50"] = df["close"].ewm(span=50).mean()
    df["RSI14"] = rsi(df["close"], 14)

    if len(df) < 60:
        return False

    latest = df.iloc[-1]
    cond_trend = latest.close > latest.EMA20 and latest.close > latest.EMA50
    cond_rsi = latest.RSI14 > 55
    return bool(cond_trend and cond_rsi)


# ========= RISK / SIZING =========
def calc_qty(price: float) -> int:
    risk_amt = ACCOUNT_EQUITY_DKK * RISK_PER_TRADE_PCT
    risk_per_share = price * STOP_LOSS_PCT
    if risk_per_share <= 0:
        return 0
    qty = int(math.floor(risk_amt / risk_per_share))
    return max(0, qty)


# ========= ORDERS =========
def place_bracket(ib: IB, contract, qty: int, entry_ref_price: float):
    tp_price = round(entry_ref_price * (1 + TAKE_PROFIT_PCT), 2)
    sl_price = round(entry_ref_price * (1 - STOP_LOSS_PCT), 2)

    parent = MarketOrder("BUY", qty)
    parent.transmit = False
    parent_trade = ib.placeOrder(contract, parent)
    ib.sleep(0.5)

    parent_id = parent_trade.order.orderId

    take_profit = LimitOrder("SELL", qty, tp_price)
    take_profit.parentId = parent_id
    take_profit.transmit = False

    stop_loss = StopOrder("SELL", qty, sl_price)
    stop_loss.parentId = parent_id
    stop_loss.transmit = True

    oca_group = f"OCA_{parent_id}"
    take_profit.ocaGroup = oca_group
    take_profit.ocaType = 1
    stop_loss.ocaGroup = oca_group
    stop_loss.ocaType = 1

    ib.placeOrder(contract, take_profit)
    ib.placeOrder(contract, stop_loss)

    return parent_trade, tp_price, sl_price, parent_id


# ========= CLOSE LOGIC =========
def parse_ib_time(t) -> datetime | None:
    try:
        if isinstance(t, datetime):
            return t.astimezone(timezone.utc) if t.tzinfo else t.replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(str(t)).astimezone(timezone.utc)
    except Exception:
        return None

def check_and_log_close(ib: IB, state: dict) -> dict:
    open_pos = state.get("open_position")
    if not open_pos:
        return state

    # Hvis IB stadig viser positioner eller åbne ordrer, så antager vi ikke "closed" endnu
    if ib.positions() or ib.openOrders():
        return state

    symbol = open_pos.get("symbol")
    qty = int(open_pos.get("qty", 0))
    entry_price = float(open_pos.get("entry_price", 0.0))

    entry_time = None
    try:
        entry_time = datetime.fromisoformat(open_pos.get("entry_time"))
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)
        else:
            entry_time = entry_time.astimezone(timezone.utc)
    except Exception:
        entry_time = None

    last_close_time = None
    if state.get("last_close_time"):
        try:
            last_close_time = datetime.fromisoformat(state["last_close_time"])
            if last_close_time.tzinfo is None:
                last_close_time = last_close_time.replace(tzinfo=timezone.utc)
            else:
                last_close_time = last_close_time.astimezone(timezone.utc)
        except Exception:
            last_close_time = None

    execs = ib.executions()
    best = None
    best_time = None

    for e in execs:
        if getattr(e, "side", None) != "SLD":
            continue
        if getattr(e.contract, "symbol", None) != symbol:
            continue

        et = parse_ib_time(e.time)
        if et is None:
            continue
        if entry_time and et <= entry_time:
            continue
        if last_close_time and et <= last_close_time:
            continue

        if best is None or et > best_time:
            best = e
            best_time = et

    # Hvis vi ikke kan finde SELL execution (fx fordi ordren blev cancelled uden fill),
    # så nulstiller vi state alligevel, fordi der ikke er positioner/ordrer i IB.
    if best is None:
        print("\n=== POSITION CLEARED (no open pos/orders, no sell execution found) ===")
        state["open_position"] = None
        write_state(state)
        return state

    exit_price = float(best.price)
    pnl = (exit_price - entry_price) * qty

    print("\n=== TRADE CLOSED ===")
    print(f"Symbol: {symbol}")
    print(f"Qty: {qty}")
    print(f"Entry: {entry_price}")
    print(f"Exit:  {exit_price}")
    print(f"P/L:   {pnl:.2f}")
    print(f"Time:  {best_time.isoformat()}")

    ensure_csv_header(CLOSE_LOG, [
        "timestamp_utc", "symbol", "qty", "entry_price", "exit_price", "pnl", "exit_time_utc"
    ])
    append_csv(CLOSE_LOG, [
        utc_now_iso(), symbol, qty, entry_price, exit_price, round(pnl, 4), best_time.isoformat()
    ])

    state["open_position"] = None
    state["last_close_time"] = best_time.isoformat()
    write_state(state)
    return state


# ========= MAIN =========
def main():
    ensure_csv_header(ENTRY_LOG, [
        "timestamp_utc", "symbol", "qty", "entry_price", "entry_price_source",
        "tp_price", "sl_price", "order_id"
    ])
    ensure_csv_header(CLOSE_LOG, [
        "timestamp_utc", "symbol", "qty", "entry_price", "exit_price", "pnl", "exit_time_utc"
    ])

    state = reset_state_if_new_day(read_state())

    # --- Robust connect (timeout + retries) ---
    ib = IB()
    ib.RequestTimeout = 30

    connected = False
    for attempt in range(1, 4):
        try:
            ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=30)
            connected = True
            break
        except Exception as e:
            print(f"CONNECT attempt {attempt} failed: {e}")
            try:
                ib.disconnect()
            except Exception:
                pass
            time.sleep(3)

    if not connected:
        print("STOP: Could not connect to IB Gateway/TWS. Restart Gateway and ensure it is fully logged in.")
        return

    ib.reqMarketDataType(MARKET_DATA_TYPE)

    # 1) Først: log evt. lukning / ryd state hvis nødvendigt
    state = check_and_log_close(ib, state)

    # 2) HARD GATE: hvis der findes åbne ordrer eller positioner -> ingen nye trades
    open_orders = ib.openOrders()
    positions = ib.positions()

    if open_orders:
        print("STOP: There are open orders already. Not placing new trades.")
        ib.disconnect()
        return

    if positions:
        print("STOP: There is an open position already. Not placing new trades.")
        ib.disconnect()
        return

    # 3) Daily limit
    if int(state.get("trades_today", 0)) >= MAX_TRADES_PER_DAY:
        print("STOP: Max trades reached today.")
        ib.disconnect()
        return

    # 4) Scan + place første trade
    for sym in TICKERS:
        contract = Stock(sym, "SMART", "USD")
        ib.qualifyContracts(contract)

        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=BAR_DURATION,
            barSizeSetting=BAR_SIZE,
            whatToShow="TRADES",
            useRTH=USE_RTH
        )
        df = util.df(bars)
        signal = get_signal(df)

        t = ib.reqMktData(contract, snapshot=True)
        ib.sleep(1.2)
        price, source = pick_price(t)

        print(f"{sym}: signal={signal}, price={price} ({source})")

        if not signal or price is None:
            continue

        qty = calc_qty(price)
        if qty <= 0:
            print(f"SKIP {sym}: qty computed as {qty}")
            continue

        parent_trade, tp_price, sl_price, order_id = place_bracket(ib, contract, qty, price)

        # IMPORTANT: kun “registrer” trade hvis den faktisk bliver fyldt
        ib.sleep(1.5)
        if not parent_trade.fills:
            print("Order not filled. Skipping state update.")
            # ryd evt. hængende child orders hvis de blev lagt (sjældent ved cancelled parent)
            ib.disconnect()
            print("\nDONE")
            return

        entry_price = float(parent_trade.fills[-1].execution.price)
        source = "fill"

        print("\nPLACED PAPER TRADE")
        print(f"Symbol: {sym}")
        print(f"Qty: {qty}")
        print(f"Entry price: {entry_price} ({source})")
        print(f"TP: {tp_price}")
        print(f"SL: {sl_price}")
        print(f"OrderID: {order_id}")

        append_csv(ENTRY_LOG, [
            utc_now_iso(), sym, qty, entry_price, source, tp_price, sl_price, order_id
        ])

        state["open_position"] = {
            "symbol": sym,
            "qty": qty,
            "entry_price": entry_price,
            "entry_time": utc_now_iso(),
            "order_id": order_id
        }
        state["trades_today"] = int(state.get("trades_today", 0)) + 1
        write_state(state)

        break

    ib.disconnect()
    print("\nDONE")


if __name__ == "__main__":
    main()
