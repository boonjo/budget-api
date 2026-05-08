"""FastAPI backend for the personal finance budget planner."""

from __future__ import annotations
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
import uvicorn

from taxes import JSON_PATH, calc_all_taxes, reload as reload_taxes, state_list, FilingStatus

app = FastAPI(title="Budget Planner API", docs_url="/api/docs")

# ── Constants ──────────────────────────────────────────────────────────────────

FIDELITY_TARGETS = [(30,1),(35,2),(40,3),(45,4),(50,6),(55,7),(60,8),(67,10)]

LIMIT_401K   = 23_500    # 2025 IRS elective deferral limit
LIMIT_IRA    =  7_000    # 2025 IRS IRA contribution limit (Roth or traditional)
SS_WAGE_BASE = 168_600   # 2024 Social Security wage base
SS_BEND1     =  1_226    # 2024 PIA bend point 1 (monthly AIME)
SS_BEND2     =  7_391    # 2024 PIA bend point 2 (monthly AIME)

# ── Models ─────────────────────────────────────────────────────────────────────

class Balances(BaseModel):
    checking:   Optional[float] = Field(None, ge=0)
    savings:    Optional[float] = Field(None, ge=0)
    retirement: Optional[float] = Field(None, ge=0)


class CalcRequest(BaseModel):
    gross_annual:   float        = Field(..., gt=0, le=10_000_000)
    pretax_contrib: Optional[float] = Field(None, ge=0)   # 401k + HSA + FSA
    filing_status:  FilingStatus = "single"
    state:          str          = Field(..., min_length=2, max_length=2)
    age:            Optional[int]     = Field(None, ge=16, le=100)
    actuals:        Optional[dict[str, float]] = None
    balances:       Optional[Balances]         = None
    tax_year:       Optional[int] = Field(None, ge=2024, le=2030)

    @field_validator("state")
    @classmethod
    def upper_state(cls, v: str) -> str:
        return v.upper()


# ── Budget categories ──────────────────────────────────────────────────────────

CATEGORIES = [
    {"key": "housing",        "label": "Housing",                    "detail": "Rent / mortgage + utilities",             "pct_min": 0.25, "pct_max": 0.30, "note": "28% gross is the classic mortgage qualifier; keep total housing ≤30%", "bucket": "needs"},
    {"key": "transportation", "label": "Transportation",             "detail": "Car payment, gas, insurance, transit",     "pct_min": 0.10, "pct_max": 0.15, "note": "All-in vehicle costs; include insurance and gas",                      "bucket": "needs"},
    {"key": "food",           "label": "Food",                       "detail": "Groceries + dining out",                  "pct_min": 0.10, "pct_max": 0.15, "note": "USDA low-cost plan benchmark; dining out inflates this quickly",         "bucket": "needs"},
    {"key": "healthcare",     "label": "Healthcare",                 "detail": "Premiums, copays, prescriptions",         "pct_min": 0.05, "pct_max": 0.10, "note": "Includes insurance premiums; grows with age and family size",           "bucket": "needs"},
    {"key": "utilities",      "label": "Utilities & Subscriptions",  "detail": "Phone, internet, streaming, electric",    "pct_min": 0.04, "pct_max": 0.08, "note": "Audit subscriptions regularly — they creep up fast",                   "bucket": "needs"},
    {"key": "insurance",      "label": "Life & Disability Insurance","detail": "Term life + disability premiums",         "pct_min": 0.01, "pct_max": 0.03, "note": "Skip if no dependents; critical once you have them",                   "bucket": "needs"},
    {"key": "retirement",     "label": "Retirement Savings",         "detail": "401(k), IRA, Roth IRA",                  "pct_min": 0.10, "pct_max": 0.15, "note": "At minimum capture full employer match; target 15% by age 35",          "bucket": "savings"},
    {"key": "emergency_fund", "label": "Emergency Fund",             "detail": "Building / maintaining liquid savings",  "pct_min": 0.05, "pct_max": 0.10, "note": "Until you hit 3–6 months of expenses; then redirect to investing",      "bucket": "savings"},
    {"key": "debt",           "label": "Debt Repayment",             "detail": "Student loans, credit cards (non-mtg)",  "pct_min": 0.00, "pct_max": 0.15, "note": "CFPB: keep total DTI ≤36% including housing",                         "bucket": "needs"},
    {"key": "personal",       "label": "Personal Care & Clothing",   "detail": "Haircuts, gym, clothing",                "pct_min": 0.02, "pct_max": 0.05, "note": "Easy place to overspend; track monthly",                               "bucket": "wants"},
    {"key": "entertainment",  "label": "Entertainment & Fun",        "detail": "Vacations, hobbies, dining out, events", "pct_min": 0.03, "pct_max": 0.08, "note": "The 'wants' bucket — reward yourself, but cap it",                     "bucket": "wants"},
    {"key": "giving",         "label": "Giving & Charity",           "detail": "Charitable donations, tithing",          "pct_min": 0.00, "pct_max": 0.10, "note": "Even 1–2% creates meaningful impact",                                  "bucket": "wants"},
]

