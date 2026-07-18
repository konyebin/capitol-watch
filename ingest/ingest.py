#!/usr/bin/env python3
"""
Capitol Watch — congressional trade ingester.

Trade source : official U.S. Senate eFD (Periodic Transaction Reports).
Member info  : public congress-legislators roster (party / state).
Sector info  : Finnhub company profiles (optional — set FINNHUB_API_KEY), so real
               stocks get a real sector and can be conflict-flagged. ETFs/funds and
               unknown tickers stay "Other".
Sink         : Supabase table public.congress_trades (upsert on external_id).

No third-party dependencies. No paid data feed required.

  python ingest.py --dry-run                              # scrape -> out_trades.csv, no DB
  FINNHUB_API_KEY=... python ingest.py --dry-run          # + real sectors
  SUPABASE_SERVICE_KEY=sb_secret_... FINNHUB_API_KEY=... python ingest.py   # write to DB

Keys live in env vars / GitHub Secrets — never in the browser app or committed code.
The Finnhub *free* tier cannot read congressional-trading (premium); it is used here
only for company-profile sectors. To add the House or use Finnhub trade data directly,
upgrade the plan and add a fetch_* adapter returning the same row dicts.
"""
import argparse, csv, html, io, json, os, re, sys, time, zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from urllib import request, parse

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://bywwvjljyhfpxpaestta.supabase.co")
TABLE = "congress_trades"
EFD = "https://efdsearch.senate.gov"
HOUSE = "https://disclosures-clerk.house.gov"
ROSTER_URL = "https://unitedstates.github.io/congress-legislators/legislators-current.json"
FINNHUB_KEY = (os.environ.get("FINNHUB_API_KEY") or "").strip()
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sector_cache.json")
UA = "CapitolWatch-Scraper/1.0 (Python)"

# Fast offline path for common tickers: ticker -> (company, sector).
SECTORS = {
    "LMT": ("Lockheed Martin", "Defense"), "RTX": ("RTX Corporation", "Defense"),
    "NOC": ("Northrop Grumman", "Defense"), "GD": ("General Dynamics", "Defense"),
    "BA": ("Boeing", "Defense"),
    "AAPL": ("Apple", "Technology"), "MSFT": ("Microsoft", "Technology"),
    "NVDA": ("NVIDIA", "Technology"), "GOOGL": ("Alphabet", "Technology"),
    "GOOG": ("Alphabet", "Technology"), "META": ("Meta Platforms", "Technology"),
    "AMZN": ("Amazon", "Technology"), "AVGO": ("Broadcom", "Technology"),
    "AMD": ("Advanced Micro Devices", "Technology"), "PLTR": ("Palantir", "Technology"),
    "JPM": ("JPMorgan Chase", "Financials"), "GS": ("Goldman Sachs", "Financials"),
    "BAC": ("Bank of America", "Financials"), "WFC": ("Wells Fargo", "Financials"),
    "MS": ("Morgan Stanley", "Financials"), "V": ("Visa", "Financials"),
    "PFE": ("Pfizer", "Healthcare"), "JNJ": ("Johnson & Johnson", "Healthcare"),
    "LLY": ("Eli Lilly", "Healthcare"), "UNH": ("UnitedHealth Group", "Healthcare"),
    "MRNA": ("Moderna", "Healthcare"), "ABBV": ("AbbVie", "Healthcare"),
    "XOM": ("ExxonMobil", "Energy"), "CVX": ("Chevron", "Energy"),
    "COP": ("ConocoPhillips", "Energy"), "OXY": ("Occidental Petroleum", "Energy"),
    "T": ("AT&T", "Telecom"), "VZ": ("Verizon", "Telecom"), "TMUS": ("T-Mobile US", "Telecom"),
    "NKE": ("Nike", "Consumer"), "DIS": ("Walt Disney", "Consumer"),
    "SBUX": ("Starbucks", "Consumer"), "TSLA": ("Tesla", "Consumer"),
}

