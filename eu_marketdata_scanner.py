# eu_marketdata_scanner.py
# Scan EU/Europe stocks for IBKR API market-data permissions (historical bars)
# Output: OK / BLOCKED / UNKNOWN csv files + console summary
#
# Requirements:
#   pip install ib_insync
#
# Run:
#   python eu_marketdata_scanner.py
#
# Notes:
# - "OK" means we could qualify the contract AND retrieve historical bars.
# - "BLOCKED" typically means missing market data subscription / permissions (Error 162/10089).
# - "UNKNOWN" means contract not found (Error 200) or other issues.

from ib_insync import IB, Stock
import datetime as dt
import time
import csv
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple

# ===============================
# CONFIG (TWS paper)
# ===============================
HOST = "127.0.0.1"
PORT = 7497          # TWS paper default
CLIENT_ID = 7        # pick a number not used by your other script

# Historical request settings (safe & cheap)
DURATION_STR = "1 D"         # IMPORTANT: format must be "int SPACE unit" => "1 D"
BAR_SIZE = "5 mins"
WHAT_TO_SHOW = "TRADES"
USE_RTH = True

# Timing / throttles
QUALIFY_SLEEP = 0.15
HIST_SLEEP = 0.35
HIST_TIMEOUT_SEC = 12
MAX_RETRIES_PER_SYMBOL = 2

# If we see these error codes for a request, we classify as BLOCKED/UNKNOWN
BLOCKED_CODES = {162, 10089}    # no market data permissions / needs additional subscription for API
UNKNOWN_CODES = {200}           # no security definition found

# ===============================
# Universe (curated "mega" EU list)
# ===============================
# Format: symbol, currency, primaryExchange, label
#
# Primary exchange codes that commonly work in IB:
# - AEB  = Euronext Amsterdam
# - IBIS = XETRA (Germany)
# - SBF  = Euronext Paris
# - BME  = Madrid
# - BIT  = Milan
# - SWX  = Swiss Exchange (CHF)
# - LSE  = London (GBP)
# - STO  = Stockholm (often XSTO; but IB primaryExchange strings can vary)
# - CPH  = Copenhagen
# - OSE  = Oslo
# - HEX  = Helsinki
#
# IMPORTANT:
# - Some Nordic tickers are finicky (spaces, share classes). We include a few,
#   but expect some UNKNOWN until you refine symbols to IB's exact format.
UNIVERSE: List[Tuple[str, str, str, str]] = [
    # Netherlands (AEB)
    ("ASML", "EUR", "AEB", "ASML Holding"),
    ("ADYEN", "EUR", "AEB", "Adyen"),
    ("INGA", "EUR", "AEB", "ING Groep"),
    ("PHIA", "EUR", "AEB", "Philips"),
    ("HEIA", "EUR", "AEB", "Heineken"),

    # Germany (IBIS / XETRA)
    ("SAP",  "EUR", "IBIS", "SAP"),
    ("SIE",  "EUR", "IBIS", "Siemens"),
    ("BAS",  "EUR", "IBIS", "BASF"),
    ("VOW3", "EUR", "IBIS", "Volkswagen"),
    ("BMW",  "EUR", "IBIS", "BMW"),
    ("ALV",  "EUR", "IBIS", "Allianz"),
    ("DTE",  "EUR", "IBIS", "Deutsche Telekom"),
    ("MBG",  "EUR", "IBIS", "Mercedes-Benz"),

    # France (SBF / Euronext Paris)
    ("MC",   "EUR", "SBF", "LVMH"),
    ("OR",   "EUR", "SBF", "L'Oreal"),
    ("AIR",  "EUR", "SBF", "Airbus"),
    ("SU",   "EUR", "SBF", "Schneider Electric"),
    ("SAN",  "EUR", "SBF", "Sanofi"),
    ("BNP",  "EUR", "SBF", "BNP Paribas"),

    # Spain (BME / Madrid)
    ("IBE",  "EUR", "BME", "Iberdrola"),
    ("SAN",  "EUR", "BME", "Banco Santander"),
    ("BBVA", "EUR", "BME", "BBVA"),

    # Italy (BIT / Milan)
    ("ENI",  "EUR", "BIT", "ENI"),
    ("ISP",  "EUR", "BIT", "Intesa Sanpaolo"),
    ("UCG",  "EUR", "BIT", "UniCredit"),

    # Switzerland (SWX / CHF)
    ("NESN", "CHF", "SWX", "Nestle"),
    ("NOVN", "CHF", "SWX", "Novartis"),
    ("ROG",  "CHF", "SWX", "Roche"),
    ("UBSG", "CHF", "SWX", "UBS"),

    # UK (LSE / GBP)  (you may or may not have this subscription)
    ("ULVR", "GBP", "LSE", "Unilever"),
    ("HSBA", "GBP", "LSE", "HSBC"),
    ("BP",   "GBP", "LSE", "BP"),
    ("SHEL", "GBP", "LSE", "Shell"),
    ("AZN",  "GBP", "LSE", "AstraZeneca"),

    # Nordics (can be finicky — included as probes)
    # Denmark (CPH / DKK)
    ("DSV",     "DKK", "CPH", "DSV"),
    ("VWS",     "DKK", "CPH", "Vestas"),
    ("MAERSK B","DKK", "CPH", "Maersk B (often space)"),
    ("NOVO B",  "DKK", "CPH", "Novo Nordisk B (often space)"),

    # Sweden (try Stockholm) — symbols often "VOLV B", "ERIC B", etc.
    ("VOLV B",  "SEK", "SSE", "Volvo B (probe)"),
    ("ERIC B",  "SEK", "SSE", "Ericsson B (probe)"),
    ("ATCO A",  "SEK", "SSE", "Atlas Copco A (probe)"),

    # Norway (OSE / NOK)
    ("EQNR", "NOK", "OSE", "Equinor"),
    ("DNB",  "NOK", "OSE", "DNB"),

    # Finland (HEX / EUR)
    ("NOKIA", "EUR", "HEX", "Nokia"),
]

