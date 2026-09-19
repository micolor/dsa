# -*- coding: utf-8 -*-
"""Contracts for selective CI gates and their cross-layer inputs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from fnmatch import fnmatchcase
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _workflow(relative_path: str) -> dict:
    return yaml.load(_read(relative_path), Loader=yaml.BaseLoader)


def _change_filters(ci: dict) -> tuple[dict, dict, dict]:
    changes_job = ci["jobs"]["changes"]
    filter_step = next(
        step for step in changes_job["steps"] if step.get("id") == "filter"
    )
    backend_filter_step = next(
        step for step in changes_job["steps"] if step.get("id") == "backend-filter"
    )
    return changes_job, filter_step, backend_filter_step


def test_heavy_ci_jobs_are_path_filtered_and_backend_tests_are_sharded() -> None:
    ci = _workflow(".github/workflows/ci.yml")
    changes_job, filter_step, backend_filter_step = _change_filters(ci)
    filters = str(filter_step["with"]["filters"])
    parsed_filters = yaml.load(filters, Loader=yaml.BaseLoader)
    backend_contract_paths = set(parsed_filters["backend_web_contract"])
    backend_filters = yaml.load(
        str(backend_filter_step["with"]["filters"]),
        Loader=yaml.BaseLoader,
    )

    assert changes_job["outputs"]["backend"] == (
        "${{ steps.backend-filter.outputs.backend_non_web == 'true' || "
        "steps.filter.outputs.backend_web_contract == 'true' }}"
    )
    assert changes_job["outputs"]["docker"] == "${{ steps.filter.outputs.docker }}"
    assert backend_filter_step["with"]["predicate-quantifier"] == "every"
    assert backend_filters["backend_non_web"] == ["**", "!apps/dsa-web/**"]
    assert "docker:" in filters
    assert "docker/**" in filters
    assert {
        "apps/dsa-web/public/**",
        "apps/dsa-web/src/components/settings/llmProviderTemplates.ts",
        "apps/dsa-web/src/locales/settingsHelp.ts",
    } == backend_contract_paths

    backend_tests_job = ci["jobs"]["backend-tests"]
    backend_gate_job = ci["jobs"]["backend-gate"]
    docker_job = ci["jobs"]["docker-build"]
    assert backend_tests_job["needs"] == ["changes", "ai-governance"]
    assert backend_tests_job["if"] == "needs.changes.outputs.backend == 'true'"
    assert backend_tests_job["strategy"]["fail-fast"] == "false"
    assert backend_tests_job["strategy"]["matrix"]["shard"] == ["1", "2", "3"]
    assert backend_gate_job["needs"] == [
        "changes",
        "ai-governance",
        "backend-tests",
    ]
    assert backend_gate_job["if"] == "always()"
    assert docker_job["needs"] == ["changes", "ai-governance"]
    assert docker_job["if"] == "needs.changes.outputs.docker == 'true'"

    install_step = next(
        step
        for step in backend_tests_job["steps"]
        if step["name"] == "📦 Install dependencies"
    )
    assert "python -m pip install -r .github/requirements-ci.txt" in install_step["run"]
    shard_step = next(
        step
        for step in backend_tests_job["steps"]
        if step["name"] == "✅ Offline test suite shard ${{ matrix.shard }}/3"
    )
    assert shard_step["env"] == {
        "PYTEST_SPLITS": "3",
        "PYTEST_GROUP": "${{ matrix.shard }}",
        "PYTEST_FIRST_SHARD_OVERHEAD": "20",
    }

    requirements = _read(".github/requirements-ci.txt")
    ci_gate = _read("scripts/ci_gate.sh")
    assert "pytest-xdist" not in requirements
    assert "PYTEST_WORKERS" not in ci_gate
    assert "pytest-split" not in requirements
    assert 'python scripts/ci_test_shard.py' in ci_gate
    assert '--first-shard-overhead "${PYTEST_FIRST_SHARD_OVERHEAD:-0}"' in ci_gate
    assert '.github/ci-test-durations.json' in ci_gate
    assert '--durations=30' in ci_gate


def test_backend_filter_covers_mixed_changes_and_shared_web_assets() -> None:
    """Model the complete backend output, including shared Web-owned assets."""
    ci = _workflow(".github/workflows/ci.yml")
    _, filter_step, backend_filter_step = _change_filters(ci)
    backend_web_contract = yaml.load(
        str(filter_step["with"]["filters"]),
        Loader=yaml.BaseLoader,
    )["backend_web_contract"]
    backend_filters = yaml.load(
        str(backend_filter_step["with"]["filters"]),
        Loader=yaml.BaseLoader,
    )["backend_non_web"]

    def matches_every_rule(path: str) -> bool:
        return all(
            not fnmatchcase(path, rule[1:])
            if rule.startswith("!")
            else fnmatchcase(path, rule)
            for rule in backend_filters
        )

    def backend_output(changed_paths: list[str]) -> bool:
        backend_non_web = any(matches_every_rule(path) for path in changed_paths)
        web_contract_hit = any(
            fnmatchcase(path, rule)
            for path in changed_paths
            for rule in backend_web_contract
        )
        return backend_non_web or web_contract_hit

    assert backend_filter_step["with"]["predicate-quantifier"] == "every"
    assert backend_output(["apps/dsa-web/src/App.tsx"]) is False
    assert backend_output(["apps/dsa-web/src/App.tsx", "src/config.py"]) is True
    assert backend_output(["apps/dsa-web/src/App.tsx", "docs/CHANGELOG.md"]) is True
    assert backend_output(["apps/dsa-web/public/stocks.index.json"]) is True
    assert backend_output(["apps/dsa-web/public/runtime/new-asset.json"]) is True


def _run_auto_tag_marker_step(commit_message: str) -> str:
    """执行 auto-tag.yml 里那段检测脚本，返回它写出的 bump 值。"""
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - 仅在没有 bash 的平台上跳过
        pytest.skip("bash is required to exercise the auto-tag marker gate")
    auto_tag = _workflow(".github/workflows/auto-tag.yml")
    steps = auto_tag["jobs"]["tag"]["steps"]
    marker_step = next(
        step for step in steps if step.get("id") == "release_marker"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "github_output"
        output_path.write_text("", encoding="utf-8")
        completed = subprocess.run(
            [bash, "-c", marker_step["run"]],
            env={
                **os.environ,
                "COMMIT_MESSAGE": commit_message,
                "GITHUB_OUTPUT": str(output_path),
            },
            capture_output=True,
            text=True,
            check=True,
        )
        assert completed.returncode == 0
        written = output_path.read_text(encoding="utf-8")
    for line in written.splitlines():
        if line.startswith("bump="):
            return line[len("bump="):]
    raise AssertionError("the marker step did not write bump to GITHUB_OUTPUT")


def test_auto_tag_reads_the_release_marker_from_the_commit_title_only() -> None:
    """版本标记只认 commit title，正文里出现 #patch 不得触发发布。

    ``github.event.head_commit.message`` 是整条 commit message，squash 合并会把
    被合并分支的提交正文一并带进来。用真实历史提交 c4e94203 复现：它的 title 是
    「fix: 修正 akshare 腾讯数据源字段索引和 ETF 行情字段映射 (#579)」，正文里带着
    一行裸 ``#patch``（来自被 squash 的提交），且改动路径是 data_provider/*.py
    （不在 paths-ignore 内）。修复前该 push 会走完 tag job 并推出一个没有人要求的
    版本号，与 AGENTS.md §7「只有 commit title 含 #patch/#minor/#major 才触发」冲突。
    """
    auto_tag = _workflow(".github/workflows/auto-tag.yml")
    job = auto_tag["jobs"]["tag"]
    steps = job["steps"]
    by_id = {step.get("id"): step for step in steps}

    # 门禁不再建立在整条 message 上：job 级 if 已移除，且全文件不再用 contains() 匹配
    assert "if" not in job
    workflow_text = _read(".github/workflows/auto-tag.yml")
    assert "contains(" not in workflow_text

    marker_step = by_id["release_marker"]
    assert marker_step["env"]["COMMIT_MESSAGE"] == (
        "${{ github.event.head_commit.message }}"
    )
    assert "${COMMIT_MESSAGE%%$'\\n'*}" in marker_step["run"]
    # 只有检测步骤能读到整条 message，其余步骤不得直接引用
    for step in steps:
        if step is marker_step:
            continue
        assert "head_commit.message" not in json.dumps(step, ensure_ascii=False)

    # 检出与发版都挂在检测结果上，空标记时一步都不跑
    tag_steps = [step for step in steps if step is not marker_step]
    assert tag_steps, "expected checkout/action steps after the marker detection"
    for step in tag_steps:
        assert step["if"] == "steps.release_marker.outputs.bump != ''"

    bump_step = next(
        step for step in steps if step.get("uses") == "anothrNick/github-tag-action@v1"
    )
    assert "steps.release_marker.outputs.bump" in bump_step["env"]["DEFAULT_BUMP"]

    # 真实历史 squash 提交：正文有 #patch，title 没有 → 不发版
    assert (
        _run_auto_tag_marker_step(
            "fix: 修正 akshare 腾讯数据源字段索引和 ETF 行情字段映射 (#579)\n\n"
            "* fix: add TRADING_DAY_CHECK_ENABLED env var\n\n#patch\n"
        )
        == ""
    )
    # title 里有标记：按标记档位发版，且正文里更大的标记不能顶掉 title
    assert _run_auto_tag_marker_step("fix: 修复量能单位 (#patch)\n\n后续说明\n") == "patch"
    assert (
        _run_auto_tag_marker_step(
            "feat: add agent strategy chat #minor (#367)\n\n正文提到 #patch 不算\n"
        )
        == "minor"
    )
    assert _run_auto_tag_marker_step("feat: 重构配置语义 #major") == "major"
    # 没有 head_commit（如分支删除）时不得崩，也不得发版
    assert _run_auto_tag_marker_step("") == ""


def test_manual_docker_publish_builds_the_requested_release_tag() -> None:
    """手动发布必须检出 image_tag 指向的那份代码，且不得无条件覆盖 `latest`。

    修复前该 workflow 只在分派时选中的 ref（默认分支）上构建，却把产物打成
    调用方填写的版本号并同时推送 `latest`，于是「重建某个已发布版本」会发布
    主干上未发布的代码，`latest` 也会被悄悄挪到没有任何 Release 的提交上。
    """
    manual = _workflow(".github/workflows/ghcr-dockerhub.yml")
    job = manual["jobs"]["build-and-push"]
    steps = job["steps"]
    by_name = {step.get("name"): step for step in steps}
    names = [step.get("name") for step in steps]

    resolve = by_name["Resolve release ref"]
    assert 'git checkout "$RELEASE_TAG"' in resolve["run"]
    assert r"^v[0-9]+\.[0-9]+\.[0-9]+$" in resolve["run"]
    assert "refs/tags/${RELEASE_TAG}" in resolve["run"]

    # 检出必须在冒烟与构建之前，否则验证的与发布的是两份代码
    assert names.index("Resolve release ref") < names.index("Pre-publish docker smoke")
    assert names.index("Resolve release ref") < names.index(
        "Build and push multi-arch images"
    )

    for meta_step_name in (
        "Extract metadata for GHCR",
        "Extract metadata for Docker Hub",
    ):
        tags = by_name[meta_step_name]["with"]["tags"]
        assert "type=raw,value=latest,enable=" in tags
        assert all(line.strip() != "type=raw,value=latest" for line in tags.splitlines())
