"""Unit tests for the allocation-matrix engine (BACKTEST_SPEC_2008.md §2, §5).

Synthetic paths whose correct outcome is worked out by hand, so a failure
points at the engine rather than at market data. The engine is pure Python, so
this runs without pandas/network.
"""

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_engine as be

FAIL = []


def check(name, got, want, tol=1e-6):
    if isinstance(want, bool):
        ok = got == want
    elif isinstance(want, float):
        ok = abs(got - want) <= tol
    else:
        ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def days(n, start=date(2020, 1, 1)):
    return [start + timedelta(days=i) for i in range(n)]


def path(tail):
    """200 flat bars at 100 (EMA seeds to 100) then the given tail."""
    px = [100.0] * 200 + list(tail)
    return days(len(px)), px


# ── 1. Baseline allocation at t0 ─────────────────────────────
print("\n[1] Initial baseline allocation")
d, px = path([100.0] * 5)
r = be.run_matrix(d, px, px, px, {}, reserve_over_base=0.20,
                  gates_on=False, deposit_yield=0.0)
C0 = r.total[0]
base_ref = C0 / 1.20
check("equity ~80% of base", r.equity[0] / base_ref, 0.80, tol=1e-6)
check("debt ~15% of base", r.debt[0] / base_ref, 0.15, tol=1e-6)
check("gold ~5% of base", r.gold[0] / base_ref, 0.05, tol=1e-6)
check("reserve = rob*base", r.reserve[0] / base_ref, 0.20, tol=1e-6)
check("total = initial", r.total[0], 1_000_000.0, tol=1.0)


# ── 2. Conservation: sleeves sum to total every bar ──────────
print("\n[2] Sleeve conservation")
d, px = path([94.0] * 20 + [130.0] * 20 + [68.0] * 20)
r = be.run_matrix(d, px, px, px, {}, gates_on=False, deposit_yield=0.0)
worst = max(abs(r.equity[i] + r.debt[i] + r.gold[i] + r.reserve[i] - r.total[i])
            for i in range(len(r.total)))
check("equity+debt+gold+reserve == total", worst < 1e-3, True)


# ── 3. Flat market + no yield -> total constant ──────────────
print("\n[3] No yield, flat -> capital constant")
d, px = path([100.0] * 30)
r = be.run_matrix(d, px, px, px, {}, gates_on=False, deposit_yield=0.0)
check("flat capital unchanged", r.total[-1], r.total[0], tol=1.0)
# With yield, idle deposits + debt grow it.
r2 = be.run_matrix(d, px, px, px, {}, gates_on=False, deposit_yield=0.065)
check("yield grows capital", r2.total[-1] > r2.total[0], True)


# ── 4. XIRR == CAGR (the §3 accounting cross-check) ──────────
print("\n[4] XIRR cross-check")
d, px = path([94.0] * 40 + [125.0] * 200)
r = be.run_matrix(d, px, px, px, {}, gates_on=False)
check("xirr ~ cagr (no phantom infusion)", abs(r.xirr() - r.cagr()) < 2e-3, True)


# ── 5. Above-100% deployment depletes the reserve ────────────
print("\n[5] Depletion tracking")
# Drop to -32% (T6, equity target capped at 1+rob=1.20) then deeper to -45%.
d, px = path([68.0] * 10 + [55.0] * 10)
r = be.run_matrix(d, px, px, px, {}, reserve_over_base=0.20,
                  gates_on=False, deposit_yield=0.0)
check("reserve depleted", r.depletion_date is not None, True)
check("idle reserve ~0 after depletion", r.reserve[-1] < 1.0, True)
check("days above 100% > 0", r.days_above_100 > 0, True)
check("depletion EMA-distance is deep", r.depletion_dist < -25.0, True)
# Deepest dist after depletion should reach roughly -45% (55 vs EMA ~100).
check("max dist after depletion ~ -45%", r.max_dist_after_depletion < -40.0, True)


