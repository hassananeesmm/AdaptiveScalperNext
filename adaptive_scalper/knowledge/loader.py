"""Safe OKF bundle loader.

Treats the bundle as untrusted input: `yaml.safe_load` only (no object
construction), a per-file size cap, symlinks and paths escaping the root
refused, non-UTF-8 files reported rather than crashing. Loading never
raises for bad content -- every problem becomes an `Issue`, and
`validate.validate_bundle()` adds the project policy checks on top.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from adaptive_scalper.knowledge.model import RESERVED_FILES, Concept, Issue, KnowledgeBundle

MAX_FILE_BYTES = 256 * 1024
MAX_FILES = 2000


class BundleNotFoundError(FileNotFoundError):
    """The bundle root does not exist or is not a directory."""


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """(frontmatter_yaml, body). OKF frontmatter is a leading block
    delimited by `---` lines; None when the file has none."""
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return "".join(lines[1:i]), "".join(lines[i + 1:])
    return None, text


def parse_frontmatter(raw: str | None) -> tuple[dict | None, str | None]:
    """(mapping, error). An empty block parses to {}."""
    if raw is None:
        return None, None
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return None, f"frontmatter is not valid YAML: {str(exc).splitlines()[0]}"
    if data is None:
        return {}, None
    if not isinstance(data, dict):
        return None, "frontmatter must be a YAML mapping"
    return data, None


def load_bundle(root: str | Path) -> KnowledgeBundle:
    root_path = Path(root)
    if not root_path.is_dir():
        raise BundleNotFoundError(f"OKF bundle root {root_path} is not a directory")
    resolved_root = root_path.resolve()
    bundle = KnowledgeBundle(root=str(root_path), okf_version=None)

    files = sorted(p for p in root_path.rglob("*.md"))
    if len(files) > MAX_FILES:
        bundle.issues.append(Issue("ERROR", ".", "LOAD_TOO_MANY_FILES",
                                   f"{len(files)} Markdown files exceeds the {MAX_FILES} cap; only the first are loaded"))
        files = files[:MAX_FILES]

    for path in files:
        rel = path.relative_to(root_path).as_posix()
        if path.is_symlink() or not path.resolve().is_relative_to(resolved_root):
            bundle.issues.append(Issue("ERROR", rel, "LOAD_SYMLINK_REFUSED", "symlinks are not followed"))
            continue
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            bundle.issues.append(Issue("ERROR", rel, "LOAD_FILE_TOO_LARGE",
                                       f"{size} bytes exceeds the {MAX_FILE_BYTES}-byte cap"))
            continue
        data = path.read_bytes()
        bundle.file_hashes[rel] = hashlib.sha256(data).hexdigest()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            bundle.issues.append(Issue("ERROR", rel, "LOAD_NOT_UTF8", "file is not valid UTF-8"))
            continue

        raw, body = split_frontmatter(text)
        frontmatter, error = parse_frontmatter(raw)
        name = path.name

        if name in RESERVED_FILES:
            _check_reserved(bundle, rel, name, raw, frontmatter, error)
            continue
        if raw is None:
            bundle.issues.append(Issue("ERROR", rel, "OKF_FRONTMATTER_MISSING",
                                       "every non-reserved concept file needs YAML frontmatter"))
            continue
        if error is not None:
            bundle.issues.append(Issue("ERROR", rel, "OKF_FRONTMATTER_INVALID", error))
            continue
        concept_type = frontmatter.get("type")
        if not isinstance(concept_type, str) or not concept_type.strip():
            bundle.issues.append(Issue("ERROR", rel, "OKF_TYPE_MISSING", "frontmatter needs a non-empty `type`"))
            continue
        bundle.concepts[rel] = Concept(rel, frontmatter, body, bundle.file_hashes[rel])
    return bundle


def _check_reserved(bundle: KnowledgeBundle, rel: str, name: str, raw, frontmatter, error) -> None:
    if name == "log.md":
        if raw is not None:
            bundle.issues.append(Issue("ERROR", rel, "OKF_RESERVED_LOG", "log.md must not carry frontmatter"))
        return
    # index.md: frontmatter only at the bundle root, and only `okf_version`
    if raw is None:
        return
    if error is not None:
        bundle.issues.append(Issue("ERROR", rel, "OKF_FRONTMATTER_INVALID", error))
        return
    if rel != "index.md":
        bundle.issues.append(Issue("ERROR", rel, "OKF_RESERVED_INDEX",
                                   "only the bundle-root index.md may carry frontmatter"))
        return
    extra = set(frontmatter) - {"okf_version"}
    if extra:
        bundle.issues.append(Issue("ERROR", rel, "OKF_RESERVED_INDEX",
                                   f"root index.md frontmatter may only hold okf_version, found {sorted(extra)}"))
    if "okf_version" in frontmatter:
        bundle.okf_version = str(frontmatter["okf_version"])
