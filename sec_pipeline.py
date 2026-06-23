"""
InsiderEdge â€” SEC Form 4 Insider Trading Pipeline
Fetches the latest 50 Form 4 filings from SEC EDGAR, parses transaction
details (buy/sell, dollar amount, ticker, etc.), and saves results to
filings.json.
"""

import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional

import urllib.request
import urllib.error

# ── Configuration ─────────────────────────────────────────────────────────────
HEADERS = {"User-Agent": "insideredge research@insideredge.com"}

# NOTE: The EFTS API ignores sort/order URL params and returns results by
# relevance score instead. We work around this by using a rolling date window
# (last LOOKBACK_DAYS days) and then sorting the returned hits by file_date
# descending in Python before processing. This guarantees we always see the
# most recently filed Form 4s regardless of when the script runs.
LOOKBACK_DAYS = 30
MAX_FILINGS   = 50

def _build_search_url() -> str:
    """Build the EDGAR EFTS search URL with a dynamic rolling date window."""
    today = datetime.now(timezone.utc)
    start = today - timedelta(days=LOOKBACK_DAYS)
    return (
        "https://efts.sec.gov/LATEST/search-index"
        "?q=%22form+4%22&forms=4"
        f"&dateRange=custom&startdt={start.strftime('%Y-%m-%d')}&enddt={today.strftime('%Y-%m-%d')}"
        "&_source=adsh,display_names,file_date,period_ending,ciks"
        "&from=0&size=200"          # fetch extra so we can sort & trim to MAX_FILINGS
    )

EDGAR_BASE = "https://www.sec.gov"
TICKER_API = "https://www.sec.gov/files/company_tickers.json"
OUTPUT_FILE = "filings.json"
RATE_LIMIT_SLEEP = 0.12          # ~8 req/sec  (SEC allows 10/sec)


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def fetch(url: str, retries: int = 3, backoff: float = 2.0) -> Optional[bytes]:
    """HTTP GET with retries and exponential back-off."""
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = backoff ** (attempt + 1)
                print(f"  [rate-limited] sleeping {wait:.1f}s â€¦", flush=True)
                time.sleep(wait)
            else:
                print(f"  [HTTP {e.code}] {url}", flush=True)
                return None
        except Exception as e:
            print(f"  [error] {e}  url={url}", flush=True)
            time.sleep(backoff)
    return None


def load_ticker_map() -> dict[str, str]:
    """Download SEC company â†’ ticker mapping (CIK â†’ ticker)."""
    print("ðŸ“¥  Loading SEC ticker map â€¦", flush=True)
    raw = fetch(TICKER_API)
    if not raw:
        return {}
    data = json.loads(raw)
    # data keys are str integers; value has 'cik_str', 'ticker', 'title'
    return {
        str(v["cik_str"]).lstrip("0"): v["ticker"].upper()
        for v in data.values()
    }


def parse_form4_xml(xml_bytes: bytes) -> dict:
    """
    Parse a Form 4 XML document.
    Returns a dict with: insider_name, relationship, transactions[]
    Each transaction: {type, shares, price_per_share, dollar_amount,
                       transaction_date, acquired_disposed}
    """
    result = {
        "insider_name": "Unknown",
        "relationship": "Unknown",
        "transactions": [],
    }
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return result

    # â”€â”€ Insider name â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    rp = root.find(".//reportingOwner")
    if rp is not None:
        name_el = rp.find(".//rptOwnerName")
        if name_el is not None and name_el.text:
            result["insider_name"] = name_el.text.strip().title()

        # Relationship
        rel_el = rp.find(".//reportingOwnerRelationship")
        if rel_el is not None:
            parts = []
            for tag in ("isDirector", "isOfficer", "isTenPercentOwner", "isOther"):
                el = rel_el.find(tag)
                if el is not None and el.text and el.text.strip() == "1":
                    parts.append(tag[2:])          # strip leading "is"
            title_el = rel_el.find("officerTitle")
            if title_el is not None and title_el.text:
                parts.append(title_el.text.strip())
            if parts:
                result["relationship"] = ", ".join(parts)

    # â”€â”€ Non-derivative transactions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    for txn in root.findall(".//nonDerivativeTransaction"):
        _parse_txn(txn, result["transactions"], derivative=False)

    # â”€â”€ Derivative transactions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    for txn in root.findall(".//derivativeTransaction"):
        _parse_txn(txn, result["transactions"], derivative=True)

    return result


def _safe_float(el, tag: str) -> Optional[float]:
    child = el.find(tag)
    if child is not None and child.text:
        try:
            return float(child.text.strip().replace(",", ""))
        except ValueError:
            pass
    return None


