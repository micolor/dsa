# -*- coding: utf-8 -*-
"""跨进程写同一个 SQLite 库时的契约。

告警 worker 与 Web 进程写同一个库，`AlertRepository.upsert_cooldown` 现在走
`DatabaseManager._run_write_transaction`（文件 SQLite 上先取写锁再读、遇
`database is locked` 退避重试）。单进程用例只能证明「代码调了那条路径」，
证明不了「另一个进程正握着写锁时这次写入仍然落地」——那正是这条修复存在的
理由，因此这里用真实的多进程。

两个场景：
1. 多个进程同时 upsert 同一个 `(rule_id, target, severity)`：唯一键下的竞态
   不能让任何一方丢行或抛错。
2. 另一个进程握着写锁约 1 秒（模拟真实锁竞争），写入方必须在退避重试预算内
   完成——修复前用的裸 session 没有重试，会在自己的 busy timeout 后直接失败，
   冷静期整行丢失，而告警已经发出去了。
"""
from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

_SHARED_TARGET = "600519"
_SHARED_RULE_ID = 101
_WRITES_PER_PROCESS = 12

# 锁竞争场景：写入方的 busy timeout 与重试预算。
_LOCK_HOLD_SECONDS = 1.0
_WRITER_BUSY_TIMEOUT_MS = 100
_WRITER_RETRY_MAX = 4
_WRITER_RETRY_BASE_DELAY = 0.2
# 重试预算 ≈ 5×0.1s(busy) + 0.2+0.4+0.8+1.6s(backoff) ≈ 3.5s，是持锁时长的 3 倍以上。


