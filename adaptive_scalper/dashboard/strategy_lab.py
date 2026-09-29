"""Strategy Lab evidence engine: trade attribution, accounting, metrics.

READ-ONLY. Every function here takes a read-only SQLite connection and
returns plain data; nothing writes, nothing talks to MT5, nothing can
place an order, change risk or touch the kill switch.

Attribution rules (docs/STRATEGY_LAB.md is the operator-facing copy):

A broker position is ATTRIBUTED to a strategy only when ALL of these
durable, runtime-written records agree:

1. a local `positions` row with that exact broker position id;
2. its `entry_order_id` resolves to an `orders` row whose `chain_key` is a
   runtime entry chain (`entry:<symbol>:<bar>:<strategy>`);
3. that chain contains a `PROPOSAL_CREATED` journal event whose
   `strategy_key` equals `positions.strategy_key`;
4. when present, `position_entry_context` for the position names the same
   strategy and chain (it is also the source of strategy version/regime);
5. when the broker entry deal is known, its order ticket equals the local
   order's `broker_order_id`.

Symbol, direction, timestamp proximity and broker comments are NEVER used
to assign a strategy. Anything failing the chain is UNATTRIBUTED, with the
failing link named.

Accounting (per broker position, deals de-duplicated by broker ticket):

    gross    = sum(deal.profit)          (broker-recorded; already includes
                                          spread and slippage effects)
    costs    = sum(commission) + sum(fee) + sum(swap)
    net      = gross + costs
    realized = exit-deal profit + exit-deal commission/fee + swap
               + entry commission/fee * (closed volume / entry volume)

The entry-cost share of any still-open volume is reported separately as
"entry costs allocated to open volume" so that the population identity

    sum(net of every deal) = realized(all positions)
                             + open-volume entry costs + non-trade deals

holds exactly and is re-checked against an independent SQL SUM.
"""

from __future__ import annotations

import ast
import inspect
import json
import sqlite3
import textwrap
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field

from adaptive_scalper.config.constants import RETIRED_STRATEGY_KEYS

# --- MT5 enumerations (raw integer codes as stored by the importer) ------
DEAL_TYPE_BUY, DEAL_TYPE_SELL = 0, 1
DEAL_TYPE_NAMES = {
    0: "BUY", 1: "SELL", 2: "BALANCE", 3: "CREDIT", 4: "CHARGE", 5: "CORRECTION", 6: "BONUS",
    7: "COMMISSION", 8: "COMMISSION_DAILY", 9: "COMMISSION_MONTHLY", 10: "COMMISSION_AGENT_DAILY",
    11: "COMMISSION_AGENT_MONTHLY", 12: "INTEREST", 13: "BUY_CANCELED", 14: "SELL_CANCELED",
    15: "DIVIDEND", 16: "DIVIDEND_FRANKED", 17: "TAX",
}
ENTRY_NAMES = {0: "IN", 1: "OUT", 2: "INOUT", 3: "OUT_BY"}
DEAL_REASON_NAMES = {
    0: "CLIENT", 1: "MOBILE", 2: "WEB", 3: "EXPERT", 4: "SL", 5: "TP", 6: "SO",
    7: "ROLLOVER", 8: "VMARGIN", 9: "SPLIT",
}
MANUAL_REASONS = frozenset({0, 1, 2})
EXPERT_REASON = 3

# --- source classes -------------------------------------------------------
ATTRIBUTED = "ATTRIBUTED"
ASN_UNATTRIBUTED = "ASN_UNATTRIBUTED"          # this runtime's record/magic, but the chain is incomplete
MANUAL = "MANUAL"                              # broker deal reason CLIENT/MOBILE/WEB
EXTERNAL_EXPERT = "EXTERNAL_EXPERT"            # another expert adviser (foreign magic / EXPERT reason)
UNKNOWN_SOURCE = "UNKNOWN_SOURCE"              # magic 0 and no broker reason recorded
SOURCE_CLASSES = (ATTRIBUTED, ASN_UNATTRIBUTED, MANUAL, EXTERNAL_EXPERT, UNKNOWN_SOURCE)

BREAKEVEN_EPSILON = 0.005   # |net| below half a cent is a breakeven, not a win/loss
VOLUME_EPSILON = 1e-9
MONEY_MISMATCH_EPSILON = 0.005

RUNTIME_ENTRY_CHAIN_PREFIX = "entry:"
LEDGER_CACHE_SECONDS = 30.0

EVIDENCE_SOURCES = ("DEMO", "PAPER", "BACKTEST")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}  # nosec B608 - internal table names only


def _json(text: str | None):
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _runtime_state(conn: sqlite3.Connection, key: str) -> dict | None:
    if not _table_exists(conn, "runtime_state"):
        return None
    row = conn.execute("SELECT value_json FROM runtime_state WHERE key = ?", (key,)).fetchone()
    value = _json(row[0]) if row else None
    return value if isinstance(value, dict) else None


def account_currency(conn: sqlite3.Connection) -> str | None:
    """The broker account currency exactly as the runtime last sampled it;
    None (never a guessed 'USD') when no telemetry was recorded."""
    telemetry = _runtime_state(conn, "live_telemetry") or {}
    return (telemetry.get("account") or {}).get("currency")


LIVE_FLOATING_MAX_AGE_SECONDS = 15  # = dashboard.panels.STALE_HEARTBEAT_SECONDS


def live_floating_pnl(conn: sqlite3.Connection, now: int | None = None) -> dict[str, float] | None:
    """Broker floating P&L per open DEMO position id from the runtime's
    sampled snapshot, or None when that snapshot is missing or stale.
    Unrealized P&L is shown beside, never inside, realized results."""
    if not _table_exists(conn, "runtime_state"):
        return None
    row = conn.execute("SELECT value_json, updated_at_utc FROM runtime_state WHERE key = 'live_telemetry'").fetchone()
    if row is None:
        return None
    now = int(time.time()) if now is None else now
    telemetry = _json(row[0])
    if (not isinstance(telemetry, dict) or now - int(row[1]) > LIVE_FLOATING_MAX_AGE_SECONDS
            or not (telemetry.get("terminal") or {}).get("connected")
            or not isinstance(telemetry.get("positions"), list)):  # None = the broker query failed: unknown
        return None
    return {str(p["broker_position_id"]): p["floating_pnl"] for p in telemetry["positions"]
            if isinstance(p.get("floating_pnl"), (int, float))}


def _session_of_hour(hour: int | None) -> str | None:
    """UTC session bucket, applied ONLY to simulated trades (which record
    no session); a DEMO trade's session is the one recorded at execution."""
    if hour is None:
        return None
    if hour < 7:
        return "ASIA"
    if hour < 12:
        return "LONDON"
    if hour < 16:
        return "LONDON_NY_OVERLAP"
    if hour < 21:
        return "NEW_YORK"
    return "LATE"


def _rows(conn: sqlite3.Connection, sql: str, params: tuple | list = ()) -> list[dict]:
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# strategy source metadata (read from the ACTUAL registered classes)
# ---------------------------------------------------------------------------
def _eligible_regimes(cls: type) -> dict:
    """Parse the regime gate at the top of `evaluate()` from source. Returns
    {'eligible': [...], 'rule': '<source>'}; eligible is None when the gate
    is not in a recognised form (the verbatim rule is still shown)."""
    from adaptive_scalper.regimes import classifier

    all_regimes = [getattr(classifier, n) for n in (
        "TRENDING_UP", "TRENDING_DOWN", "RANGE", "COMPRESSION", "VOLATILITY_EXPANSION",
        "BREAKOUT", "ERRATIC", "UNKNOWN",
    )]
    module = inspect.getmodule(cls)
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(cls.evaluate)))
    except (OSError, TypeError, SyntaxError):
        return {"eligible": None, "rule": None}

    def resolve(node: ast.AST) -> list[str] | None:
        if isinstance(node, ast.Name):
            value = getattr(module, node.id, None)
            if value is None:
                value = getattr(classifier, node.id, None)
            if isinstance(value, str):
                return [value]
            if isinstance(value, (set, frozenset, tuple, list)):
                return sorted(str(v) for v in value)
            return None
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            out: list[str] = []
            for elt in node.elts:
                part = resolve(elt)
                if part is None:
                    return None
                out.extend(part)
            return out
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        test = node.test
        if ast.unparse(test.left) != "regime.regime" or len(test.ops) != 1:
            continue
        values = resolve(test.comparators[0])
        rule = f"return FLAT if {ast.unparse(test)}"
        if values is None:
            return {"eligible": None, "rule": rule}
        op = test.ops[0]
        if isinstance(op, (ast.NotIn, ast.NotEq)):
            return {"eligible": values, "rule": rule}
        if isinstance(op, (ast.In, ast.Eq)):
            return {"eligible": [r for r in all_regimes if r not in values], "rule": rule}
        return {"eligible": None, "rule": rule}
    return {"eligible": None, "rule": None}


def strategy_source_catalog() -> dict[str, dict]:
    """Every ACTIVE strategy exactly as the runtime's own registry builder
    constructs it: version, current instance parameters, regime gate and
    the verbatim `evaluate()` source (the actual entry rules)."""
    from adaptive_scalper.strategies import build_active_registry

    catalog: dict[str, dict] = {}
    for strategy in build_active_registry().all_active():
        cls = type(strategy)
        module = inspect.getmodule(cls)
        params = {k: v for k, v in vars(strategy).items() if not k.startswith("_")}
        try:
            evaluate_source = textwrap.dedent(inspect.getsource(cls.evaluate))
        except (OSError, TypeError):
            evaluate_source = None
        catalog[strategy.key] = {
            "strategy_key": strategy.key,
            "version": strategy.version,
            "class_name": cls.__name__,
            "source_module": module.__name__ if module else None,
            "description": " ".join((module.__doc__ or "").split()) if module else "",
            "current_parameters": params,
            "stop_atr_multiple": params.get("stop_atr_multiple"),
            "target_atr_multiple": params.get("target_atr_multiple"),
            "min_confidence": params.get("min_confidence"),
            "expected_duration_seconds": params.get("expected_duration_seconds"),
            "regime_gate": _eligible_regimes(cls),
            "entry_rules_source": evaluate_source,
        }
    return catalog


# ---------------------------------------------------------------------------
# DEMO ledger
# ---------------------------------------------------------------------------
@dataclass
class _Deal:
    ticket: str
    order_ticket: str | None
    position_id: str | None
    time_utc: int
    type_code: int | None
    entry: str | None
    magic: int | None
    reason: int | None
    volume: float
    price: float
    profit: float
    commission: float
    swap: float
    fee: float
    symbol: str | None
    comment: str
    login: int | None
    sources: list[str] = field(default_factory=list)

    @property
    def is_trade(self) -> bool:
        return self.type_code in (DEAL_TYPE_BUY, DEAL_TYPE_SELL)

    @property
    def net(self) -> float:
        return self.profit + self.commission + self.swap + self.fee

    def as_dict(self) -> dict:
        return {
            "ticket": self.ticket, "order_ticket": self.order_ticket, "position_id": self.position_id,
            "time_utc": self.time_utc, "type": DEAL_TYPE_NAMES.get(self.type_code, self.type_code),
            "entry": self.entry, "magic": self.magic,
            "broker_reason": DEAL_REASON_NAMES.get(self.reason) if self.reason is not None else None,
            "volume": self.volume, "price": self.price, "profit": self.profit,
            "commission": self.commission, "swap": self.swap, "fee": self.fee, "symbol": self.symbol,
            "comment": self.comment, "recorded_by": list(self.sources),
        }


