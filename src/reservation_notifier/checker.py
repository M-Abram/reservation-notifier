from __future__ import annotations

import logging
import os
import re
import threading
import time as time_mod
from datetime import date, datetime, time, timedelta
from typing import List, Mapping, Optional, Sequence

from reservation_notifier.browser_env import (
    format_browser_startup_error,
    is_linux,
    linux_browser_setup_hint,
    resolve_chrome_binary,
    resolve_chromedriver_path,
)
from reservation_notifier.config import AppConfig, Venue
from reservation_notifier.models import Slot

log = logging.getLogger(__name__)

class BrowserStartupError(RuntimeError):
    """Chrome/Chromium or chromedriver could not be started."""

_active_driver: Optional[object] = None
_active_driver_lock = threading.Lock()
_poll_abort: Optional[threading.Event] = None


def set_poll_abort_event(event: Optional[threading.Event]) -> None:
    """When set, ``fetch_available_slots`` exits early (used by GUI Stop)."""
    global _poll_abort
    _poll_abort = event


def stop_active_browser() -> None:
    """Force-close the active Selenium session.

    Must **not** run on the Tk main thread — ``driver.quit()`` can block for many
    seconds and will freeze the GUI. Call from a worker / daemon thread only.
    """
    global _active_driver
    with _active_driver_lock:
        driver = _active_driver
        _active_driver = None
    if driver is None:
        return

    try:
        service = getattr(driver, "service", None)  # type: ignore[union-attr]
        proc = getattr(service, "process", None) if service else None
        if proc is not None:
            proc.terminate()
    except Exception:
        pass

    try:
        driver.quit()  # type: ignore[union-attr]
    except Exception:
        pass

    try:
        service = getattr(driver, "service", None)  # type: ignore[union-attr]
        proc = getattr(service, "process", None) if service else None
        if proc is not None and proc.poll() is None:
            proc.kill()
    except Exception:
        pass


def _aborted() -> bool:
    return _poll_abort is not None and _poll_abort.is_set()


def _register_driver(driver: object) -> None:
    global _active_driver
    with _active_driver_lock:
        _active_driver = driver


# Map Resy `/3/venue` ``location`` shortcodes to `/cities/{slug}/venues/...` path segment.
_CITY_URL_SLUG_BY_LOCATION: Mapping[str, str] = {
    "ny": "new-york-ny",
    "nyc": "new-york-ny",
}

# Loose label like "7:00 PM" / "07:00PM"
_TIME_LABEL_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*(AM|PM)\s*$", re.IGNORECASE)


def slot_in_time_windows(t: time, windows: List[tuple[time, time]]) -> bool:
    for start, end in windows:
        if start <= t <= end:
            return True
    return False


def filter_slots(cfg: AppConfig, slots: List[Slot]) -> List[Slot]:
    windows = [(w.start, w.end) for w in cfg.time_windows]
    filtered: List[Slot] = []
    for s in slots:
        if cfg.date_range.start <= s.start.date() <= cfg.date_range.end:
            if slot_in_time_windows(s.start.time(), windows):
                filtered.append(s)
    return filtered


def _city_url_slug(venue: Venue) -> str | None:
    raw = venue.city_url_slug.strip() if venue.city_url_slug else ""
    if raw:
        return raw
    if venue.location_shortcode:
        return _CITY_URL_SLUG_BY_LOCATION.get(venue.location_shortcode.lower())
    return None


def _daterange_inclusive(start: date, end: date):
    d = start
    one = timedelta(days=1)
    while d <= end:
        yield d
        d += one


def _parse_time_label(day: date, label: str) -> datetime | None:
    m = _TIME_LABEL_RE.match(label.strip())
    if not m:
        return None
    h12, mn, ampm = int(m.group(1)), int(m.group(2)), m.group(3).upper()
    if h12 < 1 or h12 > 12 or mn > 59:
        return None
    if ampm == "PM" and h12 != 12:
        h24 = h12 + 12
    elif ampm == "AM" and h12 == 12:
        h24 = 0
    elif ampm == "AM":
        h24 = h12
    else:
        h24 = h12
    return datetime.combine(day, time(h24, mn))


