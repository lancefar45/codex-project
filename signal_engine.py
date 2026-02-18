from ib_insync import IB, Stock, util
import pandas as pd

ib = IB()
ib.connect("127.0.0.1", 7497, clientId=30)

contract = Stock("SPY", "SMART", "USD")
ib.qualifyContracts(contract)

bars = ib.reqHistoricalData(
    contract,
    endDateTime='',
    durationStr='2 D',
    barSizeSetting='5 mins',
    whatToShow='TRADES',
    useRTH=True
)

df = util.df(bars)

# Beregn EMA20
df["EMA20"] = df["close"].ewm(span=20).mean()

# Beregn volumen-gennemsnit
df["VolAvg20"] = df["volume"].rolling(20).mean()

latest = df.iloc[-1]
previous = df.iloc[-2]

print("Latest Close:", latest.close)
print("EMA20:", latest.EMA20)
print("Volume:", latest.volume)
print("Volume Avg20:", latest.VolAvg20)

signal = False

if (
    latest.close > latest.EMA20 and
    latest.close > previous.close and
    latest.volume > latest.VolAvg20
):
    signal = True

print("\nBUY SIGNAL:", signal)

ib.disconnect()