def _parse_txn(txn_el, out_list: list, derivative: bool):
    """Extract fields from a single <*Transaction> element."""
    date_el = txn_el.find(".//transactionDate/value")
    date_str = date_el.text.strip() if (date_el is not None and date_el.text) else "N/A"

    # Transaction code  (P=Purchase, S=Sale, A=Award, etc.)
    code_el = txn_el.find(".//transactionCoding/transactionCode")
    code = code_el.text.strip() if (code_el is not None and code_el.text) else "?"

    # Shares / units
    shares = _safe_float(txn_el, ".//transactionAmounts/transactionShares/value")
    if shares is None:
        shares = _safe_float(txn_el, ".//transactionAmounts/transactionTotalValue/value")

    # Price per share
    price = _safe_float(txn_el, ".//transactionAmounts/transactionPricePerShare/value")

    # Acquired (A) or Disposed (D)
    acq_disp_el = txn_el.find(".//transactionAmounts/transactionAcquiredDisposedCode/value")
    acq_disp = acq_disp_el.text.strip() if (acq_disp_el is not None and acq_disp_el.text) else "?"

    # Dollar amount
    dollar_amount = None
    if shares is not None and price is not None:
        dollar_amount = shares * price

    # Translate code to action label
    action_map = {
        "P": "BUY",
        "S": "SELL",
        "A": "AWARD",
        "M": "EXERCISE",
        "C": "CONVERT",
        "G": "GIFT",
        "F": "TAX WITHHOLD",
        "D": "DISPOSE",
        "I": "DISCRETIONARY",
        "J": "OTHER",
    }
    action = action_map.get(code, f"OTHER ({code})")

    out_list.append({
        "transaction_date": date_str,
        "transaction_code": code,
        "action": action,
        "acquired_or_disposed": acq_disp,
        "shares": shares,
        "price_per_share": price,
        "dollar_amount": dollar_amount,
        "is_derivative": derivative,
    })


def fmt_money(amount: Optional[float]) -> str:
    if amount is None:
        return "N/A"
    if amount >= 1_000_000:
        return f"${amount/1_000_000:.2f}M"
    if amount >= 1_000:
        return f"${amount/1_000:.1f}K"
    return f"${amount:.2f}"


def summarize_transactions(transactions: list) -> tuple[str, Optional[float]]:
    """
    Roll up a list of transactions into a dominant action label and total $.
    Returns (action_label, total_dollar_amount).
    """
    if not transactions:
        return ("N/A", None)

    buys = [t for t in transactions if t["transaction_code"] == "P"]
    sells = [t for t in transactions if t["transaction_code"] == "S"]

    def total(lst):
        amounts = [t["dollar_amount"] for t in lst if t["dollar_amount"] is not None]
        return sum(amounts) if amounts else None

    buy_total = total(buys)
    sell_total = total(sells)

    if buys and not sells:
        return ("BUY", buy_total)
    if sells and not buys:
        return ("SELL", sell_total)
    if buys and sells:
        # Mixed â€” pick dominant by dollar value
        bt = buy_total or 0
        st = sell_total or 0
        if bt >= st:
            return (f"BUY (mixed, sell ${st:,.0f})", buy_total)
        return (f"SELL (mixed, buy ${bt:,.0f})", sell_total)

    # Awards, exercises, etc.
    first = transactions[0]
    return (first["action"], first["dollar_amount"])


