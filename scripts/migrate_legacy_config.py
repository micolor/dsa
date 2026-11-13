# -*- coding: utf-8 -*-
"""Copy legacy config aliases to their canonical keys.

This helper is intentionally conservative:

- dry-run is the default; nothing is written unless ``--apply`` is passed;
- the existing ``.env`` is backed up before an apply;
- legacy keys are kept, so an older release can still read the file;
- secret values are copied verbatim and never printed.

It only handles file-based ``.env``. Docker Compose ``env_file``, runtime
environment variables and GitHub Actions secrets must be updated separately.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

# Ordered: the same order is used by plan output and apply.
_MIGRATIONS: Tuple[Tuple[str, str], ...] = (
    ("OPENAI_API_KEY", "OPENAI_API_KEYS"),
    ("DEEPSEEK_API_KEY", "DEEPSEEK_API_KEYS"),
    ("GEMINI_API_KEY", "GEMINI_API_KEYS"),
    ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEYS"),
    ("SCHEDULE_TIME", "SCHEDULE_TIMES"),
    ("RUN_IMMEDIATELY", "SCHEDULE_RUN_IMMEDIATELY"),
)

_ENV_LINE_RE = re.compile(
    r"^(?P<prefix>\s*(?:export\s+)?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)$"
)


@dataclass
class MigrationPlan:
    env_path: Path
    changes: List[Tuple[str, str]] = field(default_factory=list)
    error: Optional[str] = None

    def describe(self) -> str:
        """Human-readable plan that never includes secret values."""
        if self.error:
            return f"error: {self.error}"
        if not self.changes:
            return "no legacy aliases need migration"
        keys = ", ".join(canonical for canonical, _ in self.changes)
        return f"would write canonical keys: {keys} (legacy keys are kept)"


@dataclass
class MigrationResult:
    applied_keys: List[str]
    backup_path: Optional[Path] = None


def _read_lines(env_path: Path) -> List[str]:
    return env_path.read_text(encoding="utf-8").splitlines()


def _parse_env(lines: Iterable[str]) -> dict:
    values = {}
    for line in lines:
        match = _ENV_LINE_RE.match(line)
        if not match:
            continue
        values[match.group("key").upper()] = match.group("value")
    return values


def plan_migrations(env_path: Path) -> MigrationPlan:
    """Return the canonical keys that would be written; never writes."""
    env_path = Path(env_path)
    if not env_path.exists():
        return MigrationPlan(env_path=env_path, error=f"env file not found: {env_path}")

    values = _parse_env(_read_lines(env_path))
    changes: List[Tuple[str, str]] = []
    for legacy, canonical in _MIGRATIONS:
        legacy_value = str(values.get(legacy, "") or "").strip()
        canonical_value = str(values.get(canonical, "") or "").strip()
        if legacy_value and not canonical_value:
            changes.append((canonical, values[legacy]))
    return MigrationPlan(env_path=env_path, changes=changes)


def _upsert(lines: List[str], key: str, value: str) -> List[str]:
    updated = list(lines)
    for index, line in enumerate(updated):
        match = _ENV_LINE_RE.match(line)
        if not match or match.group("key").upper() != key:
            continue
        prefix = match.group("prefix")
        updated[index] = f"{prefix}{key}={value}"
        return updated
    updated.append(f"{key}={value}")
    return updated


def apply_migrations(env_path: Path, plan: MigrationPlan) -> MigrationResult:
    """Write ``plan.changes`` to ``env_path`` after making a backup."""
    if plan.error:
        raise ValueError(plan.error)
    if not plan.changes:
        return MigrationResult(applied_keys=[])

    env_path = Path(env_path)
    lines = _read_lines(env_path)
    for key, value in plan.changes:
        lines = _upsert(lines, key, value)

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = env_path.parent / f"{env_path.name}.bak.{stamp}"
    shutil.copy2(env_path, backup_path)

    content = "\n".join(lines) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{env_path.name}.", dir=str(env_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        shutil.copymode(env_path, temp_name)
        os.replace(temp_name, env_path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)

    return MigrationResult(
        applied_keys=[key for key, _ in plan.changes],
        backup_path=backup_path,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default=os.getenv("ENV_FILE", ".env"),
        help="path to the .env file (default: ENV_FILE or ./.env)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write changes and create a timestamped backup (default: dry-run)",
    )
    args = parser.parse_args(argv)

    env_path = Path(args.env_file)
    plan = plan_migrations(env_path)
    print(plan.describe())
    if plan.error:
        return 1
    if not args.apply or not plan.changes:
        return 0

    result = apply_migrations(env_path, plan)
    print(f"updated: {', '.join(result.applied_keys)}")
    if result.backup_path:
        print(f"backup: {result.backup_path}")
    print("legacy keys were kept; update Docker/Actions secrets separately if needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
