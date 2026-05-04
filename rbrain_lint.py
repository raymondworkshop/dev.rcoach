#!/usr/bin/env python3
"""Validate atom notes: broken internal [[links]], orphans, thin trace sections."""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

from rbrain_config import get_config

LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
# Match atomizer filename sanitization (see atomizer.save_to_wiki).
SAFE_RE = re.compile(r'[\\/:*?"<>|#]')


def _safe_stem(name: str) -> str:
    base = name.strip().split("|")[0].strip()
    return SAFE_RE.sub("_", base)


def _body_after_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2]
    return text


def main(argv: list[str] | None = None) -> int:
    cfg = get_config()
    atoms_dir = Path(cfg["atoms_dir"])
    if not atoms_dir.is_dir():
        print(f"rbrain_lint: atoms directory missing: {atoms_dir}", file=sys.stderr)
        return 1

    md_paths = sorted(atoms_dir.glob("*.md"))
    stems = {p.stem for p in md_paths}

    broken: list[tuple[str, str, str]] = []
    # incoming[stem] = atoms that link to stem (internal only)
    incoming: dict[str, set[str]] = defaultdict(set)

    invalid_metadata: list[tuple[str, str]] = []

    for path in md_paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        body = _body_after_frontmatter(text)
        for m in LINK_RE.finditer(body):
            raw_target = m.group(1).strip()
            if not raw_target or "/" in raw_target or ".." in raw_target:
                continue
            # Skip Obsidian/header noise and obvious non-entity wikilinks
            if raw_target.startswith("#") or re.match(r"^#+$", raw_target):
                continue
            if raw_target.startswith("{") or "entity':" in raw_target or "'entity'" in raw_target:
                continue
            raw_target = raw_target.lstrip("[").rstrip("]")
            if not raw_target:
                continue
            safe = _safe_stem(raw_target)
            if not safe or safe.replace("_", "").strip() == "":
                continue
            incoming[safe].add(path.stem)
            if safe not in stems:
                broken.append((path.name, raw_target, safe))

        # Validate new metadata fields
        if text.startswith("---"):
            try:
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    meta = yaml.safe_load(parts[1]) or {}
                    # Check perspective
                    perspective = meta.get("perspective")
                    if perspective and perspective not in ["self", "other", "society"]:
                        invalid_metadata.append((path.name, f"invalid perspective: {perspective}"))
                    # Check classification
                    classification = meta.get("classification", [])
                    if isinstance(classification, str):
                        classification = [classification]
                    valid_class = {"decision", "problem", "learning", "observation"}
                    for cls in classification:
                        if cls not in valid_class:
                            invalid_metadata.append((path.name, f"invalid classification: {cls}"))
                    # Check emotion_triggers
                    emotion_triggers = meta.get("emotion_triggers", [])
                    if isinstance(emotion_triggers, str):
                        emotion_triggers = [emotion_triggers]
                    # No strict validation for emotion_triggers, just ensure it's a list
            except yaml.YAMLError as e:
                invalid_metadata.append((path.name, f"YAML error: {e}"))

    orphans = sorted(stems - set(incoming.keys()))

    weak_trace: list[tuple[str, str]] = []
    for path in md_paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        if "## 📜 Trace" not in text:
            weak_trace.append((path.name, "missing_trace_header"))
            continue
        trace_part = text.split("## 📜 Trace", 1)[1]
        if "## " in trace_part:
            trace_part = trace_part.split("## ", 1)[0]
        bullets = [ln for ln in trace_part.splitlines() if ln.strip().startswith("- **")]
        if not bullets:
            weak_trace.append((path.name, "empty_trace"))
        elif all("Referenced." in b and "Logic" not in b for b in bullets):
            weak_trace.append((path.name, "only_placeholder_insights"))

    exit_code = 0
    print(f"Atoms scanned: {len(md_paths)} in {atoms_dir}")

    if broken:
        exit_code = 1
        print("\nBroken internal links (target has no matching .md):")
        for src, raw, safe in broken:
            print(f"  {src} -> [[{raw}]] (expected file stem: {safe}.md)")

    if invalid_metadata:
        exit_code = 1
        print("\nInvalid metadata fields:")
        for fname, issue in invalid_metadata:
            print(f"  {fname}: {issue}")

    if orphans:
        print("\nOrphan atoms (no incoming [[link]] from another atom):")
        for o in orphans[:200]:
            print(f"  [[{o}]]")
        if len(orphans) > 200:
            print(f"  ... and {len(orphans) - 200} more")

    if weak_trace:
        print("\nWeak / empty traces (informational):")
        for fname, reason in weak_trace[:100]:
            print(f"  {fname}: {reason}")
        if len(weak_trace) > 100:
            print(f"  ... and {len(weak_trace) - 100} more")

    if exit_code == 0:
        print("\nNo broken internal links (orphans / weak traces are advisory).")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