# â”€â”€ Main pipeline â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def main():
    print("=" * 65)
    print("  InsiderEdge  â€¢  SEC Form 4 Pipeline")
    print("=" * 65)

    # 1. Load ticker map
    ticker_map = load_ticker_map()
    time.sleep(RATE_LIMIT_SLEEP)

    # 2. Fetch search results using a dynamic rolling date window
    search_url = _build_search_url()
    print(f"\n[**]  Fetching latest Form 4 filings (rolling {LOOKBACK_DAYS}-day window) ...", flush=True)
    raw = fetch(search_url)
    if not raw:
        print("[!!]  Failed to fetch search results. Aborting.")
        sys.exit(1)

    data = json.loads(raw)
    hits = data.get("hits", {}).get("hits", [])
    total_available = data.get("hits", {}).get("total", {}).get("value", 0)

    # Sort by file_date descending in Python (EFTS ignores sort/order URL params)
    hits.sort(key=lambda h: h["_source"].get("file_date", ""), reverse=True)
    hits = hits[:MAX_FILINGS]

    newest = hits[0]["_source"]["file_date"] if hits else "?"
    oldest = hits[-1]["_source"]["file_date"] if hits else "?"
    print(f"    In window: {total_available:,} filings  |  Showing top {len(hits)} most recent  ({oldest} -> {newest}) ...\n")

    filings_output = []

    for i, hit in enumerate(hits, start=1):
        src = hit.get("_source", {})
        adsh = src.get("adsh", "")           # accession number  e.g. 0001628280-26-041829
        file_date = src.get("file_date", "N/A")
        period_ending = src.get("period_ending", "N/A")
        display_names: list[str] = src.get("display_names", [])
        ciks: list[str] = src.get("ciks", [])

        # â”€â”€ Parse display_names to find company vs. insider â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Names are like "Santana Rafael  (CIK 0001767799)" or
        #                 "WESTINGHOUSE AIR BRAKE TECHNOLOGIES CORP  (CIK 0000943452)"
        company_name = "Unknown Company"
        insider_name_meta = "Unknown Insider"

        # The last display_name with a matching CIK to the issuer is the company.
        # We'll heuristically separate by checking which CIK corresponds to a ticker.
        parsed_entries = []
        for dn in display_names:
            m = re.match(r"^(.+?)\s+\(CIK\s+([\d]+)\)$", dn.strip())
            if m:
                name_part = m.group(1).strip()
                cik_part = m.group(2).lstrip("0")
                ticker = ticker_map.get(cik_part, "")
                parsed_entries.append({"name": name_part, "cik": cik_part, "ticker": ticker})

        companies = [e for e in parsed_entries if e["ticker"]]
        insiders = [e for e in parsed_entries if not e["ticker"]]

        if companies:
            company_name = companies[0]["name"].title()
            ticker_symbol = companies[0]["ticker"]
        else:
            # Fallback: last entry is usually the issuer
            if parsed_entries:
                company_name = parsed_entries[-1]["name"].title()
                ticker_symbol = "N/A"
            else:
                ticker_symbol = "N/A"

        if insiders:
            insider_name_meta = insiders[0]["name"].title()
        elif parsed_entries:
            insider_name_meta = parsed_entries[0]["name"].title()

        # â”€â”€ Fetch the actual XML filing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Build the filing index URL
        adsh_path = adsh.replace("-", "")          # remove dashes for URL
        # Accession number parts: XXXXXXXXXX-YY-ZZZZZZ
        # URL: /Archives/edgar/data/<CIK>/<ADSH_nodashes>/<ADSH>.txt
        # We use the company CIK (last entry usually)
        company_cik = companies[0]["cik"] if companies else (ciks[-1].lstrip("0") if ciks else "")
        filing_index_url = (
            f"{EDGAR_BASE}/cgi-bin/browse-edgar"
            f"?action=getcompany&CIK={company_cik}"
            f"&type=4&dateb=&owner=include&count=40"
        )
        # Directly fetch the XML document linked in the _id field
        doc_id = hit.get("_id", "")   # e.g. "0001628280-26-041829:wk-form4_xxx.xml"
        xml_filename = doc_id.split(":")[-1] if ":" in doc_id else ""
        xml_url = (
            f"{EDGAR_BASE}/Archives/edgar/data/{company_cik}"
            f"/{adsh_path}/{xml_filename}"
        )

        print(f"[{i:02d}/{MAX_FILINGS}] {company_name} ({ticker_symbol})", flush=True)
        print(f"       Filing: {adsh}  |  Filed: {file_date}", flush=True)

        time.sleep(RATE_LIMIT_SLEEP)
        xml_bytes = fetch(xml_url)

        parsed = {"insider_name": insider_name_meta, "relationship": "N/A", "transactions": []}
        if xml_bytes:
            try:
                parsed = parse_form4_xml(xml_bytes)
            except Exception as e:
                print(f"       [warn] XML parse error: {e}", flush=True)

        # Use XML insider name if better
        if parsed["insider_name"] != "Unknown":
            insider_name_meta = parsed["insider_name"]

        action_label, total_dollars = summarize_transactions(parsed["transactions"])

        print(f"       Insider: {insider_name_meta}  ({parsed['relationship']})", flush=True)
        print(f"       Action: {action_label}  |  Amount: {fmt_money(total_dollars)}", flush=True)
        print()

        filings_output.append({
            "rank": i,
            "company_name": company_name,
            "ticker": ticker_symbol,
            "insider_name": insider_name_meta,
            "relationship": parsed["relationship"],
            "action": action_label,
            "dollar_amount": total_dollars,
            "dollar_amount_fmt": fmt_money(total_dollars),
            "file_date": file_date,
            "period_ending": period_ending,
            "accession_number": adsh,
            "sec_filing_url": f"https://www.sec.gov/Archives/edgar/data/{company_cik}/{adsh_path}/{xml_filename}",
            "transactions": parsed["transactions"],
        })

    # â”€â”€ Pretty print summary table â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    print("\n" + "=" * 65)
    print("  SUMMARY TABLE")
    print("=" * 65)
    hdr = f"{'#':>3}  {'Company':<28} {'Tick':<6} {'Insider':<22} {'Action':<8} {'Amount':>12}  {'Date'}"
    print(hdr)
    print("-" * 95)
    for f in filings_output:
        company_disp = f["company_name"][:27]
        insider_disp = f["insider_name"][:21]
        action_disp  = f["action"].split()[0][:8]   # first word only for table
        print(
            f"{f['rank']:>3}  {company_disp:<28} {f['ticker']:<6} "
            f"{insider_disp:<22} {action_disp:<8} {f['dollar_amount_fmt']:>12}  {f['file_date']}"
        )

    # â”€â”€ Save to JSON â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    output_path = OUTPUT_FILE
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "total_filings_in_range": total_available,
                "filings_retrieved": len(filings_output),
                "filings": filings_output,
            },
            fh,
            indent=2,
            default=str,
        )

    print(f"\nâœ…  Saved {len(filings_output)} filings â†’ {output_path}")
    print("=" * 65)


if __name__ == "__main__":
    main()

