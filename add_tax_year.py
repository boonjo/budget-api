#!/usr/bin/env python3
"""
Interactive tool to add a new federal tax year and state overrides.

Local mode (default) — writes directly to tax_rates.json:
    python3 add_tax_year.py

API mode — POSTs to a deployed server:
    python3 add_tax_year.py --api https://yourapp.pythonanywhere.com
    python3 add_tax_year.py --api https://yourapp.pythonanywhere.com --key YOUR_ADMIN_KEY

The admin key can also be set via the ADMIN_KEY environment variable.

What you need:
  1. New IRS Rev. Proc. (released Oct/Nov each year)
     → https://www.irs.gov/pub/irs-drop/   (search rp-20XX-YY.pdf)
  2. New SS wage base (announced in October)
     → https://www.ssa.gov/oact/cola/cbb.html
  3. State rate changes:
     → https://taxfoundation.org/state-individual-income-tax-rates-brackets/
"""

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

JSON_PATH = Path(__file__).parent / "tax_rates.json"
FILING_STATUSES = ["single", "married_jointly", "married_separately", "head_of_household"]

# ── Terminal colors ────────────────────────────────────────────────────────────
def bold(s):   return f"\033[1m{s}\033[0m"
def green(s):  return f"\033[32m{s}\033[0m"
def yellow(s): return f"\033[33m{s}\033[0m"
def cyan(s):   return f"\033[36m{s}\033[0m"
def dim(s):    return f"\033[2m{s}\033[0m"
def red(s):    return f"\033[31m{s}\033[0m"

def hr(): print(dim("  " + "─" * 58))


def prompt(label: str, default=None, cast=float):
    hint = f"  [{default:,.0f}]" if isinstance(default, (int, float)) else (f"  [{default}]" if default else "")
    while True:
        raw = input(f"  {label}{hint}: ").strip()
        if not raw and default is not None:
            return default
        try:
            return cast(raw.replace(",", "").replace("$", ""))
        except (ValueError, TypeError):
            print(red("  ✗  Invalid — enter a number or press Enter to use the default."))


def confirm(question: str, default_yes=True) -> bool:
    hint = "(Y/n)" if default_yes else "(y/N)"
    raw = input(f"\n  {question} {hint}: ").strip().lower()
    return default_yes if not raw else raw.startswith("y")


# ── Bracket helpers ────────────────────────────────────────────────────────────

def inflate(value, factor: float):
    if value is None:
        return None
    return round(value * factor / 50) * 50


def fmt_upper(v) -> str:
    return "  (no limit)" if v is None else f"${v:>11,.0f}"


def show_bracket_table(filing: str, prev_brackets, new_brackets, prev_year: int, new_year: int):
    label = filing.replace("_", " ").title()
    print(f"\n  {cyan(label)}")
    print(f"  {'Rate':>6}  {'Upper ' + str(prev_year):>14}  {'Suggested ' + str(new_year):>16}")
    hr()
    for i, (upper, rate) in enumerate(new_brackets):
        prev_upper = prev_brackets[i][0]
        print(f"  {rate*100:>5.1f}%  {fmt_upper(prev_upper):>14}  {fmt_upper(upper):>16}")


# ── API helpers ────────────────────────────────────────────────────────────────

def api_post(base_url: str, path: str, key: str, payload: dict) -> dict:
    url = base_url.rstrip("/") + path
    body = json.dumps(payload).encode()
    req = Request(url, data=body, headers={
        "Content-Type": "application/json",
        "x-admin-key": key,
    }, method="POST")
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        detail = json.loads(e.read()).get("detail", str(e))
        print(red(f"\n  ✗  API error {e.code}: {detail}"))
        sys.exit(1)
    except URLError as e:
        print(red(f"\n  ✗  Could not reach {url}: {e.reason}"))
        sys.exit(1)


def api_get(base_url: str, path: str, key: str) -> dict:
    url = base_url.rstrip("/") + path
    req = Request(url, headers={"x-admin-key": key})
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        detail = json.loads(e.read()).get("detail", str(e))
        print(red(f"\n  ✗  API error {e.code}: {detail}"))
        sys.exit(1)


# ── Federal year collection ────────────────────────────────────────────────────

