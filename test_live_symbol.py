from ib_insync import *
ib=IB(); ib.connect("127.0.0.1",7497,clientId=2)
c=Stock("AAPL","SMART","USD"); ib.qualifyContracts(c)
ib.reqMarketDataType(1)
t=ib.reqMktData(c,"",snapshot=True,regulatorySnapshot=False)
ib.sleep(1.5)
print("marketDataType:",t.marketDataType,"last:",t.last,"bid:",t.bid,"ask:",t.ask)
bars=ib.reqHistoricalData(c,"","2 D","5 mins","TRADES",useRTH=True); print("bars:",len(bars))
ib.disconnect()
