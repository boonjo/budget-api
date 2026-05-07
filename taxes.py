"""
Federal and state income tax data + calculation logic.

Federal rate data lives in tax_rates.json — edit that file to add a new year.
State overrides for mid-year transitions live in STATE_OVERRIDES_BY_YEAR below.

To add a new tax year: see the _how_to_add_a_new_year instructions in tax_rates.json.
"""

from __future__ import annotations
import json
import math
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

FilingStatus = Literal["single", "married_jointly", "married_separately", "head_of_household"]

# ── Shared JSON path ──────────────────────────────────────────────────────────

JSON_PATH = Path(__file__).parent / "tax_rates.json"

# ── Federal rate data — loaded from tax_rates.json ────────────────────────────

def _load_federal() -> dict[int, dict]:
    raw  = json.loads(JSON_PATH.read_text())
    result: dict[int, dict] = {}
    for year_str, entry in raw["federal_by_year"].items():
        if year_str.startswith("_"):
            continue
        year = int(year_str)
        brackets: dict[str, list[tuple[float, float]]] = {}
        for filing, pairs in entry["brackets"].items():
            brackets[filing] = [
                (math.inf if upper is None else float(upper), float(rate))
                for upper, rate in pairs
            ]
        result[year] = {
            "brackets":           brackets,
            "standard_deduction": {k: float(v) for k, v in entry["standard_deduction"].items()},
            "ss_wage_base":       float(entry["ss_wage_base"]),
        }
    return result

def _load_state_overrides() -> dict[int, dict[str, StateInfo]]:
    raw = json.loads(JSON_PATH.read_text())
    result: dict[int, dict[str, StateInfo]] = {}
    for year_str, states in raw.get("state_overrides_by_year", {}).items():
        if year_str.startswith("_"):
            continue
        year = int(year_str)
        result[year] = {}
        for code, s in states.items():
            bs = [
                (float("inf") if u is None else float(u), float(r))
                for u, r in s.get("brackets_single", [])
            ]
            bm = [
                (float("inf") if u is None else float(u), float(r))
                for u, r in s.get("brackets_married", [])
            ]
            result[year][code] = StateInfo(
                name=s["name"],
                type=s["type"],
                rate=float(s.get("rate", 0.0)),
                brackets_single=_prog(bs) if bs else [],
                brackets_married=_prog(bm) if bm else [],
                std_deduction_single=float(s.get("std_deduction_single", 0.0)),
                std_deduction_married=float(s.get("std_deduction_married", 0.0)),
                personal_exemption_single=float(s.get("personal_exemption_single", 0.0)),
                personal_exemption_married=float(s.get("personal_exemption_married", 0.0)),
                notes=s.get("notes", ""),
            )
    return result


FEDERAL_BY_YEAR: dict[int, dict] = _load_federal()

# FICA rates — unchanged year-over-year
SS_RATE            = 0.0620
MEDICARE_RATE      = 0.0145
ADD_MEDICARE_RATE  = 0.0090
ADD_MEDICARE_THRESHOLD: dict[FilingStatus, float] = {
    "single":              200_000,
    "married_jointly":     250_000,
    "married_separately":  125_000,
    "head_of_household":   200_000,
}


def resolve_tax_year(requested: int | None = None) -> tuple[int, bool]:
    """Return (year_to_use, is_current_year).

    If the requested year (or current calendar year) exceeds our data,
    we use the latest available year and return is_current_year=False.
    """
    current = requested or datetime.now().year
    available = sorted(FEDERAL_BY_YEAR.keys())
    if current > max(available):
        return max(available), False
    if current < min(available):
        return min(available), False
    return current, True


# ── State data ─────────────────────────────────────────────────────────────────