def _venue_page_url(venue: Venue, day: date, party_size: int) -> str | None:
    city = _city_url_slug(venue)
    slug = venue.url_slug
    if not city or not slug:
        return None
    return (
        f"https://resy.com/cities/{city}/venues/{slug}"
        f"?date={day.isoformat()}&seats={party_size}&activeView=list"
    )


def _selenium_major() -> int:
    try:
        import selenium

        head = selenium.__version__.split(".")[0]
        return int(head) if head.isdigit() else 3
    except Exception:
        return 3


def _build_chrome_driver(cfg: AppConfig):
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError as e:
        raise RuntimeError(
            "Selenium is required. Install dependencies: pip install -r requirements.txt"
        ) from e

    selenium_major = _selenium_major()
    Service = None
    if selenium_major >= 4:
        try:
            from selenium.webdriver.chrome.service import Service
        except ImportError:
            selenium_major = 3

    opts = Options()
    if cfg.checker.selenium_headless:
        opts.add_argument("--headless=new" if selenium_major >= 4 else "--headless")
    opts.add_argument("--window-size=1400,1000")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    if is_linux():
        opts.add_argument("--disable-setuid-sandbox")
    lang = cfg.checker.selenium_accept_language.strip() or "en-US,en;q=0.9"
    opts.add_argument(f"--lang={lang.split(',')[0].strip()}")
    opts.add_experimental_option(
        "prefs",
        {"intl.accept_languages": lang},
    )
    ua = cfg.checker.user_agent.strip()
    if ua:
        opts.add_argument(f"--user-agent={ua}")

    chrome_bin = resolve_chrome_binary(
        (
            os.environ.get("CHROME_BINARY")
            or os.environ.get("CHROMIUM_BIN")
            or os.environ.get("GOOGLE_CHROME_BIN")
            or cfg.checker.selenium_chrome_binary
            or ""
        )
    )
    if chrome_bin:
        if not os.path.isfile(chrome_bin):
            raise BrowserStartupError(
                f"Chrome binary not found at {chrome_bin!r}.\n\n{linux_browser_setup_hint()}"
            )
        opts.binary_location = chrome_bin
        log.info("Using Chrome/Chromium binary: %s", chrome_bin)
    elif is_linux():
        log.warning(
            "No Chrome/Chromium binary found on PATH. Selenium will try its default; "
            "on Linux set CHROME_BINARY (see --check-deps)."
        )

    driver_path = resolve_chromedriver_path(cfg.checker.selenium_chromedriver_path or "")
    if driver_path:
        if not os.path.isfile(driver_path):
            raise BrowserStartupError(
                f"chromedriver not found at {driver_path!r}.\n\n{linux_browser_setup_hint()}"
            )
        log.info("Using chromedriver: %s", driver_path)

    try:
        if selenium_major >= 4 and Service is not None:
            service_kwargs = {}
            if driver_path:
                service_kwargs["executable_path"] = driver_path
            service = Service(**service_kwargs)
            driver = webdriver.Chrome(service=service, options=opts)
        else:
            driver_kwargs = {"options": opts}
            if driver_path:
                driver_kwargs["executable_path"] = driver_path
            driver = webdriver.Chrome(**driver_kwargs)
    except Exception as e:
        raise BrowserStartupError(format_browser_startup_error(e)) from e
    driver.set_page_load_timeout(cfg.checker.selenium_page_load_timeout)
    wait = cfg.checker.selenium_implicit_wait
    if wait and wait > 0:
        driver.implicitly_wait(wait)
    _register_driver(driver)
    return driver


