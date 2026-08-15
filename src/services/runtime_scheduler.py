# -*- coding: utf-8 -*-
"""Runtime scheduler service for long-lived API/Web/Desktop processes."""

from __future__ import annotations

import logging
import os
import threading
import _thread
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Set

from src.config import Config, get_config
from src.scheduler import Scheduler, normalize_schedule_times

logger = logging.getLogger(__name__)
CLI_SCHEDULER_OWNER_ENV = "DSA_CLI_SCHEDULER_OWNS_SCHEDULE"
RUNTIME_SCHEDULER_FORCE_ENABLED_ENV = "DSA_RUNTIME_SCHEDULER_FORCE_ENABLED"
RUNTIME_SCHEDULER_RUN_IMMEDIATELY_ENV = "DSA_RUNTIME_SCHEDULER_RUN_IMMEDIATELY"
RUNTIME_SCHEDULER_SUPPRESS_START_ENV = "DSA_RUNTIME_SCHEDULER_SUPPRESS_START"
RUNTIME_SCHEDULER_ARGS_ENV = "DSA_RUNTIME_SCHEDULER_ARGS"
_RUNTIME_ANALYSIS_LOCK = threading.Lock()

# Bounded retry for a scheduled analysis run that failed outright (e.g. an LLM
# key that was briefly down). Prevents a single failure silently dropping the
# whole day's report with no follow-up.
MAX_SCHEDULED_RETRIES = 3
RETRY_DELAY_SECONDS = 300  # 5 min


def _analysis_lock_path_from_config(config: Any) -> str:
    """Cross-process lock file anchored next to the shared SQLite DB."""
    try:
        db_path = Path(getattr(config, "database_path", "./data/stock_analysis.db"))
        return str(db_path) + ".analysis.lock"
    except Exception:  # pragma: no cover - defensive fallback
        return "./data/stock_analysis.db.analysis.lock"


class CrossProcessAnalysisLock:
    """Non-blocking cross-process mutual exclusion for the daily analysis run.

    The process-local ``_RUNTIME_ANALYSIS_LOCK`` only coordinates threads inside
    one process; the CLI ``--schedule`` path and the API runtime scheduler (or
    several uvicorn workers) can otherwise run the same day's analysis
    concurrently and emit duplicate reports/notifications. An ``fcntl`` file
    lock shared through the on-disk lock file coordinates all of them.
    """

    def __init__(self, lock_path: str):
        self._lock_path = lock_path
        self._fd: Optional[int] = None

    def acquire(self) -> bool:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - non-POSIX; fall back to in-process lock
            return True
        parent = os.path.dirname(self._lock_path) or "."
        try:
            os.makedirs(parent, exist_ok=True)
            fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            import fcntl

            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except Exception:  # pragma: no cover - best-effort unlock
            pass
        try:
            os.close(self._fd)
        except OSError:  # pragma: no cover - best-effort close
            pass
        self._fd = None
SCHEDULE_ARGS_OVERRIDE_KEYS = {
    "no_notify",
    "no_market_review",
    "dry_run",
    "force_run",
    "single_notify",
    "no_context_snapshot",
    "workers",
    "portfolio",
}


def run_with_global_analysis_lock(
    task_runner: Callable[[Config, Any, Optional[List[str]]], Any],
    config: Config,
    args: Any,
    stock_codes: Optional[List[str]] = None,
    *,
    blocking: bool = True,
) -> bool:
    """Execute a task while holding the shared runtime analysis lock."""
    if not _RUNTIME_ANALYSIS_LOCK.acquire(blocking=blocking):
        return False
    try:
        task_runner(config, args, stock_codes)
    finally:
        _RUNTIME_ANALYSIS_LOCK.release()
    return True


def run_full_analysis_cross_process(
    config: Config,
    args: Any,
    stock_codes: Optional[List[str]] = None,
) -> Any:
    """Run the full analysis guarded by the cross-process file lock.

    The CLI ``--schedule`` path calls this instead of ``run_full_analysis``
    directly, so it shares mutual exclusion with the API runtime scheduler
    (which runs in a separate process under uvicorn). Returns ``False`` when
    another process already holds the lock; otherwise returns the underlying
    ``run_full_analysis`` result.
    """
    from main import run_full_analysis

    lock = CrossProcessAnalysisLock(_analysis_lock_path_from_config(config))
    if not lock.acquire():
        logger.warning("Cross-process analysis lock busy; skipping scheduled run")
        return False
    try:
        return run_full_analysis(config, args, stock_codes)
    finally:
        lock.release()


