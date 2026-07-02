#!/usr/bin/env python3
"""
POE2 currency exchange arbitrage checker / planner.

Features:
- Pulls POE2 Scout SnapshotPairs for POE2.
- Finds 3-leg arbitrage routes.
- Starts and ends in your budget currency.
- Uses integer-only quantities.
- Caps route size by aggregate stock.
- Supports configurable gold-cost estimates.
- Supports manual whole-number route ladder mode.
- Adds liquidity-aware filtering:
    - max middle units
    - min exit row volume
    - max liquidity use
    - liquidity-adjusted ranking

Important:
- This does NOT automate the game client.
- This does NOT place trades.
- This does NOT read memory, screens, or game files.
- This uses aggregate market data, not a live order book.
- Always manually verify every leg in-game before buying anything.
"""

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


DEFAULT_BASE = "https://poe2scout.com/api"
DEFAULT_REALM = "poe2"
DEFAULT_LEAGUE = "runes"

DEFAULT_CORE_CURRENCIES = {
    "Divine Orb",
    "Exalted Orb",
    "Chaos Orb",
}

DEFAULT_ALLOWED_CATEGORIES = {
    "currency",
}

DEFAULT_EXCLUDE_NAME_RE = re.compile(
    r"(Uncut|Level \d+|Waystone|Tablet|Soul Core)",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="POE2 currency exchange arbitrage checker / planner"
    )

    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--realm", default=DEFAULT_REALM)
    parser.add_argument("--league", default=DEFAULT_LEAGUE)

    parser.add_argument(
        "--budget-currency",
        default="Divine Orb",
        help='Currency you are starting with, e.g. "Divine Orb", "Exalted Orb", "Chaos Orb"',
    )
    parser.add_argument(
        "--budget-amount",
        type=float,
        default=200.0,
        help="Maximum amount of budget currency to spend",
    )

    parser.add_argument(
        "--min-edge",
        type=float,
        default=0.08,
        help="Minimum gross theoretical edge as decimal. Example: 0.08 = 8%%",
    )
    parser.add_argument(
        "--fee-buffer",
        type=float,
        default=0.03,
        help="Extra buffer for slippage/stale prices. Example: 0.03 = 3%%",
    )
    parser.add_argument(
        "--min-row-volume",
        type=float,
        default=50.0,
        help="Minimum aggregate row volume for a pair to be considered",
    )
    parser.add_argument(
        "--min-side-traded",
        type=float,
        default=5.0,
        help="Minimum volumeTraded on both sides of a pair",
    )
    parser.add_argument(
        "--min-stock",
        type=float,
        default=3.0,
        help="Minimum highestStock on both sides of a pair",
    )
    parser.add_argument(
        "--min-integer-profit",
        type=float,
        default=1.0,
        help="Minimum integer-plan profit in budget currency",
    )

    parser.add_argument(
        "--core",
        action="append",
        default=[],
        help='Settlement currency to include. Can be repeated. Example: --core "Divine Orb" --core "Exalted Orb"',
    )
    parser.add_argument(
        "--allow-category",
        action="append",
        default=[],
        help='Allowed middle item category. Can be repeated. Default: "currency"',
    )
    parser.add_argument(
        "--include-weird",
        action="store_true",
        help="Disable name exclusions for things like Uncut gems, Waystones, Tablets, etc.",
    )
    parser.add_argument(
        "--ignore-stock-cap",
        action="store_true",
        help="Do not cap suggested spend by aggregate highestStock fields",
    )

    # Gold model
    parser.add_argument(
        "--max-gold",
        type=float,
        default=None,
        help="Maximum gold you are willing to spend for the entire route",
    )
    parser.add_argument(
        "--gold-profile",
        default=None,
        help="Path to JSON file containing gold cost rules",
    )
    parser.add_argument(
        "--gold-per-trade",
        type=float,
        default=0.0,
        help="Default fixed gold cost per exchange leg",
    )
    parser.add_argument(
        "--gold-per-input-unit",
        type=float,
        default=0.0,
        help="Default gold cost per input unit spent on each leg",
    )
    parser.add_argument(
        "--gold-per-output-unit",
        type=float,
        default=0.0,
        help="Default gold cost per output unit received on each leg",
    )
    parser.add_argument(
        "--gold-multiplier",
        type=float,
        default=1.0,
        help="Multiplier applied to computed gold costs",
    )

    # Liquidity model
    parser.add_argument(
        "--max-middle-units",
        type=int,
        default=None,
        help="Reject routes that require more than this many units of the middle item.",
    )
    parser.add_argument(
        "--min-exit-row-volume",
        type=float,
        default=None,
        help="Reject routes where the exit leg row volume is below this value.",
    )
    parser.add_argument(
        "--max-liquidity-use",
        type=float,
        default=None,
        help="Reject routes that consume more than this fraction of apparent exit-side stock. Example: 0.25 = 25%%",
    )
    parser.add_argument(
        "--prefer-liquid",
        action="store_true",
        help="Sort by liquidity-adjusted score instead of raw profit.",
    )

    parser.add_argument(
        "--sort",
        choices=["profit", "profit_pct", "profit_per_gold", "liquidity"],
        default="profit",
        help="How to sort opportunities",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=3,
        help="Number of opportunities to print",
    )

    # Manual route ladder mode
    parser.add_argument(
        "--route",
        default=None,
        help='Manually simulate one route, e.g. "Divine Orb>Exalted Orb>Artificer\'s Orb>Divine Orb"',
    )
    parser.add_argument(
        "--amounts",
        default="1,5,10,12,13,20,50,100,200",
        help='Comma-separated starting amounts for --route, e.g. "1,5,10,12,13,20"',
    )
    parser.add_argument(
        "--rate-haircut",
        type=float,
        default=0.0,
        help="Apply safety haircut to every rate. Example: 0.02 assumes rates are 2%% worse.",
    )

    parser.add_argument(
        "--debug-first-row",
        action="store_true",
        help="Print first API row after camelCase normalization",
    )
    parser.add_argument(
        "--debug-rejections",
        action="store_true",
        help="Print rejection counts for liquidity filters.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of human output",
    )
    parser.add_argument(
        "--snapshot-file",
        default=None,
        help="Use a local SnapShotPairs JSON file instead of fetching from the API",
    )

    return parser.parse_args()


