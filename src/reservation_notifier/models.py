from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Slot:
    """A single reservation opportunity."""

    venue_id: str
    venue_name: str
    start: datetime
    resy_page_url: str | None = None

    def key(self) -> str:
        return f"{self.venue_id}:{self.start.isoformat()}"
