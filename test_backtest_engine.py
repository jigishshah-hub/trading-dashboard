"""Unit tests for the Tactical Ladder backtest engine.

Each case uses a synthetic path whose correct outcome is worked out by hand,
so a failure points at the engine rather than at market data.
"""

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_engine as be

FAIL = []


def check(name, got, want, tol=1e-6):
    ok = abs(got - want) <= tol if isinstance(want, float) else got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def days(n, start=date(2020, 1, 1)):
    return [start + timedelta(days=i) for i in range(n)]


# ── 1. EMA correctness ───────────────────────────────────────
print("\n[1] EMA")
flat = [100.0] * 250
e = be.ema(flat, 200)
check("None before seed", e[198], None)
check("seed = SMA of first 200", e[199], 100.0)
check("flat series stays flat", e[249], 100.0)

ramp = list(range(1, 251))
er = be.ema([float(x) for x in ramp], 200)
check("seed of 1..200 is 100.5", er[199], 100.5)
# EMA of a rising series must lag it but exceed the seed.
check("EMA lags price", er[249] < 250.0 and er[249] > 100.5, True)


# ── 2. No trigger while above the EMA ────────────────────────
print("\n[2] Above EMA -> no deploys")
n = 300
prices = [100.0] * 200 + [120.0] * (n - 200)   # jumps above its own EMA
d = days(n)
r = be.run(d, prices, prices, {}, use_breadth=False, enable_harvest=False)
check("no deploy events", len([x for x in r.events if x.kind == "deploy"]), 0)


# ── 3. Single tier fires once, not every bar ─────────────────
print("\n[3] T1 fires once")
# 200 flat bars seed the EMA at 100, then hold ~6% below it.
prices = [100.0] * 200 + [94.0] * 60
d = days(260)
r = be.run(d, prices, prices, {}, use_breadth=False, enable_harvest=False)
deploys = [x for x in r.events if x.kind == "deploy"]
check("exactly one T1", len([x for x in deploys if x.tier == "T1"]), 1)
# 8% of a 400,000 tactical reserve on 1,000,000 initial
check("deployed 8% of tactical", round(deploys[0].amount, 2), 32000.0, tol=1.0)


# ── 4. Breadth gate blocks when breadth is high ──────────────
print("\n[4] Breadth gate")
prices = [100.0] * 200 + [94.0] * 60
d = days(260)
high_breadth = {dd: 60.0 for dd in d}          # never <= 40
r = be.run(d, prices, prices, high_breadth, use_breadth=True, enable_harvest=False)
check("blocked by breadth", len(r.events), 0)

low_breadth = {dd: 30.0 for dd in d}           # <= 40, so T1 confirms
r = be.run(d, prices, prices, low_breadth, use_breadth=True, enable_harvest=False)
check("confirmed by breadth", len([x for x in r.events if x.tier == "T1"]), 1)

# Missing breadth must fail closed, not fall through as confirmed.
r = be.run(d, prices, prices, {}, use_breadth=True, enable_harvest=False)
check("missing breadth fails closed", len(r.events), 0)


# ── 5. Cash floor caps total deployment ──────────────────────
print("\n[5] Cash floor")
# Drive straight to -30%: every tier arms, wanting 5 x 8% = 40% of tactical,
# which is under the 75% cap, so add a 2x fear multiplier to push past it.
prices = [100.0] * 200 + [70.0] * 60
d = days(260)
fear = {dd: 3 for dd in d}                     # 2x multiplier
r = be.run(d, prices, prices, {}, use_breadth=False, enable_harvest=False,
           fear_signals=fear)
total_deployed = sum(x.amount for x in r.events if x.kind == "deploy")
# The floor is 25% of the ORIGINAL 400,000 reserve = 100,000. Cash has also
# accrued ~200 days of yield by the time the tiers arm, so total deployment can
# legitimately exceed 300,000. The invariant is the floor itself, not the sum.
check("all five tiers armed", len([x for x in r.events if x.kind == "deploy"]), 5)
check("cash never below floor", min(r.cash) >= 100_000.0 - 1.0, True)
check("deployed more than unlevered 5x8%", total_deployed > 300_000.0, True)


# ── 6. Fear multiplier sizes the tranche ─────────────────────
print("\n[6] Fear multiplier")
check("0 signals -> 1x", be.fear_multiplier(0), (1.0, 0.0))
check("2 signals -> 1.5x", be.fear_multiplier(2), (1.5, 0.0))
check("3 signals -> 2x + early", be.fear_multiplier(3), (2.0, 1.0))

prices = [100.0] * 200 + [94.0] * 60
d = days(260)
r1 = be.run(d, prices, prices, {}, use_breadth=False, enable_harvest=False)
r2 = be.run(d, prices, prices, {}, use_breadth=False, enable_harvest=False,
            fear_signals={dd: 2 for dd in d})
a1 = [x for x in r1.events if x.tier == "T1"][0].amount
a2 = [x for x in r2.events if x.tier == "T1"][0].amount
check("2/3 fear deploys 1.5x", round(a2 / a1, 3), 1.5, tol=0.001)


# ── 7. Harvest books tactical back to cash ───────────────────
print("\n[7] Harvest")
# Deploy below the EMA, then rally well above it.
prices = [100.0] * 200 + [94.0] * 40 + [130.0] * 60
d = days(300)
r = be.run(d, prices, prices, {}, use_breadth=False, enable_harvest=True)
kinds = [(x.kind, x.tier) for x in r.events]
check("deployed then harvested",
      any(k == "deploy" for k, _ in kinds) and any(k == "harvest" for k, _ in kinds), True)
h_tiers = [t for k, t in kinds if k == "harvest"]
check("H1 fires first", h_tiers[0], "H1")
check("each harvest tier once", len(h_tiers), len(set(h_tiers)))


# ── 8. Re-arm across a full cycle ────────────────────────────
print("\n[8] Re-arm")
# Down (deploy) -> up (harvest) -> down again (deploy must re-arm).
prices = ([100.0] * 200 + [94.0] * 30 + [130.0] * 30 + [94.0] * 30)
d = days(290)
r = be.run(d, prices, prices, {}, use_breadth=False, enable_harvest=True)
t1s = [x for x in r.events if x.tier == "T1"]
check("T1 fires twice across two dips", len(t1s), 2)


# ── 9. Accounting identity ───────────────────────────────────
print("\n[9] Accounting")
prices = [100.0] * 200 + [94.0] * 60
d = days(260)
r = be.run(d, prices, prices, {}, use_breadth=False, enable_harvest=False)
check("equity starts at initial", round(r.equity[0], 2), 1_000_000.0, tol=1.0)
# Flat prices, no deploys possible on bar 0 -> equity can only grow via yield.
flat_r = be.run(days(300), [100.0] * 300, [100.0] * 300, {},
                use_breadth=False, enable_harvest=False)
check("flat market + cash yield grows equity", flat_r.equity[-1] > flat_r.equity[0], True)
check("no negative cash", min(r.cash) >= 0.0, True)


# ── 10. Guards ───────────────────────────────────────────────
print("\n[10] Input guards")
try:
    be.run(days(3), [1.0, 2.0], [1.0, 2.0, 3.0], {})
    check("length mismatch raises", False, True)
except ValueError:
    check("length mismatch raises", True, True)
try:
    be.run(days(3), [1.0] * 3, [1.0] * 3, {}, core_nifty_frac=0.5,
           core_mid_frac=0.5, tactical_frac=0.5)
    check("bad allocation raises", False, True)
except ValueError:
    check("bad allocation raises", True, True)


print("\n" + "=" * 52)
print(f"{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
