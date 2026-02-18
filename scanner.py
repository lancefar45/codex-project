from datetime import datetime, timezone
import pandas as pd
from ib_insync import IB, Stock

HOST = "127.0.0.1"
PORT = 7497           # paper
CLIENT_ID = 10
MARKET_DATA_TYPE = 3  # 3=delayed
TICKERS = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA"]
EXCHANGE = "SMART"
CURRENCY = "USD"

def pick_price(ticker_obj):
    # IB kan give -1 eller nan på bid/ask i delayed; vi bruger last/close.
    last = ticker_obj.last
    close = ticker_obj.close
    if last is not None and last == last and last > 0:   # last==last filtrerer nan
        return float(last), "last"
    if close is not None and close == close and close > 0:
        return float(close), "close"
    return None, "none"

def main():
    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID)
    ib.reqMarketDataType(MARKET_DATA_TYPE)

    contracts = [Stock(sym, EXCHANGE, CURRENCY) for sym in TICKERS]
    ib.qualifyContracts(*contracts)

    rows = []
    ts = datetime.now(timezone.utc).isoformat()

    for c in contracts:
        t = ib.reqMktData(c, snapshot=True)
        ib.sleep(1.2)  # lidt luft mellem requests

        price, src = pick_price(t)
        rows.append({
            "timestamp_utc": ts,
            "symbol": c.symbol,
            "price": price,
            "price_source": src,
            "last": t.last,
            "close": t.close,
            "bid": t.bid,
            "ask": t.ask,
        })

    ib.disconnect()

    df = pd.DataFrame(rows).sort_values("symbol")
    print(df[["symbol", "price", "price_source", "last", "close", "bid", "ask"]].to_string(index=False))

    out = "prices_log.csv"
    df.to_csv(out, mode="a", index=False, header=not pd.io.common.file_exists(out))
    print(f"\nSaved to {out}")

if __name__ == "__main__":
    main()