def to_camel_key(key: Any) -> Any:
    if not isinstance(key, str):
        return key

    if "_" not in key and "-" not in key and " " not in key:
        return key[:1].lower() + key[1:]

    parts = re.split(r"[_\-\s]+", key)
    first = parts[0].lower()
    rest = [p[:1].upper() + p[1:] for p in parts[1:] if p]
    return first + "".join(rest)


def camelize(obj: Any) -> Any:
    if isinstance(obj, list):
        return [camelize(x) for x in obj]

    if isinstance(obj, dict):
        return {to_camel_key(k): camelize(v) for k, v in obj.items()}

    return obj


def num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def get_json(base: str, path: str) -> Any:
    url = base.rstrip("/") + path

    headers = {
        "User-Agent": "poe2-arb-checker/0.5 contact:lyxcrit@gmail.com",
        "Accept": "application/json",
    }

    response = requests.get(url, headers=headers, timeout=30)

    if not response.ok:
        print(
            f"HTTP error from {url}: {response.status_code} {response.text[:500]}",
            file=sys.stderr,
        )
        response.raise_for_status()

    return camelize(response.json())


def unwrap_rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        for key in ("data", "items", "results", "rows"):
            if isinstance(payload.get(key), list):
                return payload[key]

    raise ValueError(
        f"Could not find list rows in payload. "
        f"type={type(payload)} keys={list(payload)[:20] if isinstance(payload, dict) else ''}"
    )


def currency_name(item: Dict[str, Any]) -> Optional[str]:
    if not isinstance(item, dict):
        return None

    metadata = item.get("itemMetadata") or {}

    return (
        item.get("text")
        or item.get("name")
        or metadata.get("name")
        or item.get("apiId")
    )


def currency_api_id(item: Dict[str, Any]) -> Optional[str]:
    if not isinstance(item, dict):
        return None
    return item.get("apiId")


def currency_category(item: Dict[str, Any]) -> Optional[str]:
    if not isinstance(item, dict):
        return None
    return item.get("categoryApiId")


def is_excluded_name(name: Optional[str], include_weird: bool) -> bool:
    if include_weird:
        return False
    return bool(name and DEFAULT_EXCLUDE_NAME_RE.search(name))


def load_gold_profile(args: argparse.Namespace) -> Dict[str, Any]:
    profile = {
        "default_per_trade": args.gold_per_trade,
        "default_per_input_unit": args.gold_per_input_unit,
        "default_per_output_unit": args.gold_per_output_unit,
        "multiplier": args.gold_multiplier,
        "per_trade": {},
        "per_input_unit": {},
        "per_output_unit": {},
    }

    if not args.gold_profile:
        return profile

    profile_path = Path(args.gold_profile)
    if not profile_path.is_file():
        raise SystemExit(f"error: --gold-profile file not found: {profile_path}")

    try:
        with profile_path.open("r", encoding="utf-8") as f:
            user_profile = json.load(f)
    except json.JSONDecodeError as e:
        raise SystemExit(
            f"error: --gold-profile is not valid JSON: {profile_path}: {e}"
        ) from e

    for key in (
        "default_per_trade",
        "default_per_input_unit",
        "default_per_output_unit",
        "multiplier",
    ):
        if key in user_profile:
            profile[key] = float(user_profile[key])

    for key in ("per_trade", "per_input_unit", "per_output_unit"):
        if key in user_profile and isinstance(user_profile[key], dict):
            profile[key].update(
                {str(k): float(v) for k, v in user_profile[key].items()}
            )

    return profile


def gold_model_enabled(args: argparse.Namespace, profile: Dict[str, Any]) -> bool:
    if args.max_gold is not None:
        return True

    if profile["default_per_trade"] > 0:
        return True
    if profile["default_per_input_unit"] > 0:
        return True
    if profile["default_per_output_unit"] > 0:
        return True
    if profile["per_trade"]:
        return True
    if profile["per_input_unit"]:
        return True
    if profile["per_output_unit"]:
        return True

    return False


def estimate_leg_gold(
    src: str,
    dst: str,
    input_amount: float,
    output_amount: float,
    profile: Dict[str, Any],
) -> float:
    pair_key = f"{src}->{dst}"

    fixed = profile["per_trade"].get(pair_key, profile["default_per_trade"])
    per_input = profile["per_input_unit"].get(src, profile["default_per_input_unit"])
    per_output = profile["per_output_unit"].get(dst, profile["default_per_output_unit"])

    total = fixed + (input_amount * per_input) + (output_amount * per_output)
    return total * profile.get("multiplier", 1.0)


