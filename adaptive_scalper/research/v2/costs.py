"""Cost model V2 diagnostics: the entry-time cost PREDICTION the live
pre-trade gate used versus what the DEMO broker actually charged, from
`execution_cost_observations` (evidence class BROKER_DEMO_CONFIRMED).

Accounting (no double counting): broker P&L already contains spread and
slippage because it is computed from execution prices. They are measured
here only to judge the PREDICTION, never added to P&L again. Commission and
swap are separate broker deal fields and are the only components that a
P&L figure does not already contain.

Limitation stated in every report: only ENTRY-side spread/slippage is
observed per order; exit-side slippage is not recorded by the runtime.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

_COLUMNS = (
    "canonical_symbol", "direction", "session", "atr", "requested_volume", "filled_volume",
    "estimated_spread_price", "spread_price", "estimated_slippage_price", "slippage_price",
    "estimated_commission_price", "entry_commission", "exit_commission", "swap", "exit_recorded_at_utc",
)


def load_filled_observations(conn) -> list[dict]:
    rows = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM execution_cost_observations WHERE outcome_status = 'FILLED'"
    ).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


def _stats(values: list[float]) -> dict | None:
    values = [v for v in values if v is not None]
    if not values:
        return None
    s = sorted(values)
    return {"n": len(s), "mean": statistics.fmean(s), "median": statistics.median(s),
            "p90": s[min(len(s) - 1, int(0.9 * (len(s) - 1) + 0.5))], "max": s[-1]}


def _group(rows: list[dict]) -> dict:
    return {
        "fills": len(rows),
        "spread_predicted": _stats([r["estimated_spread_price"] for r in rows]),
        "spread_at_fill_quote": _stats([r["spread_price"] for r in rows]),
        "slippage_predicted": _stats([r["estimated_slippage_price"] for r in rows]),
        # signed: positive = adverse to the order, negative = price improvement
        "slippage_realized_signed": _stats([r["slippage_price"] for r in rows]),
        "slippage_realized_adverse_share": (
            sum(1 for r in rows if (r["slippage_price"] or 0) > 0) / len(rows) if rows else None),
        "slippage_prediction_error": _stats([
            (r["slippage_price"] - r["estimated_slippage_price"]) for r in rows
            if r["slippage_price"] is not None and r["estimated_slippage_price"] is not None]),
        "commission_money_per_lot_round_trip": _stats([
            abs((r["entry_commission"] or 0.0) + (r["exit_commission"] or 0.0)) / r["filled_volume"]
            for r in rows if r["exit_recorded_at_utc"] and r["filled_volume"]]),
        "swap_money": _stats([r["swap"] for r in rows if r["exit_recorded_at_utc"]]),
    }


def cost_diagnostics(rows: list[dict]) -> dict:
    by_symbol: dict[str, list] = defaultdict(list)
    for row in rows:
        by_symbol[row["canonical_symbol"]].append(row)
    report = {}
    for symbol, group in sorted(by_symbol.items()):
        sessions: dict[str, list] = defaultdict(list)
        for row in group:
            sessions[row["session"] or "UNKNOWN"].append(row)
        atrs = sorted(r["atr"] for r in group if r["atr"] is not None)
        vol = defaultdict(list)
        if len(atrs) >= 3:
            low, high = atrs[len(atrs) // 3], atrs[(2 * len(atrs)) // 3]
            for row in group:
                if row["atr"] is not None:
                    vol["low" if row["atr"] < low else "high" if row["atr"] >= high else "mid"].append(row)
        report[symbol] = {
            "all": _group(group),
            "by_session": {k: _group(v) for k, v in sorted(sessions.items())},
            "by_volatility_tercile": {k: _group(vol[k]) for k in ("low", "mid", "high") if vol[k]},
        }
    return {
        "evidence": "BROKER_DEMO_CONFIRMED (entry side)",
        "limitation": "exit-side slippage is not recorded per order; spread/slippage are inside broker P&L already",
        "symbols": report,
    }
