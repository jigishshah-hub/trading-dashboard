# Data snapshot for the 2008 stress test

These files are a frozen, offline snapshot of the series `scenario_2008.py`
replays, so the backtest reproduces without network or credentials. Each file
is `date,value;date,value;...` sorted ascending.

Captured 28 Sep 2026 from Supabase `index_history` / `breadth_readings`
(project `egfsjboyzajyemjqazot`) after the Index History Sync backfill of this
branch. Each payload was verified byte-for-byte against an in-database `md5()`
of the same string at capture time:

| file | symbol / source | bars | first | md5 |
|---|---|---|---|---|
| `nsei.txt` | `^NSEI` | 4669 | 2007-09-17 | `be40aa98e479234710225fab3b833a14` |
| `midcap.txt` | `^NSMIDCP` (Nifty Midcap 50 proxy) | 4684 | 2007-09-17 | `b862328da4f24c2ea9f866b3406a4cd9` |
| `gold.txt` | `GOLD_INR_SYNTH` | 4857 | 2007-01-02 | `03036db3d91453bb60274bf2009b5293` |
| `vix.txt` | `^INDIAVIX` | 4550 | 2008-03-03 | `6687e452652310a9a49c5943dc513ad6` |
| `breadth.txt` | `rzone_pnf_1pct` | 2804 | 2015-06-01 | `850d8b89f7ab72f9f52bb3a85e9cf732` |

To refresh from Supabase (from a machine with egress and the anon read key),
see `../fetch_series.py`. The live data itself is produced by `../index_sync.py`
via the **Index History Sync** GitHub Action.
