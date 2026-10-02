"""
Research Sync: Obsidian vault -> Supabase.

Parses the machine-parseable [TK] thesis-killer and [MC] monitoring-checklist
lines out of each research note in the vault's "12 Research" folder and upserts
them into three tables:

    research_notes        - one row per ticker (the note header / frontmatter)
    thesis_killers        - one row per [TK] line
    monitoring_checklist  - one row per [MC] line

Design rules (mirrors sync_to_supabase.py conventions):
  * Only dependency: requests.
  * Credentials from scripts/.env  (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY).
  * Upsert on (ticker, label) for the child tables. Lines that have been
    removed from a note are flipped to is_active = false — never deleted.
    This is the deliberate fix for the sync_conditions delete-and-recreate
    bug that would have wiped Obsidian-sourced rows.

Usage:
    python research_sync.py                      # sync every note in 12 Research
    python research_sync.py --file "PATH.md"     # sync one note (watcher passes this)
    python research_sync.py --dry-run            # parse + print, no DB writes
    python research_sync.py --vault "C:\\...\\My Trading Stategies"

Vault path resolution order: --vault arg, then VAULT_PATH env, then the
known default.
"""

import argparse
import datetime
import glob
import json
import os
import re
import sys
import urllib.request
import urllib.error

# ── Config ────────────────────────────────────────────────────

DEFAULT_VAULT = r"C:\Users\Jigis\Documents\My Trading Strategies\My Trading Stategies"
RESEARCH_SUBDIR = "12 Research"

# Enforcement thresholds (mirror stock-research-engine-by-jigish v2.1).
MIN_TK = 3
MIN_MC = 4
MAX_TK = 10
MC_FREQUENCIES = {"QUARTERLY", "EVENT", "MONTHLY", "ANNUAL"}
LABEL_RE = re.compile(r"^[A-Z0-9_]{1,20}$")

# Frontmatter fields we mirror into research_notes (frontmatter key -> column).
FM_FIELDS = {
    "company": "company",
    "sector": "sector",
    "date_analysed": "date_analysed",
    "last_updated": "last_updated",
    "analyst_verdict": "analyst_verdict",
    "cmp_at_analysis": "cmp_at_analysis",
    "intrinsic_value_low": "intrinsic_value_low",
    "intrinsic_value_high": "intrinsic_value_high",
    "valuation_method": "valuation_method",
    "confidence": "confidence",
    "primary_risk": "primary_risk",
    "status": "status",
    # Review scheduling (added 2026-10-02). Optional; only sent when present
    # and valid, so notes without them sync exactly as before.
    "next_results_date": "next_results_date",
    "results_date_basis": "results_date_basis",
    "next_review_date": "next_review_date",
}
DATE_COLS = {"next_results_date", "next_review_date"}
BASIS_ALLOWED = {"Confirmed", "Deadline", "Estimated"}
NUMERIC_COLS = {"cmp_at_analysis", "intrinsic_value_low", "intrinsic_value_high"}
STATUS_ALLOWED = {"Active", "Watch", "Archived", "Stale"}

# Line matchers. Tolerate leading whitespace and Obsidian checkbox "- [ ]"
# is NOT what we want — our tags are literally "[TK]" / "[MC]".
TK_RE = re.compile(r"^\s*-\s*\[TK\]\s*([A-Za-z0-9_]+)\s*:\s*(.+?)\s*$")
MC_RE = re.compile(r"^\s*-\s*\[MC\]\s*([A-Za-z0-9_]+)\s*\|\s*([A-Za-z0-9_]+)\s*:\s*(.+?)\s*$")


# ── Env / HTTP helpers ────────────────────────────────────────

