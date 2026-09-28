"""One-off: snapshot the index/breadth series the 2008 stress test needs into a
local JSON, using the project's public read (anon) key and the public SELECT
policies on index_history / breadth_readings. No service-role key is touched.

Run: SUPABASE_ANON_KEY=... python fetch_series.py
The resulting scenario_data.json makes scenario_2008.py reproducible offline.
"""
import json
import os
import sys

from supabase import create_client

URL = "https://egfsjboyzajyemjqazot.supabase.co"
KEY = os.environ["SUPABASE_ANON_KEY"]
sb = create_client(URL, KEY)

INDEX_SYMBOLS = ["^NSEI", "^NSMIDCP", "^NSEMDCP50", "NIFTYMIDCAP150.NS",
                 "GOLD_INR_SYNTH", "^INDIAVIX", "^MOVE", "HYG", "LQD"]


def page_index(symbol):
    rows, step, start = [], 1000, 0
    while True:
        page = (sb.table("index_history").select("bar_date, close")
                .eq("symbol", symbol).order("bar_date")
                .range(start, start + step - 1).execute().data or [])
        rows.extend(page)
        if len(page) < step:
            break
        start += step
    return {r["bar_date"]: float(r["close"]) for r in rows}


def page_breadth(source):
    rows, step, start = [], 1000, 0
    while True:
        page = (sb.table("breadth_readings").select("reading_date, breadth_pct")
                .eq("source", source).order("reading_date")
                .range(start, start + step - 1).execute().data or [])
        rows.extend(page)
        if len(page) < step:
            break
        start += step
    return {r["reading_date"]: float(r["breadth_pct"]) for r in rows}


out = {"index": {}, "breadth": {}}
for s in INDEX_SYMBOLS:
    d = page_index(s)
    out["index"][s] = d
    print(f"  {s}: {len(d)} bars", file=sys.stderr)
out["breadth"]["rzone_pnf_1pct"] = page_breadth("rzone_pnf_1pct")
print(f"  breadth: {len(out['breadth']['rzone_pnf_1pct'])} bars", file=sys.stderr)

with open("scenario_data.json", "w") as f:
    json.dump(out, f)
print("wrote scenario_data.json", file=sys.stderr)