# ── 6. 100%-capped control never deploys the reserve ─────────
print("\n[6] 100%-cap control")
d, px = path([68.0] * 20)                      # would be T6 if uncapped
r = be.run_matrix(d, px, px, px, {}, reserve_over_base=0.20, deploy_cap=1.0,
                  gates_on=False, deposit_yield=0.0)
check("no depletion under cap", r.depletion_date is None, True)
check("no days above 100%", r.days_above_100, 0)
# Idle reserve stays at rob*base_ref = rob/(1+rob) of total each bar.
ratios = [r.reserve[i] / r.total[i] for i in range(210, len(r.total))]
check("reserve held idle at rob/(1+rob)", max(abs(x - 0.20 / 1.20) for x in ratios) < 1e-6, True)


# ── 7. Spend order changes gold holdings pre-tax ─────────────
print("\n[7] Spend order (gold-first vs debt-first)")
d, px = path([88.0] * 10)                      # dist ~ -12% -> T2, ne=0.08*base
rg = be.run_matrix(d, px, px, px, {}, spend_order="gold_first",
                   gates_on=False, deposit_yield=0.0)
rd = be.run_matrix(d, px, px, px, {}, spend_order="debt_first",
                   gates_on=False, deposit_yield=0.0)
# At T2, debt_first keeps gold (~5% of base), gold_first sells gold to ~0.
check("gold_first sells gold at T2", rg.gold[-1] < 1.0, True)
check("debt_first keeps gold at T2", rd.gold[-1] > 1.0, True)
check("orders differ pre-tax", abs(rg.gold[-1] - rd.gold[-1]) > 1.0, True)


# ── 8. Gates: fail closed on missing breadth; EMA-only when off ─
print("\n[8] Gates")
d, px = path([88.0] * 10)                      # T2 by EMA
# gates_on but no breadth/vix -> fail closed -> stays baseline (equity ~80%)
rc = be.run_matrix(d, px, px, px, {}, gates_on=True, deposit_yield=0.0)
base_ref_c = rc.total[-1] / 1.20
check("gate fails closed -> baseline equity ~80%", rc.equity[-1] / base_ref_c, 0.80, tol=0.02)
# gates off -> deploys to T2 (equity ~92%)
ro = be.run_matrix(d, px, px, px, {}, gates_on=False, deposit_yield=0.0)
base_ref_o = ro.total[-1] / 1.20
check("gates off -> T2 equity ~92%", ro.equity[-1] / base_ref_o, 0.92, tol=0.02)
# gates on WITH confirming readings -> deploys to T2
bmap = {dd: 30.0 for dd in d}
vmap = {dd: 30.0 for dd in d}
rok = be.run_matrix(d, px, px, px, bmap, vmap, gates_on=True, deposit_yield=0.0)
base_ref_k = rok.total[-1] / 1.20
check("gate confirmed -> T2 equity ~92%", rok.equity[-1] / base_ref_k, 0.92, tol=0.02)


# ── 9. Hysteresis holds a tier through a shallow bounce ──────
print("\n[9] Whipsaw: hysteresis")
# Dip to -11% (T2), bounce to -9% (would be T1 under 'none'), then dip again.
d, px = path([89.0] * 5 + [91.0] * 5 + [89.0] * 5)
r_none = be.run_matrix(d, px, px, px, {}, whipsaw="none",
                       gates_on=False, deposit_yield=0.0)
r_hy = be.run_matrix(d, px, px, px, {}, whipsaw="hysteresis", whipsaw_param=2.0,
                     gates_on=False, deposit_yield=0.0)
# During the -9% bounce: 'none' reverts to T1, hysteresis (exit at -8%) stays T2.
i_bounce = 200 + 7
check("none reverts to T1 on bounce", r_none.band[i_bounce], "T1 -5..-10%")
check("hysteresis holds T2 on bounce", r_hy.band[i_bounce], "T2 -10..-15%")


