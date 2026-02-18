from ib_insync import IB, Stock, util
import pandas as pd

TICKERS = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA"]
HOST, PORT = "127.0.0.1", 7497

def get_signal(df: pd.DataFrame):
    df = df.copy()
    df["EMA20"] = df["close"].ewm(span=20).mean()
    df["VolAvg20"] = df["volume"].rolling(20).mean()

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    cond_ema = latest.close > latest.EMA20
    cond_momo = latest.close > prev.close
    cond_vol = latest.volume > latest.VolAvg20

    signal = bool(cond_ema and cond_momo and cond_vol)
    return signal, float(latest.close), float(latest.EMA20), float(latest.volume), float(latest.VolAvg20)

def main():
    ib = IB()
    ib.connect(HOST, PORT, clientId=40)

    results = []
    for sym in TICKERS:
        c = Stock(sym, "SMART", "USD")
        ib.qualifyContracts(c)

        bars = ib.reqHistoricalData(
            c,
            endDateTime="",
            durationStr="2 D",
            barSizeSetting="5 mins",
            whatToShow="TRADES",
            useRTH=True
        )
        df = util.df(bars)

        if len(df) < 25:
            results.append((sym, "NO_DATA", None, None, None, None))
            continue

        signal, close, ema20, vol, volavg = get_signal(df)
        results.append((sym, signal, close, ema20, vol, volavg))

    ib.disconnect()

    out = pd.DataFrame(results, columns=["symbol", "buy_signal", "close", "ema20", "volume", "volAvg20"])
    print(out.to_string(index=False))

if __name__ == "__main__":
    main()