def _wait_for_marker(path: str, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    marker = Path(path)
    while time.monotonic() < deadline:
        if marker.exists():
            return True
        time.sleep(0.02)
    return False


def _write_json(path: str, payload: dict) -> None:
    Path(path).write_text(json.dumps(payload), encoding="utf-8")


def _open_repository(db_path: str):
    """在子进程里打开同一个库并返回仓储实例。"""
    from src.repositories.alert_repo import AlertRepository
    from src.storage import DatabaseManager

    DatabaseManager.reset_instance()
    manager = DatabaseManager(f"sqlite:///{db_path}")
    return AlertRepository(manager)


def _upsert(repo, *, rule_id: int, target: str, offset: int) -> None:
    now = datetime.now()
    repo.upsert_cooldown(
        rule_id=rule_id,
        rule_key=f"rule-{rule_id}",
        target=target,
        severity="warning",
        last_triggered_at=now,
        cooldown_until=now + timedelta(minutes=30),
        reason=f"contention-{offset}",
    )


def _concurrent_writer(db_path: str, own_rule_id: int, marker: str) -> None:
    """与其他进程争抢同一个键：任何一次写入失败都必须被报告，而不是咽掉。

    每个进程都写同一个 ``(_SHARED_RULE_ID, _SHARED_TARGET, warning)``
    （唯一键上的真实竞态），另写一行只属于自己的 target 以便核对各自都落了地。
    """
    errors = []
    try:
        repo = _open_repository(db_path)
        for index in range(_WRITES_PER_PROCESS):
            try:
                _upsert(
                    repo, rule_id=_SHARED_RULE_ID, target=_SHARED_TARGET, offset=index
                )
                _upsert(repo, rule_id=own_rule_id, target=f"own-{own_rule_id}", offset=index)
            except Exception as exc:  # noqa: BLE001 - 失败必须回传给父进程
                errors.append(f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"setup {type(exc).__name__}: {exc}")
    finally:
        _write_json(marker, {"errors": errors})


def _hold_write_lock(db_path: str, hold_seconds: float, marker: str) -> None:
    """另一个进程握住写锁一段时间：真实的跨进程竞争，不是 mock。"""
    connection = sqlite3.connect(db_path, timeout=5, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("UPDATE alert_cooldowns SET reason = reason")
        Path(marker).write_text("locked", encoding="utf-8")
        time.sleep(hold_seconds)
        connection.execute("ROLLBACK")
    finally:
        connection.close()


def _lock_waiting_writer(db_path: str, ready: str, go: str, result: str) -> None:
    os.environ["SQLITE_BUSY_TIMEOUT_MS"] = str(_WRITER_BUSY_TIMEOUT_MS)
    os.environ["SQLITE_WRITE_RETRY_MAX"] = str(_WRITER_RETRY_MAX)
    os.environ["SQLITE_WRITE_RETRY_BASE_DELAY"] = str(_WRITER_RETRY_BASE_DELAY)
    try:
        repo = _open_repository(db_path)
    except Exception as exc:  # noqa: BLE001
        _write_json(ready, {"error": f"{type(exc).__name__}: {exc}"})
        _write_json(result, {"ok": False, "error": f"setup {type(exc).__name__}: {exc}"})
        return
    _write_json(ready, {"error": None})
    if not _wait_for_marker(go, timeout=60.0):
        _write_json(result, {"ok": False, "error": "the lock holder never signalled"})
        return
    try:
        _upsert(repo, rule_id=7, target=_SHARED_TARGET, offset=0)
        _write_json(result, {"ok": True, "error": None})
    except Exception as exc:  # noqa: BLE001 - 这正是修复前会丢冷静期的路径
        _write_json(result, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})


class SqliteWriteContentionTestCase(unittest.TestCase):
    """跨进程写入必须要么落地，要么带着真实异常报出来。"""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self.db_path = str(Path(self._temp_dir.name) / "contention.db")
        self.marker_dir = Path(self._temp_dir.name)

        from src.storage import DatabaseManager

        DatabaseManager.reset_instance()
        self.addCleanup(DatabaseManager.reset_instance)
        self.manager = DatabaseManager(f"sqlite:///{self.db_path}")

    def _marker(self, name: str) -> str:
        return str(self.marker_dir / f"{name}.json")

    def _cooldown_rows(self, target: str) -> list[tuple]:
        with sqlite3.connect(self.db_path, timeout=10) as connection:
            return connection.execute(
                "SELECT rule_id, target, severity, reason FROM alert_cooldowns WHERE target = ?",
                (target,),
            ).fetchall()

    def test_concurrent_processes_writing_the_same_cooldown_key_keep_one_row(self) -> None:
        context = multiprocessing.get_context("spawn")
        rule_ids = [101, 102, 103]
        markers = [self._marker(f"writer-{rule_id}") for rule_id in rule_ids]

        processes = [
            context.Process(
                target=_concurrent_writer, args=(self.db_path, rule_id, marker)
            )
            for rule_id, marker in zip(rule_ids, markers)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=120)
            self.assertEqual(process.exitcode, 0)

        for rule_id, marker in zip(rule_ids, markers):
            payload = json.loads(Path(marker).read_text(encoding="utf-8"))
            self.assertEqual(
                payload["errors"], [], f"process for rule {rule_id} lost a write"
            )

        shared_rows = self._cooldown_rows(_SHARED_TARGET)
        self.assertEqual(len(shared_rows), 1, shared_rows)
        for rule_id in rule_ids:
            own_rows = self._cooldown_rows(f"own-{rule_id}")
            self.assertEqual(len(own_rows), 1, own_rows)
            self.assertEqual(own_rows[0][0], rule_id)

    def test_a_cooldown_write_survives_a_lock_held_by_another_process(self) -> None:
        context = multiprocessing.get_context("spawn")
        ready = self._marker("writer-ready")
        go = self._marker("writer-go")
        result = self._marker("writer-result")
        locked = self._marker("holder-locked")

        writer = context.Process(
            target=_lock_waiting_writer,
            args=(self.db_path, ready, go, result),
        )
        holder = context.Process(
            target=_hold_write_lock,
            args=(self.db_path, _LOCK_HOLD_SECONDS, locked),
        )

        writer.start()
        try:
            self.assertTrue(_wait_for_marker(ready), "the writer never finished setup")
            setup = json.loads(Path(ready).read_text(encoding="utf-8"))
            self.assertIsNone(setup["error"], setup["error"])

            holder.start()
            try:
                self.assertTrue(
                    _wait_for_marker(locked, timeout=30.0), "the lock holder never locked"
                )
                Path(go).write_text("go", encoding="utf-8")
                writer.join(timeout=60)
                self.assertEqual(writer.exitcode, 0)
            finally:
                holder.join(timeout=30)
        finally:
            if writer.is_alive():
                writer.terminate()
                writer.join(timeout=10)

        payload = json.loads(Path(result).read_text(encoding="utf-8"))
        self.assertTrue(payload["ok"], payload["error"])
        rows = self._cooldown_rows(_SHARED_TARGET)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0][3], "contention-0")


if __name__ == "__main__":
    unittest.main()