def _broker_deal_rows(conn: sqlite3.Connection, login: int | None) -> list[tuple]:
    if not _table_exists(conn, "broker_account_deals"):
        return []
    reason = "reason" if "reason" in _columns(conn, "broker_account_deals") else "NULL"
    sql = (f"SELECT ticket, order_ticket, position_id, time_utc, type, entry, magic, {reason}, volume, price, "
           "profit, commission, swap, fee, symbol, comment, login FROM broker_account_deals")  # nosec B608
    if login is not None:
        return conn.execute(sql + " WHERE login = ?", (login,)).fetchall()
    return conn.execute(sql).fetchall()


def _local_deal_rows(conn: sqlite3.Connection) -> list[tuple]:
    if not _table_exists(conn, "deals"):
        return []
    cols = _columns(conn, "deals")

    def opt(c: str) -> str:
        return f"d.{c}" if c in cols else "NULL"

    return conn.execute(
        f"SELECT d.broker_deal_id, {opt('broker_order_ticket')}, d.broker_position_id, d.occurred_at_utc, "
        f"{opt('deal_type')}, {opt('entry_type')}, {opt('magic')}, d.volume, d.price, d.profit, d.commission, "
        f"d.swap, {opt('fee')}, {opt('comment')}, p.canonical_symbol "
        "FROM deals d LEFT JOIN positions p ON p.broker_position_id = d.broker_position_id"  # nosec B608
    ).fetchall()


def _load_deals(conn: sqlite3.Connection, login: int | None) -> tuple[dict[str, _Deal], list[dict]]:
    """Union of the imported broker history (for `login`) and the runtime's
    own broker-confirmed deal records, de-duplicated by broker deal ticket.
    When both sources hold a ticket, broker-history values are used and
    any disagreement is REPORTED, never silently resolved."""
    deals: dict[str, _Deal] = {}
    discrepancies: list[dict] = []
    for r in _broker_deal_rows(conn, login):
        t = str(r[0])
        deals[t] = _Deal(
            ticket=t, order_ticket=str(r[1]) if r[1] else None, position_id=str(r[2]) if r[2] else None,
            time_utc=int(r[3]), type_code=r[4], entry=ENTRY_NAMES.get(r[5]), magic=r[6], reason=r[7],
            volume=float(r[8]), price=float(r[9]), profit=float(r[10]), commission=float(r[11]),
            swap=float(r[12]), fee=float(r[13]), symbol=r[14], comment=r[15] or "", login=r[16],
            sources=["BROKER_HISTORY_IMPORT"],
        )
    for r in _local_deal_rows(conn):
        t = str(r[0])
        local = _Deal(
            ticket=t, order_ticket=str(r[1]) if r[1] else None, position_id=str(r[2]) if r[2] else None,
            time_utc=int(r[3]), type_code={"BUY": DEAL_TYPE_BUY, "SELL": DEAL_TYPE_SELL}.get(r[4]),
            entry=r[5], magic=r[6], reason=None, volume=float(r[7]), price=float(r[8]),
            profit=float(r[9] or 0.0), commission=float(r[10] or 0.0), swap=float(r[11] or 0.0),
            fee=float(r[12] or 0.0), symbol=r[14], comment=r[13] or "", login=None,
            sources=["LOCAL_RUNTIME_RECORD"],
        )
        existing = deals.get(t)
        if existing is None:
            deals[t] = local
            continue
        existing.sources.append("LOCAL_RUNTIME_RECORD")
        for attr in ("profit", "commission", "swap", "fee", "volume"):
            a, b = getattr(existing, attr), getattr(local, attr)
            if abs(a - b) > MONEY_MISMATCH_EPSILON:
                discrepancies.append({"ticket": t, "field": attr, "broker_history": a,
                                      "local_runtime_record": b, "used": "broker_history"})
        if existing.position_id is None and local.position_id is not None:
            existing.position_id = local.position_id
    return deals, discrepancies


def _default_login(conn: sqlite3.Connection) -> tuple[int | None, list[int]]:
    """The account holding the most recent imported deal (the DEMO account
    the runtime trades); every login present is reported."""
    if not _table_exists(conn, "broker_account_deals"):
        return None, []
    logins = [r[0] for r in conn.execute("SELECT DISTINCT login FROM broker_account_deals ORDER BY login")]
    row = conn.execute("SELECT login FROM broker_account_deals ORDER BY time_utc DESC, id DESC LIMIT 1").fetchone()
    return (row[0] if row else None), logins


def _asn_magics(conn: sqlite3.Connection) -> set[int]:
    """The magic numbers THIS runtime actually stamped on its own orders
    (durable evidence in `orders.magic`) — never a hard-coded guess."""
    if not _table_exists(conn, "orders") or "magic" not in _columns(conn, "orders"):
        return set()
    return {r[0] for r in conn.execute("SELECT DISTINCT magic FROM orders WHERE magic IS NOT NULL AND magic != 0")}


def _chain_events(conn: sqlite3.Connection, chain_key: str) -> list[dict]:
    rows = conn.execute(
        "SELECT j.event_type, j.strategy_key, j.event_timestamp_utc, j.payload_json FROM journal_events j "
        "JOIN decision_chains d ON d.id = j.chain_id WHERE d.chain_key = ? ORDER BY j.sequence_in_chain",
        (chain_key,),
    ).fetchall()
    return [{"event_type": r[0], "strategy_key": r[1], "event_timestamp_utc": r[2], "payload": _json(r[3])}
            for r in rows]


def _proposal_strategy(events: list[dict]) -> str | None:
    keys = {e["strategy_key"] for e in events if e["event_type"] == "PROPOSAL_CREATED" and e["strategy_key"]}
    return next(iter(keys)) if len(keys) == 1 else None


def _local_positions(conn: sqlite3.Connection) -> dict[str, dict]:
    if not _table_exists(conn, "positions"):
        return {}
    rows = _rows(conn,
                 "SELECT p.id AS local_position_id, p.broker_position_id, p.canonical_symbol, p.direction, p.volume, "
                 "p.entry_price, p.initial_monetary_risk, p.strategy_key, p.entry_order_id, p.status, "
                 "p.opened_at_utc, p.closed_at_utc, o.chain_key, o.client_request_id, o.broker_order_id, "
                 "o.magic AS order_magic, o.stop_loss AS initial_stop_loss, o.take_profit AS initial_take_profit, "
                 "o.requested_volume, o.state AS order_state "
                 "FROM positions p LEFT JOIN orders o ON o.id = p.entry_order_id")
    return {str(r["broker_position_id"]): r for r in rows}


def _entry_context(conn: sqlite3.Connection) -> dict[str, dict]:
    if not _table_exists(conn, "position_entry_context"):
        return {}
    return {str(r["broker_position_id"]): r for r in _rows(
        conn, "SELECT broker_position_id, strategy_key, strategy_version, entry_regime, raw_confidence, "
              "stop_distance_price, target_distance_price, signal_bar_time_utc, chain_key FROM position_entry_context")}


def _cost_evidence(conn: sqlite3.Connection) -> dict[int, dict]:
    if not _table_exists(conn, "execution_cost_observations"):
        return {}
    return {r["order_id"]: r for r in _rows(
        conn, "SELECT order_id, session, spread_price, slippage_price, requested_price, fill_price, "
              "estimated_spread_price, estimated_commission_price, estimated_slippage_price, estimated_total_price, "
              "outcome_status FROM execution_cost_observations")}


def _exit_evidence(conn: sqlite3.Connection) -> dict[int, dict]:
    if not _table_exists(conn, "position_management_state"):
        return {}
    return {r["position_id"]: r for r in _rows(
        conn, "SELECT position_id, decision_at_utc AS exit_decision_at_utc, request_at_utc AS exit_request_at_utc, "
              "decision_r AS exit_decision_r, fill_r AS exit_fill_r, realized_slippage AS exit_realized_slippage_price, "
              "peak_r, entry_regime FROM position_management_state")}


def _close_reasons_from_journal(conn: sqlite3.Connection) -> dict[str, str]:
    if not _table_exists(conn, "journal_events"):
        return {}
    out = {}
    for pid, payload in conn.execute(
        "SELECT broker_position_id, payload_json FROM journal_events WHERE event_type = 'POSITION_CLOSED' "
        "AND broker_position_id IS NOT NULL ORDER BY id"
    ):
        p = _json(payload)
        if isinstance(p, dict) and p.get("reason"):
            out[str(pid)] = str(p["reason"])
    return out


def _classify_exit(deals: list[_Deal], exit_ev: dict | None, journal_reason: str | None) -> dict:
    """Exit reason plus the evidence it rests on. The broker DEAL_REASON is
    authoritative when recorded; next the runtime's own recorded exit
    request; a broker comment is only ever a labelled HINT."""
    exits = [d for d in deals if d.entry in ("OUT", "OUT_BY", "INOUT")]
    if not exits:
        return {"exit_reason": None, "exit_reason_evidence": None}
    last = exits[-1]
    requested = (exit_ev or {}).get("exit_request_at_utc")
    if last.reason is not None:
        name = DEAL_REASON_NAMES.get(last.reason, str(last.reason))
        mapped = {"SL": "BROKER_STOP_LOSS", "TP": "BROKER_TAKE_PROFIT", "SO": "BROKER_STOP_OUT",
                  "EXPERT": "EXPERT_CLOSE", "CLIENT": "MANUAL_CLOSE", "MOBILE": "MANUAL_CLOSE",
                  "WEB": "MANUAL_CLOSE"}.get(name, name)
        if mapped == "EXPERT_CLOSE" and requested:
            mapped = "ADAPTIVE_EXIT"
        return {"exit_reason": mapped, "exit_reason_evidence": f"broker DEAL_REASON={name}"}
    if requested and abs(last.time_utc - requested) <= 120:
        return {"exit_reason": "ADAPTIVE_EXIT",
                "exit_reason_evidence": "runtime recorded its own exit request "
                                        f"{last.time_utc - requested}s before the closing deal"}
    comment = (last.comment or "").lower()
    for prefix, label in (("[sl", "BROKER_STOP_LOSS"), ("[tp", "BROKER_TAKE_PROFIT"), ("[so", "BROKER_STOP_OUT")):
        if comment.startswith(prefix):
            return {"exit_reason": label, "exit_reason_evidence": "broker comment only (hint; no DEAL_REASON recorded)"}
    if journal_reason:
        return {"exit_reason": "UNKNOWN", "exit_reason_evidence": f"no broker reason; journal says: {journal_reason}"}
    return {"exit_reason": "UNKNOWN", "exit_reason_evidence": "no broker reason or runtime exit request recorded"}