FRAMEWORKS = [
    {"name": "50/30/20",    "needs": 0.50, "wants": 0.30, "savings": 0.20, "desc": "Elizabeth Warren — balanced lifestyle and savings",    "best_for": "Most people starting out or maintaining balance"},
    {"name": "Dave Ramsey", "needs": 0.55, "wants": 0.20, "savings": 0.25, "desc": "Aggressive debt payoff, then wealth-building",         "best_for": "Those with debt who want to aggressively pay it off"},
    {"name": "FIRE (Lean)", "needs": 0.40, "wants": 0.10, "savings": 0.50, "desc": "Financial Independence / Retire Early",                "best_for": "High earners targeting very early retirement"},
    {"name": "70/20/10",    "needs": 0.70, "wants": 0.10, "savings": 0.20, "desc": "Living 70%, savings 20%, giving/fun 10%",              "best_for": "Lower incomes or high cost-of-living areas"},
]

# ── Insights engine ────────────────────────────────────────────────────────────

def _insight(tier, icon, title, body, action):
    return {"tier": tier, "icon": icon, "title": title, "body": body, "action": action}


def generate_insights(gross_annual, net_monthly, categories_out, health, age):
    insights = []
    gross_mo = gross_annual / 12
    actuals = {c["key"]: c["actual"] for c in categories_out if "actual" in c}
    total_actual = sum(actuals.values())

    # ── CRITICAL ──────────────────────────────────────────────────────────────

    if total_actual > 0 and total_actual > net_monthly:
        over = total_actual - net_monthly
        insights.append(_insight("critical", "🚨", "Spending Exceeds Take-Home",
            f"You're spending ${over:,.0f}/mo more than you earn. Unchecked, this forces high-interest debt.",
            "Cut entertainment, dining, and personal care first — 'wants' categories reduce fastest."))

    if health and health["months_liquid"] < 1:
        insights.append(_insight("critical", "🚨", "No Liquid Safety Net",
            f"Only ${health['liquid_total']:,.0f} liquid — under 1 month of expenses. One unexpected bill could spiral into debt.",
            f"Pause non-essential investing and build ${health['monthly_expenses_used']:,.0f} (1 month) immediately."))

    if "housing" in actuals and actuals["housing"] / gross_mo > 0.38:
        p = actuals["housing"] / gross_mo * 100
        insights.append(_insight("critical", "🏠", f"Housing at {p:.0f}% of Gross Income",
            f"Consuming {p:.0f}% of gross — far above the 30% guideline. This squeezes every other category.",
            "Consider a roommate, subletting, refinancing, or planning a move to a lower-cost area."))

    if "debt" in actuals and actuals["debt"] / gross_mo > 0.20:
        p = actuals["debt"] / gross_mo * 100
        insights.append(_insight("critical", "💳", f"Debt Payments at {p:.0f}% of Income",
            f"Above 20% severely limits financial flexibility and future saving capacity.",
            "Debt avalanche: list all debts by interest rate, attack highest-rate first while paying minimums on the rest."))

    # Frontend only sends keys with value > 0, so absence means $0 contributed
    if "retirement" not in actuals and total_actual > 0 and age and age >= 30:
        insights.append(_insight("critical", "📉", "Zero Retirement Contributions",
            f"At age {age}, contributing $0 to retirement is a serious long-term risk. Time in market is irreplaceable.",
            "Contribute at minimum the employer match to your 401(k) today — that's an instant 50–100% return."))

    if health and health.get("retirement_gap") is not None and health["retirement_gap"] < -(gross_annual * 0.5):
        gap = abs(health["retirement_gap"])
        monthly_catch_up = gap / 120
        insights.append(_insight("critical", "📉", f"Retirement ${gap:,.0f} Behind Fidelity Target",
            f"Significantly behind the Fidelity milestone for age {age}. Target: ${health['retirement_target']:,.0f}.",
            f"Increase 401(k)/IRA by ~${monthly_catch_up:,.0f}/mo over the next 10 years to close the gap."))

    # ── WARNING ───────────────────────────────────────────────────────────────

    if "housing" in actuals:
        p = actuals["housing"] / gross_mo
        if 0.30 < p <= 0.38:
            insights.append(_insight("warning", "🏠", f"Housing at {p*100:.0f}% — Slightly Over",
                "Above the 28–30% guideline. Utilities and HOA fees make this worse than it looks.",
                "Avoid any rent/mortgage increases until your income grows enough to pull this below 30%."))

    if health and 1 <= health["months_liquid"] < 3:
        insights.append(_insight("warning", "💧", f"{health['months_liquid']:.1f} Months Liquid — Below 3-Month Goal",
            f"${health['liquid_total']:,.0f} liquid. Recommended target: ${health['monthly_expenses_used']*3:,.0f}–${health['monthly_expenses_used']*6:,.0f}.",
            "Set up an automatic monthly transfer to a HYSA until you hit the 3-month mark."))

    if "retirement" in actuals:
        p = actuals["retirement"] / gross_mo
        if 0 < p < 0.10:
            insights.append(_insight("warning", "📊", f"Retirement at {p*100:.0f}% — Below 10% Floor",
                f"Saving ${actuals['retirement']:,.0f}/mo ({p*100:.0f}%). Research-backed target is 15% of gross.",
                "Increase by 1% with each raise or annually. Small consistent increases compound dramatically over 20–30 years."))

    if total_actual > 0:
        surplus = net_monthly - total_actual
        if 0 <= surplus < net_monthly * 0.05:
            insights.append(_insight("warning", "⚠️", "Very Thin Monthly Margin",
                f"Only ${surplus:,.0f}/mo ({surplus/net_monthly*100:.0f}%) unallocated. One unexpected expense can tip you into debt.",
                "Find one 'wants' category to trim by 20% to create a meaningful buffer."))

    if "transportation" in actuals and actuals["transportation"] / gross_mo > 0.15:
        p = actuals["transportation"] / gross_mo * 100
        insights.append(_insight("warning", "🚗", f"Transportation at {p:.0f}% of Income",
            "All-in vehicle costs above 15% often stem from expensive car payments relative to income.",
            "Refinance auto loans, raise insurance deductibles, or consider transit alternatives."))

    if "debt" in actuals and 0 < actuals["debt"] / gross_mo <= 0.20:
        p = actuals["debt"] / gross_mo * 100
        insights.append(_insight("warning", "💳", f"Debt Payments at {p:.0f}% — Manageable But Watch It",
            f"Within bounds but worth tracking. Total DTI (housing + debt) should stay below 36%.",
            "Direct windfalls — bonuses, tax refunds — to the highest-interest debt first."))

    if health and health.get("retirement_gap") is not None and -(gross_annual * 0.5) <= health["retirement_gap"] < 0:
        gap = abs(health["retirement_gap"])
        insights.append(_insight("warning", "📊", f"Retirement ${gap:,.0f} Behind Milestone",
            f"Slightly below the Fidelity target for age {age} (${health['retirement_target']:,.0f}).",
            "A modest increase in contributions now has outsized impact thanks to compounding."))

    # ── GOOD ──────────────────────────────────────────────────────────────────

    if health:
        mo = health["months_liquid"]
        if mo >= 6:
            insights.append(_insight("good", "✅", f"{mo:.1f} Months Liquid — Fully Funded",
                f"${health['liquid_total']:,.0f} in liquid accounts exceeds the 6-month target. Excellent security.",
                "Excess above 6 months earns more in a taxable brokerage (index funds) than a savings account."))
        elif mo >= 3:
            insights.append(_insight("good", "✅", f"{mo:.1f} Months Emergency Fund",
                f"${health['liquid_total']:,.0f} liquid meets the 3-month minimum.",
                "Keep building toward 6 months; consider a high-yield savings account (HYSA) for the best return."))

    if "retirement" in actuals and actuals["retirement"] / gross_mo >= 0.10:
        p = actuals["retirement"] / gross_mo * 100
        insights.append(_insight("good", "🎯", f"Retirement at {p:.0f}% — On Track",
            f"Saving ${actuals['retirement']:,.0f}/mo ({p:.0f}%) toward retirement.",
            "Ensure you're diversified across pre-tax 401(k) and post-tax Roth IRA for tax flexibility in retirement."))

    if "housing" in actuals and actuals["housing"] / gross_mo <= 0.25:
        p = actuals["housing"] / gross_mo * 100
        insights.append(_insight("good", "🏠", f"Housing Efficient at {p:.0f}%",
            "Well within the 25–30% guideline, leaving meaningful room for savings and discretionary spending.",
            "Direct the headroom toward retirement or a down payment fund."))

    if total_actual > 0:
        surplus = net_monthly - total_actual
        if surplus >= net_monthly * 0.20:
            future = surplus * 12 * ((1.07 ** 10 - 1) / 0.07)
            insights.append(_insight("good", "💪", f"${surplus:,.0f}/mo Strong Surplus",
                f"{surplus/net_monthly*100:.0f}% surplus means real financial flexibility.",
                f"At a 7% avg annual return, investing ${surplus:,.0f}/mo grows to ~${future:,.0f} in 10 years."))

    if health and health.get("retirement_gap") is not None and health["retirement_gap"] > 0:
        insights.append(_insight("good", "🏆", f"Retirement ${health['retirement_gap']:,.0f} Ahead of Target",
            f"You exceed the Fidelity milestone for age {age}. Target was ${health['retirement_target']:,.0f}.",
            "Consider maximizing Roth IRA ($7,000/yr cap) and backdoor Roth if income exceeds direct limits."))

    # ── BEHAVIORAL FINANCE (Ben Felix / evidence-based) ──────────────────────

    # Cash drag: holding far more liquid cash than needed has a real opportunity cost
    if health and health["months_liquid"] > 12:
        excess_mo = health["months_liquid"] - 6
        excess_amt = round(excess_mo * health["monthly_expenses_used"])
        insights.append(_insight("warning", "🧠", f"{health['months_liquid']:.0f} Months Cash — Opportunity Cost",
            f"~${excess_amt:,} beyond your 6-month emergency fund is sitting in cash. At historical real equity returns of ~5%, "
            f"cash drag costs roughly ${round(excess_amt * 0.05):,}/yr in foregone growth.",
            "Move the excess above 6–9 months into a low-cost, globally diversified index fund (e.g. a total world fund). "
            "Keep the emergency fund in a high-yield savings account, but don't let the rest idle."))

    # Idle surplus / automation gap: recurring monthly surplus not routed to investments
    if total_actual > 0:
        surplus_val = net_monthly - total_actual
        if surplus_val >= net_monthly * 0.15 and actuals.get("retirement", 0) / gross_mo < 0.10:
            insights.append(_insight("warning", "🧠", "Recurring Surplus Not Invested",
                f"${surplus_val:,.0f}/mo ({surplus_val/net_monthly*100:.0f}%) is unallocated each month. "
                "Behavioral research consistently shows unrouted surplus gets spent, not saved.",
                "Automate a transfer on payday — treat it like a bill. Even routing half of the surplus to a "
                "retirement account or taxable brokerage eliminates the decision and the drift."))

    # Present bias: brain underweights future rewards, causing chronic under-saving
    if age and 35 <= age < 55 and "retirement" in actuals:
        p = actuals["retirement"] / gross_mo
        if 0 < p < 0.08:
            delay_cost = round(actuals["retirement"] * 12 * ((1.07 ** (65 - age) - 1) / 0.07))
            insights.append(_insight("warning", "🧠", "Present Bias — Delaying Costs More Than It Feels",
                f"At age {age}, each year you defer a 1% savings increase costs you compounding decades of growth. "
                f"Your current retirement contributions at the same rate grow to ~${delay_cost:,} by 65 — "
                "but increasing by just 2% of gross today meaningfully shifts that number.",
                "Commit to increasing your retirement contribution by 1% with your next paycheck. "
                "The psychological barrier is larger than the financial one — once automated, you won't notice it."))

    # Lifestyle inflation: spending rises to match income, crowding out savings
    if total_actual > 0 and gross_annual >= 80_000:
        savings_rate = actuals.get("retirement", 0) / gross_mo
        spending_rate = total_actual / net_monthly
        if spending_rate > 0.90 and savings_rate < 0.10:
            insights.append(_insight("warning", "🧠", "Lifestyle Inflation Risk",
                f"You're spending {spending_rate*100:.0f}% of take-home on a ${gross_annual:,.0f} income. "
                "High earners frequently fail to build wealth not from low income, but because spending scales with earnings.",
                "Commit to saving at least 50% of each future raise before you adjust to the higher take-home. "
                "Automate the increase immediately — lifestyle inflation is nearly invisible until it locks you in."))

    # ── 401(k) / IRA LIMIT AWARENESS ─────────────────────────────────────────

    if "retirement" in actuals:
        annual_ret = actuals["retirement"] * 12
        if annual_ret >= LIMIT_401K + LIMIT_IRA:
            insights.append(_insight("good", "💡", "At or Beyond Max Contribution Space",
                f"Contributing ~${annual_ret:,.0f}/yr. Combined 401(k) + IRA cap is ${LIMIT_401K+LIMIT_IRA:,}.",
                "Redirect any excess over IRS limits to a taxable brokerage in low-cost index funds."))
        elif annual_ret >= LIMIT_401K * 0.85:
            insights.append(_insight("good", "💡", "Approaching 401(k) Annual Limit",
                f"At ${annual_ret:,.0f}/yr you're approaching the ${LIMIT_401K:,} 401(k) cap.",
                f"Once maxed, contribute up to ${LIMIT_IRA:,}/yr to a Roth IRA for tax-free growth."))

    order = {"critical": 0, "warning": 1, "good": 2}
    insights.sort(key=lambda x: order[x["tier"]])
    return insights