def row_quality_ok(
    row: Dict[str, Any],
    min_row_volume: float,
    min_side_traded: float,
    min_stock: float,
    include_weird: bool,
) -> Tuple[bool, str]:
    c1 = row.get("currencyOne") or {}
    c2 = row.get("currencyTwo") or {}
    d1 = row.get("currencyOneData") or {}
    d2 = row.get("currencyTwoData") or {}

    n1 = currency_name(c1)
    n2 = currency_name(c2)

    if not n1 or not n2:
        return False, "missing name"

    if is_excluded_name(n1, include_weird) or is_excluded_name(n2, include_weird):
        return False, "excluded name"

    row_volume = num(row.get("volume"))
    if row_volume < min_row_volume:
        return False, f"low row volume {row_volume}"

    v1 = num(d1.get("volumeTraded"))
    v2 = num(d2.get("volumeTraded"))
    if v1 < min_side_traded or v2 < min_side_traded:
        return False, f"low side traded {v1}/{v2}"

    s1 = num(d1.get("highestStock"))
    s2 = num(d2.get("highestStock"))
    if s1 < min_stock or s2 < min_stock:
        return False, f"low stock {s1}/{s2}"

    return True, "ok"


def add_edge(
    graph: Dict[str, Dict[str, Dict[str, Any]]],
    src: str,
    dst: str,
    rate: float,
    meta: Dict[str, Any],
) -> None:
    if not src or not dst or rate <= 0:
        return

    graph.setdefault(src, {})

    current = graph[src].get(dst)

    # Keep the edge with higher row volume if duplicates appear.
    if current and current["row_volume"] >= meta["row_volume"]:
        return

    graph[src][dst] = {
        "rate": rate,
        **meta,
    }


def build_graph(
    rows: List[Dict[str, Any]],
    args: argparse.Namespace,
) -> Tuple[Dict[str, Dict[str, Dict[str, Any]]], Dict[str, Dict[str, Any]], Dict[str, int]]:
    graph: Dict[str, Dict[str, Dict[str, Any]]] = {}
    item_meta: Dict[str, Dict[str, Any]] = {}
    skipped_reasons: Dict[str, int] = {}

    for row in rows:
        ok, reason = row_quality_ok(
            row,
            args.min_row_volume,
            args.min_side_traded,
            args.min_stock,
            args.include_weird,
        )

        if not ok:
            skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
            continue

        c1 = row.get("currencyOne") or {}
        c2 = row.get("currencyTwo") or {}
        d1 = row.get("currencyOneData") or {}
        d2 = row.get("currencyTwoData") or {}

        a = currency_name(c1)
        b = currency_name(c2)

        if not a or not b:
            skipped_reasons["missing normalized name"] = skipped_reasons.get("missing normalized name", 0) + 1
            continue

        a_cat = currency_category(c1)
        b_cat = currency_category(c2)

        item_meta[a] = {
            "category": a_cat,
            "api_id": currency_api_id(c1),
        }
        item_meta[b] = {
            "category": b_cat,
            "api_id": currency_api_id(c2),
        }

        a_amt = num(d1.get("volumeTraded"))
        b_amt = num(d2.get("volumeTraded"))
        row_volume = num(row.get("volume"))

        if a_amt <= 0 or b_amt <= 0:
            skipped_reasons["zero amount"] = skipped_reasons.get("zero amount", 0) + 1
            continue

        common_meta = {
            "row_volume": row_volume,
            "pair": f"{a} <-> {b}",
            "a_amt": a_amt,
            "b_amt": b_amt,
            "a_stock": num(d1.get("highestStock")),
            "b_stock": num(d2.get("highestStock")),
            "relative_price_a": num(d1.get("relativePrice")),
            "relative_price_b": num(d2.get("relativePrice")),
            "stock_value_a": num(d1.get("stockValue")),
            "stock_value_b": num(d2.get("stockValue")),
        }

        # 1 A ~= B_traded / A_traded B
        add_edge(
            graph,
            a,
            b,
            b_amt / a_amt,
            {
                **common_meta,
                "src_amount": a_amt,
                "dst_amount": b_amt,
                "src_stock": num(d1.get("highestStock")),
                "dst_stock": num(d2.get("highestStock")),
                "src_category": a_cat,
                "dst_category": b_cat,
            },
        )

        # 1 B ~= A_traded / B_traded A
        add_edge(
            graph,
            b,
            a,
            a_amt / b_amt,
            {
                **common_meta,
                "src_amount": b_amt,
                "dst_amount": a_amt,
                "src_stock": num(d2.get("highestStock")),
                "dst_stock": num(d1.get("highestStock")),
                "src_category": b_cat,
                "dst_category": a_cat,
            },
        )

    return graph, item_meta, skipped_reasons


def route_value(
    graph: Dict[str, Dict[str, Dict[str, Any]]],
    route: List[str],
    start: float = 1.0,
) -> Optional[Tuple[float, float, float, List[Dict[str, Any]]]]:
    amount = start
    legs: List[Dict[str, Any]] = []
    min_row_volume = float("inf")
    min_stock = float("inf")

    for src, dst in zip(route, route[1:]):
        edge = graph.get(src, {}).get(dst)
        if not edge:
            return None

        amount *= edge["rate"]
        min_row_volume = min(min_row_volume, edge["row_volume"])
        min_stock = min(min_stock, edge["src_stock"], edge["dst_stock"])

        legs.append(
            {
                "src": src,
                "dst": dst,
                "rate": edge["rate"],
                "row_volume": edge["row_volume"],
                "src_stock": edge["src_stock"],
                "dst_stock": edge["dst_stock"],
                "pair": edge["pair"],
                "src_category": edge["src_category"],
                "dst_category": edge["dst_category"],
            }
        )

    return amount, min_row_volume, min_stock, legs