# Finnhub "finnhubIndustry" -> the app's sector buckets.
INDUSTRY_MAP = {
    "aerospace & defense": "Defense",
    "technology": "Technology", "semiconductors": "Technology",
    "communications": "Technology", "electronic equipment, instruments & components": "Technology",
    "banking": "Financials", "financial services": "Financials", "insurance": "Financials",
    "diversified financials": "Financials",
    "pharmaceuticals": "Healthcare", "biotechnology": "Healthcare", "health care": "Healthcare",
    "healthcare": "Healthcare", "life sciences tools & services": "Healthcare",
    "medical equipment & devices": "Healthcare",
    "energy": "Energy", "oil & gas": "Energy",
    "telecommunication": "Telecom",
    "automobiles": "Consumer", "retail": "Consumer", "beverages": "Consumer",
    "food products": "Consumer", "media": "Consumer", "consumer products": "Consumer",
    "hotels, restaurants & leisure": "Consumer",
    "textiles, apparel & luxury goods": "Consumer",
}

_plain = request.build_opener()
_cache = {}   # ticker -> [company, sector]


def log(*a):
    print("[ingest]", *a, file=sys.stderr, flush=True)


def http(opener, url, data=None, headers=None, timeout=60):
    h = {"User-Agent": UA, "Accept": "application/json, text/html, */*",
         "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        h.update(headers)
    body = parse.urlencode(data).encode() if isinstance(data, dict) else data
    req = request.Request(url, data=body, headers=h)
    with opener.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


# ---------- Senate eFD source ----------

def senate_login():
    jar = CookieJar()
    op = request.build_opener(request.HTTPCookieProcessor(jar))
    http(op, EFD + "/search/home/")
    csrf = next((c.value for c in jar if c.name == "csrftoken"), None)
    if not csrf:
        raise RuntimeError("could not obtain csrftoken from eFD")
    http(op, EFD + "/search/home/", data={"prohibition_agreement": "1"},
         headers={"Referer": EFD + "/search/home/", "X-CSRFToken": csrf,
                  "X-Requested-With": "XMLHttpRequest",
                  "Content-Type": "application/x-www-form-urlencoded"})
    csrf = next((c.value for c in jar if c.name == "csrftoken"), csrf)
    return op, csrf


def fetch_reports(op, csrf, since, length):
    data = {"draw": "1", "start": "0", "length": str(length), "report_types": "[11]",
            "submitted_start_date": since + " 00:00:00", "submitted_end_date": "",
            "csrfmiddlewaretoken": csrf}
    j = json.loads(http(op, EFD + "/search/report/data/", data=data,
                        headers={"Referer": EFD + "/search/", "X-CSRFToken": csrf,
                                 "X-Requested-With": "XMLHttpRequest",
                                 "Content-Type": "application/x-www-form-urlencoded"}))
    rows = []
    for r in j.get("data", []):
        if len(r) < 5:
            continue
        m = re.search(r"/search/view/ptr/([0-9a-fA-F-]+)/", r[3])
        if not m:                      # paper/PDF filing -> not machine-readable
            continue
        rows.append({"first": str(r[0]).strip(), "last": str(r[1]).strip(),
                     "uuid": m.group(1), "filed": str(r[4]).strip()})
    return rows


def _clean(x):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(x))).strip()


def parse_ptr(op, uuid):
    txt = http(op, EFD + "/search/view/ptr/%s/" % uuid, headers={"Referer": EFD + "/search/"})
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", txt, re.S):
        cells = [_clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) < 8:
            continue
        # [#, Transaction Date, Owner, Ticker, Asset Name, Asset Type, Type, Amount, Comment]
        out.append({"num": cells[0], "txn_date": cells[1], "ticker": cells[3],
                    "asset": cells[4], "ttype": cells[6], "amount": cells[7]})
    return out


