from ib_insync import *
import datetime
import time

# ===============================
# CONFIG
# ===============================

HOST = "127.0.0.1"
PORT = 7497
CLIENT_ID = 1

CAPITAL_PER_TRADE = 50000          # <-- hæv den hvis du vil købe større (fx 20000 eller 50000)
MAX_OPEN_POSITIONS = 40           # max antal *aktier* med åben position (ikke antal handler)
MAX_POSITION_PER_SYMBOL = 0       # 0 = ubegrænset. Ellers max antal aktier (shares) pr. symbol
LOOP_SECONDS = 15

# US ONLY LIQUID STOCKS
SYMBOLS = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL",
    "TSLA","AMD","INTC","AVGO",
    "JPM","GS","COST","NFLX","BA"
]

# ===============================
# CONNECT
# ===============================

def connect_ib():
    ib = IB()
    for i in range(5):
        try:
            print(f"Connecting to IBKR... attempt {i+1}")
            ib.connect(HOST, PORT, clientId=CLIENT_ID)
            if ib.isConnected():
                print("Connected.")
                return ib
        except Exception:
            time.sleep(2)
    raise RuntimeError("Could not connect to IBKR")

# ===============================
# HELPERS
# ===============================

def us_market_open():
    # Simpel "er det ca. US markedstid?" (kan forbedres med DST senere)
    now = datetime.datetime.now(datetime.timezone.utc)
    est = now.astimezone(datetime.timezone(datetime.timedelta(hours=-5)))
    return 9 <= est.hour < 16

def get_sma(bars, length):
    if len(bars) < length:
        return None
    closes = [b.close for b in bars]
    return sum(closes[-length:]) / length

def get_price(ib, contract):
    bars = ib.reqHistoricalData(
        contract,
        endDateTime='',
        durationStr='1 D',
        barSizeSetting='5 mins',
        whatToShow='TRADES',
        useRTH=True
    )
    if not bars:
        return None
    return bars[-1].close

def position_size(price):
    # Derfor kan den købe "kun 1": hvis CAPITAL_PER_TRADE < pris*2, så qty bliver 1
    qty = int(CAPITAL_PER_TRADE / price)
    return max(1, qty)

def position_qty(ib, symbol):
    for p in ib.positions():
        c = p.contract
        if getattr(c, "symbol", None) == symbol and getattr(c, "secType", None) == "STK":
            return float(p.position)
    return 0.0

def has_open_order(ib, symbol):
    """
    True hvis der allerede ligger en aktiv/pending ordre på symbolet.
    Det er typisk årsagen til at man ser mange "ikke transmitter"/pending i TWS.
    """
    for tr in ib.openTrades():
        try:
            c = tr.contract
            if getattr(c, "symbol", None) != symbol:
                continue
            st = (tr.orderStatus.status or "").lower()
            # Active-ish states
            if st in ("presubmitted", "submitted", "pendingsubmit", "pendingcancel"):
                return True
        except Exception:
            pass
    return False

def count_open_position_symbols(ib):
    # Hvor mange *symboler* har vi en ikke-nul position i?
    count = 0
    for p in ib.positions():
        if getattr(p.contract, "secType", None) == "STK" and abs(float(p.position)) > 0:
            count += 1
    return count

# ===============================
# STRATEGY
# ===============================

def check_signal(ib, contract):
    bars = ib.reqHistoricalData(
        contract,
        endDateTime='',
        durationStr='2 D',
        barSizeSetting='5 mins',
        whatToShow='TRADES',
        useRTH=True
    )

    if len(bars) < 50:
        return None

    sma_fast = get_sma(bars, 10)
    sma_slow = get_sma(bars, 30)

    if sma_fast is None or sma_slow is None:
        return None

    if sma_fast > sma_slow:
        return "BUY"
    elif sma_fast < sma_slow:
        return "SELL"
    return None

# ===============================
# MAIN LOOP
# ===============================

def main():
    ib = connect_ib()
    print("US ONLY MOMENTUM LOOP STARTED")
    print(f"Add-to-position: ON  |  MAX_POSITION_PER_SYMBOL={MAX_POSITION_PER_SYMBOL} (0=unlimited)")
    print(f"CAPITAL_PER_TRADE={CAPITAL_PER_TRADE}  |  MAX_OPEN_POSITIONS={MAX_OPEN_POSITIONS}")

    contracts = [Stock(s, "SMART", "USD") for s in SYMBOLS]
    ib.qualifyContracts(*contracts)

    while True:
        if not us_market_open():
            print("US market closed. Sleeping...")
            time.sleep(60)
            continue

        open_symbol_positions = count_open_position_symbols(ib)
        if open_symbol_positions >= MAX_OPEN_POSITIONS:
            print("Max positions reached.")
            time.sleep(LOOP_SECONDS)
            continue

        for contract in contracts:
            symbol = contract.symbol

            # Undgå at spamme flere ordrer på samme symbol mens en ordre stadig er aktiv
            if has_open_order(ib, symbol):
                continue

            signal = check_signal(ib, contract)
            if not signal:
                continue

            price = get_price(ib, contract)
            if not price:
                continue

            qty = position_size(price)

            # Valgfrit loft pr symbol (shares)
            if MAX_POSITION_PER_SYMBOL and MAX_POSITION_PER_SYMBOL > 0:
                current = abs(position_qty(ib, symbol))
                if current >= MAX_POSITION_PER_SYMBOL:
                    continue
                # sørg for ikke at gå over loftet
                qty = int(min(qty, MAX_POSITION_PER_SYMBOL - current))
                if qty <= 0:
                    continue

            if signal == "BUY":
                order = MarketOrder("BUY", qty)
            else:
                order = MarketOrder("SELL", qty)

            print(f"{signal} {symbol} qty={qty} price~{price}")
            ib.placeOrder(contract, order)

            time.sleep(1.0)

        time.sleep(LOOP_SECONDS)

if __name__ == "__main__":
    main()