@dataclass
class StateInfo:
    name: str
    type: Literal["none", "flat", "progressive"]
    rate: float = 0.0
    brackets_single:             list[tuple[float, float]] = field(default_factory=list)
    brackets_married:            list[tuple[float, float]] = field(default_factory=list)
    std_deduction_single:        float = 0.0
    std_deduction_married:       float = 0.0
    personal_exemption_single:   float = 0.0
    personal_exemption_married:  float = 0.0
    notes: str = ""


def _prog(brackets: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if brackets and brackets[-1][0] != float("inf"):
        brackets[-1] = (float("inf"), brackets[-1][1])
    return brackets


# Base state data (earliest known rates — 2024 baseline)
STATES: dict[str, StateInfo] = {
    # ── No income tax ──────────────────────────────────────────────────────────
    "AK": StateInfo("Alaska",        "none"),
    "FL": StateInfo("Florida",       "none"),
    "NV": StateInfo("Nevada",        "none"),
    "NH": StateInfo("New Hampshire", "none",
                    notes="No tax on wages; dividend/interest tax fully repealed 2025"),
    "SD": StateInfo("South Dakota",  "none"),
    "TN": StateInfo("Tennessee",     "none"),
    "TX": StateInfo("Texas",         "none"),
    "WA": StateInfo("Washington",    "none",
                    notes="7% capital gains tax on gains >$262k; no wage income tax"),
    "WY": StateInfo("Wyoming",       "none"),

    # ── Flat rate ──────────────────────────────────────────────────────────────
    "AZ": StateInfo("Arizona",       "flat", rate=0.025,
                    std_deduction_single=14_600, std_deduction_married=29_200,
                    notes="2.5% flat (effective 2023)"),
    "CO": StateInfo("Colorado",      "flat", rate=0.044,
                    std_deduction_single=14_600, std_deduction_married=29_200),
    "GA": StateInfo("Georgia",       "flat", rate=0.0549,
                    std_deduction_single=5_400,  std_deduction_married=7_100,
                    notes="Phasing down annually toward 4.99%"),
    "ID": StateInfo("Idaho",         "flat", rate=0.05695,
                    std_deduction_single=14_600, std_deduction_married=29_200),
    "IL": StateInfo("Illinois",      "flat", rate=0.0495,
                    personal_exemption_single=2_425, personal_exemption_married=4_850),
    "IN": StateInfo("Indiana",       "flat", rate=0.0305,
                    personal_exemption_single=1_000, personal_exemption_married=2_000,
                    notes="County taxes 0.5–2.9% not included"),
    "KY": StateInfo("Kentucky",      "flat", rate=0.04,
                    std_deduction_single=3_160,  std_deduction_married=3_160),
    "MA": StateInfo("Massachusetts", "flat", rate=0.05,
                    personal_exemption_single=4_400, personal_exemption_married=8_800,
                    notes="4% surtax on income >$1M"),
    "MI": StateInfo("Michigan",      "flat", rate=0.0425,
                    personal_exemption_single=5_400, personal_exemption_married=10_800,
                    notes="Local taxes up to 2.4% not included"),
    "MS": StateInfo("Mississippi",   "flat", rate=0.047,
                    std_deduction_single=2_300,  std_deduction_married=4_600,
                    personal_exemption_single=6_000, personal_exemption_married=12_000,
                    notes="First $10k exempt; phasing down annually"),
    "NC": StateInfo("North Carolina","flat", rate=0.045,
                    std_deduction_single=12_750, std_deduction_married=25_500,
                    notes="Phasing down toward 3.99%"),
    "PA": StateInfo("Pennsylvania",  "flat", rate=0.0307,
                    notes="Local earned income taxes 1–3% not included"),
    "UT": StateInfo("Utah",          "flat", rate=0.0465),

    # ── Progressive ───────────────────────────────────────────────────────────
    "AL": StateInfo("Alabama", "progressive",
        brackets_single=_prog([(500,0.02),(3_000,0.04),(float("inf"),0.05)]),
        brackets_married=_prog([(1_000,0.02),(6_000,0.04),(float("inf"),0.05)]),
        std_deduction_single=2_500, std_deduction_married=7_500,
        personal_exemption_single=1_500, personal_exemption_married=3_000),
    "AR": StateInfo("Arkansas", "progressive",
        brackets_single=_prog([(4_300,0.02),(8_500,0.04),(float("inf"),0.044)]),
        brackets_married=_prog([(4_300,0.02),(8_500,0.04),(float("inf"),0.044)]),
        std_deduction_single=2_340, std_deduction_married=4_680,
        personal_exemption_single=29, personal_exemption_married=58),
    "CA": StateInfo("California", "progressive",
        brackets_single=_prog([(10_412,0.01),(24_684,0.02),(38_959,0.04),(54_081,0.06),(68_350,0.08),(349_137,0.093),(418_961,0.103),(698_274,0.113),(float("inf"),0.123)]),
        brackets_married=_prog([(20_824,0.01),(49_368,0.02),(77_918,0.04),(108_162,0.06),(136_700,0.08),(698_274,0.093),(837_922,0.103),(1_000_000,0.113),(float("inf"),0.123)]),
        std_deduction_single=5_202, std_deduction_married=10_404,
        notes="1% SDI; 1% Mental Health Services Tax on income >$1M"),
    "CT": StateInfo("Connecticut", "progressive",
        brackets_single=_prog([(10_000,0.03),(50_000,0.05),(100_000,0.055),(200_000,0.06),(250_000,0.065),(500_000,0.069),(float("inf"),0.0699)]),
        brackets_married=_prog([(20_000,0.03),(100_000,0.05),(200_000,0.055),(400_000,0.06),(500_000,0.065),(1_000_000,0.069),(float("inf"),0.0699)]),
        personal_exemption_single=15_000, personal_exemption_married=24_000,
        notes="Personal exemption phases out at higher incomes"),
    "DC": StateInfo("District of Columbia", "progressive",
        brackets_single=_prog([(10_000,0.04),(40_000,0.06),(60_000,0.065),(250_000,0.085),(500_000,0.0925),(1_000_000,0.0975),(float("inf"),0.1075)]),
        brackets_married=_prog([(10_000,0.04),(40_000,0.06),(60_000,0.065),(250_000,0.085),(500_000,0.0925),(1_000_000,0.0975),(float("inf"),0.1075)]),
        std_deduction_single=14_600, std_deduction_married=29_200),
    "DE": StateInfo("Delaware", "progressive",
        brackets_single=_prog([(2_000,0.00),(5_000,0.022),(10_000,0.039),(20_000,0.048),(25_000,0.052),(60_000,0.0555),(float("inf"),0.066)]),
        brackets_married=_prog([(2_000,0.00),(5_000,0.022),(10_000,0.039),(20_000,0.048),(25_000,0.052),(60_000,0.0555),(float("inf"),0.066)]),
        std_deduction_single=3_250, std_deduction_married=6_500,
        personal_exemption_single=110, personal_exemption_married=220),
    "HI": StateInfo("Hawaii", "progressive",
        brackets_single=_prog([(2_400,0.014),(4_800,0.032),(9_600,0.055),(14_400,0.064),(19_200,0.068),(24_000,0.072),(36_000,0.076),(48_000,0.079),(150_000,0.0825),(175_000,0.09),(200_000,0.10),(float("inf"),0.11)]),
        brackets_married=_prog([(4_800,0.014),(9_600,0.032),(19_200,0.055),(28_800,0.064),(38_400,0.068),(48_000,0.072),(72_000,0.076),(96_000,0.079),(300_000,0.0825),(350_000,0.09),(400_000,0.10),(float("inf"),0.11)]),
        std_deduction_single=2_200, std_deduction_married=4_400,
        personal_exemption_single=1_144, personal_exemption_married=2_288),
    "IA": StateInfo("Iowa", "progressive",
        brackets_single=_prog([(6_000,0.044),(30_000,0.0482),(float("inf"),0.057)]),
        brackets_married=_prog([(6_000,0.044),(30_000,0.0482),(float("inf"),0.057)]),
        std_deduction_single=14_600, std_deduction_married=29_200,
        notes="Transitioning to 3.8% flat; see 2025 override"),
    "KS": StateInfo("Kansas", "progressive",
        brackets_single=_prog([(15_000,0.031),(30_000,0.0525),(float("inf"),0.057)]),
        brackets_married=_prog([(30_000,0.031),(60_000,0.0525),(float("inf"),0.057)]),
        std_deduction_single=3_500, std_deduction_married=8_000,
        personal_exemption_single=2_250, personal_exemption_married=4_500),
    "LA": StateInfo("Louisiana", "progressive",
        brackets_single=_prog([(12_500,0.0185),(50_000,0.035),(float("inf"),0.0425)]),
        brackets_married=_prog([(25_000,0.0185),(100_000,0.035),(float("inf"),0.0425)]),
        personal_exemption_single=4_500, personal_exemption_married=9_000),
    "ME": StateInfo("Maine", "progressive",
        brackets_single=_prog([(24_500,0.058),(58_050,0.0675),(float("inf"),0.0715)]),
        brackets_married=_prog([(49_050,0.058),(116_100,0.0675),(float("inf"),0.0715)]),
        std_deduction_single=14_600, std_deduction_married=29_200,
        personal_exemption_single=4_700, personal_exemption_married=9_400),
    "MD": StateInfo("Maryland", "progressive",
        brackets_single=_prog([(1_000,0.02),(2_000,0.03),(3_000,0.04),(100_000,0.0475),(125_000,0.05),(150_000,0.0525),(250_000,0.055),(float("inf"),0.0575)]),
        brackets_married=_prog([(1_000,0.02),(2_000,0.03),(3_000,0.04),(150_000,0.0475),(175_000,0.05),(225_000,0.0525),(300_000,0.055),(float("inf"),0.0575)]),
        std_deduction_single=2_500, std_deduction_married=5_000,
        personal_exemption_single=3_200, personal_exemption_married=6_400,
        notes="County/city taxes 2.25–3.2% not included"),
    "MN": StateInfo("Minnesota", "progressive",
        brackets_single=_prog([(31_690,0.0535),(104_090,0.068),(193_240,0.0785),(float("inf"),0.0985)]),
        brackets_married=_prog([(46_330,0.0535),(184_040,0.068),(321_450,0.0785),(float("inf"),0.0985)]),
        std_deduction_single=14_575, std_deduction_married=29_150),
    "MO": StateInfo("Missouri", "progressive",
        brackets_single=_prog([(1_121,0.015),(2_242,0.02),(3_363,0.025),(4_484,0.03),(5_605,0.035),(6_726,0.04),(7_847,0.045),(8_968,0.05),(float("inf"),0.048)]),
        brackets_married=_prog([(1_121,0.015),(2_242,0.02),(3_363,0.025),(4_484,0.03),(5_605,0.035),(6_726,0.04),(7_847,0.045),(8_968,0.05),(float("inf"),0.048)]),
        std_deduction_single=14_600, std_deduction_married=29_200,
        personal_exemption_single=2_100, personal_exemption_married=4_200,
        notes="Top rate phasing down annually"),
    "MT": StateInfo("Montana", "progressive",
        brackets_single=_prog([(3_600,0.01),(6_300,0.02),(9_700,0.03),(13_000,0.04),(16_800,0.05),(21_600,0.06),(float("inf"),0.069)]),
        brackets_married=_prog([(3_600,0.01),(6_300,0.02),(9_700,0.03),(13_000,0.04),(16_800,0.05),(21_600,0.06),(float("inf"),0.069)]),
        std_deduction_single=5_540, std_deduction_married=11_080,
        personal_exemption_single=2_880, personal_exemption_married=5_760),
    "NE": StateInfo("Nebraska", "progressive",
        brackets_single=_prog([(3_700,0.0246),(22_170,0.0351),(35_730,0.0501),(float("inf"),0.0584)]),
        brackets_married=_prog([(7_390,0.0246),(44_350,0.0351),(71_460,0.0501),(float("inf"),0.0584)]),
        std_deduction_single=7_900, std_deduction_married=15_800,
        personal_exemption_single=157, personal_exemption_married=314,
        notes="Top rate reducing to 3.99% by 2027"),
    "NJ": StateInfo("New Jersey", "progressive",
        brackets_single=_prog([(20_000,0.014),(35_000,0.0175),(40_000,0.035),(75_000,0.05525),(500_000,0.0637),(1_000_000,0.0897),(float("inf"),0.1075)]),
        brackets_married=_prog([(20_000,0.014),(50_000,0.0175),(70_000,0.0245),(80_000,0.035),(150_000,0.05525),(500_000,0.0637),(1_000_000,0.0897),(float("inf"),0.1075)]),
        personal_exemption_single=1_000, personal_exemption_married=2_000),
    "NM": StateInfo("New Mexico", "progressive",
        brackets_single=_prog([(5_500,0.017),(11_000,0.032),(16_000,0.047),(210_000,0.049),(float("inf"),0.059)]),
        brackets_married=_prog([(8_000,0.017),(16_000,0.032),(24_000,0.047),(315_000,0.049),(float("inf"),0.059)]),
        std_deduction_single=14_600, std_deduction_married=29_200,
        personal_exemption_single=4_000, personal_exemption_married=8_000),
    "NY": StateInfo("New York", "progressive",
        brackets_single=_prog([(17_150,0.04),(23_600,0.045),(27_900,0.0525),(161_550,0.0585),(323_200,0.0625),(2_155_350,0.0685),(5_000_000,0.0965),(25_000_000,0.103),(float("inf"),0.109)]),
        brackets_married=_prog([(17_150,0.04),(23_600,0.045),(27_900,0.0525),(323_200,0.0585),(2_155_350,0.0625),(5_000_000,0.0685),(25_000_000,0.0965),(float("inf"),0.103)]),
        std_deduction_single=8_000, std_deduction_married=16_050,
        notes="NYC residents add 3.078–3.876%; Yonkers add 1.477%"),
    "OH": StateInfo("Ohio", "progressive",
        brackets_single=_prog([(26_050,0.00),(100_000,0.02765),(float("inf"),0.03990)]),
        brackets_married=_prog([(26_050,0.00),(100_000,0.02765),(float("inf"),0.03990)]),
        notes="Municipal taxes 1–2.5% not included"),
    "OK": StateInfo("Oklahoma", "progressive",
        brackets_single=_prog([(1_000,0.0025),(2_500,0.0075),(3_750,0.0175),(4_900,0.0275),(7_200,0.0375),(float("inf"),0.0475)]),
        brackets_married=_prog([(2_000,0.0025),(5_000,0.0075),(7_500,0.0175),(9_800,0.0275),(12_200,0.0375),(float("inf"),0.0475)]),
        std_deduction_single=6_350, std_deduction_married=12_700,
        personal_exemption_single=1_000, personal_exemption_married=2_000),
    "OR": StateInfo("Oregon", "progressive",
        brackets_single=_prog([(4_050,0.0475),(10_200,0.0675),(125_000,0.0875),(float("inf"),0.099)]),
        brackets_married=_prog([(8_100,0.0475),(20_400,0.0675),(250_000,0.0875),(float("inf"),0.099)]),
        std_deduction_single=2_420, std_deduction_married=4_865,
        personal_exemption_single=236, personal_exemption_married=472),
    "RI": StateInfo("Rhode Island", "progressive",
        brackets_single=_prog([(73_450,0.0375),(166_950,0.0475),(float("inf"),0.0599)]),
        brackets_married=_prog([(73_450,0.0375),(166_950,0.0475),(float("inf"),0.0599)]),
        std_deduction_single=10_550, std_deduction_married=21_150,
        personal_exemption_single=4_750, personal_exemption_married=9_500),
    "SC": StateInfo("South Carolina", "progressive",
        brackets_single=_prog([(3_460,0.00),(17_330,0.03),(float("inf"),0.064)]),
        brackets_married=_prog([(3_460,0.00),(17_330,0.03),(float("inf"),0.064)]),
        std_deduction_single=14_600, std_deduction_married=29_200,
        personal_exemption_single=4_610, personal_exemption_married=9_220,
        notes="Top rate reducing annually"),
    "VA": StateInfo("Virginia", "progressive",
        brackets_single=_prog([(3_000,0.02),(5_000,0.03),(17_000,0.05),(float("inf"),0.0575)]),
        brackets_married=_prog([(3_000,0.02),(5_000,0.03),(17_000,0.05),(float("inf"),0.0575)]),
        std_deduction_single=8_000, std_deduction_married=16_000,
        personal_exemption_single=930, personal_exemption_married=1_860),
    "VT": StateInfo("Vermont", "progressive",
        brackets_single=_prog([(45_400,0.0335),(110_050,0.066),(229_550,0.076),(float("inf"),0.0875)]),
        brackets_married=_prog([(75_850,0.0335),(183_400,0.066),(279_450,0.076),(float("inf"),0.0875)]),
        std_deduction_single=6_500, std_deduction_married=13_050,
        personal_exemption_single=4_500, personal_exemption_married=9_000),
    "WI": StateInfo("Wisconsin", "progressive",
        brackets_single=_prog([(14_320,0.0354),(28_640,0.0465),(315_310,0.053),(float("inf"),0.0765)]),
        brackets_married=_prog([(19_090,0.0354),(38_190,0.0465),(420_420,0.053),(float("inf"),0.0765)]),
        std_deduction_single=13_230, std_deduction_married=24_490,
        personal_exemption_single=700, personal_exemption_married=1_400),
    "WV": StateInfo("West Virginia", "progressive",
        brackets_single=_prog([(10_000,0.0236),(25_000,0.0315),(40_000,0.0354),(60_000,0.0472),(float("inf"),0.0512)]),
        brackets_married=_prog([(10_000,0.0236),(25_000,0.0315),(40_000,0.0354),(60_000,0.0472),(float("inf"),0.0512)])),
}

# ── State overrides by year — loaded from tax_rates.json ──────────────────────

STATE_OVERRIDES_BY_YEAR: dict[int, dict[str, StateInfo]] = _load_state_overrides()


def reload() -> None:
    """Reload all tax data from tax_rates.json without restarting the server."""
    global FEDERAL_BY_YEAR, STATE_OVERRIDES_BY_YEAR
    FEDERAL_BY_YEAR = _load_federal()
    STATE_OVERRIDES_BY_YEAR = _load_state_overrides()


def get_state_info(code: str, year: int) -> StateInfo | None:
    """Return the correct StateInfo for a state in a given tax year."""
    # Walk years from requested down to find the most recent override
    for y in sorted(STATE_OVERRIDES_BY_YEAR.keys(), reverse=True):
        if y <= year and code in STATE_OVERRIDES_BY_YEAR[y]:
            return STATE_OVERRIDES_BY_YEAR[y][code]
    return STATES.get(code)


# ── Calculation functions ──────────────────────────────────────────────────────

def apply_brackets(taxable: float, brackets: list[tuple[float, float]]) -> float:
    tax = 0.0
    prev = 0.0
    for upper, rate in brackets:
        if taxable <= prev:
            break
        chunk = min(taxable, upper) - prev
        tax += chunk * rate
        prev = upper
    return tax


def marginal_rate(taxable: float, brackets: list[tuple[float, float]]) -> float:
    for upper, rate in brackets:
        if taxable <= upper:
            return rate
    return brackets[-1][1]


def calc_federal(gross: float, filing: FilingStatus, year: int) -> dict:
    data     = FEDERAL_BY_YEAR[year]
    std_ded  = data["standard_deduction"][filing]
    taxable  = max(0.0, gross - std_ded)
    brackets = data["brackets"][filing]
    tax      = apply_brackets(taxable, brackets)
    marg     = marginal_rate(taxable, brackets)
    return {
        "tax":                round(tax, 2),
        "taxable_income":     round(taxable, 2),
        "standard_deduction": std_ded,
        "marginal_rate":      marg,
        "effective_rate":     round(tax / gross, 6) if gross else 0,
    }


def calc_fica(gross: float, filing: FilingStatus, year: int) -> dict:
    ss_base = FEDERAL_BY_YEAR[year]["ss_wage_base"]
    ss      = min(gross, ss_base) * SS_RATE
    med     = gross * MEDICARE_RATE
    add_med = max(0.0, gross - ADD_MEDICARE_THRESHOLD[filing]) * ADD_MEDICARE_RATE
    total   = ss + med + add_med
    return {
        "social_security":      round(ss, 2),
        "medicare":             round(med, 2),
        "additional_medicare":  round(add_med, 2),
        "total":                round(total, 2),
    }


def calc_state(gross: float, filing: FilingStatus, state_code: str, year: int) -> dict:
    code = state_code.upper()
    st   = get_state_info(code, year)
    if st is None:
        return {"tax": 0, "effective_rate": 0, "marginal_rate": 0, "notes": "Unknown state"}

    if st.type == "none":
        return {"tax": 0, "effective_rate": 0, "marginal_rate": 0,
                "name": st.name, "notes": st.notes}

    is_married = filing == "married_jointly"
    std_ded  = st.std_deduction_married  if is_married else st.std_deduction_single
    pers_ex  = st.personal_exemption_married if is_married else st.personal_exemption_single
    extra_ex = 10_000 if code == "MS" else 0  # first $10k exempt in MS
    taxable  = max(0.0, gross - std_ded - pers_ex - extra_ex)

    if st.type == "flat":
        tax  = taxable * st.rate
        marg = st.rate
    else:
        brackets = st.brackets_married if is_married else st.brackets_single
        tax  = apply_brackets(taxable, brackets)
        marg = marginal_rate(taxable, brackets)

    return {
        "tax":           round(tax, 2),
        "taxable_income": round(taxable, 2),
        "effective_rate": round(tax / gross, 6) if gross else 0,
        "marginal_rate": marg,
        "name":          st.name,
        "notes":         st.notes,
    }


def calc_all_taxes(gross: float, filing: FilingStatus, state_code: str,
                   requested_year: int | None = None) -> dict:
    year, is_current = resolve_tax_year(requested_year)
    fed   = calc_federal(gross, filing, year)
    fica  = calc_fica(gross, filing, year)
    state = calc_state(gross, filing, state_code, year)

    total = fed["tax"] + fica["total"] + state["tax"]
    net   = gross - total

    return {
        "gross":            round(gross, 2),
        "federal":          fed,
        "fica":             fica,
        "state":            state,
        "total_tax":        round(total, 2),
        "effective_rate":   round(total / gross, 6) if gross else 0,
        "net_annual":       round(net, 2),
        "net_monthly":      round(net / 12, 2),
        "gross_monthly":    round(gross / 12, 2),
        "tax_year":         year,
        "rates_current":    is_current,
        "latest_year_available": max(FEDERAL_BY_YEAR.keys()),
    }


def state_list() -> list[dict]:
    return sorted(
        [{"code": k, "name": v.name, "type": v.type} for k, v in STATES.items()],
        key=lambda x: x["name"],
    )