# ---------- enrichment ----------

def clean_name(first, last):
    name = (first + " " + last).strip()
    name = re.sub(r"\s+[A-Z]\.?(?=\s)", "", name)   # drop middle initials (Gary C Peters -> Gary Peters)
    return re.sub(r"\s+", " ", name).strip()


def load_roster(op):
    data = json.loads(http(op, ROSTER_URL))
    pmap = {"Democrat": "D", "Republican": "R", "Independent": "I"}
    roster = {}
    for m in data:
        nm, term = m["name"], m["terms"][-1]
        rec = (pmap.get(term.get("party"), "I"), term.get("state", "—"),
               "Senate" if term.get("type") == "sen" else "House")
        keys = {nm.get("official_full"),
                (nm.get("first", "") + " " + nm.get("last", "")).strip(),
                clean_name(nm.get("first", ""), nm.get("last", ""))}
        for k in filter(None, keys):
            roster[k.lower()] = rec
    return roster


def finnhub_sector(ticker):
    """(company, sector) via Finnhub profile2, cached. None if no key/result."""
    if not FINNHUB_KEY:
        return None
    if ticker in _cache:
        return tuple(_cache[ticker])
    url = "https://finnhub.io/api/v1/stock/profile2?symbol=%s&token=%s" % (ticker, FINNHUB_KEY)
    for attempt in range(2):
        try:
            d = json.loads(http(_plain, url, timeout=20))
            ind = (d.get("finnhubIndustry") or "").strip()
            name = (d.get("name") or "").strip()
            res = [name or ticker, INDUSTRY_MAP.get(ind.lower(), "Other") if ind else "Other"]
            _cache[ticker] = res
            time.sleep(1.1)            # free tier: 60 calls/min
            return tuple(res)
        except Exception as e:
            if "429" in str(e) and attempt == 0:
                time.sleep(5)
                continue
            return None


def resolve_ticker(ticker, asset):
    if ticker in SECTORS:
        return SECTORS[ticker]
    fh = finnhub_sector(ticker)
    if fh:
        company, sector = fh
        return (company or (asset[:60] or ticker), sector)
    return ((asset or ticker)[:60], "Other")