def middle_allowed(
    name: str,
    item_meta: Dict[str, Dict[str, Any]],
    core_currencies: set,
    allowed_categories: set,
) -> bool:
    if name in core_currencies:
        return False

    meta = item_meta.get(name, {})
    category = meta.get("category")

    if allowed_categories and category not in allowed_categories:
        return False

    return True


def generate_candidate_routes(
    graph: Dict[str, Dict[str, Dict[str, Any]]],
    item_meta: Dict[str, Dict[str, Any]],
    budget_currency: str,
    core_currencies: set,
    allowed_categories: set,
) -> List[List[str]]:
    routes = []
    currencies = list(graph.keys())

    if budget_currency not in graph:
        return routes

    for middle in currencies:
        if not middle_allowed(middle, item_meta, core_currencies, allowed_categories):
            continue

        for settlement in core_currencies:
            if settlement == budget_currency:
                continue

            # Both shapes matter:
            # A -> middle -> settlement -> A
            # A -> settlement -> middle -> A
            routes.append([budget_currency, middle, settlement, budget_currency])
            routes.append([budget_currency, settlement, middle, budget_currency])

    return routes


def plan_route_integer_floor(
    opp: Dict[str, Any],
    budget_amount: float,
    profile: Dict[str, Any],
    max_gold: Optional[float],
    ignore_stock_cap: bool,
) -> Optional[Dict[str, Any]]:
    """
    Simulate whole-number execution from 1..budget_amount.

    This floors outputs at every leg because POE currency quantities are whole units.
    It also estimates gold cost and rejects routes that exceed max_gold.
    """
    best = None
    max_budget = int(math.floor(budget_amount))

    if max_budget <= 0:
        return None

    for start_amount in range(1, max_budget + 1):
        amount = start_amount
        steps = []
        total_gold = 0.0
        ok = True
        fail_reason = None

        for leg in opp["legs"]:
            src = leg["src"]
            dst = leg["dst"]
            src_stock = int(math.floor(leg.get("src_stock", 0) or 0))
            dst_stock = int(math.floor(leg.get("dst_stock", 0) or 0))
            rate = leg["rate"]

            if not ignore_stock_cap and src_stock > 0 and amount > src_stock:
                ok = False
                fail_reason = f"{src} stock cap"
                break

            out_amount = int(math.floor(amount * rate))

            if out_amount <= 0:
                ok = False
                fail_reason = f"{src}->{dst} output rounds to zero"
                break

            if not ignore_stock_cap and dst_stock > 0 and out_amount > dst_stock:
                ok = False
                fail_reason = f"{dst} stock cap"
                break

            leg_gold = estimate_leg_gold(
                src=src,
                dst=dst,
                input_amount=amount,
                output_amount=out_amount,
                profile=profile,
            )

            total_gold += leg_gold

            if max_gold is not None and total_gold > max_gold:
                ok = False
                fail_reason = "gold cap"
                break

            steps.append(
                {
                    "src": src,
                    "dst": dst,
                    "in": amount,
                    "out": out_amount,
                    "rate": rate,
                    "row_volume": leg["row_volume"],
                    "src_stock": leg["src_stock"],
                    "dst_stock": leg["dst_stock"],
                    "gold": leg_gold,
                }
            )

            amount = out_amount

        if not ok:
            continue

        final_amount = amount
        profit = final_amount - start_amount

        if profit <= 0:
            continue

        candidate = {
            "start_to_use": start_amount,
            "left_unspent": budget_amount - start_amount,
            "final_amount": final_amount,
            "profit": profit,
            "profit_pct": (profit / start_amount) * 100,
            "total_gold": total_gold,
            "profit_per_million_gold": (
                profit / (total_gold / 1_000_000.0)
                if total_gold > 0
                else None
            ),
            "steps": steps,
            "fail_reason": fail_reason,
        }

        if best is None or candidate["profit"] > best["profit"]:
            best = candidate

    return best


def practical_min_start_for_route(opp: Dict[str, Any]) -> Dict[str, float]:
    amount = 1.0
    max_start_needed = 1.0

    for leg in opp["legs"]:
        amount *= leg["rate"]

        if 0 < amount < 1:
            needed = math.ceil(1 / amount)
            max_start_needed = max(max_start_needed, float(needed))

    final_amount = opp["final_amount"] * max_start_needed
    profit = final_amount - max_start_needed

    return {
        "start_needed": max_start_needed,
        "final_amount": final_amount,
        "profit": profit,
        "profit_pct": (profit / max_start_needed) * 100 if max_start_needed else 0.0,
    }


