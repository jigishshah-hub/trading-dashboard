"""Run the BACKTEST_SPEC_2008.md §6 scenarios and print the metrics table.

Reads the verified local snapshots in series/*.txt (md5-checked against
index_history at capture time — see series/README.md), runs the matrix engine
over the 2008 stress window and the modern window, and prints every scenario's
metrics. BACKTEST_2008_STRESS.md is written from this output.

No network, no Supabase, no service-role key: pure replay over the snapshot.
"""

import json
import os
from datetime import date

import backtest_engine as be

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "series")
LAKH = 100_000.0
INITIAL = 10 * LAKH


# ── Load the verified snapshots ──────────────────────────────
def _payload(name):
    s = open(os.path.join(DIR, f"{name}.txt")).read()
    if s.startswith('{"result"'):
        inner = json.loads(s)["result"]
        a = inner.find("[", inner.find("untrusted-data"))
        b = inner.rfind("]") + 1
        row = json.loads(inner[a:b])[0]
        data = row["data"]
    else:
        data = s
    return data.split("::", 1)[-1].strip()


def _series(name):
    out = {}
    for pair in _payload(name).split(";"):
        ds, vs = pair.split(",")
        out[date.fromisoformat(ds)] = float(vs)
    return out


NSEI = _series("nsei")
MIDCAP = _series("midcap")
GOLD = _series("gold")
VIX = _series("vix")
BREADTH = _series("breadth")


def align(feed_start, feed_end):
    """NSE trading days in [feed_start, feed_end] with aligned nifty/midcap/gold
    (gold forward-filled onto NSE dates for US/India calendar mismatches)."""
    tdates = sorted(d for d in (NSEI.keys() & MIDCAP.keys())
                    if feed_start <= d <= feed_end)
    gold_dates = sorted(GOLD)
    nifty, midcap, gold = [], [], []
    gi, last_gold = 0, None
    for d in tdates:
        while gi < len(gold_dates) and gold_dates[gi] <= d:
            last_gold = GOLD[gold_dates[gi]]
            gi += 1
        nifty.append(NSEI[d])
        midcap.append(MIDCAP[d])
        gold.append(last_gold)
    return tdates, nifty, midcap, gold


def window_metrics(res, win_start, win_end):
    """CAGR / XIRR / MDD / Calmar over the reporting window [win_start, win_end],
    all on total capital. Slicing lets a warmed-EMA run report only its window."""
    idx = [i for i, d in enumerate(res.dates) if win_start <= d <= win_end]
    if len(idx) < 2:
        return None
    d0, d1 = res.dates[idx[0]], res.dates[idx[-1]]
    t0, t1 = res.total[idx[0]], res.total[idx[-1]]
    yrs = (d1 - d0).days / 365.25
    cagr = (t1 / t0) ** (1 / yrs) - 1 if yrs > 0 and t0 > 0 else 0.0
    peak, mdd = float("-inf"), 0.0
    for i in idx:
        peak = max(peak, res.total[i])
        if peak > 0:
            mdd = min(mdd, res.total[i] / peak - 1)
    xr = be.xirr([(d0, -t0), (d1, t1)])
    calmar = cagr / abs(mdd) if abs(mdd) > 1e-9 else float("inf")
    return {"cagr": cagr, "xirr": xr, "mdd": mdd, "calmar": calmar,
            "final": t1, "t0": t0, "d0": d0, "d1": d1}


ROWS = []


def record(section, label, res, win_start, win_end):
    m = window_metrics(res, win_start, win_end)
    dd = res.depletion_date.isoformat() if res.depletion_date else "-"
    ddist = f"{res.depletion_dist:.1f}" if res.depletion_dist is not None else "-"
    mda = f"{res.max_dist_after_depletion:.1f}" if res.max_dist_after_depletion is not None else "-"
    ROWS.append({
        "section": section, "label": label,
        "cagr": m["cagr"], "xirr": m["xirr"], "mdd": m["mdd"], "calmar": m["calmar"],
        "final": m["final"],
        "deplete_date": dd, "deplete_dist": ddist, "max_dist_after": mda,
        "days_above_100": res.days_above_100,
        "tax": res.tax_paid, "cost": res.cost_paid,
    })
    return m