# ── 10. Confirmation delay ignores a one-bar spike ───────────
print("\n[10] Whipsaw: confirm(N)")
# One bar at -12% (T2) surrounded by -7% (T1). Confirm(3) must not adopt T2.
d, px = path([93.0] * 3 + [88.0] * 1 + [93.0] * 3)
r_cf = be.run_matrix(d, px, px, px, {}, whipsaw="confirm", whipsaw_param=3.0,
                     gates_on=False, deposit_yield=0.0)
check("confirm ignores 1-bar T2 spike", "T2 -10..-15%" in r_cf.band[200:], False)


# ── 11. Tax reduces the harvested outcome ────────────────────
print("\n[11] Tax on vs off")
# Deploy on a dip, then a long rally that forces harvesting sells with gains.
d, px = path([90.0] * 20 + [140.0] * 200)
r_notax = be.run_matrix(d, px, px, px, {}, tax=False, gates_on=False)
r_tax = be.run_matrix(d, px, px, px, {}, tax=True, gates_on=False)
check("tax charged is positive", r_tax.tax_paid > 0.0, True)
check("tax lowers final capital", r_tax.total[-1] < r_notax.total[-1], True)


# ── 12. Glide moves gradually toward the target ──────────────
print("\n[12] Whipsaw: glide")
d, px = path([88.0] * 30)                      # jump to T2 and hold
r_g = be.run_matrix(d, px, px, px, {}, whipsaw="glide", whipsaw_param=3.0,
                    gates_on=False, deposit_yield=0.0)
r_n = be.run_matrix(d, px, px, px, {}, whipsaw="none",
                    gates_on=False, deposit_yield=0.0)
# One bar after entering T2, glide has not yet reached the full 92% target...
be0 = r_g.total[201] / 1.20
check("glide lags target on bar 1", r_g.equity[201] / be0 < 0.92, True)
# ...it lags the instant-rebalance path early...
check("glide below instant early", r_g.equity[201] < r_n.equity[201], True)
# ...and climbs monotonically toward the target over the following bars
# (flat price + no yield keeps total constant, so equity value tracks fraction).
check("glide climbs toward target", r_g.equity[210] > r_g.equity[201], True)
check("glide still under target while climbing", r_g.equity[210] / (r_g.total[210] / 1.20) < 0.92, True)


# ── 13. Harvest refills the reserve on recovery ──────────────
print("\n[13] Harvest = deploy in reverse")
d, px = path([68.0] * 20 + [150.0] * 60)       # deep deploy, then strong recovery
r = be.run_matrix(d, px, px, px, {}, gates_on=False, deposit_yield=0.0)
trough_reserve = min(r.reserve[200:240])
check("reserve emptied at the trough", trough_reserve < 1.0, True)
check("reserve refilled after recovery", r.reserve[-1] > 1000.0, True)


# ── 14. fixed_rebalance baseline sanity ──────────────────────
print("\n[14] fixed_rebalance benchmark")
d, px = path([100.0] * 30)
fr = be.fixed_rebalance(d, px, px, px, deposit_yield=0.0)
check("fixed equity ~75% at t0", fr.equity[0] / fr.total[0], 0.75, tol=1e-6)
check("fixed gold ~10% at t0", fr.gold[0] / fr.total[0], 0.10, tol=1e-6)
check("fixed flat+no-yield conserves", fr.total[-1], fr.total[0], tol=1.0)


# ── 15. Input guards ─────────────────────────────────────────
print("\n[15] Guards")
d, px = path([100.0] * 3)
try:
    be.run_matrix(d, px, px, px[:-1], {})
    check("length mismatch raises", False, True)
except ValueError:
    check("length mismatch raises", True, True)
try:
    be.run_matrix(d, px, px, px, {}, spend_order="bogus")
    check("bad spend_order raises", False, True)
except ValueError:
    check("bad spend_order raises", True, True)
try:
    be.run_matrix(d, px, px, px, {}, whipsaw="bogus")
    check("bad whipsaw raises", False, True)
except ValueError:
    check("bad whipsaw raises", True, True)


print("\n" + "=" * 52)
print(f"{'ALL PASS' if not FAIL else str(len(FAIL)) + ' FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