def to_iso(d):
    try:
        return datetime.strptime(d, "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def norm_type(t):
    t = t.lower()
    if "purchase" in t:
        return "buy"
    if "sale" in t or "sold" in t:
        return "sell"
    return None


def build_rows(report, txns, roster):
    member = clean_name(report["first"], report["last"])
    party, state, chamber = roster.get(member.lower(), ("I", "—", "Senate"))
    filed = to_iso(report["filed"])
    rows = []
    for tx in txns:
        ticker = tx["ticker"].upper().strip()
        if not re.fullmatch(r"[A-Z][A-Z.]{0,5}", ticker):
            continue
        typ = norm_type(tx["ttype"])
        td = to_iso(tx["txn_date"])
        if not typ or not td:
            continue
        company, sector = resolve_ticker(ticker, tx["asset"])
        rows.append({
            "external_id": "senate:%s:%s" % (report["uuid"], tx["num"]),
            "member": member, "party": party, "state": state, "chamber": chamber,
            "ticker": ticker, "company": company, "sector": sector, "type": typ,
            "amount": tx["amount"].replace(" - ", " – "),
            "trade_date": td, "filing_date": filed, "source": "Senate",
            "price_at_trade": None, "price_current": None, "price_updated": None,
        })
    return rows


# ---------- House eFD source (PDF PTRs from the Clerk's bulk download) ----------

# House amount ranges are a fixed ladder; map a lower bound to the full label
# so we can recover rows where pdfplumber splits the amount across columns.
HOUSE_AMOUNTS = {
    "1,001": "$1,001 - $15,000", "15,001": "$15,001 - $50,000",
    "50,001": "$50,001 - $100,000", "100,001": "$100,001 - $250,000",
    "250,001": "$250,001 - $500,000", "500,001": "$500,001 - $1,000,000",
    "1,000,001": "$1,000,001 - $5,000,000", "5,000,001": "$5,000,001 - $25,000,000",
    "25,000,001": "$25,000,001 - $50,000,000",
}
_H_AMOUNT_RANGE = re.compile(r"\$[\d,]+\s*-\s*\$[\d,]+")
_H_AMOUNT_LOW = re.compile(r"\$([\d,]+)")
_H_DATE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
_H_TICKER = re.compile(r"\(([A-Z][A-Z.]{0,5})\)\s*(?:\n|\s)*\[")
_H_TYPE = re.compile(r"\b(S \(partial\)|P \(partial\)|P|S|E)\b")


def http_bytes(opener, url, timeout=40):
    req = request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with opener.open(req, timeout=timeout) as r:
        return r.read()


def house_amount(joined):
    m = _H_AMOUNT_RANGE.search(joined)
    if m:
        return re.sub(r"\s+", " ", m.group(0))
    m = _H_AMOUNT_LOW.search(joined)
    if m:
        return HOUSE_AMOUNTS.get(m.group(1))
    return None


def fetch_house_reports(op, year, since_iso):
    """PTR filings from the House Clerk's annual FD zip (XML index)."""
    raw = http_bytes(op, "%s/public_disc/financial-pdfs/%sFD.zip" % (HOUSE, year))
    z = zipfile.ZipFile(io.BytesIO(raw))
    root = ET.fromstring(z.read("%sFD.xml" % year))
    reports = []
    for m in root.findall("Member"):
        if m.findtext("FilingType") != "P":     # P = Periodic Transaction Report
            continue
        filed = to_iso(m.findtext("FilingDate") or "")
        if since_iso and filed and filed < since_iso:
            continue
        reports.append({
            "first": (m.findtext("First") or "").strip(),
            "last": (m.findtext("Last") or "").strip(),
            "doc": (m.findtext("DocID") or "").strip(),
            "year": (m.findtext("Year") or year).strip(),
            "filed": (m.findtext("FilingDate") or "").strip(),
            "state": (m.findtext("StateDst") or "")[:2],
        })
    return reports


def read_house_index_file(path, since_iso):
    """PTR filings from a locally downloaded House FD index (tab-delimited .txt).
    Columns: Prefix, Last, First, Suffix, FilingType, StateDst, Year, FilingDate, DocID."""
    reports = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader, None)
        for row in reader:
            if len(row) < 9 or row[4] != "P":     # P = Periodic Transaction Report
                continue
            filed = to_iso(row[7].strip())
            if since_iso and filed and filed < since_iso:
                continue
            reports.append({
                "first": row[2].strip(), "last": row[1].strip(),
                "doc": row[8].strip(), "year": (row[6] or "").strip(),
                "filed": row[7].strip(), "state": (row[5] or "")[:2],
            })
    return reports


def parse_house_pdf(op, doc, year):
    """Extract transactions from a House PTR PDF via table structure."""
    import pdfplumber
    raw = http_bytes(op, "%s/public_disc/ptr-pdfs/%s/%s.pdf" % (HOUSE, year, doc))
    out = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    cells = [(c or "").replace("\n", " ") for c in row]
                    joined = " ".join(cells)
                    mt = _H_TICKER.search(joined)
                    if not mt:
                        continue                # not a transaction row
                    typ = None
                    for c in cells:             # prefer a standalone type cell
                        cs = c.strip()
                        if cs in ("P", "S", "E") or cs.startswith("S (partial)") or cs.startswith("P (partial)"):
                            typ = cs
                            break
                    if not typ:
                        m2 = _H_TYPE.search(joined)
                        typ = m2.group(1) if m2 else None
                    md = _H_DATE.search(joined)
                    amt = house_amount(joined)
                    if mt and typ and md and amt:
                        out.append({"num": str(len(out) + 1), "ticker": mt.group(1),
                                    "ttype": typ, "txn_date": md.group(1), "amount": amt})
    return out


