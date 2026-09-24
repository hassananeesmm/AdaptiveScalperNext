"""OKF conformance + project knowledge policy.

`load_bundle()` already reports OKF v0.2 conformance failures (section
11: parseable frontmatter, non-empty `type`, reserved-file structure).
`validate_bundle()` adds the project policy for the Git-tracked curated
bundle:

- provenance: title, description, `generated {by, at}` with an OKF actor
  and an offset-qualified ISO-8601 timestamp; every `sources[]` entry has
  a `resource`;
- lifecycle: `status` in draft|stable|deprecated; `stale_after` parseable
  (past = WARNING); `supersedes`/`superseded_by` resolve and agree, and a
  superseded concept is deprecated;
- verification: an evidence-bearing concept (Research Finding, Model
  Card, Lesson Learned) may only be `stable` when a `human:` actor
  verified it -- an unverified research finding is never presented as
  approved;
- scope: `applies_to.symbols` only XAUUSD/GBPJPY/BTCUSD; a retired
  strategy (failed_breakout_fade, support_resistance_reaction) can only
  appear in a deprecated concept marked `retired: true` -- knowledge can
  document a retirement, never re-activate one;
- knowledge is not configuration: frontmatter keys that look like runtime
  control (risk limits, kill switch, mode, enable/activate/promote) are
  errors;
- hygiene: no credentials (password/api key/token/login assignments,
  private keys) and no raw runtime records (broker order/position/deal
  ids, client request ids, raw journal payloads, CSV dumps) in any file.

Any concept with an ERROR is excluded by the advisor (quarantined), so a
bad file degrades knowledge, never trading.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from adaptive_scalper.config.constants import ALLOWED_CANONICAL_SYMBOLS, RETIRED_STRATEGY_KEYS
from adaptive_scalper.knowledge.loader import load_bundle
from adaptive_scalper.knowledge.model import (
    EVIDENCE_TYPES,
    OKF_VERSION,
    PROJECT_TYPES,
    STATUS_DEPRECATED,
    STATUS_STABLE,
    STATUSES,
    TRUST_HUMAN_REVIEWED,
    TYPE_STRATEGY_DEFINITION,
    Concept,
    Issue,
    KnowledgeBundle,
    is_actor,
    parse_timestamp,
    verifications,
)

CONTROL_KEYS = frozenset({
    "risk_per_trade_pct", "max_total_open_risk_pct", "max_daily_loss_pct", "max_drawdown_pct", "risk_limits",
    "max_open_positions", "max_positions_per_symbol", "kill_switch", "mode", "execution_mode", "enable", "enabled", "activate", "promote",
    "promotion", "lot_size", "volume", "order", "execute", "enable_live",
})

_SECRET_PATTERNS = (
    ("SECRET_ASSIGNMENT", re.compile(
        r"(?i)\b(password|passwd|pwd|api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|bearer|"
        r"mt5[_-]?(login|password|server))\b\s*[:=]\s*[\"']?[A-Za-z0-9!@#$%^&*._/+-]{4,}")),
    ("ACCOUNT_LOGIN", re.compile(r"(?i)\b(login|account(_number|_id)?)\b\s*[:=#]\s*\d{5,}")),
    ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("CLOUD_KEY", re.compile(r"\b(AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|gh[pousr]_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9]{20,})\b")),
)
_RAW_RECORD_PATTERNS = (
    ("RAW_BROKER_ID", re.compile(
        r"(?i)[\"']?\b(broker_(position|order|deal)_id|client_request_id|ticket|position_ticket|deal_ticket)\b[\"']?"
        r"\s*[:=]\s*[\"']?\d+")),
    ("RAW_JOURNAL_PAYLOAD", re.compile(r"(?i)\bpayload_json\b\s*[:=]")),
    ("RAW_DATA_DUMP", re.compile(r"(?im)^```\s*(csv|tsv|jsonl|ndjson|sql)\s*$")),
)


def _check_timestamp(issues, path, label, value) -> None:
    if value is not None and parse_timestamp(value) is None:
        issues.append(Issue("ERROR", path, "POLICY_TIMESTAMP_INVALID",
                            f"{label} must be ISO-8601 with an explicit UTC offset, got {value!r}"))


def _check_concept(concept: Concept, bundle: KnowledgeBundle, now: datetime) -> list[Issue]:
    fm, path, issues = concept.frontmatter, concept.path, []

    if concept.type not in PROJECT_TYPES:
        issues.append(Issue("WARNING", path, "POLICY_TYPE_UNKNOWN",
                            f"type {concept.type!r} is not a project concept type (allowed by OKF)"))
    for key in ("title", "description"):
        if not isinstance(fm.get(key), str) or not fm[key].strip():
            issues.append(Issue("ERROR", path, f"POLICY_{key.upper()}_MISSING", f"curated concepts need a `{key}`"))

    generated = fm.get("generated")
    if not isinstance(generated, dict) or not is_actor(generated.get("by")) or "at" not in generated:
        issues.append(Issue("ERROR", path, "POLICY_GENERATED_MISSING",
                            "`generated: {by: <actor>, at: <ISO-8601>}` is required (provenance)"))
    else:
        _check_timestamp(issues, path, "generated.at", generated.get("at"))

    for entry in verifications(fm):
        if not is_actor(entry.get("by")):
            issues.append(Issue("ERROR", path, "POLICY_ACTOR_INVALID",
                                f"verified.by {entry.get('by')!r} is not producer/version, human:<id> or process:<id>"))
        _check_timestamp(issues, path, "verified.at", entry.get("at"))
        if "at" not in entry:
            issues.append(Issue("ERROR", path, "POLICY_TIMESTAMP_INVALID", "verified entries need `at`"))

    sources = fm.get("sources", [])
    if not isinstance(sources, list):
        issues.append(Issue("ERROR", path, "POLICY_SOURCES_INVALID", "`sources` must be a list"))
    else:
        for i, source in enumerate(sources):
            if not isinstance(source, dict) or not source.get("resource"):
                issues.append(Issue("ERROR", path, "POLICY_SOURCE_RESOURCE_MISSING", f"sources[{i}] needs `resource`"))
            elif source.get("last_modified") is not None:
                _check_timestamp(issues, path, f"sources[{i}].last_modified", source["last_modified"])

    status = fm.get("status", STATUS_STABLE)
    if status not in STATUSES:
        issues.append(Issue("ERROR", path, "POLICY_STATUS_INVALID", f"status must be one of {sorted(STATUSES)}"))
    _check_timestamp(issues, path, "stale_after", fm.get("stale_after"))
    if concept.is_stale(now) and status != STATUS_DEPRECATED:
        issues.append(Issue("WARNING", path, "POLICY_STALE", f"stale since {fm.get('stale_after')}"))
    if "version" in fm and concept.version is None:
        issues.append(Issue("ERROR", path, "POLICY_VERSION_INVALID", "`version` must be an integer"))

    if concept.type in EVIDENCE_TYPES and status == STATUS_STABLE and concept.trust_tier != TRUST_HUMAN_REVIEWED:
        issues.append(Issue("ERROR", path, "POLICY_UNVERIFIED_EVIDENCE_STABLE",
                            f"a {concept.type} may only be stable once verified by a human: actor "
                            f"(trust tier is {concept.trust_tier}); keep it draft"))

    bad_symbols = set(concept.symbols) - ALLOWED_CANONICAL_SYMBOLS
    if bad_symbols:
        issues.append(Issue("ERROR", path, "POLICY_SYMBOL_NOT_ALLOWED",
                            f"applies_to.symbols {sorted(bad_symbols)} is outside {sorted(ALLOWED_CANONICAL_SYMBOLS)}"))
    retired = set(concept.strategies) & RETIRED_STRATEGY_KEYS
    if retired and not (status == STATUS_DEPRECATED and fm.get("retired") is True):
        issues.append(Issue("ERROR", path, "POLICY_RETIRED_STRATEGY",
                            f"{sorted(retired)} are permanently retired: only a deprecated concept with "
                            "`retired: true` may reference them in scope"))
    if concept.type == TYPE_STRATEGY_DEFINITION and fm.get("strategy_key") in RETIRED_STRATEGY_KEYS \
            and fm.get("retired") is not True:
        issues.append(Issue("ERROR", path, "POLICY_RETIRED_STRATEGY", "a retired strategy definition must set retired: true"))

    control = sorted(set(fm) & CONTROL_KEYS)
    if control:
        issues.append(Issue("ERROR", path, "POLICY_CONTROL_KEY",
                            f"frontmatter keys {control} look like runtime control; knowledge never configures trading"))

    for target in _as_list(fm.get("supersedes")):
        other = bundle.concepts.get(target.lstrip("/"))
        if other is None:
            issues.append(Issue("ERROR", path, "POLICY_SUPERSEDES_UNRESOLVED", f"supersedes {target!r} which does not exist"))
        elif other.status != STATUS_DEPRECATED or str(other.frontmatter.get("superseded_by", "")).lstrip("/") != path:
            issues.append(Issue("ERROR", path, "POLICY_SUPERSEDES_INCONSISTENT",
                                f"{target!r} must be deprecated with superseded_by: /{path}"))
    successor = fm.get("superseded_by")
    if successor is not None:
        other = bundle.concepts.get(str(successor).lstrip("/"))
        if other is None:
            issues.append(Issue("ERROR", path, "POLICY_SUPERSEDES_UNRESOLVED", f"superseded_by {successor!r} does not exist"))
        elif path not in [t.lstrip("/") for t in _as_list(other.frontmatter.get("supersedes"))]:
            issues.append(Issue("ERROR", path, "POLICY_SUPERSEDES_INCONSISTENT", f"{successor!r} does not list this concept"))
        if status != STATUS_DEPRECATED:
            issues.append(Issue("ERROR", path, "POLICY_SUPERSEDES_INCONSISTENT", "a superseded concept must be deprecated"))
    return issues


def _as_list(value) -> list[str]:
    if value is None:
        return []
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def scan_text(path: str, text: str) -> list[Issue]:
    """Credential and raw-record scan over a whole file (frontmatter and
    body). Public so the CLI and tests can scan arbitrary files too."""
    issues = []
    for code, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            issues.append(Issue("ERROR", path, f"SECRET_{code}", "possible credential in a Git-tracked knowledge file"))
    for code, pattern in _RAW_RECORD_PATTERNS:
        if pattern.search(text):
            issues.append(Issue("ERROR", path, code,
                                "raw runtime records belong in SQLite, not in Git-tracked knowledge"))
    return issues


def validate_bundle(root: str | Path, *, now: datetime | None = None) -> KnowledgeBundle:
    """Load + validate. Returns the bundle with every issue attached."""
    now = now or datetime.now(timezone.utc)
    bundle = load_bundle(root)
    if bundle.okf_version is None:
        bundle.issues.append(Issue("WARNING", "index.md", "OKF_VERSION_UNDECLARED",
                                   f"root index.md should declare okf_version: \"{OKF_VERSION}\""))
    elif bundle.okf_version != OKF_VERSION:
        bundle.issues.append(Issue("WARNING", "index.md", "OKF_VERSION_MISMATCH",
                                   f"bundle declares okf_version {bundle.okf_version}; this loader implements {OKF_VERSION}"))

    seen_ids: dict[str, str] = {}
    for concept in bundle.concepts.values():
        bundle.issues.extend(_check_concept(concept, bundle, now))
        if concept.id in seen_ids:
            bundle.issues.append(Issue("ERROR", concept.path, "POLICY_ID_DUPLICATE",
                                       f"id {concept.id!r} already used by {seen_ids[concept.id]}"))
        seen_ids.setdefault(concept.id, concept.path)

    root_path = Path(root)
    for rel in bundle.file_hashes:
        text = (root_path / rel).read_text(encoding="utf-8", errors="replace")
        bundle.issues.extend(scan_text(rel, text))
    return bundle


def is_valid(bundle: KnowledgeBundle) -> bool:
    return not any(i.severity == "ERROR" for i in bundle.issues)
