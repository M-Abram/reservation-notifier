from __future__ import annotations

from reservation_notifier.checker import fetch_available_slots, filter_slots
from reservation_notifier.config import AppConfig
from reservation_notifier.models import Slot


def poll_filtered(cfg: AppConfig) -> list[Slot]:
    raw = fetch_available_slots(cfg)
    return filter_slots(cfg, raw)
