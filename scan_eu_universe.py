from ib_insync import *
import time
import datetime
from collections import defaultdict

# ===============================
# CONFIG (samme som din bot)
# ===============================
HOST = "127.0.0.1"
PORT = 7497
CLIENT_ID = 2  # brug et andet id end din bot (fx 2)

# Hvor “hårdt” vi tester hver aktie:
DURATION = "1 D"          # historik
BAR_SIZE = "5 mins"
USE_RTH = True            # kun regular trading hours
WHAT_TO_SHOW = "TRADES"

# Kandidat-univers (start med kendte large caps)
# NB: Symboler er "lokale" short symbols som IB ofte forstår sammen med exchange + currency.
EU_CANDIDATES = [
    # Germany (XETRA)
    ("SAP",  "IBIS", "EUR"),
    ("SIE",  "IBIS", "EUR"),
    ("BAS",  "IBIS", "EUR"),
    ("VOW3", "IBIS", "EUR"),
    ("BMW",  "IBIS", "EUR"),
    ("MBG",  "IBIS", "EUR"),

    # Netherlands (Euronext Amsterdam)
    ("ASML", "AEB",  "EUR"),
    ("ADYEN","AEB",  "EUR"),

    # France (Euronext Paris)
    ("MC",   "SBF",  "EUR"),
    ("OR",   "SBF",  "EUR"),
    ("GLE",  "SBF",  "EUR"),

    # Sweden (Stockholm) - ofte SFB + SEK
    ("VOLV B","SFB", "SEK"),
    ("ERIC B","SFB", "SEK"),
    ("INVE B","SFB", "SEK"),
    ("ATCO A","SFB", "SEK"),

    # Norway (Oslo)
    ("EQNR", "OSE",  "NOK"),
    ("DNB",  "OSE",  "NOK"),

    # Denmark (Copenhagen) - IB bruger ofte "CPH" eller "CSE" afhængigt af routing.
    # Vi prøver begge via en fallback-liste nedenfor.
    ("NOVO B","CPH", "DKK"),
    ("NOVO B","CSE", "DKK"),
    ("DSV",   "CPH", "DKK"),
    ("DSV",   "CSE", "DKK"),
    ("VWS",   "CPH", "DKK"),
    ("VWS",   "CSE", "DKK"),
    ("MAERSK B","CPH","DKK"),
    ("MAERSK B","CSE","DKK"),
]

# Hvis du vil udvide: tilføj flere tuples (symbol, exchange, currency)

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
        except Exception as e:
            print("Connect error:", e)
            time.sleep(2)
    raise RuntimeError("Could not connect to IBKR")

# ===============================
# TEST HELPERS
# ===============================
def try_qualify_and_bars(ib: IB, symbol: str, exch: str, ccy: str):
    """
    Return: (ok:bool, reason:str, contract_or_none)
    """
    c = Stock(symbol, exch, ccy)

    # 1) qualify
    try:
        qualified = ib.qualifyContracts(c)
        if not qualified:
            return (False, "qualify_failed", None)
        c = qualified[0]
    except Exception as e:
        return (False, f"qualify_exception:{e}", None)

    # 2) bars test (historical)
    try:
        bars = ib.reqHistoricalData(
            c,
            endDateTime="",
            durationStr=DURATION,
            barSizeSetting=BAR_SIZE,
            whatToShow=WHAT_TO_SHOW,
            useRTH=USE_RTH,
            formatDate=1
        )
    except Exception as e:
        return (False, f"hist_exception:{e}", c)

    if not bars:
        # Tomme bars kan være: ingen permissions, market closed med ingen trades, eller et andet issue
        # Men i praksis ser du ofte en error-linje i TWS log/console hvis det er permissions.
        return (False, "no_bars", c)

    # Hvis vi fik data, er det et stærkt tegn på at kontrakten virker i dit setup
    return (True, "ok", c)

def main():
    ib = connect_ib()

    ok_list = []
    fail_counts = defaultdict(int)

    print("\nEU PRE-FLIGHT SCAN STARTED\n")

    for (sym, exch, ccy) in EU_CANDIDATES:
        tag = f"{sym} @ {exch} {ccy}"
        print(f"Testing {tag} ...", end=" ")

        ok, reason, contract = try_qualify_and_bars(ib, sym, exch, ccy)

        if ok:
            # Gem “det vi faktisk bør bruge” (conId + primExch osv.)
            ok_list.append({
                "symbol": contract.symbol,
                "exchange": contract.exchange,
                "primaryExchange": getattr(contract, "primaryExchange", ""),
                "currency": contract.currency,
                "localSymbol": getattr(contract, "localSymbol", ""),
                "tradingClass": getattr(contract, "tradingClass", ""),
                "conId": contract.conId
            })
            print("✅ OK")
        else:
            fail_counts[reason] += 1
            print(f"❌ {reason}")

        time.sleep(0.25)  # vær sød ved API’et

    print("\n==============================")
    print("RESULT")
    print("==============================")
    print(f"OK contracts: {len(ok_list)}")
    for row in ok_list:
        print(
            f"- {row['symbol']} {row['currency']} "
            f"exch={row['exchange']} prim={row['primaryExchange']} "
            f"local={row['localSymbol']} conId={row['conId']}"
        )

    print("\nFailure reasons:")
    for k, v in sorted(fail_counts.items(), key=lambda x: -x[1]):
        print(f"- {k}: {v}")

    # Dump til fil så vi kan copy/paste direkte ind i din bot senere
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = f"eu_scan_ok_{ts}.txt"
    with open(out, "w", encoding="utf-8") as f:
        for row in ok_list:
            f.write(
                f"{row['symbol']},{row['exchange']},{row['primaryExchange']},"
                f"{row['currency']},{row['localSymbol']},{row['tradingClass']},{row['conId']}\n"
            )
    print(f"\nSaved OK list to: {out}")

    ib.disconnect()

if __name__ == "__main__":
    main()
