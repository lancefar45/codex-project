from ib_insync import IB

ib = IB()

try:
    ib.connect("127.0.0.1", 7497, clientId=1)
    print("CONNECTED:", ib.isConnected())
    print("ACCOUNTS:", ib.managedAccounts())
except Exception as e:
    print("ERROR:", e)
finally:
    ib.disconnect()
    print("DONE")
