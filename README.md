# InsiderEdge

**Real-time SEC Form 4 insider trading tracker for day traders.**

InsiderEdge is a SaaS tool that monitors insider buying and selling activity across U.S. public companies by pulling live data directly from SEC EDGAR. Day traders use it to spot high-conviction moves by CEOs, directors, and major shareholders before the broader market reacts.

---

## What It Does

- Fetches the latest Form 4 filings from SEC EDGAR every time the pipeline runs
- Parses each filing to extract the insider's name, role, transaction type, share count, price, and dollar amount
- Classifies every trade as a **Buy**, **Sell**, **Award**, **Exercise**, or other action
- Displays everything in a live, filterable dashboard with sortable columns

## Features

- **Live Data** — pulls directly from the SEC EDGAR full-text search index
- **Two Date Columns** — shows both the Transaction Date (when the trade happened) and the Filed Date (when it was reported to the SEC)
- **Buy/Sell Filtering** — one-click filters to isolate buys, sells, or awards
- **Search** — instantly filter by company name, ticker, or insider name
- **Trade Sizing** — visual bar indicators scaled to the largest trade in the dataset
- **Net Sentiment** — bullish/bearish signal based on total buy vs. sell volume

## How It Works

```
sec_pipeline.py   →   filings.json   →   dashboard.html
```

1. **`sec_pipeline.py`** — fetches Form 4 filings from SEC EDGAR, parses the XML, and writes results to `filings.json`
2. **`filings.json`** — structured data file with the 50 most recent filings
3. **`dashboard.html`** — standalone frontend dashboard that reads `filings.json` and renders the trading table

## Getting Started

**Run the pipeline:**
```bash
pip install requests   # only dependency (optional — pipeline uses stdlib urllib)
python sec_pipeline.py
```

**View the dashboard:**
```bash
python -m http.server 8080
```
Then open [http://localhost:8080/dashboard.html](http://localhost:8080/dashboard.html).

> The dashboard must be served over HTTP (not opened as a local file) due to browser security restrictions on `fetch()`.

## Data Source

All data comes from the [SEC EDGAR Full-Text Search Index](https://efts.sec.gov). Form 4 filings are public record and updated throughout each trading day.

---

*InsiderEdge is for informational purposes only. Not financial advice.*