def _classify_source(local: dict | None, ctx: dict | None, entries: list[_Deal], exits: list[_Deal],
                     asn_magics: set[int], chain_events: dict[str, list[dict]],
                     conn: sqlite3.Connection) -> tuple[str, str | None, list[dict]]:
    checks: list[dict] = []
    if local is not None:
        chain = local.get("chain_key")
        ok_chain = bool(chain) and str(chain).startswith(RUNTIME_ENTRY_CHAIN_PREFIX)
        checks.append({"check": "local position -> entry order -> runtime entry chain", "passed": ok_chain,
                       "detail": chain})
        proposal = None
        if ok_chain:
            if chain not in chain_events:
                chain_events[chain] = _chain_events(conn, chain)
            proposal = _proposal_strategy(chain_events[chain])
        checks.append({"check": "journal PROPOSAL_CREATED strategy == position strategy",
                       "passed": proposal is not None and proposal == local.get("strategy_key"),
                       "detail": f"proposal={proposal} position={local.get('strategy_key')}"})
        if ctx is not None:
            checks.append({"check": "position_entry_context agrees (strategy and chain)",
                           "passed": ctx.get("strategy_key") == local.get("strategy_key") and ctx.get("chain_key") == chain,
                           "detail": f"context={ctx.get('strategy_key')} / {ctx.get('chain_key')}"})
        if entries and local.get("broker_order_id"):
            checks.append({"check": "broker entry deal order ticket == local broker_order_id",
                           "passed": entries[0].order_ticket == str(local["broker_order_id"]),
                           "detail": f"deal order={entries[0].order_ticket} local={local.get('broker_order_id')}"})
        if not entries:
            checks.append({"check": "broker entry deal present in the recorded population", "passed": False,
                           "detail": "no IN deal recorded for this position"})
        if all(c["passed"] for c in checks):
            return ATTRIBUTED, local.get("strategy_key"), checks
        return ASN_UNATTRIBUTED, None, checks
    first = entries[0] if entries else (exits[0] if exits else None)
    magic = first.magic if first else None
    reason = first.reason if first else None
    if magic is not None and magic in asn_magics:
        checks.append({"check": "local position record exists", "passed": False,
                       "detail": f"magic {magic} is this runtime's, but no local position/order chain was recorded"})
        return ASN_UNATTRIBUTED, None, checks
    if reason is not None and reason in MANUAL_REASONS:
        return MANUAL, None, [{"check": "broker DEAL_REASON", "passed": True,
                               "detail": DEAL_REASON_NAMES[reason]}]
    if magic not in (None, 0) or reason == EXPERT_REASON:
        return EXTERNAL_EXPERT, None, [{"check": "foreign expert magic", "passed": True, "detail": f"magic={magic}"}]
    return UNKNOWN_SOURCE, None, [{"check": "source evidence", "passed": False,
                                   "detail": "magic 0 and no broker DEAL_REASON recorded (consistent with manual "
                                             "activity, not proven)"}]


def build_demo_ledger(conn: sqlite3.Connection, login: int | None = None) -> dict:
    """The complete DEMO trade ledger for one broker account (default: the
    account holding the most recent imported deal): one record per broker
    position, plus non-trade deals, discrepancies and the raw deals."""
    default_login, logins = _default_login(conn)
    login = login if login is not None else default_login
    deals, discrepancies = _load_deals(conn, login)
    asn_magics = _asn_magics(conn)
    local_positions = _local_positions(conn)
    contexts = _entry_context(conn)
    cost_ev = _cost_evidence(conn)
    exit_ev = _exit_evidence(conn)
    journal_close = _close_reasons_from_journal(conn)
    currency = account_currency(conn)

    by_position: dict[str, list[_Deal]] = defaultdict(list)
    non_trade: list[_Deal] = []
    orphan_trade: list[_Deal] = []
    for d in deals.values():
        if not d.is_trade and d.type_code is not None:
            non_trade.append(d)
        elif d.position_id:
            by_position[d.position_id].append(d)
        else:
            orphan_trade.append(d)

    chain_events: dict[str, list[dict]] = {}
    trades: list[dict] = []
    for pid, pdeals in by_position.items():
        pdeals.sort(key=lambda d: (d.time_utc, d.ticket))
        entries = [d for d in pdeals if d.entry == "IN"]
        exits = [d for d in pdeals if d.entry in ("OUT", "OUT_BY")]
        reversals = [d for d in pdeals if d.entry == "INOUT"]
        unknown_entry = [d for d in pdeals if d.entry not in ("IN", "OUT", "OUT_BY", "INOUT")]
        in_vol = sum(d.volume for d in entries)
        out_vol = sum(d.volume for d in exits)
        local = local_positions.get(pid)
        ctx = contexts.get(pid)
        source, strategy_key, checks = _classify_source(local, ctx, entries, exits, asn_magics, chain_events, conn)

        if reversals:
            status = "REVERSAL_UNSUPPORTED"
        elif unknown_entry:
            status = "UNKNOWN_DEAL_ENTRY"
        elif in_vol <= VOLUME_EPSILON:
            status = "ENTRY_NOT_IN_HISTORY"
        elif out_vol <= VOLUME_EPSILON:
            status = "OPEN"
        elif out_vol + VOLUME_EPSILON < in_vol:
            status = "PARTIALLY_CLOSED"
        else:
            status = "CLOSED"
        gross = sum(d.profit for d in pdeals)
        commission = sum(d.commission for d in pdeals)
        fee = sum(d.fee for d in pdeals)
        swap = sum(d.swap for d in pdeals)
        net = gross + commission + fee + swap
        entry_money = sum(d.commission + d.fee for d in entries)
        if status in ("OPEN", "PARTIALLY_CLOSED"):
            closed_fraction = min(1.0, out_vol / in_vol) if in_vol > VOLUME_EPSILON else 0.0
        else:
            closed_fraction = 1.0
        open_entry_costs = entry_money * (1.0 - closed_fraction)
        realized = net - open_entry_costs   # == exit money + swap + entry profit + entry costs x closed share

        risk = local.get("initial_monetary_risk") if local else None
        realized_r = realized / risk if (risk and risk > 0 and status == "CLOSED") else None
        order_id = local.get("entry_order_id") if local else None
        cev = cost_ev.get(order_id) if order_id is not None else None
        xev = exit_ev.get(local["local_position_id"]) if local else None
        exit_info = _classify_exit(pdeals, xev, journal_close.get(pid))
        entry_time = entries[0].time_utc if entries else (local.get("opened_at_utc") if local else None)
        exit_time = exits[-1].time_utc if exits else None
        symbol = next((d.symbol for d in entries if d.symbol), None) or (
            local.get("canonical_symbol") if local else None) or next((d.symbol for d in pdeals if d.symbol), None)
        direction = ({DEAL_TYPE_BUY: "BUY", DEAL_TYPE_SELL: "SELL"}.get(entries[0].type_code) if entries else None) \
            or (local.get("direction") if local else None)
        trades.append({
            "evidence": "DEMO", "trade_id": pid, "broker_position_id": pid,
            "source_class": source, "attribution": ATTRIBUTED if source == ATTRIBUTED else "UNATTRIBUTED",
            "attribution_checks": checks, "strategy_key": strategy_key,
            "strategy_version": (ctx or {}).get("strategy_version") if source == ATTRIBUTED else None,
            "regime": ((ctx or {}).get("entry_regime") or (xev or {}).get("entry_regime")) if local else None,
            "session": (cev or {}).get("session"),
            "symbol": symbol, "direction": direction, "status": status,
            "entry_time_utc": entry_time, "exit_time_utc": exit_time,
            "holding_seconds": (exit_time - entry_time) if (status == "CLOSED" and exit_time and entry_time) else None,
            "entry_volume": round(in_vol, 8), "closed_volume": round(out_vol, 8),
            "entry_price": (sum(d.price * d.volume for d in entries) / in_vol) if in_vol > VOLUME_EPSILON else None,
            "exit_price": (sum(d.price * d.volume for d in exits) / out_vol) if out_vol > VOLUME_EPSILON else None,
            "deal_count": len(pdeals), "entry_deal_count": len(entries), "exit_deal_count": len(exits),
            "gross_pnl": gross, "commission": commission, "fee": fee, "swap": swap,
            "costs": commission + fee + swap, "net_pnl": net,
            "realized_net_pnl": realized, "open_volume_entry_costs": open_entry_costs,
            "initial_monetary_risk": risk, "realized_r": realized_r,
            "initial_stop_loss": local.get("initial_stop_loss") if local else None,
            "initial_take_profit": local.get("initial_take_profit") if local else None,
            "exit_reason": exit_info["exit_reason"], "exit_reason_evidence": exit_info["exit_reason_evidence"],
            "entry_magic": entries[0].magic if entries else None,
            "local_position_id": local.get("local_position_id") if local else None,
            "local_status": local.get("status") if local else None,
            "entry_order_id": order_id, "chain_key": local.get("chain_key") if local else None,
            "spread_price_at_entry": (cev or {}).get("spread_price"),
            "entry_slippage_price": (cev or {}).get("slippage_price"),
            "exit_slippage_price": (xev or {}).get("exit_realized_slippage_price"),
            "expected_total_cost_price": (cev or {}).get("estimated_total_price"),
            "cost_provenance": "BROKER_RECORDED_DEALS",
            "recorded_by": sorted({s for d in pdeals for s in d.sources}), "currency": currency,
        })

    missing_local = [{"broker_position_id": pid, "strategy_key": lp.get("strategy_key"), "status": lp.get("status")}
                     for pid, lp in local_positions.items() if pid not in by_position]
    trades.sort(key=lambda t: (t["exit_time_utc"] or t["entry_time_utc"] or 0, t["trade_id"]), reverse=True)
    return {
        "login": login, "logins_in_history": logins, "currency": currency, "asn_magics": sorted(asn_magics),
        "trades": trades, "non_trade_deals": [d.as_dict() for d in sorted(non_trade, key=lambda d: d.time_utc)],
        "trade_deals_without_position": [d.as_dict() for d in orphan_trade],
        "deal_source_discrepancies": discrepancies, "local_positions_without_deals": missing_local,
        "deals_by_position": {pid: [d.as_dict() for d in ds] for pid, ds in by_position.items()},
        "population_deal_count": len(deals),
    }


def population_sql_total(conn: sqlite3.Connection, login: int | None) -> dict:
    """INDEPENDENT recomputation of the population money straight from SQL
    (not from the ledger): every broker-history deal for `login` plus every
    runtime-recorded deal whose ticket is not in that import."""
    total, count = 0.0, 0
    tickets: set[str] = set()
    if _table_exists(conn, "broker_account_deals"):
        sql = "SELECT CAST(ticket AS TEXT), profit + commission + swap + fee FROM broker_account_deals"
        rows = conn.execute(sql + " WHERE login = ?", (login,)) if login is not None else conn.execute(sql)
        for t, v in rows:
            tickets.add(t)
            total += v
            count += 1
    if _table_exists(conn, "deals"):
        fee = "fee" if "fee" in _columns(conn, "deals") else "0"
        for t, v in conn.execute(f"SELECT broker_deal_id, profit + commission + swap + {fee} FROM deals"):  # nosec B608
            if str(t) not in tickets:
                total += v
                count += 1
    return {"deal_count": count, "net_total": total}


