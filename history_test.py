from ib_insync import IB, Stock, util
import pandas as pd

ib = IB()
ib.connect("127.0.0.1", 7497, clientId=20)

contract = Stock("SPY", "SMART", "USD")
ib.qualifyContracts(contract)

bars = ib.reqHistoricalData(
    contract,
    endDateTime='',
    durationStr='1 D',
    barSizeSetting='5 mins',
    whatToShow='TRADES',
    useRTH=True
)

df = util.df(bars)
print(df.tail())

ib.disconnect()