def _agent_event_monitor_interval_seconds(config: Config) -> int:
    """Return the validated Event Monitor polling interval in seconds."""
    interval_minutes = getattr(config, "agent_event_monitor_interval_minutes", 5)
    try:
        interval_minutes = max(1, int(interval_minutes))
    except (TypeError, ValueError):  # pragma: no cover - defensive branch
        logger.warning(
            "Invalid AGENT_EVENT_MONITOR_INTERVAL_MINUTES=%r; use fallback 5",
            interval_minutes,
        )
        interval_minutes = 5
    return interval_minutes * 60


def build_agent_event_monitor_background_tasks(
    config: Config,
    *,
    config_provider: Callable[[], Config],
) -> List[Dict[str, Any]]:
    """Build scheduler background tasks used by the runtime scheduler."""
    if not getattr(config, "agent_event_monitor_enabled", False):
        return []

    from src.services.alert_worker import AlertWorker

    interval_seconds = _agent_event_monitor_interval_seconds(config)
    try:
        alert_worker = AlertWorker(config_provider=config_provider)
    except Exception as exc:  # pragma: no cover - defensive branch
        logger.warning("Failed to initialize AlertWorker for event monitor: %s", exc)
        return []

    def event_monitor_task() -> None:
        stats = alert_worker.run_once()
        triggered_count = stats.get("triggered", 0)
        if triggered_count:
            logger.info("[EventMonitor] triggered %d alert(s)", triggered_count)

    return [{
        "task": event_monitor_task,
        "interval_seconds": interval_seconds,
        "run_immediately": True,
        "name": "agent_event_monitor",
    }]