def estimate_ss_benefit(gross_annual: float, age: int | None) -> dict:
    """Simplified SSA PIA using 2024 bend points.
    Assumes stable career earnings at current gross (optimistic for early-career workers).
    """
    aime = min(gross_annual, SS_WAGE_BASE) / 12
    if aime <= SS_BEND1:
        pia = aime * 0.90
    elif aime <= SS_BEND2:
        pia = SS_BEND1 * 0.90 + (aime - SS_BEND1) * 0.32
    else:
        pia = SS_BEND1 * 0.90 + (SS_BEND2 - SS_BEND1) * 0.32 + (aime - SS_BEND2) * 0.15
    pia = round(pia, 2)
    return {
        "at_62":        round(pia * 0.70, 2),   # early claim: -30%
        "at_67":        pia,                      # full retirement age (born ≥ 1960)
        "at_70":        round(pia * 1.24, 2),   # maximum delay: +24%
        "annual_at_67": round(pia * 12,   2),
        "years_to_fra": max(0, 67 - age) if age else None,
    }


# ── Routes ─────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _cached_state_list():
    return state_list()


@app.get("/api/states")
def get_states():
    return _cached_state_list()


@app.post("/api/calculate")
def calculate(req: CalcRequest):
    pretax = req.pretax_contrib or 0

    # Pre-tax contributions (401k, HSA, FSA) reduce federal & state taxable income.
    # FICA is assessed on full gross wages (401k does not reduce SS/Medicare base).
    taxable_gross = max(0.0, req.gross_annual - pretax)
    taxes = calc_all_taxes(taxable_gross, req.filing_status, req.state, req.tax_year)

    # Patch display fields back to real gross (taxes were computed on taxable_gross)
    gross_mo = req.gross_annual / 12
    taxes["gross"]          = round(req.gross_annual, 2)
    taxes["gross_monthly"]  = round(gross_mo, 2)
    taxes["effective_rate"] = round(taxes["total_tax"] / req.gross_annual, 6) if req.gross_annual else 0
    taxes["pretax_contrib"]    = round(pretax, 2)
    taxes["marginal_combined"] = round(
        taxes["federal"]["marginal_rate"] + taxes["state"]["marginal_rate"] + 0.0765, 4
    )

    net_mo = taxes["net_monthly"]

    # Categories
    categories_out = []
    for cat in CATEGORIES:
        lo   = gross_mo * cat["pct_min"]
        hi   = gross_mo * cat["pct_max"]
        item = {**cat, "monthly_min": round(lo, 2), "monthly_max": round(hi, 2)}
        if req.actuals and cat["key"] in req.actuals:
            actual = req.actuals[cat["key"]]
            item["actual"] = round(actual, 2)
            if lo == 0 and hi == 0:
                item["status"] = "ok"
            elif actual < lo:
                item["status"] = "under"
            elif actual > hi:
                item["status"] = "over"
            else:
                item["status"] = "ok"
        categories_out.append(item)

    # Frameworks
    frameworks_out = [
        {**fw,
         "needs_monthly":   round(gross_mo * fw["needs"],   2),
         "wants_monthly":   round(gross_mo * fw["wants"],   2),
         "savings_monthly": round(gross_mo * fw["savings"], 2)}
        for fw in FRAMEWORKS
    ]

    # Surplus
    surplus = None
    if req.actuals:
        surplus = round(net_mo - sum(req.actuals.values()), 2)

    # Balance health check — computed before milestones so emergency fund targets agree
    health = None
    if req.balances:
        b            = req.balances
        liquid       = (b.checking or 0) + (b.savings or 0)
        ret_bal      = b.retirement or 0
        total_actual = sum(req.actuals.values()) if req.actuals else None
        monthly_exp  = total_actual if total_actual else net_mo * 0.80
        months_liquid = liquid / monthly_exp if monthly_exp > 0 else 0

        ret_target = ret_gap = milestone_age = None
        if req.age:
            applicable = [(a, m) for a, m in FIDELITY_TARGETS if a <= req.age]
            if applicable:
                milestone_age, mult = max(applicable, key=lambda x: x[0])
                ret_target = req.gross_annual * mult
                ret_gap    = ret_bal - ret_target

        health = {
            "checking":               round(b.checking or 0, 2),
            "savings":                round(b.savings or 0, 2),
            "retirement_balance":     round(ret_bal, 2),
            "liquid_total":           round(liquid, 2),
            "net_worth_estimate":     round(liquid + ret_bal, 2),
            "months_liquid":          round(months_liquid, 1),
            "monthly_expenses_used":  round(monthly_exp, 2),
            "retirement_target":      round(ret_target, 2) if ret_target is not None else None,
            "retirement_gap":         round(ret_gap, 2) if ret_gap is not None else None,
            "retirement_milestone_age": milestone_age,
        }

    # Milestones — emergency fund uses same monthly_expenses_used basis as health check
    exp_basis = health["monthly_expenses_used"] if health else net_mo
    milestones = [
        {"label": "Emergency Fund (3 months)", "amount": round(exp_basis * 3, 2),
         "note": f"3× monthly expenses of ${exp_basis:,.0f} (CFPB minimum)", "past": False},
        {"label": "Emergency Fund (6 months)", "amount": round(exp_basis * 6, 2),
         "note": f"6× monthly expenses of ${exp_basis:,.0f} (Fidelity recommended)", "past": False},
        {"label": "Home Purchase Price Range",  "amount": round(req.gross_annual * 2.75, 2),
         "note": f"2.5–3× gross: ${req.gross_annual*2.5:,.0f} – ${req.gross_annual*3:,.0f}", "past": False},
        {"label": "Max All-In Car Cost",        "amount": round(req.gross_annual * 0.35, 2),
         "note": "≤35% of gross annual income (all vehicles)", "past": False},
    ]
    for target_age, mult in FIDELITY_TARGETS:
        past = bool(req.age and req.age >= target_age)
        milestones.append({
            "label": f"Retirement by Age {target_age}",
            "amount": round(req.gross_annual * mult, 2),
            "note": f"{mult}× salary (Fidelity guideline){' — past target' if past else ''}",
            "past": past,
        })

    # DTI (housing + debt payments as % of gross monthly)
    dti = None
    if req.actuals:
        housing_mo = req.actuals.get("housing", 0)
        debt_mo    = req.actuals.get("debt", 0)
        if housing_mo + debt_mo > 0:
            dti = round((housing_mo + debt_mo) / gross_mo, 4)

    insights = generate_insights(req.gross_annual, net_mo, categories_out, health, req.age)

    return {
        "taxes":         taxes,
        "categories":    categories_out,
        "frameworks":    frameworks_out,
        "milestones":    milestones,
        "insights":      insights,
        "health":        health,
        "surplus":       surplus,
        "dti":           dti,
        "gross_monthly": gross_mo,
        "net_monthly":   net_mo,
        "ss_estimate":   estimate_ss_benefit(req.gross_annual, req.age),
    }