def run_window(tag, feed_start, feed_end, win_start, win_end, gates_on):
    d, n, m, g = align(feed_start, feed_end)
    # EMA-warm start for the stress window (first decision bar).
    if win_start is None:
        win_start = d[min(199, len(d) - 1)]
    breadth = BREADTH if gates_on else {}

    def mat(**kw):
        base = dict(initial=INITIAL, gates_on=gates_on, ema_span=200)
        base.update(kw)
        return be.run_matrix(d, n, m, g, breadth, VIX, **base)

    # ── Families: (rob, ceiling). Each active run has a matched 100%-cap control. ──
    fams = {
        "110 (T4)": 0.10,
        "120 (T4-T5)": 0.20,
        "120+T6-8": 0.50,   # T6/7/8 = 130/140/150 default; reserve sized to 150
    }

    # 1. Controls (100%-cap) per family, plus the headline control (rob=0.20).
    record(tag, "CONTROL 100%-cap (rob=0.20)", mat(reserve_over_base=0.20, deploy_cap=1.0),
           win_start, win_end)
    for fam, rob in fams.items():
        record(tag, f"[{fam}] 100%-cap control", mat(reserve_over_base=rob, deploy_cap=1.0),
               win_start, win_end)

    # 2-4 + 5 (spend order) : active runs, both spend orders
    for fam, rob in fams.items():
        for so in ("debt_first", "gold_first"):
            record(tag, f"[{fam}] active {so}",
                   mat(reserve_over_base=rob, deploy_cap=1.0 + rob, spend_order=so),
                   win_start, win_end)

    # 6. Anti-whipsaw on the primary family (120, debt_first)
    for ws, wp, lbl in (("hysteresis", 2.0, "hysteresis(2)"),
                        ("confirm", 3.0, "confirm(3)"),
                        ("glide", 3.0, "glide(1/3wk)")):
        record(tag, f"[120] whipsaw {lbl}",
               mat(reserve_over_base=0.20, deploy_cap=1.20, whipsaw=ws, whipsaw_param=wp),
               win_start, win_end)

    # 7. Deposit-yield sweep on the primary family (120, debt_first)
    for dy in (0.03, 0.05, 0.065, 0.07):
        record(tag, f"[120] deposit_yield {dy*100:.1f}%",
               mat(reserve_over_base=0.20, deploy_cap=1.20, deposit_yield=dy),
               win_start, win_end)

    # 8. Tax + costs on/off, on the best two (120 and 120+T6-8), debt_first
    for fam, rob in (("120 (T4-T5)", 0.20), ("120+T6-8", 0.50)):
        record(tag, f"[{fam}] tax+costs ON",
               mat(reserve_over_base=rob, deploy_cap=1.0 + rob, tax=True, tx_cost_bps=10.0),
               win_start, win_end)

    # Benchmarks (§5)
    fr = be.fixed_rebalance(d, n, m, g, initial=INITIAL)
    record(tag, "BENCH fixed 75/15/10 (annual)", fr, win_start, win_end)
    bh = be.buy_and_hold(d, n, m, initial=INITIAL, nifty_frac=0.58)
    # buy_and_hold returns the old Result (equity list = total). Adapt:
    mbh = _bh_metrics(bh, win_start, win_end)
    ROWS.append({"section": tag, "label": "BENCH buy&hold 58/42 (no reserve)",
                 "cagr": mbh["cagr"], "xirr": mbh["xirr"], "mdd": mbh["mdd"],
                 "calmar": mbh["calmar"], "final": mbh["final"],
                 "deplete_date": "-", "deplete_dist": "-", "max_dist_after": "-",
                 "days_above_100": 0, "tax": 0.0, "cost": 0.0})

    # §7 ceiling sweep: deepest depletion still ahead of its matched control at window end.
    sweep = []
    for ceil in (1.05, 1.10, 1.15, 1.20, 1.25, 1.30, 1.35, 1.40, 1.45, 1.50):
        rob = round(ceil - 1.0, 2)
        act = mat(reserve_over_base=rob, deploy_cap=ceil)
        ctl = mat(reserve_over_base=rob, deploy_cap=1.0)
        ma = window_metrics(act, win_start, win_end)
        mc = window_metrics(ctl, win_start, win_end)
        sweep.append({
            "ceiling": ceil,
            "deplete_dist": act.depletion_dist,
            "max_dist_after": act.max_dist_after_depletion,
            "active_final": ma["final"], "control_final": mc["final"],
            "ahead": ma["final"] > mc["final"],
            "active_cagr": ma["cagr"], "control_cagr": mc["cagr"],
            "active_mdd": ma["mdd"],
        })
    return {"win_start": win_start, "win_end": win_end, "sweep": sweep,
            "n_bars": len(d), "first": d[0], "last": d[-1]}