class RuntimeSchedulerService:
    """Manage scheduled analysis inside the current API/Web/Desktop process."""

    def __init__(
        self,
        *,
        config_provider: Callable[[], Config] = get_config,
        task_runner: Optional[Callable[[Config, Any, Optional[List[str]]], Any]] = None,
        owns_schedule: Optional[bool] = None,
        force_enabled: bool = False,
        run_immediately_in_background: bool = False,
        background_tasks_provider: Optional[Callable[[Config], List[Dict[str, Any]]]] = None,
        schedule_args_overrides: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._config_provider = config_provider
        self._task_runner = task_runner
        if owns_schedule is None:
            owns_schedule = os.getenv(CLI_SCHEDULER_OWNER_ENV, "").strip().lower() not in {
                "1",
                "true",
                "yes",
                "on",
            }
        self._owns_schedule = owns_schedule
        self._force_enabled = force_enabled
        self._run_immediately_in_background = run_immediately_in_background
        self._background_tasks_provider = background_tasks_provider
        self._schedule_args_overrides = {
            key: value
            for key, value in (schedule_args_overrides or {}).items()
            if key in SCHEDULE_ARGS_OVERRIDE_KEYS
        }
        self._background_task_cache: Dict[str, Dict[str, Any]] = {}
        self._background_task_registered_names: Set[str] = set()
        self._lock = threading.RLock()
        self._run_lock = _RUNTIME_ANALYSIS_LOCK
        self._scheduler: Optional[Scheduler] = None
        self._thread: Optional[threading.Thread] = None
        self._enabled = False
        self._last_run_at: Optional[str] = None
        self._last_success_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._last_failed_at: Optional[str] = None
        self._consecutive_failures: int = 0
        self._last_skipped_at: Optional[str] = None
        self._last_skip_reason: Optional[str] = None

    def _make_schedule_args(self) -> SimpleNamespace:
        defaults = {
            "schedule": True,
            "no_run_immediately": True,
            "no_notify": False,
            "no_market_review": False,
            "dry_run": False,
            "force_run": False,
            "single_notify": False,
            "no_context_snapshot": False,
            "market_review": False,
            "serve": False,
            "serve_only": True,
            "stocks": None,
            "portfolio": None,
            "workers": None,
        }
        defaults.update(self._schedule_args_overrides)
        return SimpleNamespace(**defaults)

    def _reload_config(self) -> Config:
        from main import _reload_runtime_config

        return _reload_runtime_config()

    def _record_analysis_busy_skip(self) -> None:
        self._last_skipped_at = datetime.now().isoformat()
        self._last_skip_reason = "analysis_already_running"
        logger.warning("Runtime scheduler skipped run: analysis already running")

    def _record_cross_process_busy_skip(self) -> None:
        self._last_skipped_at = datetime.now().isoformat()
        self._last_skip_reason = "analysis_running_elsewhere"
        logger.warning("Runtime scheduler skipped run: analysis running in another process")

    def _analysis_lock_path(self, config: Config) -> str:
        return _analysis_lock_path_from_config(config)

    def _run_analysis_locked(self, stock_codes: Optional[List[str]]) -> None:
        try:
            config = self._reload_config()
            runner = self._task_runner
            if runner is None:
                from main import run_scheduled_analysis

                runner = run_scheduled_analysis
            xp_lock = CrossProcessAnalysisLock(self._analysis_lock_path(config))
            if not xp_lock.acquire():
                self._record_cross_process_busy_skip()
                return
            try:
                self._last_run_at = datetime.now().isoformat()
                result = runner(config, self._make_schedule_args(), stock_codes)
            finally:
                xp_lock.release()
            if result is False:
                raise RuntimeError("runtime scheduled analysis reported failure")
            self._last_success_at = datetime.now().isoformat()
            self._last_error = None
            self._last_failed_at = None
            self._consecutive_failures = 0
        except Exception as exc:  # noqa: BLE001 - scheduled runs must not kill API process.
            self._last_error = str(exc)
            self._last_failed_at = datetime.now().isoformat()
            self._consecutive_failures += 1
            logger.exception("Runtime scheduled analysis failed: %s", exc)
            self._schedule_retry_if_needed()

    def _schedule_retry_if_needed(self) -> None:
        if self._consecutive_failures >= MAX_SCHEDULED_RETRIES:
            logger.warning(
                "Runtime scheduled analysis failed %d consecutive times; no more retries",
                self._consecutive_failures,
            )
            return
        retry_num = self._consecutive_failures

        def _retry() -> None:
            # Skip if a newer run already succeeded or changed the failure count.
            if self._consecutive_failures != retry_num:
                return
            logger.warning(
                "Retrying runtime scheduled analysis (attempt %d/%d)",
                retry_num,
                MAX_SCHEDULED_RETRIES,
            )
            self._run_analysis_once(None)

        timer = threading.Timer(RETRY_DELAY_SECONDS, _retry)
        timer.daemon = True
        timer.start()

    def _run_analysis_once(self, stock_codes: Optional[List[str]] = None) -> bool:
        if not self._run_lock.acquire(blocking=False):
            self._record_analysis_busy_skip()
            return False
        try:
            self._run_analysis_locked(stock_codes)
        finally:
            self._run_lock.release()
        return True

    def _current_times(self) -> List[str]:
        config = self._config_provider()
        return normalize_schedule_times(
            getattr(config, "schedule_times", None),
            fallback_time=getattr(config, "schedule_time", "18:00"),
        )

    def _is_schedule_enabled(self, config: Config) -> bool:
        return self._force_enabled or bool(getattr(config, "schedule_enabled", False))

    def _current_background_tasks(self, config: Config) -> List[Dict[str, Any]]:
        if self._background_tasks_provider is not None:
            return self._background_tasks_provider(config)
        tasks = list(self._current_agent_event_monitor_background_tasks(config))
        tasks.extend(self._current_paper_valuation_background_tasks(config))
        return tasks

    def _current_paper_valuation_background_tasks(self, config: Config) -> List[Dict[str, Any]]:
        name = "paper_daily_valuation"
        if not getattr(config, "paper_trading_enabled", False):
            self._background_task_cache.pop(name, None)
            self._background_task_registered_names.discard(name)
            return []

        cached = self._background_task_cache.get(name)
        if cached is None:
            from src.services.paper_service import PaperService

            interval_seconds = 1800  # 30 min; run_daily_valuation is idempotent per day

            def paper_valuation_task() -> None:
                try:
                    account = PaperService().get_or_create_account()
                    PaperService().run_daily_valuation(account["account_id"])
                except Exception as exc:  # pragma: no cover - defensive branch
                    logger.warning("paper daily valuation failed: %s", exc)

            cached = {"task": paper_valuation_task, "interval_seconds": interval_seconds}
            self._background_task_cache[name] = cached

        run_immediately = name not in self._background_task_registered_names
        self._background_task_registered_names.add(name)
        return [{
            "task": cached["task"],
            "interval_seconds": int(cached["interval_seconds"]),
            "run_immediately": run_immediately,
            "name": name,
        }]

    def _current_agent_event_monitor_background_tasks(self, config: Config) -> List[Dict[str, Any]]:
        name = "agent_event_monitor"
        if not getattr(config, "agent_event_monitor_enabled", False):
            self._background_task_cache.pop(name, None)
            self._background_task_registered_names.discard(name)
            return []

        cached = self._background_task_cache.get(name)
        if cached is None:
            entries = build_agent_event_monitor_background_tasks(
                config,
                config_provider=self._reload_config,
            )
            if not entries:
                self._background_task_cache.pop(name, None)
                self._background_task_registered_names.discard(name)
                return []
            cached = dict(entries[0])
            cached["name"] = name
            self._background_task_cache[name] = cached
            interval_seconds = int(cached["interval_seconds"])
        else:
            interval_seconds = _agent_event_monitor_interval_seconds(config)

        run_immediately = (
            bool(cached.get("run_immediately", False))
            and name not in self._background_task_registered_names
        )
        self._background_task_registered_names.add(name)
        return [{
            "task": cached["task"],
            "interval_seconds": interval_seconds,
            "run_immediately": run_immediately,
            "name": name,
        }]

    @staticmethod
    def _run_in_background_thread(target: Callable[[], None]) -> None:
        """Run a callback in a background thread without blocking startup."""
        try:
            _thread.start_new_thread(target, ())
            return
        except Exception:
            # Best-effort fallback for environments where the low-level thread API
            # is unavailable or restricted.
            thread = threading.Thread(target=target, daemon=True)
            thread.start()

    def start(self, *, run_immediately: bool = False) -> None:
        with self._lock:
            if not self._owns_schedule:
                self.stop()
                return
            config = self._config_provider()
            if not self._is_schedule_enabled(config):
                self.stop()
                return
            background_tasks = self._current_background_tasks(config)
            self.stop()
            times = normalize_schedule_times(
                getattr(config, "schedule_times", None),
                fallback_time=getattr(config, "schedule_time", "18:00"),
            )
            scheduler = Scheduler(
                schedule_time=getattr(config, "schedule_time", "18:00"),
                schedule_times=times,
                schedule_times_provider=self._current_times,
                register_signals=False,
            )
            if run_immediately and self._run_immediately_in_background:
                scheduler.set_daily_task(self._run_analysis_once, run_immediately=False)
            else:
                scheduler.set_daily_task(self._run_analysis_once, run_immediately=run_immediately)
            for entry in background_tasks:
                scheduler.add_background_task(
                    entry["task"],
                    interval_seconds=entry["interval_seconds"],
                    run_immediately=entry.get("run_immediately", False),
                    name=entry.get("name"),
                )
            if run_immediately and self._run_immediately_in_background:
                self._run_in_background_thread(self._run_analysis_once)
            thread = threading.Thread(
                target=scheduler.run,
                daemon=True,
                name="runtime-scheduler",
            )
            self._scheduler = scheduler
            self._thread = thread
            self._enabled = True
            thread.start()

    def stop(self) -> None:
        scheduler = self._scheduler
        if scheduler is not None:
            scheduler.stop()
        self._scheduler = None
        self._thread = None
        self._enabled = False

    def reconcile_from_config(
        self,
        *,
        run_immediately: bool = False,
        clear_enabled_override: bool = False,
    ) -> None:
        if clear_enabled_override:
            self._force_enabled = False
        if not self._owns_schedule:
            self.stop()
            return
        config = self._config_provider()
        if self._is_schedule_enabled(config):
            self.start(run_immediately=run_immediately)
        else:
            self.stop()

    def run_now(self) -> Dict[str, Any]:
        if not self._run_lock.acquire(blocking=False):
            self._record_analysis_busy_skip()
            return {
                "accepted": False,
                "running": True,
                "reason": "analysis_already_running",
            }

        def run_and_release() -> None:
            try:
                self._run_analysis_locked(None)
            finally:
                self._run_lock.release()

        worker = threading.Thread(
            target=run_and_release,
            daemon=True,
            name="runtime-scheduler-run-now",
        )
        try:
            worker.start()
        except Exception:
            self._run_lock.release()
            raise
        return {"accepted": True, "running": True}

    def status(self) -> Dict[str, Any]:
        scheduler = self._scheduler
        jobs = scheduler.schedule.get_jobs() if scheduler is not None else []
        next_run = None
        if jobs:
            next_run = min(job.next_run for job in jobs).isoformat()
        if scheduler is not None:
            schedule_times = list(getattr(scheduler, "schedule_times", []))
        else:
            try:
                schedule_times = self._current_times()
            except Exception:  # pragma: no cover - defensive status fallback
                schedule_times = []
        running = self._run_lock.locked()
        return {
            "enabled": self._enabled,
            "running": running,
            "schedule_times": schedule_times,
            "next_run_at": next_run,
            "last_run_at": self._last_run_at,
            "last_success_at": self._last_success_at,
            "last_error": self._last_error,
            "last_failed_at": self._last_failed_at,
            "consecutive_failures": self._consecutive_failures,
            "last_skipped_at": self._last_skipped_at,
            "last_skip_reason": self._last_skip_reason,
        }