def collect_federal_year(prev_year: int, prev_data: dict) -> tuple[int, float, str, dict, dict, int]:
    """Interactively collect new federal year data. Returns (year, factor, source, brackets, std_ded, ss_base)."""

    hr()
    print(bold(f"\n  Step 1 — New year"))
    raw_year = input(f"  Year to add [{prev_year + 1}]: ").strip()
    new_year = int(raw_year) if raw_year else prev_year + 1

    hr()
    print(bold(f"\n  Step 2 — Bracket inflation factor"))
    print(dim("  The IRS inflates thresholds by CPI each year (~2–3%)."))
    print(dim("  Find it in the new Rev. Proc.: https://www.irs.gov/pub/irs-drop/"))
    print(dim("  Tip: divide any new bracket threshold by its prior-year equivalent."))
    print(dim("  Enter 1.0 to type all brackets manually.\n"))
    factor_raw = input(f"  Inflation factor [1.028]: ").strip()
    factor = float(factor_raw) if factor_raw else 1.028

    suggested: dict[str, list] = {}
    for filing in FILING_STATUSES:
        suggested[filing] = [
            [inflate(upper, factor), rate]
            for upper, rate in prev_data["brackets"][filing]
        ]

    hr()
    print(bold(f"\n  Step 3 — Review bracket thresholds"))
    print(dim("  Press Enter to accept each suggestion, or type a new amount.\n"))
    for filing in FILING_STATUSES:
        show_bracket_table(filing, prev_data["brackets"][filing], suggested[filing], prev_year, new_year)

    if confirm("  Accept all suggested bracket thresholds?", default_yes=True):
        new_brackets = suggested
    else:
        new_brackets = {}
        for filing in FILING_STATUSES:
            label = filing.replace("_", " ").title()
            print(f"\n  {cyan(label)} — enter each upper limit (Enter = use suggestion):")
            new_brackets[filing] = []
            for upper, rate in suggested[filing]:
                if upper is None:
                    new_brackets[filing].append([None, rate])
                    print(f"  {rate*100:.1f}%  top bracket — no upper limit")
                else:
                    val = int(prompt(f"{rate*100:.1f}%  upper", default=upper))
                    new_brackets[filing].append([val, rate])

    hr()
    print(bold(f"\n  Step 4 — Standard deductions"))
    print(dim("  Found in the same Rev. Proc. as the brackets.\n"))
    new_std = {}
    for filing in FILING_STATUSES:
        label = filing.replace("_", " ").title()
        suggested_std = inflate(prev_data["standard_deduction"][filing], factor)
        new_std[filing] = int(prompt(f"{label}", default=suggested_std))

    hr()
    print(bold(f"\n  Step 5 — Social Security wage base"))
    print(dim("  → https://www.ssa.gov/oact/cola/cbb.html\n"))
    new_ss = int(prompt("SS wage base", default=inflate(int(prev_data["ss_wage_base"]), factor)))

    hr()
    print(bold(f"\n  Step 6 — Source reference"))
    default_source = f"IRS Rev. Proc. {new_year - 1}-XX"
    raw_src = input(f"  Rev. Proc. reference [{default_source}]: ").strip()
    source = raw_src if raw_src else default_source

    return new_year, factor, source, new_brackets, new_std, new_ss


# ── State override collection ──────────────────────────────────────────────────

