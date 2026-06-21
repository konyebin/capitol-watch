-- ============================================================
--  Capitol Watch  ·  Supabase schema + seed
--  Run this in the Supabase dashboard:  SQL Editor -> New query -> Run
--  Safe to re-run: it drops and recreates the table.
-- ============================================================

drop table if exists public.congress_trades cascade;

create table public.congress_trades (
  id           bigint generated always as identity primary key,
  external_id  text unique,        -- stable id from the source (ingester upserts on this)
  member       text not null,
  party        text,            -- 'D' | 'R' | 'I'
  state        text,
  chamber      text,            -- 'Senate' | 'House'
  ticker       text not null,
  company      text,
  sector       text,            -- Defense | Technology | Financials | Healthcare | Energy | Telecom | Consumer
  type         text not null,   -- 'buy' | 'sell'
  amount       text,            -- disclosure range, e.g. '$50,001 – $100,000'
  trade_date   date not null,
  filing_date  date,
  source       text not null,   -- 'Senate' | 'House' | 'Capitol Trades'
  created_at   timestamptz not null default now()
);

create index on public.congress_trades (source);
create index on public.congress_trades (trade_date desc);

-- Row Level Security: allow the public (publishable key / anon role) to READ only.
alter table public.congress_trades enable row level security;

drop policy if exists "Public read access" on public.congress_trades;
create policy "Public read access"
  on public.congress_trades
  for select
  using (true);

grant select on public.congress_trades to anon, authenticated;

insert into public.congress_trades
  (external_id,member,party,state,chamber,ticker,company,sector,type,amount,trade_date,filing_date,source)
