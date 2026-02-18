from ib_insync import IB, Stock

ib = IB()
ib.connect("127.0.0.1", 7497, clientId=2)

# 1=live, 2=frozen, 3=delayed, 4=delayed frozen
ib.reqMarketDataType(3)

contract = Stock("SPY", "ARCA", "USD")
ib.qualifyContracts(contract)

ticker = ib.reqMktData(contract, "", False, False)
ib.sleep(2)

print("SPY last:", ticker.last)
print("SPY bid:", ticker.bid)
print("SPY ask:", ticker.ask)

ib.disconnect()