_SCRAPE_SCRIPT = """
const re = /^\\s*\\d{1,2}:\\d{2}\\s*(AM|PM)\\s*$/i;
const out = [];
const seen = new Set();
const selectors = 'button, a[href], [role="button"]';
for (const el of document.querySelectorAll(selectors)) {
  if (el.closest('header, nav, [role="navigation"], [role="combobox"], select, footer'))
    continue;
  const raw = ((el.innerText || '') + '').trim();
  const t = raw.split('\\n').map(s => s.trim()).filter(Boolean)[0] || '';
  const normalized = t.replace(/\\s+/g, ' ');
  if (!re.test(normalized) || normalized.length > 12) continue;
  const key = normalized.toUpperCase();
  if (seen.has(key)) continue;
  seen.add(key);
  out.push(normalized);
}
return out;
"""


def _dismiss_common_overlays(driver) -> None:
    if _aborted():
        return
    try:
        from selenium.common.exceptions import (
            ElementClickInterceptedException,
            NoSuchElementException,
            TimeoutException,
        )
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError:
        return

    for xpath in (
        "//button[contains(translate(., 'ACEGIPT', 'acegipt'), 'accept')]",
        "//button[contains(., 'Got it')]",
        "//button[contains(., 'I Agree')]",
    ):
        try:
            el = WebDriverWait(driver, 2.0).until(EC.element_to_be_clickable((By.XPATH, xpath)))
            el.click()
            time_mod.sleep(0.3)
            return
        except (TimeoutException, NoSuchElementException, ElementClickInterceptedException):
            continue


def _scrape_slot_labels(driver) -> Sequence[str]:
    raw = driver.execute_script(_SCRAPE_SCRIPT)
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for x in raw:
        if isinstance(x, str):
            out.append(x)
    return out


def fetch_available_slots(cfg: AppConfig) -> List[Slot]:
    """Open each venue page in Chrome and collect visible time labels (e.g. ``7:00 PM``)."""
    global _active_driver

    out: List[Slot] = []
    driver = None
    try:
        driver = _build_chrome_driver(cfg)
    except BrowserStartupError:
        raise
    except Exception as e:
        raise BrowserStartupError(format_browser_startup_error(e)) from e

    party = cfg.checker.party_size
    post_wait = cfg.checker.selenium_post_load_wait_seconds

    try:
        for venue in cfg.venues:
            if _aborted():
                break
            city = _city_url_slug(venue)
            if not venue.url_slug or not city:
                log.warning(
                    "Selenium needs url_slug and city_url_slug (or a known location_shortcode "
                    "like ny) for venue %r",
                    venue.name,
                )
                continue
            vid = venue.id or venue.url_slug

            for day in _daterange_inclusive(cfg.date_range.start, cfg.date_range.end):
                if _aborted():
                    break
                url = _venue_page_url(venue, day, party)
                if not url:
                    continue
                try:
                    driver.get(url)
                except Exception as e:
                    if _aborted():
                        break
                    log.warning("Page load failed %s: %s", url, e)
                    continue

                if _aborted():
                    break
                if _poll_abort is not None:
                    if _poll_abort.wait(post_wait):
                        break
                else:
                    time_mod.sleep(post_wait)
                if _aborted():
                    break
                _dismiss_common_overlays(driver)
                labels = _scrape_slot_labels(driver)

                parsed: List[datetime] = []
                for label in labels:
                    dt = _parse_time_label(day, label)
                    if dt:
                        parsed.append(dt)

                log.info(
                    "Selenium poll: %s | day=%s | seats=%s | raw_labels=%s | parsed=%s",
                    venue.name,
                    day.isoformat(),
                    party,
                    len(labels),
                    len(parsed),
                )
                log.debug("Selenium labels: %s", labels)

                for dt in parsed:
                    out.append(
                        Slot(
                            venue_id=str(vid),
                            venue_name=venue.name,
                            start=dt,
                            resy_page_url=url,
                        )
                    )
    finally:
        if driver is not None:
            with _active_driver_lock:
                still_active = _active_driver is driver
                if still_active:
                    _active_driver = None
            if still_active:
                try:
                    driver.quit()
                except Exception:
                    pass

    return out