# ── Admin ──────────────────────────────────────────────────────────────────────

ADMIN_KEY = os.getenv("ADMIN_KEY", "")


def _verify_admin(x_admin_key: str = Header(..., alias="x-admin-key")):
    if not ADMIN_KEY:
        raise HTTPException(503, "Admin not configured — set ADMIN_KEY env var")
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(401, "Invalid admin key")


class FederalYearRequest(BaseModel):
    year: int = Field(..., ge=2024, le=2040)
    factor: float = Field(..., gt=0.95, lt=1.10, description="CPI inflation factor, e.g. 1.027")
    source: str = ""
    # Optional explicit overrides — if omitted, values are auto-inflated from the previous year
    brackets: Optional[dict[str, list]] = None
    standard_deduction: Optional[dict[str, float]] = None
    ss_wage_base: Optional[float] = Field(None, gt=0)


class StateOverrideRequest(BaseModel):
    year: int = Field(..., ge=2024, le=2040)
    state: str = Field(..., min_length=2, max_length=2)
    name: str
    type: Literal["none", "flat", "progressive"]
    rate: float = 0.0
    brackets_single: Optional[list] = None
    brackets_married: Optional[list] = None
    std_deduction_single: float = 0.0
    std_deduction_married: float = 0.0
    personal_exemption_single: float = 0.0
    personal_exemption_married: float = 0.0
    notes: str = ""

    @field_validator("state")
    @classmethod
    def upper_state(cls, v: str) -> str:
        return v.upper()


