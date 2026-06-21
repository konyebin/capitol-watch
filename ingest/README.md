# Capitol Watch — data ingester

Scrapes real congressional trades and loads them into Supabase, where the app reads
them. **No data-provider API key required** — it uses the official U.S. Senate eFD
system plus the public `congress-legislators` roster.

```
ingest.py        the scraper + Supabase upserter (Python 3, standard library only)
```

## What it does

1. Authenticates with the Senate eFD (accepts the required search agreement).
2. Lists recent **electronic Periodic Transaction Reports**.
3. Parses each report's transaction table (date, ticker, buy/sell, amount).
4. Enriches each senator with party/state from `congress-legislators`.
5. Upserts rows into `public.congress_trades` keyed on `external_id` (idempotent —
   re-running never duplicates).

## One-time setup

1. **Create the table.** Run [`../supabase_schema.sql`](../supabase_schema.sql) in
   the Supabase dashboard → SQL Editor. (It also seeds 41 demo rows; delete them once
   real data is flowing — see below.)
2. **Get your Supabase secret key.** Dashboard → Settings → API → **service_role / secret**
   key (starts `sb_secret_…`). This key bypasses RLS to *write*, so it stays server-side —
   never put it in the app/browser.

## Run it

Dry run (no key — just scrapes and writes `out_trades.csv` so you can eyeball it):

```bash
python ingest.py --dry-run
```

Write to Supabase:

```bash
# PowerShell
$env:SUPABASE_SERVICE_KEY = "sb_secret_xxx"; python ingest.py

# bash
SUPABASE_SERVICE_KEY=sb_secret_xxx python ingest.py
```

Options: `--since MM/DD/YYYY` (default 30 days ago), `--max-reports N` (default 40),
`--dry-run`, `--out file.csv`.

### Real sectors (optional, recommended)

Set **`FINNHUB_API_KEY`** and the ingester looks up each stock's sector from Finnhub
company profiles, so real trades get a real sector and can be conflict-flagged (ETFs/funds
stay "Other"). The **free** Finnhub tier is enough for this — it covers company profiles.
(It does *not* include congressional-trading data; that's a premium endpoint.) Results are
cached in `sector_cache.json` to respect the 60-calls/min free limit.

```bash
FINNHUB_API_KEY=xxx SUPABASE_SERVICE_KEY=sb_secret_xxx python ingest.py
```

## Automate (GitHub Actions)

A scheduled workflow is included at [`../.github/workflows/ingest.yml`](../.github/workflows/ingest.yml)
(daily, plus manual trigger). To enable it:

1. Push this folder to a GitHub repo (the workflow assumes the repo root is `capitol-watch/`).
2. Repo → Settings → Secrets and variables → Actions → add **`SUPABASE_SERVICE_KEY`**
   (and optionally **`FINNHUB_API_KEY`** for real sectors).
3. The Action runs daily and keeps `congress_trades` fresh. Trigger it once manually from
   the **Actions** tab to backfill.

## Going live (remove demo seed rows)

```sql
delete from public.congress_trades where external_id like 'seed:%';
```

## Known limits (and how to lift them)

- **Senate only.** House transaction data is published as PDFs (no clean feed), so it's
  not scraped here. A paid trade feed (below) is the practical way to add the House.
- **Sectors** come from the built-in map + Finnhub (if `FINNHUB_API_KEY` is set). ETFs/funds
  and stocks in unmapped industries stay "Other" and won't be conflict-flagged — that's
  expected, since conflict flagging only makes sense for sectors a committee regulates.
- **Conflict flagging** still depends on the app's curated committee rosters (16 members).
  Name-cleaning strips middle initials so e.g. "Gary C Peters" matches "Gary Peters", but
  members outside the curated set simply have no committee mapping (so no flags).

### Getting House data / using a paid trade feed later

The Finnhub **free** key only does sectors here. For actual trade data beyond the Senate
(i.e. the House, or a single clean feed for both chambers) you need a paid plan — Finnhub
premium, Quiver, or Unusual Whales. Add a `fetch_*` function that returns the same row dicts
(`external_id, member, party, state, chamber, ticker, company, sector, type, amount,
trade_date, filing_date, source`) and call it from `main()`. The schema, the Supabase upsert,
and the app need **no changes** — they already read whatever is in the table.