def build_house_rows(report, txns, roster):
    member = clean_name(report["first"], report["last"])
    party, state, chamber = roster.get(member.lower(), ("I", report["state"] or "—", "House"))
    filed = to_iso(report["filed"])
    rows = []
    for tx in txns:
        ticker = tx["ticker"].upper().strip()
        if not re.fullmatch(r"[A-Z][A-Z.]{0,5}", ticker):
            continue
        typ = norm_house_type(tx["ttype"])
        td = to_iso(tx["txn_date"])
        if not typ or not td:
            continue
        company, sector = resolve_ticker(ticker, "")
        rows.append({
            "external_id": "house:%s:%s" % (report["doc"], tx["num"]),
            "member": member, "party": party, "state": state, "chamber": "House",
            "ticker": ticker, "company": company, "sector": sector, "type": typ,
            "amount": tx["amount"].replace(" - ", " – "),
            "trade_date": td, "filing_date": filed, "source": "House",
            "price_at_trade": None, "price_current": None, "price_updated": None,
        })
    return rows


def norm_house_type(t):
    t = t.lower()
    if t.startswith("p"):
        return "buy"
    if t.startswith("s") or t.startswith("e"):
        return "sell"
    return None


# ---------- price enrichment (Yahoo Finance chart API: free, no key) ----------

_price_cache = {}   # ticker -> list of (iso_date, close) sorted ascending, or None


def yahoo_daily(ticker):
    """Daily close history (up to 2y) for a US ticker via Yahoo chart API. Cached per run."""
    if ticker in _price_cache:
        return _price_cache[ticker]
    sym = ticker.replace(".", "-")   # BRK.B -> BRK-B
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=2y&interval=1d" % sym
    hist = None
    try:
        d = json.loads(http(_plain, url, timeout=20))
        res = d["chart"]["result"][0]
        ts = res["timestamp"]
        closes = res["indicators"]["quote"][0]["close"]
        rows = []
        for t, c in zip(ts, closes):
            if c is None:
                continue
            iso = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")
            rows.append((iso, float(c)))
        hist = rows or None
    except Exception:
        hist = None
    _price_cache[ticker] = hist
    time.sleep(0.2)                  # be polite to Yahoo
    return hist


def enrich_prices(rows):
    """Attach price_at_trade (close on/just-before trade_date) and price_current."""
    now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    priced = 0
    for r in rows:
        hist = yahoo_daily(r["ticker"])
        if not hist:
            continue
        cur = hist[-1][1]
        pat = None
        for d, c in hist:            # history is ascending by date
            if d <= r["trade_date"]:
                pat = c
            else:
                break
        if pat is None:
            continue
        r["price_at_trade"] = round(pat, 2)
        r["price_current"] = round(cur, 2)
        r["price_updated"] = now_iso
        priced += 1
    return priced


# ---------- Supabase sink ----------

def upsert(rows, key):
    url = "%s/rest/v1/%s?on_conflict=external_id" % (SUPABASE_URL, TABLE)
    done = 0
    for i in range(0, len(rows), 200):
        chunk = rows[i:i + 200]
        req = request.Request(url, data=json.dumps(chunk).encode(), headers={
            "apikey": key, "Authorization": "Bearer " + key,
            "Content-Type": "application/json", "User-Agent": UA,
            "Prefer": "resolution=merge-duplicates,return=minimal"})
        with request.urlopen(req, timeout=60):
            done += len(chunk)
    return done