# ---------------------------------------------------------------------------
# cache (keyed by a cheap database fingerprint, so freshness is exact)
# ---------------------------------------------------------------------------
_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple, tuple[float, dict]] = {}
_FINGERPRINT_TABLES = ("broker_account_deals", "deals", "positions", "orders", "journal_events",
                       "position_entry_context", "execution_cost_observations", "position_management_state")


def _drop_cache_entries() -> None:
    """Caller holds _CACHE_LOCK. (Deliberately not dict.clear(): dashboard modules are statically
    checked never to call anything named `clear`, the kill-switch operator verb.)"""
    for key in list(_CACHE):
        del _CACHE[key]


def data_fingerprint(conn: sqlite3.Connection) -> tuple:
    """MAX(rowid)/COUNT(*) per source table (index-only, cheap) plus the
    positions' status/close markers, which change in place."""
    parts: list = [tuple((r[1], r[2]) for r in conn.execute("PRAGMA database_list"))]  # which database file
    for table in _FINGERPRINT_TABLES:
        if _table_exists(conn, table):
            parts.append((table, *conn.execute(f"SELECT MAX(rowid), COUNT(*) FROM {table}").fetchone()))  # nosec B608
    if _table_exists(conn, "positions"):
        parts.append(conn.execute("SELECT COALESCE(SUM(closed_at_utc), 0), SUM(status = 'CLOSED') FROM positions")
                     .fetchone())
    if _table_exists(conn, "position_management_state"):
        parts.append(conn.execute("SELECT MAX(updated_at_utc) FROM position_management_state").fetchone())
    parts.append(account_currency(conn))
    return tuple(parts)


def cached_demo_ledger(conn: sqlite3.Connection, login: int | None = None) -> tuple[dict, dict]:
    """(ledger, freshness). Recomputed whenever any underlying table
    changes; an identical database is served from memory for at most
    LEDGER_CACHE_SECONDS. The freshness block tells the UI exactly when the
    figures were computed."""
    key = (login, data_fingerprint(conn))
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None and now - hit[0] <= LEDGER_CACHE_SECONDS:
            return hit[1], {"computed_at_utc": int(hit[0]), "cache": "HIT", "database_unchanged_since": True}
    started = time.perf_counter()
    ledger = build_demo_ledger(conn, login)
    ledger["compute_seconds"] = round(time.perf_counter() - started, 4)
    with _CACHE_LOCK:
        _drop_cache_entries()
        _CACHE[key] = (now, ledger)
    return ledger, {"computed_at_utc": int(now), "cache": "MISS", "database_unchanged_since": True}


def reset_cache() -> None:
    with _CACHE_LOCK:
        _drop_cache_entries()


# ---------------------------------------------------------------------------
# PAPER / BACKTEST trade records (normalised to the same shape)
# ---------------------------------------------------------------------------
def _sim_trade(d: dict, evidence: str, currency: str | None) -> dict:
    entry_t, exit_t = d.get("entry_time_utc"), d.get("exit_time_utc")

    def pair(a: str, b: str) -> float | None:
        if d.get(a) is None and d.get(b) is None:
            return None
        return (d.get(a) or 0.0) + (d.get(b) or 0.0)

    total_cost = d.get("total_cost")
    return {
        "evidence": evidence, "trade_id": str(d["id"]), "run_id": d.get("run_id"),
        "session_key": d.get("session_key"),
        "source_class": ATTRIBUTED if d.get("strategy_key") else UNKNOWN_SOURCE,
        "attribution": ATTRIBUTED if d.get("strategy_key") else "UNATTRIBUTED",
        "strategy_key": d.get("strategy_key"), "strategy_version": d.get("strategy_version"),
        "regime": d.get("entry_regime"), "exit_regime": d.get("exit_regime"),
        "session": _session_of_hour(time.gmtime(entry_t).tm_hour) if entry_t else None,
        "symbol": d.get("canonical_symbol"), "direction": d.get("direction"), "status": "CLOSED",
        "entry_time_utc": entry_t, "exit_time_utc": exit_t,
        "holding_seconds": (exit_t - entry_t) if (entry_t and exit_t) else None,
        "entry_volume": d.get("volume"), "closed_volume": d.get("volume"),
        "entry_price": d.get("entry_price"), "exit_price": d.get("exit_price"),
        # simulated gross is BEFORE simulated costs; spread/slippage are separate simulated costs here
        "gross_pnl": d.get("gross_pnl"), "commission": d.get("commission_cost"), "fee": d.get("fee_cost"),
        "swap": d.get("swap_cost"), "spread_cost": pair("entry_spread_cost", "exit_spread_cost"),
        "slippage_cost": pair("entry_slippage_cost", "exit_slippage_cost"),
        "costs": (-total_cost if total_cost is not None else None), "net_pnl": d.get("realized_pnl"),
        "realized_net_pnl": d.get("realized_pnl"), "open_volume_entry_costs": 0.0,
        "initial_monetary_risk": d.get("initial_monetary_risk"), "realized_r": d.get("realized_r"),
        "exit_reason": d.get("exit_reason"), "exit_reason_evidence": "simulation record",
        "cost_provenance": d.get("cost_provenance"), "fill_model_version": d.get("fill_model_version"),
        "currency": currency, "currency_basis": "simulated; account currency of the captured broker specs",
    }


_SIM_COLUMNS = ("id", "canonical_symbol", "strategy_key", "strategy_version", "direction", "entry_time_utc",
                "exit_time_utc", "entry_price", "exit_price", "volume", "initial_monetary_risk", "entry_regime",
                "exit_regime", "exit_reason", "realized_r", "realized_pnl", "gross_pnl", "total_cost",
                "commission_cost", "fee_cost", "swap_cost", "entry_spread_cost", "exit_spread_cost",
                "entry_slippage_cost", "exit_slippage_cost", "cost_provenance", "fill_model_version")


def load_sim_trades(conn: sqlite3.Connection, evidence: str, run_id: str | None = None,
                    session_key: str | None = None) -> list[dict]:
    table = "paper_trades" if evidence == "PAPER" else "backtest_trades"
    if not _table_exists(conn, table):
        return []
    have = _columns(conn, table)
    extra = "run_id" if evidence == "BACKTEST" else "session_key"
    cols = [c if c in have else f"NULL AS {c}" for c in (*_SIM_COLUMNS, extra)]
    sql = f"SELECT {', '.join(cols)} FROM {table}"  # nosec B608 - fixed column/table names
    params: list = []
    if evidence == "BACKTEST" and run_id:
        sql += " WHERE run_id = ?"
        params.append(run_id)
    elif evidence == "PAPER" and session_key:
        sql += " WHERE session_key = ?"
        params.append(session_key)
    currency = account_currency(conn)
    trades = [_sim_trade(r, evidence, currency) for r in _rows(conn, sql, params)]
    trades.sort(key=lambda t: (t["exit_time_utc"] or 0, t["trade_id"]), reverse=True)
    return trades


def backtest_runs(conn: sqlite3.Connection) -> list[dict]:
    if not _table_exists(conn, "backtest_runs"):
        return []
    return _rows(conn, "SELECT run_id, created_at_utc, canonical_symbol, resolution, run_type, range_start_utc, "
                       "range_end_utc, trade_count, gross_pnl, net_pnl, total_cost, cost_provenance, "
                       "fill_model_version, config_fingerprint, dataset_id, origin FROM backtest_runs "
                       "ORDER BY created_at_utc DESC, run_id")


def paper_sessions(conn: sqlite3.Connection) -> list[dict]:
    if not _table_exists(conn, "paper_trades"):
        return []
    return _rows(conn, "SELECT session_key, canonical_symbol AS symbol, COUNT(*) AS trades FROM paper_trades "
                       "GROUP BY session_key, canonical_symbol ORDER BY session_key")


# ---------------------------------------------------------------------------
# filters & metrics
# ---------------------------------------------------------------------------
_EQUALITY_FILTERS = (("symbol", "symbol"), ("regime", "regime"), ("direction", "direction"),
                     ("session", "session"), ("exit_reason", "exit_reason"), ("cost_provenance", "cost_provenance"),
                     ("source_class", "source_class"), ("status", "status"), ("version", "strategy_version"))


def apply_filters(trades: list[dict], f: dict) -> list[dict]:
    """Date filters select by close time (entry time for trades that are not
    closed). `strategy=UNATTRIBUTED` selects every trade without a proven
    strategy. Nothing is ever dropped for being a loss."""
    def keep(t: dict) -> bool:
        wanted = f.get("strategy")
        if wanted:
            if wanted == "UNATTRIBUTED":
                if t["strategy_key"] is not None:
                    return False
            elif t["strategy_key"] != wanted:
                return False
        for key, col in _EQUALITY_FILTERS:
            if f.get(key) not in (None, "") and str(t.get(col)) != str(f[key]):
                return False
        ts = t.get("exit_time_utc") or t.get("entry_time_utc") or 0
        if f.get("date_from") is not None and ts < int(f["date_from"]):
            return False
        if f.get("date_to") is not None and ts >= int(f["date_to"]):
            return False
        if f.get("q"):
            hay = " ".join(str(t.get(k) or "") for k in (
                "trade_id", "strategy_key", "symbol", "source_class", "exit_reason", "chain_key", "run_id"))
            if str(f["q"]).lower() not in hay.lower():
                return False
        return True
    return [t for t in trades if keep(t)]