# ===============================
# Data structures
# ===============================

@dataclass
class ScanRow:
    symbol: str
    currency: str
    primaryExchange: str
    name: str
    status: str  # OK / BLOCKED / UNKNOWN / ERROR
    reason: str
    conId: Optional[int] = None
    localSymbol: Optional[str] = None
    exchange: Optional[str] = None
    tradingClass: Optional[str] = None


# ===============================
# IB Connection + error capture
# ===============================

class ErrorTracker:
    """
    Captures IB errors keyed by reqId, so we can classify the outcome
    of qualify/historical calls deterministically.
    """
    def __init__(self):
        self.by_reqid: Dict[int, List[Tuple[int, str]]] = {}

    def on_error(self, reqId: int, errorCode: int, errorString: str, contract):
        if reqId not in self.by_reqid:
            self.by_reqid[reqId] = []
        self.by_reqid[reqId].append((errorCode, errorString))

    def pop(self, reqId: int) -> List[Tuple[int, str]]:
        return self.by_reqid.pop(reqId, [])

    def peek(self, reqId: int) -> List[Tuple[int, str]]:
        return self.by_reqid.get(reqId, [])


def connect_ib() -> IB:
    ib = IB()
    for attempt in range(1, 6):
        try:
            print(f"Connecting to IBKR (TWS)... attempt {attempt}")
            ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=5)
            if ib.isConnected():
                print("Connected.")
                return ib
        except Exception as e:
            print("Connect failed:", e)
            time.sleep(2)
    raise RuntimeError("Could not connect to IBKR. Is TWS running + API enabled?")


# ===============================
# Core scanning logic
# ===============================

def make_contract(symbol: str, currency: str, primary_exch: str) -> Stock:
    # Use SMART routing, but force primaryExchange so IB finds correct listing.
    # We intentionally do NOT set exchange=primary_exch because in IB
    # exchange should usually remain SMART for best routing, and primaryExchange
    # helps resolve the listing.
    return Stock(symbol=symbol, exchange="SMART", currency=currency, primaryExchange=primary_exch)


def qualify_one(ib: IB, c: Stock) -> Optional[Stock]:
    # qualifyContracts modifies contract in-place (fills conId etc)
    try:
        q = ib.qualifyContracts(c)
        if not q:
            return None
        return q[0]
    except Exception:
        return None


def request_bars_with_timeout(ib: IB, contract: Stock, timeout_sec: int) -> List:
    """
    Request historical bars and wait up to timeout_sec.
    """
    # ib_insync is synchronous here, but can still hang on network/IB load.
    # We'll use time-limited polling by running it in a simple pattern:
    start = time.time()
    bars = None
    while True:
        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=DURATION_STR,
                barSizeSetting=BAR_SIZE,
                whatToShow=WHAT_TO_SHOW,
                useRTH=USE_RTH,
                formatDate=1,
                keepUpToDate=False
            )
            break
        except Exception:
            bars = []
            break

        if (time.time() - start) > timeout_sec:
            bars = []
            break

    return bars or []


def classify_from_errors(errors: List[Tuple[int, str]]) -> Tuple[str, str]:
    """
    Return (status, reason) given error list.
    """
    if not errors:
        return ("OK", "bars_ok")

    codes = {c for (c, _) in errors}

    if codes & BLOCKED_CODES:
        # pick the first matching code
        for c, msg in errors:
            if c in BLOCKED_CODES:
                return ("BLOCKED", f"err{c}:{msg[:160]}")
        return ("BLOCKED", "blocked")

    if codes & UNKNOWN_CODES:
        for c, msg in errors:
            if c in UNKNOWN_CODES:
                return ("UNKNOWN", f"err{c}:{msg[:160]}")
        return ("UNKNOWN", "unknown_contract")

    # other errors => ERROR
    c, msg = errors[0]
    return ("ERROR", f"err{c}:{msg[:160]}")