def load_env():
    """Load SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY from env or scripts/.env."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not (url and key):
        here = os.path.dirname(os.path.abspath(__file__))
        for candidate in (os.path.join(here, "scripts", ".env"),
                          os.path.join(here, ".env")):
            if os.path.exists(candidate):
                with open(candidate, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        v = v.strip().strip('"').strip("'")
                        if k.strip() == "SUPABASE_URL" and not url:
                            url = v
                        elif k.strip() == "SUPABASE_SERVICE_ROLE_KEY" and not key:
                            key = v
                break
    return url, key


def _request(method, url, key, payload=None, extra_headers=None):
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def pg_upsert(base_url, key, table, rows, on_conflict):
    """PostgREST upsert (INSERT ... ON CONFLICT DO UPDATE) via merge-duplicates."""
    if not rows:
        return
    url = f"{base_url}/rest/v1/{table}?on_conflict={on_conflict}"
    status, body = _request(
        "POST", url, key, payload=rows,
        extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
    )
    if status >= 300:
        raise RuntimeError(f"upsert {table} failed [{status}]: {body}")


def pg_deactivate_missing(base_url, key, table, ticker, keep_labels):
    """Flip is_active=false for (ticker, label) rows no longer present in the note."""
    if keep_labels:
        quoted = ",".join(f'"{lbl}"' for lbl in sorted(keep_labels))
        flt = f"ticker=eq.{ticker}&label=not.in.({quoted})&is_active=is.true"
    else:
        flt = f"ticker=eq.{ticker}&is_active=is.true"
    url = f"{base_url}/rest/v1/{table}?{flt}"
    status, body = _request(
        "PATCH", url, key, payload={"is_active": False},
        extra_headers={"Prefer": "return=minimal"},
    )
    if status >= 300:
        raise RuntimeError(f"deactivate {table} failed [{status}]: {body}")


def pg_ensure_stock(base_url, key, ticker, sector):
    """Guarantee a stocks row exists so the FK holds. Only sets ticker/sector;
    never clobbers an existing sleeve/status (merge-duplicates on ticker)."""
    row = {"ticker": ticker}
    if sector:
        row["sector"] = sector
    pg_upsert(base_url, key, "stocks", [row], "ticker")


# ── Parsing ───────────────────────────────────────────────────

def read_note(path):
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()
    # Vault files use Windows CRLF; normalize so regexes and splits behave.
    return content.replace("\r\n", "\n").replace("\r", "\n")


def parse_frontmatter(text):
    """Minimal YAML-frontmatter reader: top-of-file block delimited by '---'."""
    if not text.startswith("---"):
        return {}
    lines = text.split("\n")
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}
    fm = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        # Strip inline "# comment" annotations from template values.
        v = v.split("#", 1)[0].strip().strip('"').strip("'")
        if v != "":
            fm[k] = v
    return fm


def ticker_from(fm, path):
    if fm.get("ticker"):
        return fm["ticker"].strip().upper()
    # Fall back to the "TICKER - Research.md" filename convention.
    base = os.path.basename(path)
    m = re.match(r"([A-Za-z0-9_&.-]+?)\s*-\s*Research", base)
    return m.group(1).strip().upper() if m else None


def _num(v):
    if v is None:
        return None
    cleaned = str(v).replace(",", "").replace("₹", "").replace("%", "").strip()
    if cleaned == "" or cleaned in ("000", "000–000"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_note(path):
    """Return (note_row, tk_rows, mc_rows, warnings) for one research file."""
    warnings = []
    text = read_note(path)
    fm = parse_frontmatter(text)
    ticker = ticker_from(fm, path)
    if not ticker:
        return None, [], [], [f"{os.path.basename(path)}: no ticker in frontmatter or filename"]

    # --- note header ---
    note = {"ticker": ticker, "source_file": f"{RESEARCH_SUBDIR}/{os.path.basename(path)}"}
    for fm_key, col in FM_FIELDS.items():
        if fm_key in fm:
            val = _num(fm[fm_key]) if col in NUMERIC_COLS else fm[fm_key]
            if col in DATE_COLS:
                try:
                    datetime.datetime.strptime(str(val), "%Y-%m-%d")
                except ValueError:
                    warnings.append(f"{ticker}: {fm_key} '{val}' is not YYYY-MM-DD; skipped")
                    val = None
            elif col == "results_date_basis" and val not in BASIS_ALLOWED:
                warnings.append(f"{ticker}: results_date_basis '{val}' not in {sorted(BASIS_ALLOWED)}; skipped")
                val = None
            if val is not None:
                note[col] = val
    if note.get("status") and note["status"] not in STATUS_ALLOWED:
        warnings.append(f"{ticker}: status '{note['status']}' not in {sorted(STATUS_ALLOWED)}; leaving to DB default")
        note.pop("status")

    # --- tagged lines ---
    tk_rows, mc_rows = [], []
    tk_seen, mc_seen = set(), set()
    for raw in text.split("\n"):
        m = TK_RE.match(raw)
        if m:
            label, desc = m.group(1).upper(), m.group(2).strip()
            if not LABEL_RE.match(label):
                warnings.append(f"{ticker} [TK]: bad label '{label}' (need UPPERCASE/underscore, <=20)")
                continue
            if label in tk_seen:
                warnings.append(f"{ticker} [TK]: duplicate label '{label}' skipped")
                continue
            tk_seen.add(label)
            tk_rows.append({"ticker": ticker, "label": label, "description": desc, "is_active": True})
            continue
        m = MC_RE.match(raw)
        if m:
            freq, label, desc = m.group(1).upper(), m.group(2).upper(), m.group(3).strip()
            if freq not in MC_FREQUENCIES:
                warnings.append(f"{ticker} [MC]: bad frequency '{freq}' for {label}")
                continue
            if not LABEL_RE.match(label):
                warnings.append(f"{ticker} [MC]: bad label '{label}'")
                continue
            if label in mc_seen:
                warnings.append(f"{ticker} [MC]: duplicate label '{label}' skipped")
                continue
            mc_seen.add(label)
            mc_rows.append({"ticker": ticker, "frequency": freq, "label": label,
                           "description": desc, "is_active": True})

    # --- enforcement-gate consistency check ---
    if note.get("status") == "Active" and (len(tk_rows) < MIN_TK or len(mc_rows) < MIN_MC):
        warnings.append(
            f"{ticker}: status=Active but only {len(tk_rows)} [TK] / {len(mc_rows)} [MC] "
            f"(gate needs {MIN_TK}/{MIN_MC}) — the note violates the enforcement gate"
        )
    if len(tk_rows) > MAX_TK:
        warnings.append(f"{ticker}: {len(tk_rows)} [TK] lines exceeds max {MAX_TK}")

    return note, tk_rows, mc_rows, warnings


# ── Sync ──────────────────────────────────────────────────────

def sync_file(path, base_url, key, dry_run):
    note, tk_rows, mc_rows, warnings = parse_note(path)
    for w in warnings:
        print(f"  ! {w}")
    if note is None:
        return False

    ticker = note["ticker"]
    print(f"  {ticker}: {len(tk_rows)} thesis killer(s), {len(mc_rows)} monitoring item(s)")

    if dry_run:
        print(f"    [dry-run] research_notes <- {json.dumps(note, default=str)}")
        for r in tk_rows:
            print(f"    [dry-run] thesis_killers <- {r['label']}: {r['description'][:60]}")
        for r in mc_rows:
            print(f"    [dry-run] monitoring_checklist <- {r['frequency']}|{r['label']}: {r['description'][:50]}")
        return True

    pg_ensure_stock(base_url, key, ticker, note.get("sector"))
    pg_upsert(base_url, key, "research_notes", [note], "ticker")
    pg_upsert(base_url, key, "thesis_killers", tk_rows, "ticker,label")
    pg_upsert(base_url, key, "monitoring_checklist", mc_rows, "ticker,label")
    pg_deactivate_missing(base_url, key, "thesis_killers", ticker, {r["label"] for r in tk_rows})
    pg_deactivate_missing(base_url, key, "monitoring_checklist", ticker, {r["label"] for r in mc_rows})
    return True


def main():
    ap = argparse.ArgumentParser(description="Sync Obsidian research notes to Supabase.")
    ap.add_argument("--file", help="Sync a single research .md file")
    ap.add_argument("--vault", help="Vault root (overrides VAULT_PATH env / default)")
    ap.add_argument("--dry-run", action="store_true", help="Parse and print, no DB writes")
    args = ap.parse_args()

    base_url, key = (None, None)
    if not args.dry_run:
        base_url, key = load_env()
        if not (base_url and key):
            sys.exit("ERROR: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not found (env or scripts/.env)")
        base_url = base_url.rstrip("/")

    if args.file:
        files = [args.file]
    else:
        vault = args.vault or os.environ.get("VAULT_PATH") or DEFAULT_VAULT
        research_dir = os.path.join(vault, RESEARCH_SUBDIR)
        files = sorted(glob.glob(os.path.join(research_dir, "*.md")))
        if not files:
            print(f"No research notes found in {research_dir}")
            return

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Syncing {len(files)} research note(s)...")
    ok = fail = 0
    for path in files:
        print(f"- {os.path.basename(path)}")
        try:
            if sync_file(path, base_url, key, args.dry_run):
                ok += 1
            else:
                fail += 1
        except Exception as e:
            print(f"  ✗ {e}")
            fail += 1

    print(f"\nDone. {ok} note(s) synced, {fail} failed/skipped.")


if __name__ == "__main__":
    main()