def compute_metrics(trades: list[dict]) -> dict:
    """Metrics over CLOSED trades only (an entry is never a completed
    trade). Any figure that cannot be computed honestly is None; zero
    closed trades is NO_CLOSED_TRADES, never a 0 % win rate."""
    closed = sorted((t for t in trades if t["status"] == "CLOSED"),
                    key=lambda t: (t.get("exit_time_utc") or 0, t["trade_id"]))
    n = len(closed)
    nets = [t["realized_net_pnl"] for t in closed if t.get("realized_net_pnl") is not None]
    wins = [x for x in nets if x > BREAKEVEN_EPSILON]
    losses = [x for x in nets if x < -BREAKEVEN_EPSILON]

    def total(key: str) -> float | None:
        vals = [t.get(key) for t in closed]
        if any(v is None for v in vals):
            return None
        return float(sum(vals))

    out: dict = {
        "closed_trades": n, "sample_status": "NO_CLOSED_TRADES" if n == 0 else "OK",
        "wins": len(wins), "losses": len(losses), "breakevens": len(nets) - len(wins) - len(losses),
        "win_rate": (len(wins) / n) if n else None,
        "gross_pnl": total("gross_pnl"), "commission": total("commission"), "fee": total("fee"),
        "swap": total("swap"), "net_pnl": float(sum(nets)),
        "gross_profit_of_winners": float(sum(wins)), "gross_loss_of_losers": float(sum(losses)),
    }
    if not n:
        out["profit_factor"], out["profit_factor_note"] = None, "NO_CLOSED_TRADES"
    elif losses:
        out["profit_factor"], out["profit_factor_note"] = sum(wins) / abs(sum(losses)), None
    elif wins:
        out["profit_factor"], out["profit_factor_note"] = None, "NO_LOSSES (undefined, not an infinite edge)"
    else:
        out["profit_factor"], out["profit_factor_note"] = None, "ALL_BREAKEVEN"
    rs = [t["realized_r"] for t in closed if t.get("realized_r") is not None]
    out["avg_r"] = (sum(rs) / len(rs)) if rs else None
    out["r_sample"] = len(rs)
    out["expectancy_per_trade"] = (sum(nets) / n) if n else None
    holds = [t["holding_seconds"] for t in closed if t.get("holding_seconds") is not None]
    out["avg_holding_seconds"] = (sum(holds) / len(holds)) if holds else None
    running = peak = max_dd = 0.0
    curve = []
    for t in closed:
        running += t["realized_net_pnl"] or 0.0
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
        curve.append({"t": t.get("exit_time_utc"), "cum_net": round(running, 2), "trade_id": t["trade_id"]})
    out["max_drawdown_closed_trade_basis"] = max_dd if n else None
    out["cumulative_net_curve"] = curve
    out["last_closed_trade"] = ({"trade_id": closed[-1]["trade_id"], "exit_time_utc": closed[-1]["exit_time_utc"],
                                 "net_pnl": closed[-1]["realized_net_pnl"]} if n else None)
    spreads = [t["spread_price_at_entry"] for t in closed if t.get("spread_price_at_entry") is not None]
    slips = [t["entry_slippage_price"] for t in closed if t.get("entry_slippage_price") is not None]
    xslips = [t["exit_slippage_price"] for t in closed if t.get("exit_slippage_price") is not None]
    out["spread_slippage_evidence"] = {
        "avg_entry_spread_price": (sum(spreads) / len(spreads)) if spreads else None, "spread_sample": len(spreads),
        "avg_entry_slippage_price": (sum(slips) / len(slips)) if slips else None, "entry_slippage_sample": len(slips),
        "avg_exit_slippage_price": (sum(xslips) / len(xslips)) if xslips else None, "exit_slippage_sample": len(xslips),
    }
    sim_spread = [t["spread_cost"] for t in closed if t.get("spread_cost") is not None]
    sim_slip = [t["slippage_cost"] for t in closed if t.get("slippage_cost") is not None]
    if sim_spread or sim_slip:
        out["spread_slippage_evidence"]["simulated_spread_cost_total"] = sum(sim_spread) if sim_spread else None
        out["spread_slippage_evidence"]["simulated_slippage_cost_total"] = sum(sim_slip) if sim_slip else None
    return out


def _distribution(values: list[float], edges: list[float]) -> list[dict]:
    out = []
    bounds = [float("-inf"), *edges, float("inf")]
    for lo, hi in zip(bounds[:-1], bounds[1:], strict=True):
        if lo == float("-inf"):
            label = f"< {hi:g}"
        elif hi == float("inf"):
            label = f">= {lo:g}"
        else:
            label = f"{lo:g} to {hi:g}"
        out.append({"bucket": label, "count": sum(1 for v in values if lo <= v < hi)})
    return out


def breakdowns(trades: list[dict]) -> dict:
    closed = [t for t in trades if t["status"] == "CLOSED"]

    def group(key: str) -> list[dict]:
        g: dict = defaultdict(list)
        missing = "UNATTRIBUTED" if key == "strategy_key" else "N/A (not recorded)"
        for t in closed:
            g[str(t[key]) if t.get(key) is not None else missing].append(t)
        return [{key: k, "closed_trades": len(v), "net_pnl": sum(x["realized_net_pnl"] or 0.0 for x in v),
                 "gross_pnl": (sum(x["gross_pnl"] for x in v) if all(x.get("gross_pnl") is not None for x in v)
                               else None),
                 "costs": (sum(x["costs"] for x in v) if all(x.get("costs") is not None for x in v) else None),
                 "wins": sum(1 for x in v if (x["realized_net_pnl"] or 0) > BREAKEVEN_EPSILON),
                 "losses": sum(1 for x in v if (x["realized_net_pnl"] or 0) < -BREAKEVEN_EPSILON)}
                for k, v in sorted(g.items())]

    by_day: dict = defaultdict(lambda: {"closed_trades": 0, "net_pnl": 0.0})
    for t in closed:
        if t.get("exit_time_utc"):
            day = time.strftime("%Y-%m-%d", time.gmtime(t["exit_time_utc"]))
            by_day[day]["closed_trades"] += 1
            by_day[day]["net_pnl"] += t["realized_net_pnl"] or 0.0
    nets = [t["realized_net_pnl"] for t in closed if t.get("realized_net_pnl") is not None]
    rs = [t["realized_r"] for t in closed if t.get("realized_r") is not None]
    return {
        "by_strategy": group("strategy_key"), "by_symbol": group("symbol"), "by_regime": group("regime"),
        "by_direction": group("direction"), "by_session": group("session"), "by_exit_reason": group("exit_reason"),
        "by_day": [{"day": k, **v} for k, v in sorted(by_day.items())],
        "net_distribution": _distribution(nets, [-50, -20, -10, -5, -1, 0, 1, 5, 10, 20, 50]),
        "r_distribution": _distribution(rs, [-2, -1, -0.5, -0.25, 0, 0.25, 0.5, 1, 2]),
    }


# ---------------------------------------------------------------------------
# DEMO runtime funnel: signal -> selected -> allowed -> submitted -> filled
# ---------------------------------------------------------------------------
_EMPTY_FUNNEL = {"signals": 0, "signals_rejected": 0, "proposals_rejected": 0, "selected_proposals": 0,
                 "entry_allowed_chains": 0,
                 "entry_blocked_chains": 0, "orders_created": 0, "orders_submitted": 0, "orders_filled": 0,
                 "signal_to_order_conversion": None, "order_to_fill_conversion": None}


def demo_funnel(conn: sqlite3.Connection, lo: int | None, hi: int | None) -> dict:
    """Per-strategy conversion counted ONLY from runtime entry chains
    (`entry:` — the one chain family the DEMO runtime writes). Journal
    chains from any other producer are counted separately, never mixed in."""
    lo = int(lo) if lo is not None else 0
    hi = int(hi) if hi is not None else 2**62
    counts: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    if not _table_exists(conn, "journal_events"):
        return {"by_strategy": {}, "non_runtime_signal_events": 0, "note": "no journal"}
    for key, etype, n in conn.execute(
        "SELECT j.strategy_key, j.event_type, COUNT(*) FROM journal_events j "
        "JOIN decision_chains d ON d.id = j.chain_id "
        "WHERE d.chain_key LIKE 'entry:%' AND j.event_timestamp_utc >= ? AND j.event_timestamp_utc < ? "
        "AND j.event_type IN ('SIGNAL_CREATED','SIGNAL_REJECTED','PROPOSAL_CREATED','PROPOSAL_REJECTED') "
        "AND j.strategy_key IS NOT NULL "
        "GROUP BY j.strategy_key, j.event_type", (lo, hi),
    ):
        counts[key][etype] = n
    # ENTRY_ALLOWED/BLOCKED rows written by the execution layer carry no strategy: resolve via the chain's proposal
    for key, etype, n in conn.execute(
        "SELECT (SELECT p.strategy_key FROM journal_events p WHERE p.chain_id = j.chain_id "
        "        AND p.event_type = 'PROPOSAL_CREATED' LIMIT 1) AS sk, j.event_type, COUNT(DISTINCT j.chain_id) "
        "FROM journal_events j JOIN decision_chains d ON d.id = j.chain_id "
        "WHERE d.chain_key LIKE 'entry:%' AND j.event_timestamp_utc >= ? AND j.event_timestamp_utc < ? "
        "AND j.event_type IN ('ENTRY_ALLOWED','ENTRY_BLOCKED') GROUP BY sk, j.event_type", (lo, hi),
    ):
        if key:
            counts[key][etype + "_CHAINS"] = n
    if _table_exists(conn, "orders"):
        for key, created, submitted, filled in conn.execute(
            "SELECT (SELECT p.strategy_key FROM journal_events p JOIN decision_chains d ON d.id = p.chain_id "
            "        WHERE d.chain_key = o.chain_key AND p.event_type = 'PROPOSAL_CREATED' LIMIT 1) AS sk, "
            "COUNT(*), "
            "SUM(CASE WHEN EXISTS (SELECT 1 FROM order_state_transitions t WHERE t.order_id = o.id "
            "    AND t.to_state = 'SUBMITTED') THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN o.filled_volume > 0 THEN 1 ELSE 0 END) "
            "FROM orders o WHERE o.chain_key LIKE 'entry:%' AND o.created_at_utc >= ? AND o.created_at_utc < ? "
            "GROUP BY sk", (lo, hi),
        ):
            if key:
                counts[key]["ORDERS_CREATED"] = created
                counts[key]["ORDERS_SUBMITTED"] = submitted or 0
                counts[key]["ORDERS_FILLED"] = filled or 0
    other = conn.execute(
        "SELECT COUNT(*) FROM journal_events j JOIN decision_chains d ON d.id = j.chain_id "
        "WHERE d.chain_key NOT LIKE 'entry:%' AND j.event_type = 'SIGNAL_CREATED' "
        "AND j.event_timestamp_utc >= ? AND j.event_timestamp_utc < ?", (lo, hi),
    ).fetchone()[0]
    result = {}
    for key, c in counts.items():
        signals, submitted, filled = c.get("SIGNAL_CREATED", 0), c.get("ORDERS_SUBMITTED", 0), c.get("ORDERS_FILLED", 0)
        result[key] = {
            "signals": signals, "signals_rejected": c.get("SIGNAL_REJECTED", 0),
            "proposals_rejected": c.get("PROPOSAL_REJECTED", 0),
            "selected_proposals": c.get("PROPOSAL_CREATED", 0),
            "entry_allowed_chains": c.get("ENTRY_ALLOWED_CHAINS", 0),
            "entry_blocked_chains": c.get("ENTRY_BLOCKED_CHAINS", 0),
            "orders_created": c.get("ORDERS_CREATED", 0), "orders_submitted": submitted, "orders_filled": filled,
            "signal_to_order_conversion": (submitted / signals) if signals else None,
            "order_to_fill_conversion": (filled / submitted) if submitted else None,
        }
    return {"by_strategy": result, "non_runtime_signal_events": other,
            "note": "Counted from DEMO runtime entry chains only. An order row that never reached SUBMITTED was "
                    "stopped by a permission gate before any broker call and is not a submitted order. "
                    f"{other} SIGNAL_CREATED event(s) in this window belong to non-runtime journal chains "
                    "(legacy replay/CLI) and are excluded."}


