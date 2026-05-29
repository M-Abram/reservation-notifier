from __future__ import annotations

import logging
import os
from typing import Iterable
from urllib.parse import quote

import httpx

from reservation_notifier.config import NotificationSettings
from reservation_notifier.models import Slot

log = logging.getLogger(__name__)

NTFY_TOPIC_ENV = "NTFY_TOPIC"
NTFY_SERVER_ENV = "NTFY_SERVER"


def _notify_ntfy(slots: list[Slot]) -> None:
    topic = (os.environ.get(NTFY_TOPIC_ENV) or "").strip()
    if not topic:
        return

    server = (os.environ.get(NTFY_SERVER_ENV) or "https://ntfy.sh").strip().rstrip("/")
    url = f"{server}/{quote(topic, safe='')}"

    lines = []
    for s in slots:
        start_local = s.start.strftime("%Y-%m-%d %H:%M")
        line = f"{s.venue_name} ({s.venue_id}) — {start_local}"
        if s.resy_page_url:
            line += f"\n{s.resy_page_url}"
        lines.append(line)
    body = "Reservation slots:\n" + "\n".join(lines)

    try:
        r = httpx.post(
            url,
            content=body.encode("utf-8"),
            headers={
                "Title": "Resy: slots available",
                "Content-Type": "text/plain; charset=utf-8",
            },
            timeout=30.0,
        )
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("ntfy notify failed: %s", e)


def notify(settings: NotificationSettings, slots: Iterable[Slot]) -> None:
    slots = list(slots)
    if not slots:
        return

    if settings.console:
        header = "Reservation slots found"
        if os.environ.get("NO_COLOR") is None and hasattr(os, "isatty"):
            try:
                if os.isatty(1):
                    header = f"\033[1;32m{header}\033[0m"
            except Exception:
                pass
        log.info(header)
        for s in slots:
            start_local = s.start.strftime("%Y-%m-%d %H:%M")
            url = f" ({s.resy_page_url})" if s.resy_page_url else ""
            log.info(
                "  - %s (%s) at %s%s",
                s.venue_name,
                s.venue_id,
                start_local,
                url,
            )

    if settings.webhook_url:
        payload = {
            "text": "Reservation slots found",
            "slots": [
                {
                    "venue_id": s.venue_id,
                    "venue_name": s.venue_name,
                    "start": s.start.isoformat(),
                    "start_local": s.start.strftime("%Y-%m-%d %H:%M"),
                    "resy_page_url": s.resy_page_url,
                }
                for s in slots
            ],
        }
        try:
            r = httpx.post(settings.webhook_url, json=payload, timeout=30.0)
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("Webhook notify failed: %s", e)

    _notify_ntfy(slots)
