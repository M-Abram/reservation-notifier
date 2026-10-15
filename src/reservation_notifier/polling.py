from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set

from reservation_notifier.checker import (
    BrowserStartupError,
    PollError,
    fetch_available_slots,
    filter_slots,
    poll_aborted,
)
from reservation_notifier.config import AppConfig
from reservation_notifier.models import Slot
from reservation_notifier.notifier import notify, notify_status

log = logging.getLogger(__name__)

#: Send a "monitor failing" alert after this many failed polls in a row.
FAILURE_ALERT_AFTER = 5
#: After failures the wait between polls doubles each time, up to this cap.
MAX_BACKOFF_SECONDS = 600
#: Exit code for config problems (e.g. date range passed); systemd shouldn't restart on it.
CONFIG_ERROR_EXIT_CODE = 2


def poll_filtered(cfg: AppConfig) -> list[Slot]:
    raw = fetch_available_slots(cfg)
    return filter_slots(cfg, raw)


def describe_error(error: BaseException) -> str:
    """Error type plus first line of its message (Selenium appends long stack traces)."""
    lines = str(error).strip().splitlines()
    return f"{type(error).__name__}: {lines[0]}" if lines else type(error).__name__


@dataclass
class PollResult:
    slots: List[Slot]
    new_slots: List[Slot]
    error: Optional[BaseException] = None


class PollMonitor:
    """Polls Resy, notifies about slots not seen in the previous poll, and tracks failures.

    With ``state_path``, the previous poll's slots survive restarts so a restarted
    monitor doesn't re-send the same alert.
    """

    def __init__(self, cfg: AppConfig, *, state_path: Optional[Path] = None) -> None:
        self.cfg = cfg
        self.failures = 0
        self._state_path = state_path
        self._previous: Optional[Set[str]] = self._load_state()

    def poll(self) -> PollResult:
        """One poll. Raises ``BrowserStartupError``; any other failure is recorded."""
        error: Optional[BaseException] = None
        try:
            slots = poll_filtered(self.cfg)
        except BrowserStartupError:
            raise
        except PollError as e:
            slots = filter_slots(self.cfg, e.slots)
            error = e
        except Exception as e:  # keep monitoring through unexpected errors
            if not poll_aborted():
                log.exception("Poll iteration failed")
            slots = []
            error = e

        if poll_aborted():  # stopped mid-poll: results are partial, errors expected
            return PollResult(slots=[], new_slots=[])

        if error is None:
            self.record_success()
        else:
            self.record_failure(error)

        new_slots = self._track(slots, complete=error is None)
        if new_slots:
            notify(self.cfg.notifications, new_slots)
        return PollResult(slots=slots, new_slots=new_slots, error=error)

    def record_failure(self, error: BaseException) -> None:
        self.failures += 1
        log.warning("Check failed (%d in a row): %s", self.failures, describe_error(error))
        if self.failures == FAILURE_ALERT_AFTER:
            names = ", ".join(v.name for v in self.cfg.venues)
            notify_status(
                f"Resy monitor failing: {names}",
                f"The last {self.failures} checks failed, so openings may be missed.\n\n"
                f"Latest error: {describe_error(error)}",
                tags="warning",
            )

    def record_success(self) -> None:
        if self.failures >= FAILURE_ALERT_AFTER:
            names = ", ".join(v.name for v in self.cfg.venues)
            notify_status(
                f"Resy monitor recovered: {names}",
                f"Checks are working again after {self.failures} failures.",
                priority="low",
                tags="white_check_mark",
            )
        self.failures = 0

    def next_wait_seconds(self) -> float:
        """Poll interval, doubled per consecutive failure up to ``MAX_BACKOFF_SECONDS``."""
        base = max(5, self.cfg.poll_interval_seconds)
        if not self.failures:
            return base
        return min(base * 2 ** min(self.failures, 10), max(MAX_BACKOFF_SECONDS, base))

    def _track(self, slots: List[Slot], *, complete: bool) -> List[Slot]:
        """Slots not in the previous poll. After a partial (failed) poll, remember both."""
        keys = {s.key() for s in slots}
        previous = self._previous or set()
        new_slots = [s for s in slots if s.key() not in previous]
        current = keys if complete else previous | keys
        if current != self._previous:
            self._previous = current
            self._save_state()
        return new_slots

    def _load_state(self) -> Optional[Set[str]]:
        if self._state_path is None:
            return None
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        keys = state.get("slot_keys") if isinstance(state, dict) else None
        return {str(k) for k in keys} if isinstance(keys, list) else None

    def _save_state(self) -> None:
        if self._state_path is None or self._previous is None:
            return
        try:
            self._state_path.write_text(
                json.dumps({"slot_keys": sorted(self._previous)}), encoding="utf-8"
            )
        except OSError as e:
            log.warning("Failed to save state to %s: %s", self._state_path, e)