def scan_universe(ib: IB, universe: List[Tuple[str, str, str, str]]) -> List[ScanRow]:
    tracker = ErrorTracker()
    ib.errorEvent += tracker.on_error

    results: List[ScanRow] = []

    print(f"Scanning {len(universe)} EU/Nordic/UK symbols for historical data permissions...")
    print(f"Hist settings: duration={DURATION_STR}, barsize={BAR_SIZE}, RTH={USE_RTH}, what={WHAT_TO_SHOW}")
    print("-" * 80)

    for idx, (symbol, ccy, pex, name) in enumerate(universe, start=1):
        row = ScanRow(symbol=symbol, currency=ccy, primaryExchange=pex, name=name,
                      status="UNKNOWN", reason="not_scanned")

        print(f"[{idx:03d}/{len(universe)}] {symbol} {ccy} (primary={pex}) ...", end=" ")

        contract = make_contract(symbol, ccy, pex)

        qualified = None
        for attempt in range(1, MAX_RETRIES_PER_SYMBOL + 1):
            qualified = qualify_one(ib, contract)
            if qualified and getattr(qualified, "conId", 0):
                break
            time.sleep(0.15)

        if not qualified:
            row.status = "UNKNOWN"
            row.reason = "qualify_failed"
            print("UNKNOWN (qualify_failed)")
            results.append(row)
            time.sleep(QUALIFY_SLEEP)
            continue

        # Fill details
        row.conId = getattr(qualified, "conId", None)
        row.localSymbol = getattr(qualified, "localSymbol", None)
        row.exchange = getattr(qualified, "exchange", None)
        row.tradingClass = getattr(qualified, "tradingClass", None)

        time.sleep(QUALIFY_SLEEP)

        # Request bars. Errors for historical data often come with reqId,
        # but ib_insync doesn't expose reqId directly here.
        # We'll still classify by whether bars came back + if any recent error fired.
        # To make this deterministic, we clear "most recent errors" before request and
        # then inspect tracker for ANY new errors within a short window.
        before_snapshot = dict(tracker.by_reqid)

        bars = request_bars_with_timeout(ib, qualified, HIST_TIMEOUT_SEC)
        time.sleep(0.25)  # allow error events to arrive

        # Collect new errors since snapshot
        new_errors: List[Tuple[int, str]] = []
        for reqId, errs in tracker.by_reqid.items():
            if reqId not in before_snapshot:
                new_errors.extend(errs)
            else:
                # reqId existed before; include only newly appended errors
                old = before_snapshot[reqId]
                if len(errs) > len(old):
                    new_errors.extend(errs[len(old):])

        # If bars exist, we accept OK even if there are harmless warnings.
        if bars:
            row.status = "OK"
            row.reason = f"bars_ok(n={len(bars)})"
            print(f"OK (bars={len(bars)})")
        else:
            status, reason = classify_from_errors(new_errors)
            # If we got no bars and no useful error, treat as ERROR timeout/no_data
            if status == "OK":
                status = "ERROR"
                reason = "no_bars_no_error(timeout_or_no_data)"
            row.status = status
            row.reason = reason
            print(f"{row.status} ({row.reason})")

        results.append(row)
        time.sleep(HIST_SLEEP)

    ib.errorEvent -= tracker.on_error
    return results


def write_csv(path: str, rows: List[ScanRow]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "symbol", "currency", "primaryExchange", "name",
            "status", "reason",
            "conId", "localSymbol", "exchange", "tradingClass"
        ])
        for r in rows:
            w.writerow([
                r.symbol, r.currency, r.primaryExchange, r.name,
                r.status, r.reason,
                r.conId or "", r.localSymbol or "", r.exchange or "", r.tradingClass or ""
            ])


def main():
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    ib = connect_ib()

    # Print account (optional)
    try:
        acc = ib.managedAccounts()
        if acc:
            print("Managed account:", acc[0])
    except Exception:
        pass

    print("\nEU MARKETDATA SCANNER STARTED\n")
    results = scan_universe(ib, UNIVERSE)

    ok = [r for r in results if r.status == "OK"]
    blocked = [r for r in results if r.status == "BLOCKED"]
    unknown = [r for r in results if r.status == "UNKNOWN"]
    error = [r for r in results if r.status == "ERROR"]

    ok_path = f"eu_scan_ok_{ts}.csv"
    blocked_path = f"eu_scan_blocked_{ts}.csv"
    unknown_path = f"eu_scan_unknown_{ts}.csv"
    all_path = f"eu_scan_all_{ts}.csv"

    write_csv(ok_path, ok)
    write_csv(blocked_path, blocked)
    write_csv(unknown_path, unknown)
    write_csv(all_path, results)

    print("\n" + "=" * 80)
    print("SCAN SUMMARY")
    print(f"OK      : {len(ok)}  -> {ok_path}")
    print(f"BLOCKED : {len(blocked)}  -> {blocked_path}")
    print(f"UNKNOWN : {len(unknown)}  -> {unknown_path}")
    print(f"ERROR   : {len(error)}")
    print(f"ALL     : {len(results)}  -> {all_path}")
    print("=" * 80)

    if ok:
        print("\nTop OK examples:")
        for r in ok[:10]:
            print(f"  {r.symbol:10s} {r.currency:3s} primary={r.primaryExchange:5s} conId={r.conId} local={r.localSymbol} exch={r.exchange}")

    ib.disconnect()
    print("\nDONE")


if __name__ == "__main__":
    main()