values
  ('seed:001','Tommy Tuberville','R','AL','Senate','LMT','Lockheed Martin','Defense','buy','$50,001 – $100,000','2026-06-10','2026-06-16','Senate'),
  ('seed:002','Nancy Pelosi','D','CA','House','NVDA','NVIDIA','Technology','buy','$1,000,001 – $5,000,000','2026-06-02','2026-06-12','Capitol Trades'),
  ('seed:003','Mark Warner','D','VA','Senate','NVDA','NVIDIA','Technology','buy','$100,001 – $250,000','2026-05-28','2026-06-09','Senate'),
  ('seed:004','Dan Crenshaw','R','TX','House','XOM','ExxonMobil','Energy','buy','$15,001 – $50,000','2026-05-20','2026-06-05','House'),
  ('seed:005','Josh Gottheimer','D','NJ','House','GS','Goldman Sachs','Financials','buy','$50,001 – $100,000','2026-05-15','2026-05-30','House'),
  ('seed:006','Marjorie Taylor Greene','R','GA','House','TSLA','Tesla','Consumer','buy','$1,001 – $15,000','2026-05-10','2026-05-26','House'),
  ('seed:007','Rick Scott','R','FL','Senate','NOC','Northrop Grumman','Defense','buy','$250,001 – $500,000','2026-04-28','2026-05-20','Senate'),
  ('seed:008','Nancy Pelosi','D','CA','House','AAPL','Apple','Technology','buy','$250,001 – $500,000','2026-04-20','2026-05-02','Capitol Trades'),
  ('seed:009','Shelley Moore Capito','R','WV','Senate','T','AT&T','Telecom','buy','$15,001 – $50,000','2026-04-12','2026-04-30','Senate'),
  ('seed:010','Ro Khanna','D','CA','House','AAPL','Apple','Technology','buy','$1,001 – $15,000','2026-04-05','2026-04-22','House'),
  ('seed:011','Ron Wyden','D','OR','Senate','LLY','Eli Lilly','Healthcare','sell','$50,001 – $100,000','2026-03-25','2026-04-10','Senate'),
  ('seed:012','Gary Peters','D','MI','Senate','GD','General Dynamics','Defense','buy','$15,001 – $50,000','2026-03-18','2026-04-02','Capitol Trades'),
  ('seed:013','Kevin Hern','R','OK','House','BAC','Bank of America','Financials','buy','$50,001 – $100,000','2026-03-10','2026-03-28','House'),
  ('seed:014','Dan Crenshaw','R','TX','House','CVX','Chevron','Energy','buy','$15,001 – $50,000','2026-02-28','2026-03-16','House'),
  ('seed:015','Mark Warner','D','VA','Senate','PFE','Pfizer','Healthcare','sell','$15,001 – $50,000','2026-02-15','2026-03-01','Senate'),
  ('seed:016','Marjorie Taylor Greene','R','GA','House','DIS','Walt Disney','Consumer','buy','$1,001 – $15,000','2026-02-05','2026-02-22','Capitol Trades'),
  ('seed:017','Josh Gottheimer','D','NJ','House','V','Visa','Financials','buy','$15,001 – $50,000','2026-01-28','2026-02-12','House'),
  ('seed:018','Tommy Tuberville','R','AL','Senate','RTX','RTX Corporation','Defense','buy','$100,001 – $250,000','2026-01-15','2026-02-01','Senate'),
  ('seed:019','Nancy Pelosi','D','CA','House','GOOGL','Alphabet','Technology','buy','$500,001 – $1,000,000','2026-01-08','2026-01-22','Capitol Trades'),
  ('seed:020','Rick Scott','R','FL','Senate','AAPL','Apple','Technology','buy','$50,001 – $100,000','2025-12-20','2026-01-06','Senate'),
  ('seed:021','Sheldon Whitehouse','D','RI','Senate','UNH','UnitedHealth Group','Healthcare','sell','$15,001 – $50,000','2025-12-10','2025-12-28','Senate'),
  ('seed:022','Michael McCaul','R','TX','House','MSFT','Microsoft','Technology','buy','$50,001 – $100,000','2025-11-25','2025-12-12','House'),
  ('seed:023','Ron Wyden','D','OR','Senate','MSFT','Microsoft','Technology','buy','$15,001 – $50,000','2025-11-12','2025-11-29','Senate'),
  ('seed:024','Shelley Moore Capito','R','WV','Senate','VZ','Verizon','Telecom','buy','$1,001 – $15,000','2025-10-30','2025-11-15','Senate'),
  ('seed:025','Gary Peters','D','MI','Senate','MSFT','Microsoft','Technology','buy','$15,001 – $50,000','2025-10-18','2025-11-03','Capitol Trades'),
  ('seed:026','Dan Crenshaw','R','TX','House','PFE','Pfizer','Healthcare','buy','$1,001 – $15,000','2025-10-05','2025-10-22','House'),
  ('seed:027','Susan Collins','R','ME','Senate','JNJ','Johnson & Johnson','Healthcare','buy','$1,001 – $15,000','2025-09-20','2025-10-08','Senate'),
  ('seed:028','Kevin Hern','R','OK','House','XOM','ExxonMobil','Energy','buy','$15,001 – $50,000','2025-09-08','2025-09-25','House'),
  ('seed:029','Tommy Tuberville','R','AL','Senate','AAPL','Apple','Technology','buy','$15,001 – $50,000','2025-08-22','2025-09-09','Senate'),
  ('seed:030','Nancy Pelosi','D','CA','House','TSLA','Tesla','Consumer','sell','$250,001 – $500,000','2025-08-10','2025-08-25','Capitol Trades'),
  ('seed:031','Josh Gottheimer','D','NJ','House','AAPL','Apple','Technology','buy','$1,001 – $15,000','2025-07-15','2025-08-01','House'),
  ('seed:032','Marjorie Taylor Greene','R','GA','House','ABBV','AbbVie','Healthcare','buy','$1,001 – $15,000','2025-06-28','2025-07-14','House'),
  ('seed:033','Mark Warner','D','VA','Senate','JPM','JPMorgan Chase','Financials','buy','$50,001 – $100,000','2025-05-15','2025-06-01','Senate'),
  ('seed:034','Virginia Foxx','R','NC','House','SBUX','Starbucks','Consumer','buy','$1,001 – $15,000','2025-03-12','2025-03-29','House'),
  ('seed:035','Nancy Pelosi','D','CA','House','AVGO','Broadcom','Technology','buy','$500,001 – $1,000,000','2024-12-15','2024-12-30','Capitol Trades'),
  ('seed:036','Rick Scott','R','FL','Senate','NOC','Northrop Grumman','Defense','buy','$100,001 – $250,000','2024-09-20','2024-10-08','Senate'),
  ('seed:037','Tommy Tuberville','R','AL','Senate','GD','General Dynamics','Defense','buy','$50,001 – $100,000','2024-07-10','2024-07-28','Senate'),
  ('seed:038','Tommy Tuberville','R','AL','Senate','BA','Boeing','Defense','buy','$50,001 – $100,000','2026-06-17','2026-06-18','Senate'),
  ('seed:039','Nancy Pelosi','D','CA','House','AVGO','Broadcom','Technology','buy','$1,000,001 – $5,000,000','2026-06-15','2026-06-17','Capitol Trades'),
  ('seed:040','Dan Crenshaw','R','TX','House','COP','ConocoPhillips','Energy','buy','$15,001 – $50,000','2026-06-14','2026-06-16','House'),
  ('seed:041','Mark Warner','D','VA','Senate','GS','Goldman Sachs','Financials','buy','$50,001 – $100,000','2026-06-12','2026-06-15','Senate');
