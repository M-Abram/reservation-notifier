from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, List


@dataclass(frozen=True)
class Venue:
    """Resy venue page: ``url_slug`` + city (see below); optional numeric ``id`` for display."""

    name: str
    id: str | None = None
    url_slug: str | None = None
    location_shortcode: str | None = None
    #: Path segment after /cities/ (e.g. ``new-york-ny``). If omitted, ``location_shortcode``
    #: may map to a default (e.g. ``ny`` → ``new-york-ny``).
    city_url_slug: str | None = None


@dataclass(frozen=True)
class TimeWindow:
    start: time
    end: time


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date


@dataclass(frozen=True)
class NotificationSettings:
    console: bool
    webhook_url: str | None


@dataclass(frozen=True)
class CheckerSettings:
    user_agent: str
    party_size: int = 2
    selenium_headless: bool = True
    selenium_page_load_timeout: int = 45
    selenium_implicit_wait: float = 0.0
    selenium_post_load_wait_seconds: float = 4.0
    selenium_accept_language: str = "en-US,en;q=0.9"
    #: Optional paths for embedded Linux (Jetson); env ``CHROME_BINARY`` / ``CHROMEDRIVER_PATH`` win.
    selenium_chrome_binary: str = ""
    selenium_chromedriver_path: str = ""


@dataclass(frozen=True)
class AppConfig:
    poll_interval_seconds: int
    venues: List[Venue]
    date_range: DateRange
    time_windows: List[TimeWindow]
    notifications: NotificationSettings
    checker: CheckerSettings


def load_config(path: Path) -> AppConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("config root must be an object")

    venues = []
    for v in raw.get("venues", []):
        raw_id = v.get("id")
        vid = str(raw_id).strip() if raw_id not in (None, "") else None
        slug = v.get("url_slug")
        loc = v.get("location_shortcode")
        slug_s = str(slug).strip() if slug else None
        loc_s = str(loc).strip() if loc else None
        name = str(v["name"])
        city_us = v.get("city_url_slug")
        city_us_s = str(city_us).strip() if city_us else None
        if not slug_s:
            raise ValueError(
                f'Venue "{name}" needs url_slug so Selenium can open the Resy venue page.'
            )
        if not city_us_s and not loc_s:
            raise ValueError(
                f'Venue "{name}" needs city_url_slug (e.g. "new-york-ny") or '
                'location_shortcode (e.g. "ny") to build booking URLs.'
            )
        venues.append(
            Venue(
                name=name,
                id=vid,
                url_slug=slug_s,
                location_shortcode=loc_s.lower() if loc_s else None,
                city_url_slug=city_us_s,
            )
        )

    dr = raw["date_range"]
    date_range = DateRange(
        start=date.fromisoformat(str(dr["start"])),
        end=date.fromisoformat(str(dr["end"])),
    )

    windows: List[TimeWindow] = []
    for w in raw.get("time_windows", []):
        windows.append(
            TimeWindow(
                start=_parse_hhmm(str(w["start"])),
                end=_parse_hhmm(str(w["end"])),
            )
        )

    n = raw.get("notifications") or {}
    webhook = n.get("webhook_url")
    notifications = NotificationSettings(
        console=bool(n.get("console", True)),
        webhook_url=str(webhook) if webhook else None,
    )

    c = raw.get("checker") or {}
    checker = CheckerSettings(
        user_agent=str(
            c.get(
                "user_agent",
                "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
        ),
        party_size=int(c.get("party_size", 2)),
        selenium_headless=bool(c.get("selenium_headless", True)),
        selenium_page_load_timeout=int(c.get("selenium_page_load_timeout", 45)),
        selenium_implicit_wait=float(c.get("selenium_implicit_wait", 0.0)),
        selenium_post_load_wait_seconds=float(c.get("selenium_post_load_wait_seconds", 4.0)),
        selenium_accept_language=str(c.get("selenium_accept_language", "en-US,en;q=0.9")),
        selenium_chrome_binary=str(c.get("selenium_chrome_binary", "") or ""),
        selenium_chromedriver_path=str(c.get("selenium_chromedriver_path", "") or ""),
    )

    return AppConfig(
        poll_interval_seconds=int(raw.get("poll_interval_seconds", 120)),
        venues=venues,
        date_range=date_range,
        time_windows=windows,
        notifications=notifications,
        checker=checker,
    )


def _parse_hhmm(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config.json"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Reservation availability notifier")
    p.add_argument(
        "--config",
        type=Path,
        default=default_config_path(),
        help="Path to config.json",
    )
    p.add_argument(
        "--gui",
        action="store_true",
        help="Open a desktop window to configure and start/stop searches.",
    )
    p.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for restaurant, date, and time window (defaults to NYC).",
    )
    p.add_argument(
        "--loop",
        action="store_true",
        help="Run forever, polling every poll_interval_seconds",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be checked without calling the checker",
    )
    return p


def parse_args(argv: list[str] | None = None) -> Any:
    return build_arg_parser().parse_args(argv)