def _latest_event(conn: sqlite3.Connection, strategy_key: str, event_type: str) -> dict | None:
    if not _table_exists(conn, "journal_events"):
        return None
    row = conn.execute(
        "SELECT j.event_timestamp_utc, j.canonical_symbol, d.chain_key, j.payload_json FROM journal_events j "
        "JOIN decision_chains d ON d.id = j.chain_id WHERE j.event_type = ? AND j.strategy_key = ? "
        "AND d.chain_key LIKE 'entry:%' ORDER BY j.event_timestamp_utc DESC, j.id DESC LIMIT 1",
        (event_type, strategy_key),
    ).fetchone()
    if row is None:
        return None
    return {"event_timestamp_utc": row[0], "symbol": row[1], "chain_key": row[2], "payload": _json(row[3])}


def _latest_evaluation(conn: sqlite3.Connection, strategy_key: str) -> dict | None:
    if not _table_exists(conn, "entry_decisions"):
        return None
    rows = _rows(conn, "SELECT decided_at_utc, mode, canonical_symbol AS symbol, stage, decision, reason, chain_key "
                       "FROM entry_decisions WHERE strategy_key = ? ORDER BY id DESC LIMIT 1", (strategy_key,))
    return rows[0] if rows else None


def _why_no_trade(conn: sqlite3.Connection, strategy_key: str, limit: int = 25) -> list[dict]:
    if not _table_exists(conn, "entry_decisions"):
        return []
    return _rows(conn, "SELECT decided_at_utc, mode, canonical_symbol AS symbol, stage, decision, reason, chain_key "
                       "FROM entry_decisions WHERE strategy_key = ? AND decision != 'FILLED' "
                       "ORDER BY id DESC LIMIT ?", (strategy_key, limit))


# ---------------------------------------------------------------------------
# public API used by the dashboard endpoints
# ---------------------------------------------------------------------------
def _evidence_trades(conn: sqlite3.Connection, evidence: str, params: dict) -> tuple[list[dict], dict]:
    if evidence == "DEMO":
        ledger, freshness = cached_demo_ledger(conn, params.get("login"))
        return ledger["trades"], {"ledger": ledger, "freshness": freshness}
    if evidence == "PAPER":
        return load_sim_trades(conn, "PAPER", session_key=params.get("session_key")), {
            "sessions": paper_sessions(conn), "session_key": params.get("session_key")}
    runs = backtest_runs(conn)
    run_id = params.get("run_id") or (runs[0]["run_id"] if runs else None)
    return load_sim_trades(conn, "BACKTEST", run_id=run_id), {"runs": runs, "run_id": run_id}


def _strip(t: dict) -> dict:
    return {k: v for k, v in t.items() if k != "attribution_checks"}


def _filter_options(trades: list[dict]) -> dict:
    def opts(col: str) -> list:
        return sorted({str(t[col]) for t in trades if t.get(col) is not None})
    return {k: opts(c) for k, c in (("strategy", "strategy_key"), ("symbol", "symbol"), ("version", "strategy_version"),
                                    ("regime", "regime"), ("direction", "direction"), ("session", "session"),
                                    ("exit_reason", "exit_reason"), ("cost_provenance", "cost_provenance"),
                                    ("source_class", "source_class"), ("status", "status"))}


def summary(conn: sqlite3.Connection, evidence: str, filters: dict, params: dict) -> dict:
    """The comparison table: one row per ACTIVE strategy (always all six,
    even with no evidence), retired strategies listed separately, plus the
    UNATTRIBUTED block, all computed from ONE filtered trade set of ONE
    evidence source."""
    evidence = evidence.upper()
    if evidence not in EVIDENCE_SOURCES:
        raise ValueError(f"unknown evidence source {evidence!r}")
    catalog = strategy_source_catalog()
    trades, extra = _evidence_trades(conn, evidence, params)
    filtered = apply_filters(trades, filters)
    funnel = demo_funnel(conn, filters.get("date_from"), filters.get("date_to")) if evidence == "DEMO" else None
    keys = list(catalog) + sorted({t["strategy_key"] for t in filtered if t["strategy_key"]} - set(catalog))
    floating = live_floating_pnl(conn) if evidence == "DEMO" else None

    def unrealized(open_trades: list[dict]) -> tuple[float | None, str | None]:
        if not open_trades:
            return None, "NO_OPEN_POSITIONS"
        if evidence != "DEMO":
            return None, "SIMULATED (no broker floating P&L)"
        if floating is None:
            return None, "NO_FRESH_BROKER_SAMPLE"
        vals = [floating.get(str(t["broker_position_id"])) for t in open_trades]
        if any(v is None for v in vals):
            return None, "POSITION_NOT_IN_BROKER_SAMPLE"
        return float(sum(vals)), None

    rows, curves = [], {}
    for key in keys:
        st = [t for t in filtered if t["strategy_key"] == key]
        m = compute_metrics(st)
        curves[key] = m.pop("cumulative_net_curve")
        open_trades = [t for t in st if t["status"] in ("OPEN", "PARTIALLY_CLOSED")]
        last_exec = max((t for t in st if t.get("entry_time_utc")), key=lambda t: t["entry_time_utc"], default=None)
        row = {
            "strategy_key": key,
            "strategy_version": (catalog.get(key) or {}).get("version"),
            "versions_in_evidence": sorted({t["strategy_version"] for t in st if t.get("strategy_version") is not None}),
            "registration_status": ("RETIRED_PERMANENTLY" if key in RETIRED_STRATEGY_KEYS else
                                    "REGISTERED" if key in catalog else "NOT_REGISTERED"),
            "trade_records": len(st), "open_positions": len(open_trades),
            "open_exposure_initial_risk": sum(t.get("initial_monetary_risk") or 0.0 for t in open_trades),
            **dict(zip(("unrealized_pnl", "unrealized_note"), unrealized(open_trades), strict=True)),
            "most_recent_executed_trade": ({"trade_id": last_exec["trade_id"],
                                            "entry_time_utc": last_exec["entry_time_utc"],
                                            "symbol": last_exec["symbol"]} if last_exec else None),
            **m,
        }
        if funnel is not None:
            row["funnel"] = funnel["by_strategy"].get(key, dict(_EMPTY_FUNNEL))
        rows.append(row)
    un = [t for t in filtered if t["strategy_key"] is None]
    un_by_class: dict = defaultdict(lambda: {"trade_records": 0, "closed": 0, "realized_net_pnl": 0.0,
                                             "net_pnl_all_deals": 0.0})
    for t in un:
        c = un_by_class[t["source_class"]]
        c["trade_records"] += 1
        c["closed"] += 1 if t["status"] == "CLOSED" else 0
        c["realized_net_pnl"] += t["realized_net_pnl"] or 0.0
        c["net_pnl_all_deals"] += t["net_pnl"] or 0.0
    un_metrics = compute_metrics(un)
    un_metrics.pop("cumulative_net_curve")
    out = {
        "evidence": evidence, "filters": filters, "currency": account_currency(conn),
        "strategies": rows, "retired_strategies": sorted(RETIRED_STRATEGY_KEYS),
        "active_strategy_keys": list(catalog),
        "unattributed": {"trade_records": len(un), "metrics": un_metrics, "by_source_class": dict(un_by_class)},
        "filtered_trade_records": len(filtered), "total_trade_records": len(trades),
        "filter_options": _filter_options(trades), "curves": curves, "breakdowns": breakdowns(filtered),
        "generated_at_utc": int(time.time()),
    }
    if evidence == "DEMO":
        out["funnel_note"] = funnel["note"]
        out["non_runtime_signal_events"] = funnel["non_runtime_signal_events"]
        out["reconciliation"] = reconciliation(conn, extra["ledger"])
        out["freshness"] = extra["freshness"]
        out["broker_history_coverage"] = _history_coverage(conn, extra["ledger"])
        out["open_position_note"] = ("Floating P&L of open positions comes from the runtime's live telemetry "
                                     "(Overview), never from this realized ledger.")
    elif evidence == "BACKTEST":
        out["runs"], out["run_id"] = extra["runs"], extra["run_id"]
        out["run"] = next((r for r in extra["runs"] if r["run_id"] == extra["run_id"]), None)
        out["evidence_note"] = ("Historical research evaluation of one run only; not evidence of current live "
                                "performance. The reserved out-of-sample interval is never run from here.")
    else:
        out["sessions"], out["session_key"] = extra["sessions"], extra["session_key"]
        out["evidence_note"] = ("PAPER sessions are independent per-symbol simulations: figures are per-trade "
                                "statistics, never one pooled portfolio equity curve.")
    return out


def _history_coverage(conn: sqlite3.Connection, ledger: dict) -> dict:
    cov: dict = {"login": ledger.get("login"), "logins_in_history": ledger.get("logins_in_history"),
                 "broker_history_latest_deal_utc": None, "broker_history_last_import": None,
                 "local_latest_deal_utc": None, "local_deals_missing_from_broker_import": 0}
    if _table_exists(conn, "broker_account_deals"):
        row = conn.execute("SELECT MAX(time_utc), MAX(imported_at) FROM broker_account_deals").fetchone()
        cov["broker_history_latest_deal_utc"], cov["broker_history_last_import"] = row[0], row[1]
    if _table_exists(conn, "deals"):
        cov["local_latest_deal_utc"] = conn.execute("SELECT MAX(occurred_at_utc) FROM deals").fetchone()[0]
        if _table_exists(conn, "broker_account_deals"):
            cov["local_deals_missing_from_broker_import"] = conn.execute(
                "SELECT COUNT(*) FROM deals d WHERE NOT EXISTS (SELECT 1 FROM broker_account_deals b "
                "WHERE CAST(b.ticket AS TEXT) = d.broker_deal_id)").fetchone()[0]
    cov["note"] = ("Deals the runtime recorded (broker-confirmed at execution) but not yet present in the broker-"
                   "history import are included from the runtime record. Refresh the independent broker copy with "
                   "`python -m adaptive_scalper.cli broker-history import`.")
    return cov