def liquidity_metrics_for_plan(opp: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute liquidity risk from the integer execution plan.

    For a 3-leg route:
      A -> B -> C -> A

    The dangerous part is usually the middle inventory that must be sold.
    We identify the non-core-ish middle leg by using step 2 and step 3:
      - step 2 output is often the middle item in A -> settlement -> middle -> A
      - step 3 input is the quantity that must be sold into the exit leg
    """
    plan = opp["integer_plan"]
    steps = plan["steps"]

    if len(steps) < 3:
        return {
            "middle_units": 0,
            "exit_input_units": 0,
            "exit_row_volume": 0,
            "exit_src_stock": 0,
            "liquidity_use": 1.0,
            "liquidity_adjusted_profit": 0,
            "gold_risk_hint": "unknown",
        }

    exit_step = steps[-1]

    # The amount entering the final leg is the inventory that must be exited.
    exit_input_units = float(exit_step["in"])
    exit_row_volume = float(exit_step["row_volume"])
    exit_src_stock = float(exit_step["src_stock"] or 0)

    # In a 3-leg route, the thing entering final leg is the risky inventory.
    middle_units = exit_input_units

    if exit_src_stock > 0:
        liquidity_use = exit_input_units / exit_src_stock
    else:
        liquidity_use = 1.0

    profit = float(plan["profit"])

    # Base score starts at profit.
    score = profit

    # Penalize large unit counts. This pushes Artificer's / Alchemy / Scrap down.
    score = score / math.sqrt(max(middle_units, 1.0))

    # Penalize consuming too much of apparent exit liquidity.
    score = score / max(1.0, liquidity_use * 10.0)

    # Reward higher row volume on exit.
    score = score * math.log10(max(exit_row_volume, 10.0))

    # Flag obvious gold-risk routes when gold model is disabled.
    if middle_units >= 5000:
        gold_risk_hint = "HIGH"
    elif middle_units >= 1000:
        gold_risk_hint = "MEDIUM"
    else:
        gold_risk_hint = "LOW"

    return {
        "middle_units": middle_units,
        "exit_input_units": exit_input_units,
        "exit_row_volume": exit_row_volume,
        "exit_src_stock": exit_src_stock,
        "liquidity_use": liquidity_use,
        "liquidity_adjusted_profit": score,
        "gold_risk_hint": gold_risk_hint,
    }


def liquidity_rejection_reason(
    liq: Dict[str, Any],
    args: argparse.Namespace,
) -> Optional[str]:
    if args.max_middle_units is not None and liq["middle_units"] > args.max_middle_units:
        return f"middle units {liq['middle_units']:.0f} > max {args.max_middle_units}"

    if args.min_exit_row_volume is not None and liq["exit_row_volume"] < args.min_exit_row_volume:
        return f"exit row volume {liq['exit_row_volume']:.2f} < min {args.min_exit_row_volume}"

    if args.max_liquidity_use is not None and liq["liquidity_use"] > args.max_liquidity_use:
        return f"liquidity use {liq['liquidity_use']:.2%} > max {args.max_liquidity_use:.2%}"

    return None


def sort_key_for_opp(opp: Dict[str, Any], sort_mode: str, prefer_liquid: bool) -> Tuple[float, float, float, float]:
    plan = opp["integer_plan"]
    liq = opp.get("liquidity", {})

    if prefer_liquid or sort_mode == "liquidity":
        return (
            float(liq.get("liquidity_adjusted_profit", 0)),
            plan["profit"],
            plan["profit_pct"],
            opp["min_row_volume"],
        )

    if sort_mode == "profit_pct":
        return (
            plan["profit_pct"],
            plan["profit"],
            opp["min_row_volume"],
            opp["min_stock"],
        )

    if sort_mode == "profit_per_gold":
        ppg = plan["profit_per_million_gold"]
        if ppg is None:
            ppg = 0.0

        return (
            ppg,
            plan["profit"],
            plan["profit_pct"],
            opp["min_row_volume"],
        )

    return (
        plan["profit"],
        plan["profit_pct"],
        opp["min_row_volume"],
        opp["min_stock"],
    )


def find_opportunities(
    graph: Dict[str, Dict[str, Dict[str, Any]]],
    item_meta: Dict[str, Dict[str, Any]],
    args: argparse.Namespace,
    core_currencies: set,
    allowed_categories: set,
    profile: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    opportunities = []
    rejection_counts: Dict[str, int] = {}

    routes = generate_candidate_routes(
        graph=graph,
        item_meta=item_meta,
        budget_currency=args.budget_currency,
        core_currencies=core_currencies,
        allowed_categories=allowed_categories,
    )

    seen = set()

    for route in routes:
        route_key = tuple(route)
        if route_key in seen:
            continue
        seen.add(route_key)

        result = route_value(graph, route, start=1.0)
        if not result:
            continue

        final_amount, min_row_volume, min_stock, legs = result
        edge = final_amount - 1.0

        if edge <= args.min_edge + args.fee_buffer:
            rejection_counts["edge below min+buffer"] = rejection_counts.get("edge below min+buffer", 0) + 1
            continue

        opp = {
            "route": route,
            "gross_edge_pct": edge * 100,
            "final_amount": final_amount,
            "min_row_volume": min_row_volume,
            "min_stock": min_stock,
            "legs": legs,
        }

        integer_plan = plan_route_integer_floor(
            opp=opp,
            budget_amount=args.budget_amount,
            profile=profile,
            max_gold=args.max_gold,
            ignore_stock_cap=args.ignore_stock_cap,
        )

        if not integer_plan:
            rejection_counts["no integer plan"] = rejection_counts.get("no integer plan", 0) + 1
            continue

        if integer_plan["profit"] < args.min_integer_profit:
            rejection_counts["profit below min"] = rejection_counts.get("profit below min", 0) + 1
            continue

        opp["integer_plan"] = integer_plan
        opp["practical_min_start"] = practical_min_start_for_route(opp)
        opp["liquidity"] = liquidity_metrics_for_plan(opp)

        liq_reason = liquidity_rejection_reason(opp["liquidity"], args)
        if liq_reason:
            rejection_counts[liq_reason] = rejection_counts.get(liq_reason, 0) + 1
            continue

        opportunities.append(opp)

    opportunities.sort(
        key=lambda x: sort_key_for_opp(x, args.sort, args.prefer_liquid),
        reverse=True,
    )

    return opportunities, rejection_counts


def parse_route_arg(route_text: str) -> List[str]:
    return [part.strip() for part in route_text.split(">") if part.strip()]


def parse_amounts_arg(amounts_text: str) -> List[int]:
    amounts = []

    for part in amounts_text.split(","):
        part = part.strip()
        if not part:
            continue
        amounts.append(int(float(part)))

    return sorted(set(amounts))


def simulate_route_ladder(
    graph: Dict[str, Dict[str, Dict[str, Any]]],
    route: List[str],
    start_amounts: List[int],
    rate_haircut: float = 0.0,
    enforce_stock: bool = True,
    profile: Optional[Dict[str, Any]] = None,
    max_gold: Optional[float] = None,
) -> List[Dict[str, Any]]:
    results = []

    if profile is None:
        profile = {
            "default_per_trade": 0,
            "default_per_input_unit": 0,
            "default_per_output_unit": 0,
            "multiplier": 1.0,
            "per_trade": {},
            "per_input_unit": {},
            "per_output_unit": {},
        }

    for start_amount in start_amounts:
        amount = int(start_amount)
        steps = []
        ok = True
        warnings = []
        total_gold = 0.0

        for src, dst in zip(route, route[1:]):
            edge = graph.get(src, {}).get(dst)

            if not edge:
                ok = False
                warnings.append(f"missing edge: {src} -> {dst}")
                break

            raw_rate = edge["rate"]
            safe_rate = raw_rate * (1.0 - rate_haircut)

            src_stock = int(math.floor(edge.get("src_stock", 0) or 0))
            dst_stock = int(math.floor(edge.get("dst_stock", 0) or 0))

            if enforce_stock and src_stock > 0 and amount > src_stock:
                ok = False
                warnings.append(
                    f"{src} input {amount} exceeds aggregate src stock {src_stock} "
                    f"on {src} -> {dst}"
                )
                break

            out_amount = int(math.floor(amount * safe_rate))

            if out_amount <= 0:
                ok = False
                warnings.append(
                    f"{src} -> {dst} rounds to zero at input {amount}"
                )
                break

            if enforce_stock and dst_stock > 0 and out_amount > dst_stock:
                ok = False
                warnings.append(
                    f"{dst} output {out_amount} exceeds aggregate dst stock {dst_stock} "
                    f"on {src} -> {dst}"
                )
                break

            leg_gold = estimate_leg_gold(
                src=src,
                dst=dst,
                input_amount=amount,
                output_amount=out_amount,
                profile=profile,
            )

            total_gold += leg_gold

            if max_gold is not None and total_gold > max_gold:
                ok = False
                warnings.append(
                    f"gold cap exceeded: estimated {total_gold:,.0f} > max {max_gold:,.0f}"
                )
                break

            steps.append(
                {
                    "src": src,
                    "dst": dst,
                    "in": amount,
                    "out": out_amount,
                    "raw_rate": raw_rate,
                    "safe_rate": safe_rate,
                    "row_volume": edge.get("row_volume", 0),
                    "src_stock": src_stock,
                    "dst_stock": dst_stock,
                    "gold": leg_gold,
                }
            )

            amount = out_amount

        final_amount = amount if ok else None
        profit = final_amount - start_amount if final_amount is not None else None

        results.append(
            {
                "start_amount": start_amount,
                "ok": ok,
                "final_amount": final_amount,
                "profit": profit,
                "profit_pct": (profit / start_amount * 100) if ok and start_amount else None,
                "total_gold": total_gold,
                "profit_per_million_gold": (
                    profit / (total_gold / 1_000_000.0)
                    if ok and total_gold > 0
                    else None
                ),
                "warnings": warnings,
                "steps": steps,
            }
        )

    return results


def print_route_ladder(
    graph: Dict[str, Dict[str, Dict[str, Any]]],
    route: List[str],
    start_amounts: List[int],
    rate_haircut: float,
    profile: Dict[str, Any],
    max_gold: Optional[float],
    gold_enabled: bool,
    enforce_stock: bool,
) -> None:
    print("=" * 108)
    print("Manual whole-number route ladder")
    print(f"Route: {' -> '.join(route)}")
    print(f"Amounts: {', '.join(str(x) for x in start_amounts)}")
    print(f"Rate haircut: {rate_haircut:.2%}")
    print(f"Stock cap: {'enabled' if enforce_stock else 'ignored'}")
    if gold_enabled:
        print("Gold model: enabled")
        if max_gold is not None:
            print(f"Gold cap: {max_gold:,.0f}")
    else:
        print("Gold model: disabled")
    print("=" * 108)

    results = simulate_route_ladder(
        graph=graph,
        route=route,
        start_amounts=start_amounts,
        rate_haircut=rate_haircut,
        enforce_stock=enforce_stock,
        profile=profile,
        max_gold=max_gold,
    )

    for result in results:
        start = result["start_amount"]

        if not result["ok"]:
            print(f"{start} {route[0]}: NOT EXECUTABLE")
            for warning in result["warnings"]:
                print(f"  - {warning}")
            print()
            continue

        final_amount = result["final_amount"]
        profit = result["profit"]
        profit_pct = result["profit_pct"]

        print(
            f"{start} {route[0]} -> {final_amount} {route[-1]} "
            f"| profit {profit} {route[-1]} ({profit_pct:.2f}%)"
        )

        if gold_enabled:
            print(f"  Estimated gold: {result['total_gold']:,.0f}")
            if result["profit_per_million_gold"] is not None:
                print(
                    f"  Profit / 1M gold: {result['profit_per_million_gold']:.4f} {route[-1]}"
                )

        for step in result["steps"]:
            print(
                f"  {step['in']} {step['src']} "
                f"-> {step['out']} {step['dst']} "
                f"| rate={step['safe_rate']:.10f} "
                f"| stock={step['src_stock']}/{step['dst_stock']} "
                f"| rowVol={step['row_volume']:.2f}"
            )

            if gold_enabled:
                print(f"    Estimated leg gold: {step['gold']:,.0f}")

        print()


def print_human_header(
    rows: List[Dict[str, Any]],
    graph: Dict[str, Dict[str, Dict[str, Any]]],
    skipped_reasons: Dict[str, int],
    rejection_counts: Dict[str, int],
    args: argparse.Namespace,
    core_currencies: set,
    allowed_categories: set,
    profile: Dict[str, Any],
    gold_enabled: bool,
) -> None:
    path = f"/{args.realm}/Leagues/{args.league}/SnapshotPairs"

    print(f"Loaded {len(rows)} exchange pair rows from {path}")
    print(f"Built filtered graph with {len(graph)} currencies/items.")
    print(f"Budget: {args.budget_amount:.2f} {args.budget_currency}")
    print(f"Core settlement currencies: {', '.join(sorted(core_currencies))}")
    print(f"Allowed middle categories: {', '.join(sorted(allowed_categories))}")
    print(
        f"Filters: min_edge={args.min_edge:.2%}, "
        f"fee_buffer={args.fee_buffer:.2%}, "
        f"min_row_volume={args.min_row_volume}, "
        f"min_side_traded={args.min_side_traded}, "
        f"min_stock={args.min_stock}, "
        f"min_integer_profit={args.min_integer_profit}"
    )

    if args.ignore_stock_cap:
        print("Stock cap: ignored")
    else:
        print("Stock cap: aggregate highestStock enabled")

    print("Liquidity filters:")
    print(f"  max_middle_units: {args.max_middle_units}")
    print(f"  min_exit_row_volume: {args.min_exit_row_volume}")
    print(f"  max_liquidity_use: {args.max_liquidity_use}")
    print(f"  prefer_liquid: {args.prefer_liquid}")

    if gold_enabled:
        print("Gold model: enabled")
        if args.max_gold is not None:
            print(f"Gold cap: {args.max_gold:,.0f}")
        else:
            print("Gold cap: none")

        print(
            "Gold defaults: "
            f"per_trade={profile['default_per_trade']:,.0f}, "
            f"per_input_unit={profile['default_per_input_unit']:,.0f}, "
            f"per_output_unit={profile['default_per_output_unit']:,.0f}, "
            f"multiplier={profile['multiplier']}"
        )
    else:
        print("Gold model: disabled. Use --max-gold and/or --gold-profile to enable.")

    print(f"Sort mode: {args.sort}")
    print(f"Top results: {args.top}")

    print("\nTop skip reasons:")
    for reason, count in sorted(skipped_reasons.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"  {count:5d}  {reason}")

    if args.debug_rejections and rejection_counts:
        print("\nTop opportunity rejection reasons:")
        for reason, count in sorted(rejection_counts.items(), key=lambda x: x[1], reverse=True)[:15]:
            print(f"  {count:5d}  {reason}")

    print("\nScanning routes shaped like:")
    print(f"  {args.budget_currency} -> ITEM -> settlement -> {args.budget_currency}")
    print(f"  {args.budget_currency} -> settlement -> ITEM -> {args.budget_currency}")
    print()


def print_human_opportunity(
    idx: int,
    opp: Dict[str, Any],
    budget_currency: str,
    gold_enabled: bool,
) -> None:
    plan = opp["integer_plan"]
    practical = opp["practical_min_start"]
    liq = opp["liquidity"]

    print("=" * 108)
    print(f"{idx}. {' -> '.join(opp['route'])}")
    print(f"   Gross theoretical edge: {opp['gross_edge_pct']:.2f}%")
    print(f"   1.00 {budget_currency} theoretically becomes {opp['final_amount']:.6f} {budget_currency}")
    print(
        f"   Practical minimum start: ~{practical['start_needed']:.2f} {budget_currency} "
        f"-> ~{practical['final_amount']:.2f} {budget_currency} "
        f"(profit ~{practical['profit']:.2f})"
    )

    print()
    print("   Integer execution plan:")
    print(f"   Suggested spend: {plan['start_to_use']} {budget_currency}")
    print(f"   Left unspent:    {plan['left_unspent']:.2f} {budget_currency}")
    print(f"   Expected final:  {plan['final_amount']} {budget_currency}")
    print(
        f"   Expected profit: {plan['profit']} {budget_currency} "
        f"({plan['profit_pct']:.2f}%)"
    )

    if gold_enabled:
        print(f"   Estimated gold:  {plan['total_gold']:,.0f}")
        ppg = plan["profit_per_million_gold"]
        if ppg is not None:
            print(f"   Profit / 1M gold: {ppg:.4f} {budget_currency}")
    else:
        if liq["gold_risk_hint"] != "LOW":
            print(f"   Gold risk hint:  {liq['gold_risk_hint']} — large middle quantity and gold model is disabled")

    print()
    print("   Liquidity metrics")
    print(f"   Middle/exit units:       {liq['middle_units']:.0f}")
    print(f"   Exit row volume:         {liq['exit_row_volume']:.2f}")
    print(f"   Exit apparent stock:     {liq['exit_src_stock']:.0f}")
    print(f"   Apparent liquidity use:  {liq['liquidity_use']:.2%}")
    print(f"   Liquidity score:         {liq['liquidity_adjusted_profit']:.4f}")

    print(f"   Min row volume:          {opp['min_row_volume']:.2f}")
    print(f"   Min stock:               {opp['min_stock']:.2f}")

    print()
    print("   Buy / convert plan:")
    for step_num, step in enumerate(plan["steps"], start=1):
        print(
            f"   {step_num}. Spend {step['in']} {step['src']} "
            f"-> receive ~{step['out']} {step['dst']}"
        )
        print(
            f"      Rate: 1 {step['src']} -> {step['rate']:.10f} {step['dst']} "
            f"| rowVol={step['row_volume']:.2f} "
            f"| stock={step['src_stock']:.0f}/{step['dst_stock']:.0f}"
        )

        if gold_enabled:
            print(f"      Estimated gold for this leg: {step['gold']:,.0f}")

    print()
    print("   Manual validation checklist:")
    print("   - Check leg 3 first if possible; confirm you can exit back into the budget currency.")
    print("   - Check leg 2 next; confirm enough stock exists at the expected rate.")
    print("   - Check leg 1 last; do not buy the starting conversion until legs 2 and 3 are real.")
    print("   - Check the in-game gold cost before committing.")
    print("   - Stop if any leg is materially worse than the printed rate.")
    print("   - Do not scale above the shown middle/exit units unless you recheck live depth.")


def main() -> None:
    args = parse_args()
    profile = load_gold_profile(args)
    gold_enabled = gold_model_enabled(args, profile)

    if args.prefer_liquid:
        args.sort = "liquidity"

    if args.max_gold is not None and not gold_enabled:
        print(
            "Warning: --max-gold was set, but the gold model has no costs configured. "
            "Use --gold-profile, --gold-per-trade, --gold-per-input-unit, or --gold-per-output-unit.",
            file=sys.stderr,
        )

    core_currencies = set(args.core) if args.core else set(DEFAULT_CORE_CURRENCIES)
    core_currencies.add(args.budget_currency)

    allowed_categories = (
        set(args.allow_category) if args.allow_category else set(DEFAULT_ALLOWED_CATEGORIES)
    )

    path = f"/{args.realm}/Leagues/{args.league}/SnapshotPairs"

    if args.snapshot_file:
        snapshot_path = Path(args.snapshot_file)
        if not snapshot_path.is_file():
            raise SystemExit(f"error: --snapshot-file not found: {snapshot_path}")

        try:
            with snapshot_path.open("r", encoding="utf-8") as f:
                payload = camelize(json.load(f))
        except json.JSONDecodeError as e:
            raise SystemExit(
                f"error: --snapshot-file is not valid JSON: {snapshot_path}: {e}"
            ) from e
    else:
        payload = get_json(args.base, path)

    rows = unwrap_rows(payload)

    if args.debug_first_row and rows:
        print("First row after camelCase normalization:")
        print(json.dumps(rows[0], indent=2)[:5000])
        print()

    graph, item_meta, skipped_reasons = build_graph(rows, args)

    if args.route:
        route = parse_route_arg(args.route)
        amounts = parse_amounts_arg(args.amounts)

        print_route_ladder(
            graph=graph,
            route=route,
            start_amounts=amounts,
            rate_haircut=args.rate_haircut,
            profile=profile,
            max_gold=args.max_gold,
            gold_enabled=gold_enabled,
            enforce_stock=not args.ignore_stock_cap,
        )
        return

    opportunities, rejection_counts = find_opportunities(
        graph=graph,
        item_meta=item_meta,
        args=args,
        core_currencies=core_currencies,
        allowed_categories=allowed_categories,
        profile=profile,
    )

    if args.json:
        output = {
            "budget_currency": args.budget_currency,
            "budget_amount": args.budget_amount,
            "league": args.league,
            "realm": args.realm,
            "gold_enabled": gold_enabled,
            "max_gold": args.max_gold,
            "gold_profile": profile,
            "row_count": len(rows),
            "graph_size": len(graph),
            "skipped_reasons": skipped_reasons,
            "rejection_counts": rejection_counts,
            "opportunities": opportunities[: args.top],
        }
        print(json.dumps(output, indent=2))
        return

    print_human_header(
        rows=rows,
        graph=graph,
        skipped_reasons=skipped_reasons,
        rejection_counts=rejection_counts,
        args=args,
        core_currencies=core_currencies,
        allowed_categories=allowed_categories,
        profile=profile,
        gold_enabled=gold_enabled,
    )

    if not opportunities:
        print("No opportunities found with current filters.")
        print()
        print("Try loosening one setting at a time:")
        print("  --min-edge 0.05")
        print("  --min-row-volume 500")
        print("  --min-stock 2")
        print("  --max-middle-units 5000")
        print("  --min-exit-row-volume 50000")
        print("  --max-liquidity-use 0.50")
        print()
        print("Or run without liquidity filters first:")
        print("  remove --max-middle-units, --min-exit-row-volume, and --max-liquidity-use")
        return

    for idx, opp in enumerate(opportunities[: args.top], start=1):
        print_human_opportunity(
            idx=idx,
            opp=opp,
            budget_currency=args.budget_currency,
            gold_enabled=gold_enabled,
        )


if __name__ == "__main__":
    main()
