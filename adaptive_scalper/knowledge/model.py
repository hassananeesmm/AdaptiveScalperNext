"""OKF v0.2 concept model (Open Knowledge Format, Google Cloud;
canonical spec: github.com/GoogleCloudPlatform/open-knowledge-format
SPEC.md, v0.2 -- the latest published version as of 2026-09).

A concept is one Markdown file with YAML frontmatter. OKF itself requires
only a non-empty `type`; everything else here is either an OKF
recommended/optional field (title, description, resource, tags, sources,
usage_window, generated, verified, status, stale_after) or a project
extension key (OKF consumers must tolerate unknown keys):

    id            stable concept id (default: bundle path without ".md")
    version       integer content version, bumped on meaningful change
    supersedes    list of bundle-relative paths this concept replaces
    superseded_by bundle-relative path of the replacement
    applies_to    {symbols: [...], strategies: [...]} -- retrieval scope
    strategy_key / strategy_version / retired   (Strategy Definition)

Knowledge is documentation, never configuration: nothing in this package
is read by the selector, risk governor, final permission, kill switch or
execution service (enforced by `tests/test_knowledge.py`).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime

OKF_VERSION = "0.2"

TRUST_UNVERIFIED = "unverified"
TRUST_MACHINE_CONFIRMED = "machine-confirmed"
TRUST_HUMAN_REVIEWED = "human-reviewed"

STATUS_DRAFT = "draft"
STATUS_STABLE = "stable"
STATUS_DEPRECATED = "deprecated"
STATUSES = frozenset({STATUS_DRAFT, STATUS_STABLE, STATUS_DEPRECATED})

RESERVED_FILES = frozenset({"index.md", "log.md"})

# Project concept types. OKF has no type registry and consumers must accept
# unknown types; the project validator only warns on one.
TYPE_ARCHITECTURE_DECISION = "Architecture Decision"
TYPE_ENGINEERING_DECISION = "Engineering Decision"
TYPE_STRATEGY_DEFINITION = "Strategy Definition"
TYPE_RESEARCH_FINDING = "Research Finding"
TYPE_MODEL_CARD = "Model Card"
TYPE_LESSON_LEARNED = "Lesson Learned"
TYPE_SAFETY_PROCEDURE = "Safety Procedure"
TYPE_RUNBOOK = "Runbook"
PROJECT_TYPES = frozenset({
    TYPE_ARCHITECTURE_DECISION, TYPE_ENGINEERING_DECISION, TYPE_STRATEGY_DEFINITION, TYPE_RESEARCH_FINDING,
    TYPE_MODEL_CARD, TYPE_LESSON_LEARNED, TYPE_SAFETY_PROCEDURE, TYPE_RUNBOOK,
})
# Evidence-bearing types: may only be `stable` once a human reviewed them,
# so an unverified research finding can never present as approved.
EVIDENCE_TYPES = frozenset({TYPE_RESEARCH_FINDING, TYPE_MODEL_CARD, TYPE_LESSON_LEARNED})

_ACTOR = re.compile(r"^(human:[A-Za-z0-9._-]+|process:[A-Za-z0-9._-]+|[A-Za-z0-9._-]+/[A-Za-z0-9._-]+)$")


def is_actor(value) -> bool:
    return isinstance(value, str) and bool(_ACTOR.match(value))


def parse_timestamp(value) -> datetime | None:
    """ISO-8601 with an explicit UTC offset (OKF: "all timestamps"). YAML
    may already have produced a datetime; a naive one is rejected."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def verifications(frontmatter: dict) -> list[dict]:
    """`verified` may be a list of {by, at} or a single bare mapping."""
    raw = frontmatter.get("verified")
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [v for v in raw if isinstance(v, dict)]
    return []


def trust_tier(frontmatter: dict) -> str:
    """Derived, never stored (OKF 5.2): any `human:` verifier ->
    human-reviewed; other verifiers only -> machine-confirmed."""
    verifiers = [v.get("by") for v in verifications(frontmatter)]
    verifiers = [v for v in verifiers if isinstance(v, str)]
    if not verifiers:
        return TRUST_UNVERIFIED
    if any(v.startswith("human:") for v in verifiers):
        return TRUST_HUMAN_REVIEWED
    return TRUST_MACHINE_CONFIRMED


@dataclass(frozen=True)
class Concept:
    path: str                 # bundle-relative POSIX path, e.g. "strategies/momentum_continuation.md"
    frontmatter: dict
    body: str
    sha256: str

    @property
    def id(self) -> str:
        explicit = self.frontmatter.get("id")
        return explicit if isinstance(explicit, str) and explicit else self.path[:-3]

    @property
    def type(self) -> str:
        return str(self.frontmatter.get("type") or "")

    @property
    def title(self) -> str:
        return str(self.frontmatter.get("title") or self.id)

    @property
    def description(self) -> str:
        return str(self.frontmatter.get("description") or "")

    @property
    def tags(self) -> tuple[str, ...]:
        tags = self.frontmatter.get("tags") or []
        return tuple(str(t) for t in tags) if isinstance(tags, list) else ()

    @property
    def status(self) -> str:
        return str(self.frontmatter.get("status") or STATUS_STABLE)  # OKF: absent = stable

    @property
    def version(self) -> int | None:
        v = self.frontmatter.get("version")
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    @property
    def trust_tier(self) -> str:
        return trust_tier(self.frontmatter)

    @property
    def stale_after(self) -> datetime | None:
        return parse_timestamp(self.frontmatter.get("stale_after"))

    def is_stale(self, now: datetime) -> bool:
        stale_after = self.stale_after
        return stale_after is not None and now >= stale_after

    def _applies(self, key: str) -> tuple[str, ...]:
        scope = self.frontmatter.get("applies_to") or {}
        values = scope.get(key) if isinstance(scope, dict) else None
        return tuple(str(v) for v in values) if isinstance(values, list) else ()

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._applies("symbols")

    @property
    def strategies(self) -> tuple[str, ...]:
        found = list(self._applies("strategies"))
        key = self.frontmatter.get("strategy_key")
        if isinstance(key, str) and key not in found:
            found.append(key)
        return tuple(found)

    def search_text(self) -> str:
        return " ".join((self.title, self.description, " ".join(self.tags), self.body))


@dataclass(frozen=True)
class Issue:
    severity: str        # ERROR / WARNING
    path: str
    code: str
    message: str


@dataclass
class KnowledgeBundle:
    root: str
    okf_version: str | None
    concepts: dict[str, Concept] = field(default_factory=dict)   # by path
    issues: list[Issue] = field(default_factory=list)
    file_hashes: dict[str, str] = field(default_factory=dict)    # every .md incl. reserved

    @property
    def checksum(self) -> str:
        """Content checksum over every Markdown file (path + sha256), so a
        journaled advisory payload pins exactly which knowledge it saw."""
        digest = hashlib.sha256()
        for path in sorted(self.file_hashes):
            digest.update(f"{path}\0{self.file_hashes[path]}\n".encode())
        return digest.hexdigest()

    def by_id(self, concept_id: str) -> Concept | None:
        for concept in self.concepts.values():
            if concept.id == concept_id:
                return concept
        return self.concepts.get(concept_id) or self.concepts.get(f"{concept_id}.md")

    def errors_for(self, path: str) -> list[Issue]:
        return [i for i in self.issues if i.path == path and i.severity == "ERROR"]