def _bh_metrics(res, win_start, win_end):
    idx = [i for i, d in enumerate(res.dates) if win_start <= d <= win_end]
    d0, d1 = res.dates[idx[0]], res.dates[idx[-1]]
    t0, t1 = res.equity[idx[0]], res.equity[idx[-1]]
    yrs = (d1 - d0).days / 365.25
    cagr = (t1 / t0) ** (1 / yrs) - 1
    peak, mdd = float("-inf"), 0.0
    for i in idx:
        peak = max(peak, res.equity[i])
        mdd = min(mdd, res.equity[i] / peak - 1)
    return {"cagr": cagr, "xirr": be.xirr([(d0, -t0), (d1, t1)]),
            "mdd": mdd, "calmar": cagr / abs(mdd) if mdd else 0, "final": t1}


def print_table(tag):
    print(f"\n{'='*118}\n{tag}\n{'='*118}")
    hdr = (f"{'scenario':38} {'CAGR%':>7} {'XIRR%':>7} {'MDD%':>7} {'Calmar':>6} "
           f"{'final(L)':>9} {'deplete@':>9} {'dist%':>6} {'maxaft%':>7} {'d>100':>5}")
    print(hdr)
    print("-" * len(hdr))
    for r in ROWS:
        if r["section"] != tag:
            continue
        print(f"{r['label']:38} {r['cagr']*100:7.2f} {r['xirr']*100:7.2f} "
              f"{r['mdd']*100:7.1f} {r['calmar']:6.2f} {r['final']/LAKH:9.2f} "
              f"{r['deplete_date']:>9} {r['deplete_dist']:>6} {r['max_dist_after']:>7} "
              f"{r['days_above_100']:5d}"
              + (f"  tax={r['tax']/LAKH:.2f}L cost={r['cost']/LAKH:.2f}L" if r['tax'] or r['cost'] else ""))


def print_sweep(tag, info):
    print(f"\n--- §7 ceiling sweep [{tag}]  window {info['win_start']} → {info['win_end']} ---")
    print(f"{'ceiling':>8} {'deplete_dist%':>13} {'maxaft%':>8} {'act_final(L)':>12} "
          f"{'ctl_final(L)':>12} {'ahead?':>7} {'act_cagr%':>9} {'act_mdd%':>8}")
    for s in info["sweep"]:
        dd = f"{s['deplete_dist']:.1f}" if s['deplete_dist'] is not None else "-"
        ma = f"{s['max_dist_after']:.1f}" if s['max_dist_after'] is not None else "-"
        print(f"{s['ceiling']:8.2f} {dd:>13} {ma:>8} {s['active_final']/LAKH:12.2f} "
              f"{s['control_final']/LAKH:12.2f} {str(s['ahead']):>7} "
              f"{s['active_cagr']*100:9.2f} {s['active_mdd']*100:8.1f}")


if __name__ == "__main__":
    stress = run_window("STRESS 2008 (Jul 2008 → Mar 2010, gates OFF = EMA-only)",
                        date(2007, 9, 17), date(2010, 3, 31), None, date(2010, 3, 31),
                        gates_on=False)
    modern = run_window("MODERN (2015-06 → 2026-09, gates ON = breadth+VIX)",
                        date(2014, 6, 1), date(2026, 12, 31),
                        date(2015, 6, 1), date(2026, 12, 31), gates_on=True)
    for tag in sorted({r["section"] for r in ROWS}):
        print_table(tag)
    print_sweep("STRESS 2008", stress)
    print_sweep("MODERN", modern)
    print(f"\nstress window bars={stress['n_bars']} {stress['first']}→{stress['last']}; "
          f"first decision {stress['win_start']}")
    print(f"modern window bars={modern['n_bars']} {modern['first']}→{modern['last']}")