def _inflate(value, factor: float) -> int:
    """Inflate a bracket threshold to nearest $50 (IRS rounding convention)."""
    return round(value * factor / 50) * 50


@app.get("/admin/status", dependencies=[Depends(_verify_admin)])
def admin_status():
    from taxes import FEDERAL_BY_YEAR, STATE_OVERRIDES_BY_YEAR
    years = sorted(FEDERAL_BY_YEAR.keys())
    overrides = {str(y): sorted(states.keys()) for y, states in STATE_OVERRIDES_BY_YEAR.items()}
    return {
        "federal_years": years,
        "latest_year": max(years),
        "state_overrides": overrides,
    }


@app.post("/admin/add-federal-year", dependencies=[Depends(_verify_admin)])
def admin_add_federal_year(req: FederalYearRequest):
    data = json.loads(JSON_PATH.read_text())
    years = sorted(int(y) for y in data["federal_by_year"] if not y.startswith("_"))
    prev = data["federal_by_year"][str(max(years))]

    new_brackets = req.brackets or {
        filing: [
            [None if upper is None else _inflate(upper, req.factor), rate]
            for upper, rate in pairs
        ]
        for filing, pairs in prev["brackets"].items()
    }

    new_std = req.standard_deduction or {
        k: _inflate(v, req.factor) for k, v in prev["standard_deduction"].items()
    }

    new_ss = req.ss_wage_base or _inflate(prev["ss_wage_base"], req.factor)

    new_entry = {
        "_source": req.source or f"IRS Rev. Proc. {req.year - 1}-XX",
        "brackets": new_brackets,
        "standard_deduction": new_std,
        "ss_wage_base": new_ss,
    }

    data["federal_by_year"][str(req.year)] = new_entry
    JSON_PATH.write_text(json.dumps(data, indent=2))
    reload_taxes()

    return {"ok": True, "year": req.year, "entry": new_entry}


@app.post("/admin/add-state-override", dependencies=[Depends(_verify_admin)])
def admin_add_state_override(req: StateOverrideRequest):
    data = json.loads(JSON_PATH.read_text())
    overrides = data.setdefault("state_overrides_by_year", {})
    year_block = overrides.setdefault(str(req.year), {})

    entry: dict = {
        "name": req.name,
        "type": req.type,
        "rate": req.rate,
        "std_deduction_single": req.std_deduction_single,
        "std_deduction_married": req.std_deduction_married,
        "personal_exemption_single": req.personal_exemption_single,
        "personal_exemption_married": req.personal_exemption_married,
        "notes": req.notes,
    }
    if req.brackets_single:
        entry["brackets_single"] = req.brackets_single
    if req.brackets_married:
        entry["brackets_married"] = req.brackets_married

    year_block[req.state] = entry
    JSON_PATH.write_text(json.dumps(data, indent=2))
    reload_taxes()

    return {"ok": True, "year": req.year, "state": req.state}


# ── Static files ───────────────────────────────────────────────────────────────

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/guide")
def guide():
    return FileResponse(STATIC_DIR / "guide.html")


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)