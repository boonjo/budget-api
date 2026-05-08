# Budget Planner — Owner & Maintenance Guide

A reference for running, updating, and extending the application.

---

## Table of Contents

1. [Project Structure](#1-project-structure)
2. [Running Locally](#2-running-locally)
3. [Annual Tax Year Update](#3-annual-tax-year-update)
4. [Updating State Tax Rates](#4-updating-state-tax-rates)
5. [Adding a Brand-New State](#5-adding-a-brand-new-state)
6. [Updating Budget Categories or Frameworks](#6-updating-budget-categories-or-frameworks)
7. [Admin API](#7-admin-api)
8. [Dependencies](#8-dependencies)
9. [Deployment](#9-deployment)

---

## 1. Project Structure

```
pit/
├── main.py              # FastAPI app — routes, budget logic, insights engine, admin endpoints
├── taxes.py             # Tax calculation logic; loads all rate data from JSON
├── tax_rates.json       # ALL tax data: federal brackets + state overrides (edit this, not taxes.py)
├── add_tax_year.py      # Interactive CLI to add a new tax year locally or via the deployed API
├── requirements.txt     # Python dependencies
├── Procfile             # Start command for PythonAnywhere / Railway
└── static/
    ├── index.html       # Single-page frontend (Tailwind + Chart.js)
    └── guide.html       # User-facing "How It Works" documentation
```

**Rule of thumb for edits:**

| What changed | Where to edit |
|---|---|
| New federal tax year | `tax_rates.json` via `add_tax_year.py` or `POST /admin/add-federal-year` |
| State rate changed | `tax_rates.json` via `add_tax_year.py` or `POST /admin/add-state-override` |
| Budget category / insight logic | `main.py` |
| Base state data (permanent rate) | `taxes.py` → `STATES` dict |
| UI change | `static/index.html` |
| User guide content | `static/guide.html` |

> **Note:** State *overrides* (year-specific rate changes) live in `tax_rates.json`.
> The base `STATES` dict in `taxes.py` stays as the permanent baseline for each state
> and should only change if a state permanently adopts a new structure.

---

## 2. Running Locally

### First-time setup

```bash
cd pit
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Start the server

```bash
# Default — localhost only
uvicorn main:app --reload

# Accessible from other devices on your network
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Open `http://localhost:8000` in your browser.

### Stopping the server

`Ctrl-C` in the terminal running uvicorn.

---

## 3. Annual Tax Year Update

### When to do this

The IRS releases the next year's inflation-adjusted brackets in a **Revenue Procedure**
each **October or November**:

- 2025 brackets → IRS Rev. Proc. 2024-40, released October 2024
- 2026 brackets → IRS Rev. Proc. 2025-XX, expected October 2025

The SSA announces the Social Security wage base at the same time:
https://www.ssa.gov/oact/cola/cbb.html

You need both before running the update.

---

### Option A — Local (writes directly to `tax_rates.json`)

```bash
python3 add_tax_year.py
```

**Step 1 — Year:** Enter the new year (defaults to current year + 1).

**Step 2 — Inflation factor:** Find this by dividing any new bracket threshold by its
prior-year counterpart.

```
2025 single 12% bracket top: $48,475
2026 single 12% bracket top: $49,850
Factor: 49,850 ÷ 48,475 ≈ 1.0284
```

Enter `1.0284`. The script applies this to all 28 thresholds and rounds to the
nearest $50 (IRS convention).

**Step 3 — Review brackets:** Press Enter to accept all auto-inflated suggestions,
or type `n` to edit individual thresholds.

**Step 4 — Standard deductions:** Enter the four values from the Rev. Proc. The script
auto-suggests inflated values; verify and correct if needed.

**Step 5 — SS wage base:** Enter the new value from the SSA announcement.

**Step 6 — Source reference:** e.g. `IRS Rev. Proc. 2025-40`.

**Step 7 — State overrides:** The script then prompts for any state rate changes for
the new year. Add as many as needed, or skip.

The script shows a full JSON preview before saving. Press Enter to confirm.

---

### Option B — API mode (updates the deployed server directly)

Use this when the server is already deployed and you don't want to re-deploy just
to update rates.

ADMIN_KEY = 28de02831fdbff18a8da07d656d5b321

```bash
python3 add_tax_year.py --api https://yourapp.pythonanywhere.com --key YOUR_ADMIN_KEY
```

The `ADMIN_KEY` can also be set as an environment variable to avoid typing it each time:

```bash
export ADMIN_KEY=your-secret-key
python3 add_tax_year.py --api https://yourapp.pythonanywhere.com
```

The flow is identical to local mode. The script POSTs the collected data to
`/admin/add-federal-year` and `/admin/add-state-override` on the live server.
The server writes to `tax_rates.json` and reloads the data in-memory — **no restart required**.

---

### Option C — Direct API call (curl)

For a quick update without the interactive script:

```bash
curl -X POST https://yourapp.pythonanywhere.com/admin/add-federal-year \
  -H "x-admin-key: YOUR_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"year": 2026, "factor": 1.0284, "source": "IRS Rev. Proc. 2025-40"}'
```

The `factor` field is required; all bracket thresholds and standard deductions are
auto-inflated from the previous year. Provide explicit `brackets` and
`standard_deduction` fields to override any auto-inflated value.

---

### Verification after update

```bash
python3 -c "
from taxes import calc_all_taxes, FEDERAL_BY_YEAR
print('Years loaded:', sorted(FEDERAL_BY_YEAR.keys()))
r = calc_all_taxes(100_000, 'single', 'CA')
print(f'Year: {r[\"tax_year\"]}, current: {r[\"rates_current\"]}')
print(f'Federal: \${r[\"federal\"][\"tax\"]:,.0f}')
print(f'Net annual: \${r[\"net_annual\"]:,.0f}')
"
```

Cross-check against the IRS withholding calculator or TurboTax/FreeTaxUSA.

---

## 4. Updating State Tax Rates

State overrides live in `tax_rates.json` under `state_overrides_by_year`.
The server loads them at startup and on every admin write — no code changes or
restart required.

Check https://taxfoundation.org/state-individual-income-tax-rates-brackets/ each
January for that year's state rate changes.

---

### Method 1 — Via `add_tax_year.py`

The script prompts for state overrides at the end of a federal year update.
In API mode it POSTs directly to the server.

---

### Method 2 — Direct API call (flat-rate state)

```bash
curl -X POST https://yourapp.pythonanywhere.com/admin/add-state-override \
  -H "x-admin-key: YOUR_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "year": 2026,
    "state": "NC",
    "name": "North Carolina",
    "type": "flat",
    "rate": 0.04,
    "std_deduction_single": 12750,
    "std_deduction_married": 25500,
    "notes": "4.0% in 2026; phasing to 3.99%"
  }'
```

For a progressive state, add `brackets_single` and `brackets_married` arrays.
Use `null` for the top bracket (no upper limit):

```bash
-d '{
    "year": 2026,
    "state": "MO",
    "name": "Missouri",
    "type": "progressive",
    "brackets_single": [[1121,0.015],[2242,0.02],[3363,0.025],[4484,0.03],[5605,0.035],[6726,0.04],[7847,0.045],[8968,0.05],[null,0.046]],
    "brackets_married": [[1121,0.015],[2242,0.02],[3363,0.025],[4484,0.03],[5605,0.035],[6726,0.04],[7847,0.045],[8968,0.05],[null,0.046]],
    "std_deduction_single": 14600,
    "std_deduction_married": 29200,
    "personal_exemption_single": 2100,
    "personal_exemption_married": 4200,
    "notes": "Top rate 4.6% in 2026"
  }'
```

---

### Method 3 — Direct JSON edit

On PythonAnywhere, open `tax_rates.json` in the Files tab and add a block under
`state_overrides_by_year`. Then reload the web app from the Web tab.

---

### States currently phasing down (check each January)

| State | 2025 Rate | Direction | Target |
|-------|-----------|-----------|--------|
| NC    | 4.25%     | ↓         | 3.99% |
| GA    | 5.39%     | ↓         | 4.99% by 2029 |
| IN    | 3.0%      | ↓         | ~2.9% |
| IA    | 3.8% flat | ↓         | ~3.5% |
| MO    | 4.7%      | ↓         | ~4.5% |
| MS    | 4.4%      | ↓         | ~4.0% |
| SC    | 6.2%      | ↓         | ~5.7% |

---

## 5. Adding a Brand-New State

All 50 states + DC are in the `STATES` dict in `taxes.py`. This section covers
adding a territory or correcting permanent base data.

Each state is a `StateInfo` dataclass:

```python
@dataclass
class StateInfo:
    name: str
    type: Literal["none", "flat", "progressive"]
    rate: float = 0.0                              # flat rate only
    brackets_single:  list[tuple[float, float]]   # progressive only
    brackets_married: list[tuple[float, float]]   # progressive only
    std_deduction_single:       float
    std_deduction_married:      float
    personal_exemption_single:  float
    personal_exemption_married: float
    notes: str = ""                               # shown in UI
```

**No income tax:**
```python
"XX": StateInfo("Example State", "none"),
```

**Flat rate:**
```python
"XX": StateInfo("Example State", "flat", rate=0.045,
                std_deduction_single=10_000, std_deduction_married=20_000),
```

**Progressive:**
```python
"XX": StateInfo("Example State", "progressive",
    brackets_single=_prog([(10_000,0.02),(50_000,0.04),(float("inf"),0.06)]),
    brackets_married=_prog([(20_000,0.02),(100_000,0.04),(float("inf"),0.06)]),
    std_deduction_single=8_000, std_deduction_married=16_000),
```

`_prog()` ensures the last bracket uses `float("inf")` as its upper limit.
After adding to `STATES`, the state appears automatically in the dropdown.

---

## 6. Updating Budget Categories or Frameworks

Both are plain Python lists in `main.py` — no logic changes required.

### Categories (`CATEGORIES` list, ~line 46)

```python
{
    "key":     "housing",
    "label":   "Housing",
    "detail":  "Rent / mortgage + utilities",
    "pct_min": 0.25,
    "pct_max": 0.30,
    "note":    "28% gross is the classic mortgage qualifier...",
    "bucket":  "needs",   # "needs", "wants", or "savings"
}
```

### Frameworks (`FRAMEWORKS` list, ~line 61)

```python
{
    "name":     "50/30/20",
    "needs":    0.50,
    "wants":    0.30,
    "savings":  0.20,
    "desc":     "Elizabeth Warren...",
    "best_for": "Most people starting out",
}
```

---

## 7. Admin API

Three protected endpoints allow updating tax data on a live server without
redeploying or restarting.

### Authentication

All admin endpoints require the `x-admin-key` header. Set the key as an
environment variable on the server:

```
ADMIN_KEY=your-secret-key-here
```

If `ADMIN_KEY` is not set, all admin endpoints return `503`.

---

### `GET /admin/status`

Returns the federal years and state overrides currently loaded in memory.

```bash
curl https://yourapp.pythonanywhere.com/admin/status \
  -H "x-admin-key: YOUR_KEY"
```

```json
{
  "federal_years": [2024, 2025],
  "latest_year": 2025,
  "state_overrides": {
    "2025": ["GA", "IA", "IN", "MO", "MS", "NC", "SC"]
  }
}
```

---

### `POST /admin/add-federal-year`

Adds or replaces a federal tax year. Writes to `tax_rates.json` and reloads
in-memory data immediately.

| Field | Type | Required | Description |
|---|---|---|---|
| `year` | int | ✓ | Tax year to add (2024–2040) |
| `factor` | float | ✓ | CPI inflation factor, e.g. `1.027` |
| `source` | string | | Rev. Proc. reference, e.g. `IRS Rev. Proc. 2025-40` |
| `brackets` | object | | Explicit bracket overrides (omit to auto-inflate) |
| `standard_deduction` | object | | Explicit deduction overrides (omit to auto-inflate) |
| `ss_wage_base` | float | | SS wage base (omit to auto-inflate) |

When `brackets`, `standard_deduction`, or `ss_wage_base` are omitted, the server
inflates the previous year's values using `factor` and rounds to the nearest $50.

---

### `POST /admin/add-state-override`

Adds or replaces a state rate for a specific year. Writes to `tax_rates.json`
and reloads immediately.

| Field | Type | Required | Description |
|---|---|---|---|
| `year` | int | ✓ | Tax year |
| `state` | string | ✓ | Two-letter state code |
| `name` | string | ✓ | Full state name |
| `type` | string | ✓ | `flat`, `progressive`, or `none` |
| `rate` | float | | Flat rate (flat type only) |
| `brackets_single` | array | | `[[upper, rate], ...]` (progressive only) |
| `brackets_married` | array | | `[[upper, rate], ...]` (progressive only) |
| `std_deduction_single` | float | | |
| `std_deduction_married` | float | | |
| `personal_exemption_single` | float | | |
| `personal_exemption_married` | float | | |
| `notes` | string | | Shown in UI |

Use `null` in bracket arrays for the top bracket (no upper limit).

---

## 8. Dependencies

```
fastapi>=0.136     # Web framework
uvicorn>=0.44      # ASGI server
python-dotenv>=1.0 # .env file loading
```

To update:

```bash
pip install --upgrade fastapi uvicorn python-dotenv
pip freeze | grep -E "fastapi|uvicorn|python-dotenv" > requirements.txt
```

---

## 9. Deployment

### PythonAnywhere (recommended — free, no credit card)

1. Sign up at [pythonanywhere.com](https://pythonanywhere.com) with a free Beginner account

2. **Upload files** via the Files tab:
   - `main.py`, `taxes.py`, `tax_rates.json`, `requirements.txt`

3. **Install dependencies** — open a Bash console and run:
   ```bash
   pip install --user fastapi uvicorn python-dotenv
   ```

4. **Create a web app** — Web tab → Add new web app:
   - Framework: **Manual configuration**
   - Python version: **3.11**

5. **Configure the WSGI file** — click the WSGI config link and replace the contents with:
   ```python
   import sys
   import os
   sys.path.insert(0, '/home/YOUR_USERNAME')

   # Set env vars here — this file stays on the server, not in git
   os.environ.setdefault('ADMIN_KEY', 'your-secret-key-here')

   from main import app
   from a2wsgi import ASGIMiddleware
   application = ASGIMiddleware(app)
   ```
   > FastAPI is ASGI; PythonAnywhere free tier is WSGI. `a2wsgi` bridges the two.

7. **Reload** the web app from the Web tab.

8. Your API is live at `https://YOUR_USERNAME.pythonanywhere.com`

9. **Verify:**
   ```bash
   curl https://YOUR_USERNAME.pythonanywhere.com/api/states | head -c 100
   curl https://YOUR_USERNAME.pythonanywhere.com/admin/status \
     -H "x-admin-key: YOUR_KEY"
   ```

**Free tier limits:** 100 CPU seconds/day — more than enough for a portfolio project
with light traffic.

---

### Connecting to the Next.js portfolio (Netlify)

In the Netlify dashboard → **Site configuration → Environment variables → Add**:

```
BUDGET_API_URL = https://YOUR_USERNAME.pythonanywhere.com
```

Then push the portfolio repo to trigger a redeploy:

```bash
cd portfolio
git add .
git commit -m "add budgeting page"
git push origin main
```

The Next.js proxy routes (`/api/budget/states` and `/api/budget/calculate`) forward
requests to this URL server-side, so there are no CORS issues.

---

### Re-deploying after code changes

PythonAnywhere does not auto-deploy from GitHub. When you push a code change:

1. Open a Bash console on PythonAnywhere
2. Download the updated files (or use their Files tab to paste/edit)
3. Click **Reload** in the Web tab

For `tax_rates.json` data changes only (no code change), use the admin API —
no reload needed.

---

### Other deployment options

| Platform | Cost | Notes |
|---|---|---|
| Railway | Free tier (CC required) | Add `Procfile`: `web: uvicorn main:app --host 0.0.0.0 --port $PORT` |
| Render | Free tier (CC required) | Build: `pip install -r requirements.txt` · Start: `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| Fly.io | Free tier (CC required) | `fly launch && fly deploy` |

All options support the `ADMIN_KEY` env var through their dashboard environment variable settings.

---

*Last updated: May 2026*