def collect_state_override(year: int) -> dict | None:
    """Interactively collect a state override. Returns payload dict or None if skipped."""
    if not confirm(f"\n  Add a state rate override for {year}?", default_yes=False):
        return None

    print()
    state = input("  State code (e.g. NC): ").strip().upper()
    name  = input(f"  Full name: ").strip()
    t     = input("  Type [flat/progressive/none]: ").strip().lower()

    payload: dict = {"year": year, "state": state, "name": name, "type": t,
                     "rate": 0.0, "notes": ""}

    if t == "flat":
        payload["rate"] = float(prompt("  Flat rate (e.g. 0.0399 for 3.99%)", cast=float))
    elif t == "progressive":
        print(dim("  Enter brackets as 'upper rate' pairs, one per line. Type 'done' when finished."))
        print(dim("  Use 'null' for the top bracket (no upper limit). Example:  50000 0.05"))
        bs, bm = [], []
        for label, target in [("single", bs), ("married", bm)]:
            print(f"\n  {cyan(label.title())} brackets:")
            while True:
                line = input("    upper rate (or 'done'): ").strip()
                if line.lower() == "done":
                    break
                parts = line.split()
                upper = None if parts[0].lower() == "null" else float(parts[0])
                rate  = float(parts[1])
                target.append([upper, rate])
        payload["brackets_single"] = bs
        payload["brackets_married"] = bm

    for field, default in [
        ("std_deduction_single", 0), ("std_deduction_married", 0),
        ("personal_exemption_single", 0), ("personal_exemption_married", 0),
    ]:
        val_raw = input(f"  {field.replace('_', ' ').title()} [{default}]: ").strip()
        payload[field] = float(val_raw) if val_raw else default

    payload["notes"] = input("  Notes (optional): ").strip()
    return payload


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Add a federal tax year and/or state overrides.")
    parser.add_argument("--api", metavar="URL", help="POST to a deployed API instead of writing locally")
    parser.add_argument("--key", metavar="KEY", help="Admin key (or set ADMIN_KEY env var)")
    args = parser.parse_args()

    api_url = args.api
    api_key = args.key or os.getenv("ADMIN_KEY", "")

    if api_url and not api_key:
        print(red("  ✗  --api requires an admin key. Pass --key or set ADMIN_KEY env var."))
        sys.exit(1)

    mode = f"API → {api_url}" if api_url else "local file"

    print(bold("\n  ╔══════════════════════════════════════════════╗"))
    print(bold("  ║   Federal Tax Year Update Tool               ║"))
    print(bold("  ╚══════════════════════════════════════════════╝"))
    print(f"\n  Mode: {dim(mode)}")

    # ── Load previous year data ────────────────────────────────────────────────
    if api_url:
        status = api_get(api_url, "/admin/status", api_key)
        prev_year = status["latest_year"]
        # Fetch the JSON directly to get bracket data for preview
        data = json.loads(JSON_PATH.read_text()) if JSON_PATH.exists() else None
        if data:
            prev_data = data["federal_by_year"].get(str(prev_year))
        else:
            prev_data = None
        print(f"  Server years: {dim(', '.join(str(y) for y in status['federal_years']))}\n")
    else:
        data = json.loads(JSON_PATH.read_text())
        years = sorted(int(y) for y in data["federal_by_year"] if not y.startswith("_"))
        prev_year = years[-1]
        prev_data = data["federal_by_year"][str(prev_year)]
        print(f"  Years in file: {dim(', '.join(str(y) for y in years))}\n")

    if not prev_data:
        print(yellow("  ⚠  Could not load previous year data locally for bracket preview."))
        print(dim("  Bracket suggestions will be skipped. Enter values manually."))
        prev_data = {"brackets": {f: [[None, 0.37]] for f in FILING_STATUSES},
                     "standard_deduction": {f: 0 for f in FILING_STATUSES},
                     "ss_wage_base": 0}

    # ── Collect federal year data ──────────────────────────────────────────────
    new_year, factor, source, new_brackets, new_std, new_ss = collect_federal_year(prev_year, prev_data)

    # ── Preview ────────────────────────────────────────────────────────────────
    new_entry = {"_source": source, "brackets": new_brackets,
                 "standard_deduction": new_std, "ss_wage_base": new_ss}
    hr()
    print(bold(f"\n  Preview — {new_year} entry:"))
    for line in json.dumps({str(new_year): new_entry}, indent=4).splitlines():
        print(f"    {line}")

    if not confirm(green("  Save federal year?"), default_yes=True):
        print("  Cancelled — no changes saved.\n")
        sys.exit(0)

    # ── Save federal year ──────────────────────────────────────────────────────
    if api_url:
        result = api_post(api_url, "/admin/add-federal-year", api_key, {
            "year": new_year, "factor": factor, "source": source,
            "brackets": new_brackets, "standard_deduction": new_std, "ss_wage_base": new_ss,
        })
        print(green(f"\n  ✓  Year {result['year']} saved on server."))
    else:
        if str(new_year) in data["federal_by_year"]:
            if not confirm(yellow(f"  ⚠  Year {new_year} already exists. Overwrite?"), default_yes=False):
                print("  Cancelled.\n")
                sys.exit(0)
        data["federal_by_year"][str(new_year)] = new_entry
        JSON_PATH.write_text(json.dumps(data, indent=2))
        print(green(f"\n  ✓  Year {new_year} saved to {JSON_PATH.name}."))
        print(dim("  Restart the server (or call /admin/status to trigger reload) to apply.\n"))

    if any(c in source for c in ["XX", "??"]):
        print(yellow("  ⚠  Remember to update the source reference once you know the Rev. Proc. number.\n"))

    # ── Optional: add state overrides ─────────────────────────────────────────
    hr()
    print(bold(f"\n  State Overrides"))
    print(dim("  Reference: https://taxfoundation.org/state-individual-income-tax-rates-brackets/\n"))

    while True:
        state_payload = collect_state_override(new_year)
        if not state_payload:
            break

        if api_url:
            result = api_post(api_url, "/admin/add-state-override", api_key, state_payload)
            print(green(f"\n  ✓  State override saved: {result['state']} {result['year']} on server."))
        else:
            data = json.loads(JSON_PATH.read_text())
            overrides = data.setdefault("state_overrides_by_year", {})
            year_block = overrides.setdefault(str(new_year), {})
            year_block[state_payload["state"]] = {k: v for k, v in state_payload.items()
                                                   if k not in ("year", "state")}
            JSON_PATH.write_text(json.dumps(data, indent=2))
            print(green(f"\n  ✓  State override saved: {state_payload['state']} {new_year}."))

        if not confirm("  Add another state override?", default_yes=False):
            break

    print(dim("\n  Done.\n"))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Cancelled.\n")