def reconciliation(conn: sqlite3.Connection, ledger: dict) -> dict:
    """Proves attributed + unattributed + non-trade money equals the broker
    deal population, recomputed independently in SQL. Covers the FULL
    population (all dates) so the identity can be exact; UI filters narrow
    the table view only."""
    by_class: dict = {c: {"positions": 0, "closed": 0, "partially_closed": 0, "open": 0, "other_status": 0,
                          "net_all_deals": 0.0, "realized": 0.0, "open_volume_entry_costs": 0.0}
                      for c in SOURCE_CLASSES}
    for t in ledger["trades"]:
        c = by_class[t["source_class"]]
        c["positions"] += 1
        c[{"CLOSED": "closed", "PARTIALLY_CLOSED": "partially_closed", "OPEN": "open"}.get(t["status"], "other_status")] += 1
        c["net_all_deals"] += t["net_pnl"]
        c["realized"] += t["realized_net_pnl"]
        c["open_volume_entry_costs"] += t["open_volume_entry_costs"]
    non_trade = sum(d["profit"] + d["commission"] + d["swap"] + d["fee"] for d in ledger["non_trade_deals"])
    orphan = sum(d["profit"] + d["commission"] + d["swap"] + d["fee"] for d in ledger["trade_deals_without_position"])
    ledger_total = sum(c["net_all_deals"] for c in by_class.values()) + non_trade + orphan
    split_total = sum(c["realized"] + c["open_volume_entry_costs"] for c in by_class.values()) + non_trade + orphan
    sql = population_sql_total(conn, ledger.get("login"))
    ledger_deals = sum(t["deal_count"] for t in ledger["trades"]) + len(ledger["non_trade_deals"]) + len(
        ledger["trade_deals_without_position"])
    attributed_closed = by_class[ATTRIBUTED]["closed"]
    local_closed = 0
    if _table_exists(conn, "positions"):
        local_closed = conn.execute("SELECT COUNT(*) FROM positions WHERE status = 'CLOSED'").fetchone()[0]
    money_gap = round(ledger_total - sql["net_total"], 6)
    split_gap = round(split_total - ledger_total, 6)
    count_gap = ledger_deals - sql["deal_count"]
    return {
        "currency": ledger.get("currency"), "login": ledger.get("login"),
        "population": "every imported broker-history deal for this account plus every runtime-recorded deal not "
                      "in that import, de-duplicated by broker deal ticket (all dates)",
        "population_deal_count_sql": sql["deal_count"], "population_deal_count_ledger": ledger_deals,
        "population_net_sql": sql["net_total"], "population_net_ledger": ledger_total,
        "ledger_vs_sql_money_discrepancy": money_gap, "ledger_vs_sql_deal_count_discrepancy": count_gap,
        "realized_split_discrepancy": split_gap,
        "reconciles": abs(money_gap) < 0.01 and abs(split_gap) < 0.01 and count_gap == 0,
        "by_source_class": by_class, "non_trade_deals_net": non_trade,
        "non_trade_deal_count": len(ledger["non_trade_deals"]),
        "trade_deals_without_position_net": orphan,
        "trade_deals_without_position_count": len(ledger["trade_deals_without_position"]),
        "deal_source_discrepancies": ledger["deal_source_discrepancies"],
        "local_positions_without_deals": ledger["local_positions_without_deals"],
        "closed_count_check": {
            "attributed_closed_positions": attributed_closed, "local_positions_marked_closed": local_closed,
            "asn_unattributed_closed_positions": by_class[ASN_UNATTRIBUTED]["closed"],
            "matches": attributed_closed + by_class[ASN_UNATTRIBUTED]["closed"] >= local_closed - len(
                [p for p in ledger["local_positions_without_deals"] if p["status"] == "CLOSED"]),
        },
        "formula": "per deal: net = profit + commission + swap + fee. Population = sum over every source class of "
                   "position net + non-trade deals (deposits, balance corrections) + trade deals without a position "
                   "id. Realized = net - entry commission/fee share of still-open volume.",
    }


