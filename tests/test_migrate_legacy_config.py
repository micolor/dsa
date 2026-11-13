# -*- coding: utf-8 -*-
"""Tests for the legacy config migration helper."""

from __future__ import annotations

from pathlib import Path

from scripts.migrate_legacy_config import apply_migrations, plan_migrations


def _write_env(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_plan_migrates_legacy_keys_to_canonical_keys(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    _write_env(env_path, [
        "STOCK_LIST=600519",
        "OPENAI_API_KEY=sk-legacy-openai",
        "DEEPSEEK_API_KEY=sk-legacy-deepseek",
        "SCHEDULE_TIME=09:20",
        "RUN_IMMEDIATELY=false",
    ])

    plan = plan_migrations(env_path)

    assert plan.changes == [
        ("OPENAI_API_KEYS", "sk-legacy-openai"),
        ("DEEPSEEK_API_KEYS", "sk-legacy-deepseek"),
        ("SCHEDULE_TIMES", "09:20"),
        ("SCHEDULE_RUN_IMMEDIATELY", "false"),
    ]


def test_plan_does_not_overwrite_existing_canonical_values(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    _write_env(env_path, [
        "OPENAI_API_KEYS=sk-new",
        "OPENAI_API_KEY=sk-legacy",
        "SCHEDULE_TIMES=09:20,15:10",
        "SCHEDULE_TIME=09:20",
    ])

    plan = plan_migrations(env_path)

    assert plan.changes == []


def test_plan_is_idempotent_after_apply(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    _write_env(env_path, [
        "OPENAI_API_KEY=sk-legacy",
        "SCHEDULE_TIME=09:20",
    ])

    first = apply_migrations(env_path, plan_migrations(env_path))
    second = plan_migrations(env_path)

    assert first.applied_keys == ["OPENAI_API_KEYS", "SCHEDULE_TIMES"]
    assert second.changes == []


def test_dry_run_does_not_write_and_never_prints_secret_values(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    original = "OPENAI_API_KEY=sk-super-secret\n"
    env_path.write_text(original, encoding="utf-8")

    plan = plan_migrations(env_path)

    assert env_path.read_text(encoding="utf-8") == original
    assert "sk-super-secret" not in plan.describe()
    assert "OPENAI_API_KEYS" in plan.describe()


def test_apply_creates_backup_and_preserves_legacy_keys(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    _write_env(env_path, ["OPENAI_API_KEY=sk-legacy"])

    result = apply_migrations(env_path, plan_migrations(env_path))
    content = env_path.read_text(encoding="utf-8")

    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert "OPENAI_API_KEYS=sk-legacy" in content
    assert "OPENAI_API_KEY=sk-legacy" in content


def test_plan_handles_missing_env_file(tmp_path: Path) -> None:
    plan = plan_migrations(tmp_path / "missing.env")

    assert plan.changes == []
    assert plan.error is not None
