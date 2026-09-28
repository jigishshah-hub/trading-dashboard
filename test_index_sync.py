"""Unit tests for the pure data-shaping logic in index_sync.py.

Only the network-free pieces are covered here — the synthetic-gold splice, whose
arithmetic must be provably continuous. The download path is exercised by the
GitHub Actions run, not here.
"""

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import index_sync as ix

FAIL = []


def check(name, got, want, tol=1e-6):
    ok = abs(got - want) <= tol if isinstance(want, float) else got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def bdays(n, start=date(2008, 1, 1)):
    """n consecutive calendar days from start."""
    return [start + timedelta(days=i) for i in range(n)]


# ── 1. Splice is continuous at the anchor ────────────────────
print("\n[1] Gold splice continuity")
# Synthetic leg: GC=F x USDINR=X. ETF leg overlaps from the splice date.
# Choose numbers where the hand answer is obvious.
splice = "2008-12-31"
d_pre = [date(2008, 12, 29), date(2008, 12, 30)]
d_post = [date(2008, 12, 31), date(2009, 1, 1), date(2009, 1, 2)]

gc = {date(2008, 12, 29): 800.0, date(2008, 12, 30): 810.0, date(2008, 12, 31): 820.0,
      date(2009, 1, 1): 830.0, date(2009, 1, 2): 840.0}
fx = {d: 48.0 for d in gc}                       # flat FX -> synth = gc*48
etf = {date(2008, 12, 31): 100.0, date(2009, 1, 1): 101.0, date(2009, 1, 2): 103.0}

out = ix.splice_synthetic_gold(gc, fx, etf, splice_on=splice)
# Anchor is 2008-12-31 (first common date >= splice). synth[anchor]=820*48=39360.
# f = 39360/100 = 393.6. So the ETF leg is rescaled by 393.6.
check("anchor level continuous (synth = etf*f)",
      out[date(2008, 12, 31)][0], 820.0 * 48.0, tol=1e-3)
check("pre-splice uses synthetic leg", out[date(2008, 12, 30)][1], "synthetic")
check("pre-splice level = gc*fx", out[date(2008, 12, 30)][0], 810.0 * 48.0, tol=1e-3)
check("post-splice uses etf leg", out[date(2009, 1, 2)][1], "etf")
# 2009-01-02 ETF level = 103 * 393.6 = 40540.8
check("post-splice level = etf*f", out[date(2009, 1, 2)][0], 103.0 * (820.0 * 48.0 / 100.0), tol=1e-3)

# The join must not introduce a spurious return: the return from the last
# pre-splice bar to the anchor must equal the SYNTHETIC return over that step.
r_join = out[date(2008, 12, 31)][0] / out[date(2008, 12, 30)][0] - 1
r_synth = (820.0 * 48.0) / (810.0 * 48.0) - 1
check("no spurious return across the join", r_join, r_synth, tol=1e-9)

# Post-anchor returns must be exactly the ETF's returns.
r_etf_out = out[date(2009, 1, 2)][0] / out[date(2008, 12, 31)][0] - 1
r_etf_raw = etf[date(2009, 1, 2)] / etf[date(2008, 12, 31)] - 1
check("post-anchor returns are the tradeable ETF's", r_etf_out, r_etf_raw, tol=1e-9)


# ── 2. Pre-anchor ETF bars are dropped, post-anchor synth bars are dropped ──
print("\n[2] Leg selection at the boundary")
# ETF has a stray early bar before the splice; it must be ignored.
etf2 = dict(etf)
etf2[date(2008, 12, 1)] = 95.0
out2 = ix.splice_synthetic_gold(gc, fx, etf2, splice_on=splice)
check("early ETF bar ignored", date(2008, 12, 1) in out2, False)
# synth has bars after the anchor (2009-01-01, -02); ETF wins there.
check("post-anchor date resolves to etf", out2[date(2009, 1, 1)][1], "etf")


# ── 3. FX guards: zero/None FX rows are skipped ──────────────
print("\n[3] Bad FX rows skipped")
fx_bad = dict(fx)
fx_bad[date(2008, 12, 30)] = 0.0        # would divide/scale to nonsense
fx_bad[date(2008, 12, 29)] = None
out3 = ix.splice_synthetic_gold(gc, fx_bad, etf, splice_on=splice)
check("zero-FX bar dropped", date(2008, 12, 30) in out3, False)
check("None-FX bar dropped", date(2008, 12, 29) in out3, False)


# ── 4. No ETF overlap -> synthetic-only, no crash ────────────
print("\n[4] Missing ETF degrades to synthetic-only")
out4 = ix.splice_synthetic_gold(gc, fx, {}, splice_on=splice)
check("all synthetic when ETF empty", all(v[1] == "synthetic" for v in out4.values()), True)
check("synthetic covers all gc*fx dates", len(out4), len(gc))


# ── 5. Config invariants the spec fixes ──────────────────────
print("\n[5] Config")
check("backfill starts 2007", ix.BACKFILL_START, "2007-01-01")
check("^CNXMID removed", "^CNXMID" in ix.MIDCAP_CANDIDATES, False)
check("HYG stored", "HYG" in ix.SYMBOLS, True)
check("LQD stored", "LQD" in ix.SYMBOLS, True)
check("gold symbol name", ix.GOLD_SYMBOL, "GOLD_INR_SYNTH")


print("\n" + "=" * 52)
print(f"{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