def main():
    ap = argparse.ArgumentParser(description="Scrape congressional trades into Supabase.")
    ap.add_argument("--since", default=(datetime.now() - timedelta(days=30)).strftime("%m/%d/%Y"))
    ap.add_argument("--max-reports", type=int, default=40)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-prices", action="store_true", help="skip Yahoo price enrichment")
    ap.add_argument("--no-house", action="store_true", help="skip House PDF scraping")
    ap.add_argument("--house-index", help="path to a locally downloaded House FD index .txt (ingest ALL its PTRs, no cap)")
    ap.add_argument("--out", default="out_trades.csv")
    a = ap.parse_args()

    if os.path.exists(CACHE_FILE):
        try:
            _cache.update(json.load(open(CACHE_FILE, encoding="utf-8")))
        except Exception:
            pass
    log("Finnhub sectors: %s | cached tickers: %d" % ("ON" if FINNHUB_KEY else "off", len(_cache)))

    op, csrf = senate_login()
    log("authenticated with Senate eFD")
    roster = load_roster(op)
    log("roster loaded: %d members" % len(roster))
    reports = fetch_reports(op, csrf, a.since, a.max_reports)
    log("electronic PTRs since %s: %d" % (a.since, len(reports)))

    by_id = {}
    for i, rep in enumerate(reports):
        try:
            rows = build_rows(rep, parse_ptr(op, rep["uuid"]), roster)
            for r in rows:
                by_id[r["external_id"]] = r
            log("  [%d/%d] %s -> %d trades" % (i + 1, len(reports), rep["last"], len(rows)))
        except Exception as e:
            log("  parse error %s: %s" % (rep["uuid"], e))
        time.sleep(0.4)

    # ---- House PTRs (PDF filings from the Clerk's bulk download) ----
    if not a.no_house:
        try:
            year = a.since.split("/")[-1]
            if a.house_index:
                hreports = read_house_index_file(a.house_index, to_iso(a.since))
                log("House PTRs from local index %s: %d (no cap)" % (a.house_index, len(hreports)))
            else:
                hreports = fetch_house_reports(op, year, to_iso(a.since))
                hreports = hreports[:a.max_reports]
                log("House PTRs since %s: %d" % (a.since, len(hreports)))
            hcount = 0
            for i, rep in enumerate(hreports):
                try:
                    rows = build_house_rows(rep, parse_house_pdf(op, rep["doc"], rep["year"]), roster)
                    for r in rows:
                        by_id[r["external_id"]] = r
                    hcount += len(rows)
                    if rows:
                        log("  [H %d/%d] %s -> %d trades" % (i + 1, len(hreports), rep["last"], len(rows)))
                except Exception as e:
                    log("  House parse error %s: %s" % (rep["doc"], e))
                time.sleep(0.3)
            log("House trades parsed: %d" % hcount)
        except Exception as e:
            log("House scrape skipped: %s" % e)

    try:
        json.dump(_cache, open(CACHE_FILE, "w", encoding="utf-8"))
    except Exception:
        pass

    rows = sorted(by_id.values(), key=lambda r: r["trade_date"], reverse=True)
    sectored = sum(1 for r in rows if r["sector"] != "Other")
    log("normalized trades: %d (%d with a known sector)" % (len(rows), sectored))

    if not a.no_prices:
        priced = enrich_prices(rows)
        log("price snapshots: %d/%d trades (Yahoo)" % (priced, len(rows)))

    key = (os.environ.get("SUPABASE_SERVICE_KEY") or "").strip()
    if a.dry_run or not key:
        if rows:
            with open(a.out, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
        log("DRY RUN: wrote %s (%d rows). Set SUPABASE_SERVICE_KEY to upsert." % (a.out, len(rows)))
        for r in rows[:8]:
            print("  %s  %-20s %-4s %-6s %-14s %s" % (
                r["trade_date"], r["member"][:20], r["type"], r["ticker"], r["sector"], r["amount"]))
    else:
        log("UPSERTED %d rows into %s.%s" % (upsert(rows, key), SUPABASE_URL, TABLE))


if __name__ == "__main__":
    main()
