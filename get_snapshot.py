from ib_insync import IB, Stock

ib = IB()
ib.connect("127.0.0.1", 7497, clientId=3)

ib.reqMarketDataType(3)  # 3 = delayed

contract = Stock("SPY", "ARCA", "USD")
ib.qualifyContracts(contract)

# Snapshot request (korrekt syntaks)
ticker = ib.reqMktData(contract, snapshot=True)
ib.sleep(2)

print("SPY last:", ticker.last)
print("SPY close:", ticker.close)
print("SPY bid:", ticker.bid)
print("SPY ask:", ticker.ask)

ib.disconnect()