def trades_page(conn: sqlite3.Connection, evidence: str, filters: dict, params: dict,
                page: int = 1, page_size: int = 50) -> dict:
    evidence = evidence.upper()
    if evidence not in EVIDENCE_SOURCES:
        raise ValueError(f"unknown evidence source {evidence!r}")
    trades, extra = _evidence_trades(conn, evidence, params)
    filtered = apply_filters(trades, filters)
    page_size = max(1, min(int(page_size), 200))
    pages = max(1, (len(filtered) + page_size - 1) // page_size)
    page = max(1, min(int(page), pages))
    return {"evidence": evidence, "page": page, "pages": pages, "page_size": page_size, "total": len(filtered),
            "trades": [_strip(t) for t in filtered[(page - 1) * page_size: page * page_size]],
            "currency": account_currency(conn), "run_id": extra.get("run_id")}


def trade_lifecycle(conn: sqlite3.Connection, evidence: str, trade_id: str, params: dict) -> dict | None:
    evidence = evidence.upper()
    if evidence in ("PAPER", "BACKTEST"):
        table = "paper_trades" if evidence == "PAPER" else "backtest_trades"
        if not _table_exists(conn, table):
            return None
        rows = _rows(conn, f"SELECT * FROM {table} WHERE id = ?", (trade_id,))  # nosec B608 - fixed pair
        if not rows:
            return None
        raw = rows[0]
        for k in ("evidence_json", "entry_features_json"):
            if raw.get(k):
                raw[k] = _json(raw[k])
        out = {"trade": _sim_trade(raw, evidence, account_currency(conn)), "simulation_record": raw}
        if evidence == "BACKTEST" and raw.get("run_id"):
            out["run"] = next((r for r in backtest_runs(conn) if r["run_id"] == raw["run_id"]), None)
        return out
    if evidence != "DEMO":
        return None
    ledger, freshness = cached_demo_ledger(conn, params.get("login"))
    trade = next((t for t in ledger["trades"] if t["trade_id"] == str(trade_id)), None)
    if trade is None:
        return None
    out: dict = {"trade": trade, "broker_deals": ledger["deals_by_position"].get(str(trade_id), []),
                 "freshness": freshness}
    chain = trade.get("chain_key")
    if chain:
        events = _chain_events(conn, chain)
        out["signal"] = next((e for e in events if e["event_type"] == "SIGNAL_CREATED"
                              and e["strategy_key"] == trade.get("strategy_key")), None)
        out["selection"] = next((e for e in events if e["event_type"] == "PROPOSAL_CREATED"), None)
        out["other_signals_in_chain"] = [e for e in events if e["event_type"] == "SIGNAL_CREATED"
                                         and e["strategy_key"] != trade.get("strategy_key")]
        out["permission_checks"] = [e for e in events if e["event_type"] in ("ENTRY_ALLOWED", "ENTRY_BLOCKED")]
        out["order_events"] = [e for e in events if e["event_type"].startswith("ORDER_")
                               or e["event_type"] == "POSITION_OPENED"]
        out["advisory_evidence"] = [
            {"event_type": e["event_type"],
             "source": e["payload"].get("source") if isinstance(e["payload"], dict) else None,
             "status": e["payload"].get("status") if isinstance(e["payload"], dict) else None,
             "authority": "NONE (advisory only; never authorizes an order)"}
            for e in events if e["event_type"] in ("MODEL_USED", "RAG_USED")]
        if _table_exists(conn, "entry_decisions"):
            out["entry_decisions"] = _rows(conn, "SELECT decided_at_utc, stage, decision, reason FROM entry_decisions "
                                                 "WHERE chain_key = ? ORDER BY id", (chain,))
    if trade.get("entry_order_id") is not None:
        orders = _rows(conn, "SELECT * FROM orders WHERE id = ?", (trade["entry_order_id"],))
        out["local_order"] = orders[0] if orders else None
        if out["local_order"] and out["local_order"].get("raw_broker_response_json"):
            out["local_order"]["raw_broker_response_json"] = _json(out["local_order"]["raw_broker_response_json"])
        out["order_transitions"] = _rows(conn, "SELECT from_state, to_state, occurred_at_utc, detail FROM "
                                               "order_state_transitions WHERE order_id = ? ORDER BY id",
                                         (trade["entry_order_id"],))
        if _table_exists(conn, "execution_cost_observations"):
            cev = _rows(conn, "SELECT * FROM execution_cost_observations WHERE order_id = ?",
                        (trade["entry_order_id"],))
            out["execution_cost_evidence"] = cev[0] if cev else None
    tickets = sorted({d["order_ticket"] for d in out["broker_deals"] if d.get("order_ticket")})
    if tickets and _table_exists(conn, "broker_account_orders"):
        marks = ",".join("?" * len(tickets))
        out["broker_orders"] = _rows(
            conn, "SELECT ticket, time_setup_utc, time_done_utc, type, state, magic, volume_initial, price_open, sl, "
                  f"tp, symbol, comment FROM broker_account_orders WHERE CAST(ticket AS TEXT) IN ({marks})",  # nosec
            tickets)
    if trade.get("local_position_id") is not None:
        pms = _rows(conn, "SELECT * FROM position_management_state WHERE position_id = ?",
                    (trade["local_position_id"],))
        out["position_management_state"] = pms[0] if pms else None
        out["management_actions"] = [
            {**r, "payload": _json(r.pop("payload_json"))} for r in _rows(
                conn, "SELECT event_type, event_timestamp_utc, payload_json FROM journal_events "
                      "WHERE broker_position_id = ? AND event_type IN ('STOP_ADVANCED','POSITION_CLOSED',"
                      "'RECONCILIATION_ACTION') ORDER BY id", (str(trade_id),))]
        rv = conn.execute("SELECT COUNT(*), MIN(event_timestamp_utc), MAX(event_timestamp_utc) FROM journal_events "
                          "WHERE broker_position_id = ? AND event_type = 'POSITION_REVIEWED'",
                          (str(trade_id),)).fetchone()
        latest = _rows(conn, "SELECT event_timestamp_utc, payload_json FROM journal_events WHERE broker_position_id = ? "
                             "AND event_type = 'POSITION_REVIEWED' ORDER BY id DESC LIMIT 5", (str(trade_id),))
        out["position_reviews"] = {"count": rv[0], "first_utc": rv[1], "last_utc": rv[2],
                                   "latest": [{"event_timestamp_utc": r["event_timestamp_utc"],
                                               "payload": _json(r["payload_json"])} for r in latest]}
    out["accounting"] = {
        "gross_pnl": trade["gross_pnl"], "commission": trade["commission"], "fee": trade["fee"],
        "swap": trade["swap"], "net_pnl": trade["net_pnl"], "realized_net_pnl": trade["realized_net_pnl"],
        "open_volume_entry_costs": trade["open_volume_entry_costs"], "realized_r": trade["realized_r"],
        "initial_monetary_risk": trade["initial_monetary_risk"], "currency": trade["currency"],
        "formula": "net = sum(profit + commission + swap + fee) over this position's broker deals; realized R = "
                   "realized net / initial monetary risk recorded at entry. Spread and slippage are already inside "
                   "the broker-recorded profit and are shown as evidence only, never subtracted twice.",
    }
    return out


def strategy_detail(conn: sqlite3.Connection, strategy_key: str) -> dict | None:
    catalog = strategy_source_catalog()
    retired = strategy_key in RETIRED_STRATEGY_KEYS
    if strategy_key not in catalog and not retired:
        return None
    ledger, freshness = cached_demo_ledger(conn)
    demo_trades = [t for t in ledger["trades"] if t["strategy_key"] == strategy_key]
    open_positions = [t for t in demo_trades if t["status"] in ("OPEN", "PARTIALLY_CLOSED")]
    recent_closed = sorted((t for t in demo_trades if t["status"] == "CLOSED"),
                           key=lambda t: t["exit_time_utc"] or 0, reverse=True)[:20]
    latest_trade = max(demo_trades, key=lambda t: t["entry_time_utc"] or 0, default=None)
    metrics = compute_metrics(demo_trades)
    metrics.pop("cumulative_net_curve")
    return {
        "strategy_key": strategy_key,
        "registration_status": "RETIRED_PERMANENTLY" if retired else "REGISTERED",
        "source": catalog.get(strategy_key),
        "latest_evaluation": _latest_evaluation(conn, strategy_key),
        "latest_signal": _latest_event(conn, strategy_key, "SIGNAL_CREATED"),
        "latest_selected": _latest_event(conn, strategy_key, "PROPOSAL_CREATED"),
        "latest_broker_confirmed_trade": _strip(latest_trade) if latest_trade else None,
        "open_positions": [_strip(t) for t in open_positions],
        "recent_closed_trades": [_strip(t) for t in recent_closed],
        "demo_metrics": metrics,
        "why_no_trade": _why_no_trade(conn, strategy_key),
        "freshness": freshness, "currency": ledger.get("currency"),
    }


RESEARCH_REPORT_GLOB = "independent_*.json"
_RESEARCH_SUMMARY_KEYS = (
    "trades", "wins", "losses", "breakevens", "win_rate", "gross_pnl", "total_cost", "net_pnl", "profit_factor",
    "expectancy_per_trade", "avg_gross_r", "avg_cost_r", "avg_net_r", "avg_holding_seconds", "loss_classes",
    "gross_positive_share", "cost_to_abs_gross",
)


def research_reports(research_dir) -> dict:
    """Independent per-strategy research (BACKTEST origin, research
    database only), read from the JSON reports `independent-research`
    writes. Latest report per symbol. Never mixed with DEMO or PAPER."""
    import pathlib

    folder = pathlib.Path(research_dir)
    note = ("Independent research: each active strategy simulated ON ITS OWN over the same historical bars "
            "(BACKTEST origin, separate research database, reserved OOS never used). Each session has its own "
            "equity and costs; nothing here is pooled or live evidence, and nothing here promotes a strategy.")
    if not folder.is_dir():
        return {"status": "NO_DATA", "detail": f"no research folder {str(folder)!r}", "note": note, "symbols": {}}
    latest: dict[str, dict] = {}
    errors = []
    for path in sorted(folder.glob(RESEARCH_REPORT_GLOB)):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        if report.get("kind") != "INDEPENDENT_STRATEGY_RESEARCH":
            continue
        symbol = report.get("symbol")
        if symbol and (symbol not in latest or report.get("created_at_utc", 0) > latest[symbol].get("created_at_utc", 0)):
            report["_file"] = path.name
            latest[symbol] = report
    out = {}
    for symbol, r in sorted(latest.items()):
        sessions = {}
        for key, v in (r.get("sessions") or {}).items():
            if "error" in v:
                sessions[key] = {"error": v["error"]}
                continue
            summ = v.get("summary") or {}
            stats = v.get("statistics") or {}
            sessions[key] = {
                **{k: summ.get(k) for k in _RESEARCH_SUMMARY_KEYS},
                "psr_vs_zero": stats.get("psr_vs_zero"), "per_trade_sharpe": stats.get("sharpe"),
                "positive_folds": stats.get("positive_folds"), "folds": stats.get("folds"),
                "dsr": (v.get("dsr") or {}).get("dsr"), "dsr_family_trials": (v.get("dsr") or {}).get("family_trials"),
                "halted_folds": v.get("halted_folds"), "max_fold_drawdown": v.get("max_fold_drawdown"),
                "config_fingerprint": v.get("config_fingerprint"),
                "by_exit_reason": {k: {"trades": x.get("trades"), "net_pnl": x.get("net_pnl")}
                                   for k, x in ((v.get("breakdown") or {}).get("exit_reason") or {}).items()},
            }
        study = r.get("selector_study") or {}
        out[symbol] = {
            "file": r.get("_file"), "created_at_utc": r.get("created_at_utc"), "range": r.get("range"),
            "bars": r.get("bars"), "folds": r.get("folds"), "inputs_identical": r.get("inputs_identical"),
            "bars_checksum": r.get("bars_checksum"), "cost_provenance": r.get("cost_provenance"),
            "initial_equity_per_session": r.get("initial_equity_per_session"),
            "news_windows_applied": r.get("news_windows_applied"), "sessions": sessions,
            "pbo": r.get("pbo"),
            "selector": {
                "candidates": study.get("candidates"), "selected": study.get("selected"),
                "contested_bars": study.get("contested_bars"), "contested_winners": study.get("contested_winners"),
                "per_strategy": {k: {x: v.get(x) for x in (
                    "signals", "selected", "lost_to_higher_edge", "filter_rejected", "selection_share",
                    "stated_p_selected", "capped_confidence_share", "expected_net_r_selected",
                    "expected_cost_r_selected", "realized_net_r_selected_matched", "realized_cost_r_selected_matched",
                    "selected_matched", "realized_net_r_lost_matched", "lost_matched")}
                    for k, v in (study.get("per_strategy") or {}).items()},
                "expected_vs_realized_quantiles": study.get("expected_vs_realized_quantiles"),
            },
        }
    return {"status": "OK" if out else "NO_DATA", "note": note, "symbols": out, "errors": errors,
            "detail": None if out else "no independent research report found; run `independent-research`"}


RESEARCH_V2_REPORT_GLOB = "v2_*_*.json"
RESEARCH_V2_COSTS_GLOB = "v2_costs_*.json"


def _v2_row(label: str, v: dict) -> dict:
    if "error" in v:
        return {"label": label, "error": v["error"]}
    r, st, summ = v.get("r") or {}, v.get("statistics") or {}, v.get("summary") or {}
    return {
        "label": label, "trades": r.get("n", 0), "gross_r": r.get("gross_r"), "cost_r": r.get("cost_r"),
        "net_r": r.get("net_r"), "net_win_rate": r.get("net_win_rate"), "profit_factor": summ.get("profit_factor"),
        "net_pnl": summ.get("net_pnl"), "positive_folds": v.get("positive_folds"), "folds": v.get("folds"),
        "halted_folds": v.get("halted_folds"), "max_fold_drawdown": v.get("max_fold_drawdown"),
        "psr_vs_zero": st.get("psr_vs_zero"), "dsr": (v.get("dsr") or {}).get("dsr"),
        "avg_holding_seconds": summ.get("avg_holding_seconds"),
        "mae_r_median": (v.get("mae_r") or {}).get("median"), "mfe_r_median": (v.get("mfe_r") or {}).get("median"),
        "exit_reasons": v.get("exit_reasons"), "holding_duration": v.get("holding_duration"),
        "selection_distribution": v.get("selection_distribution"),
    }


def research_v2_reports(research_dir) -> dict:
    """RESEARCH-ONLY V2 candidates next to the frozen V1 BASELINE on identical
    development folds (BACKTEST origin, research DB only), plus DEMO cost
    prediction diagnostics. Latest report per symbol. Labels are kept
    separate; nothing is pooled with DEMO/PAPER and nothing here promotes."""
    import pathlib

    folder = pathlib.Path(research_dir)
    note = ("INDEPENDENT RESEARCH · V1 BASELINE vs V2 CANDIDATE: BACKTEST evidence on development data only. "
            "V2 candidates cannot reach order_send and are never promoted from this view.")
    out, errors, costs = {}, [], None
    if folder.is_dir():
        latest: dict[str, dict] = {}
        for path in sorted(folder.glob(RESEARCH_V2_REPORT_GLOB)):
            if path.name.startswith("v2_costs_"):
                continue
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                errors.append(f"{path.name}: {exc}")
                continue
            symbol = report.get("symbol")
            if report.get("kind") == "RESEARCH_V2_STUDY" and symbol and (
                    symbol not in latest or report.get("created_at_utc", 0) > latest[symbol].get("created_at_utc", 0)):
                report["_file"] = path.name
                latest[symbol] = report
        for symbol, r in sorted(latest.items()):
            groups: dict[str, list] = {}
            for label, v in (r.get("sessions") or {}).items():
                variant = label.split(":", 1)[0]
                family = "V1 BASELINE" if variant == "V1" else "V2 CANDIDATE"
                groups.setdefault(variant, []).append({**_v2_row(label, v), "evidence_label": family})
            out[symbol] = {
                "file": r.get("_file"), "created_at_utc": r.get("created_at_utc"), "range": r.get("range"),
                "bars": r.get("bars"), "folds": r.get("folds"), "cost_provenance": r.get("cost_provenance"),
                "protected_oos": r.get("protected_oos"), "variants": groups, "family_pbo": r.get("family_pbo"),
                "raw_confidence_reliability": r.get("raw_confidence_reliability"),
            }
        cost_files = sorted(folder.glob(RESEARCH_V2_COSTS_GLOB))
        if cost_files:
            try:
                costs = json.loads(cost_files[-1].read_text(encoding="utf-8"))
                costs["_file"] = cost_files[-1].name
            except (OSError, ValueError) as exc:
                errors.append(f"{cost_files[-1].name}: {exc}")
    return {"status": "OK" if out else "NO_DATA", "note": note, "symbols": out, "costs": costs, "errors": errors,
            "detail": None if out else "no V2 research report found; run scripts/research_v2.py"}


def comparison_csv_rows(summary_payload: dict, keys: list[str]) -> list[dict]:
    rows = []
    for s in summary_payload["strategies"]:
        if keys and s["strategy_key"] not in keys:
            continue
        f = s.get("funnel") or {}
        rows.append({
            "evidence": summary_payload["evidence"], "strategy_key": s["strategy_key"],
            "strategy_version": s["strategy_version"], "registration_status": s["registration_status"],
            "signals": f.get("signals"), "selected_proposals": f.get("selected_proposals"),
            "orders_submitted": f.get("orders_submitted"), "orders_filled": f.get("orders_filled"),
            "closed_trades": s["closed_trades"], "sample_status": s["sample_status"], "wins": s["wins"],
            "losses": s["losses"], "breakevens": s["breakevens"], "win_rate": s["win_rate"],
            "gross_profit_of_winners": s["gross_profit_of_winners"],
            "gross_loss_of_losers": s["gross_loss_of_losers"],
            "gross_pnl": s["gross_pnl"], "commission": s["commission"], "fee": s["fee"], "swap": s["swap"],
            "net_pnl": s["net_pnl"], "profit_factor": s["profit_factor"],
            "profit_factor_note": s["profit_factor_note"], "avg_r": s["avg_r"], "r_sample": s["r_sample"],
            "expectancy_per_trade": s["expectancy_per_trade"], "avg_holding_seconds": s["avg_holding_seconds"],
            "max_drawdown_closed_trade_basis": s["max_drawdown_closed_trade_basis"],
            "open_positions": s["open_positions"], "unrealized_pnl": s.get("unrealized_pnl"),
            "unrealized_note": s.get("unrealized_note"), "currency": summary_payload.get("currency"),
            "filters": json.dumps(summary_payload.get("filters") or {}, sort_keys=True),
        })
    return rows
