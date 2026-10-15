from __future__ import annotations

import logging
import os
import platform
import sys
import threading
import time as time_mod
from datetime import date, datetime, time
from pathlib import Path

from reservation_notifier._tk_env import prepare_tk_environment
from reservation_notifier.config import (
    AppConfig,
    CheckerSettings,
    DateRange,
    NotificationSettings,
    TimeWindow,
    Venue,
    load_config,
    parse_args,
)
from reservation_notifier.checker import BrowserStartupError, PollError, filter_slots
from reservation_notifier.notifier import notify
from reservation_notifier.polling import (
    CONFIG_ERROR_EXIT_CODE,
    PollMonitor,
    PollResult,
    poll_filtered,
)
from reservation_notifier.resy_search import search_nyc_venues
from reservation_notifier.tty_logging import install_colored_stderr_logging

log = logging.getLogger(__name__)


def _use_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(text: str, code: str) -> str:
    if not _use_color():
        return text
    return f"\033[{code}m{text}\033[0m"


def _title(text: str) -> str:
    return _c(text, "1;36")  # bold cyan


def _prompt_label(text: str) -> str:
    return _c(text, "1;33")  # bold yellow


def _error(text: str) -> str:
    return _c(text, "1;31")  # bold red


def _configure_logging() -> None:
    install_colored_stderr_logging(level=logging.INFO)


def _clear_stdout_line() -> None:
    if sys.stdout.isatty():
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()


def _start_stdout_spinner(label: str) -> tuple[threading.Event, threading.Thread]:
    """Non-blocking spinner on stdout (TTY only). Caller must ``_stop_stdout_spinner``."""
    stop = threading.Event()

    def worker() -> None:
        frames = "|/-\\"
        i = 0
        dim = "\033[2m" if _use_color() else ""
        reset = "\033[0m" if _use_color() else ""
        while not stop.wait(0.12):
            if not sys.stdout.isatty():
                continue
            ch = frames[i % len(frames)]
            i += 1
            sys.stdout.write(f"\r{dim}{label} {ch}\033[K{reset}")
            sys.stdout.flush()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return stop, t


def _stop_stdout_spinner(stop: threading.Event, t: threading.Thread) -> None:
    stop.set()
    t.join(timeout=3)
    _clear_stdout_line()


def _sleep_until_next_poll(seconds: float, venue_name: str) -> None:
    if seconds <= 0:
        return
    if not sys.stdout.isatty():
        log.info("Waiting %.0fs until next check (%s)...", seconds, venue_name)
        time_mod.sleep(seconds)
        return

    deadline = time_mod.monotonic() + seconds
    frames = "|/-\\"
    dim = "\033[2m" if _use_color() else ""
    reset = "\033[0m" if _use_color() else ""
    i = 0
    try:
        while True:
            remaining = deadline - time_mod.monotonic()
            if remaining <= 0:
                break
            ch = frames[i % len(frames)]
            i += 1
            sys.stdout.write(
                f"\r{dim}Waiting — next check for {venue_name} in "
                f"{remaining:>5.1f}s {ch}  \033[K{reset}"
            )
            sys.stdout.flush()
            time_mod.sleep(min(0.18, remaining))
    finally:
        _clear_stdout_line()


def _prompt_nonempty(prompt: str) -> str:
    while True:
        v = input(prompt).strip()
        if v:
            return v


def _prompt_date(prompt: str) -> date:
    """Blank input means today."""
    while True:
        s = input(prompt).strip()
        if not s:
            return date.today()
        try:
            return date.fromisoformat(s)
        except ValueError:
            print(_error('Invalid date. Use format YYYY-MM-DD (for example "2026-05-30").'))


def _prompt_hhmm(prompt: str) -> time:
    while True:
        s = _prompt_nonempty(prompt)
        try:
            return datetime.strptime(s, "%H:%M").time()
        except ValueError:
            print(_error('Invalid time. Use 24h format HH:MM (for example "19:00").'))


def _prompt_int(prompt: str, *, min_value: int, max_value: int) -> int:
    while True:
        s = _prompt_nonempty(prompt)
        try:
            v = int(s)
        except ValueError:
            print(_error(f"Invalid number. Enter an integer {min_value}–{max_value}."))
            continue
        if v < min_value or v > max_value:
            print(_error(f"Out of range. Enter an integer {min_value}–{max_value}."))
            continue
        return v


def _choose_venue_by_name(name: str) -> Venue:
    try:
        hits = search_nyc_venues(name, per_page=5)
    except Exception as e:
        raise RuntimeError(
            f"Could not search Resy for {name!r}: {e}\n"
            "Check network access and that api.resy.com is reachable."
        ) from e
    if not hits:
        raise ValueError(f'No NYC matches found for "{name}". Try a different name.')

    if len(hits) == 1:
        h = hits[0]
        return Venue(
            name=h.name,
            id=h.venue_id,
            url_slug=h.url_slug,
            location_shortcode=h.location_shortcode,
            city_url_slug=h.city_url_slug,
        )

    print("Found multiple matches in NYC. Choose one:")
    for i, h in enumerate(hits, start=1):
        print(f"  {i}) {h.name} (resy id {h.venue_id}, /{h.city_url_slug}/venues/{h.url_slug})")

    while True:
        raw = _prompt_nonempty(f"Enter 1-{len(hits)}: ")
        try:
            idx = int(raw)
            if 1 <= idx <= len(hits):
                h = hits[idx - 1]
                return Venue(
                    name=h.name,
                    id=h.venue_id,
                    url_slug=h.url_slug,
                    location_shortcode=h.location_shortcode,
                    city_url_slug=h.city_url_slug,
                )
        except ValueError:
            pass
        print("Invalid selection.")


def check_dependencies() -> int:
    """Print dependency status; return 0 if ready for interactive/loop mode."""
    ok = True

    def good(msg: str) -> None:
        print(f"  OK  {msg}")

    def bad(msg: str) -> None:
        nonlocal ok
        ok = False
        print(f"  FAIL  {msg}")

    print("Reservation notifier — dependency check\n")
    py = sys.version_info
    if py < (3, 6):
        bad(f"Python {py.major}.{py.minor} is too old (need 3.6+).")
    elif py < (3, 8):
        print(f"  WARN  Python {py.major}.{py.minor} — using selenium 3.x (see requirements-legacy.txt)")
    else:
        good(f"Python {py.major}.{py.minor}.{py.micro}")

    try:
        import ssl

        good(f"SSL module ({ssl.OPENSSL_VERSION})")
    except ImportError:
        bad(
            "Python SSL module missing — pip cannot install packages. "
            "Install libssl-dev and use apt Python: sudo apt install python3-venv libssl-dev"
        )

    try:
        import httpx  # noqa: F401
        import selenium

        good(f"Python packages (httpx, selenium {selenium.__version__})")
    except ImportError as e:
        bad(f"Missing package: {e}. Run: pip install -r requirements.txt && pip install -e .")

    try:
        from reservation_notifier.browser_env import (
            resolve_chrome_binary,
            resolve_chromedriver_path,
        )

        chrome = resolve_chrome_binary()
        driver = resolve_chromedriver_path()
        if chrome and os.path.isfile(chrome):
            good(f"Chrome/Chromium: {chrome}")
        elif chrome:
            bad(f"CHROME_BINARY set but file missing: {chrome}")
        else:
            bad("Chrome/Chromium not found (set CHROME_BINARY)")

        if driver and os.path.isfile(driver):
            good(f"chromedriver: {driver}")
        elif driver:
            bad(f"CHROMEDRIVER_PATH set but file missing: {driver}")
        else:
            print("  WARN  chromedriver not found — Selenium may auto-download on x86_64")
            if sys.platform.startswith("linux") and platform.machine().lower() in {
                "aarch64",
                "arm64",
                "armv7l",
            }:
                bad("ARM Linux usually needs CHROMEDRIVER_PATH set explicitly")
    except Exception as e:
        bad(f"Browser check failed: {e}")

    try:
        hits = search_nyc_venues("Lilia", per_page=1)
        if hits:
            good(f"Resy search API ({hits[0].name})")
        else:
            bad("Resy search API returned no NYC results for 'Lilia'")
    except Exception as e:
        bad(f"Resy search API: {e}")

    if not ok:
        print("\nLinux setup hint:\n")
        from reservation_notifier.browser_env import linux_browser_setup_hint

        print(linux_browser_setup_hint())
        return 1

    print("\nReady. Run: python -m reservation_notifier --interactive")
    return 0


def run_interactive() -> None:
    print(_title("NYC reservation check (interactive)"), flush=True)
    print("", flush=True)
    print(_title("Input formats"), flush=True)
    print('  - Date: YYYY-MM-DD (example: "2026-05-30")', flush=True)
    print('  - Time: 24h HH:MM (example: "19:00" for 7pm)', flush=True)
    print("", flush=True)

    restaurant = _prompt_nonempty(_prompt_label("Restaurant name") + ": ")
    seats = _prompt_int(_prompt_label("Seats (party size)") + " [1-20]: ", min_value=1, max_value=20)
    day = _prompt_date(_prompt_label("Date") + " (YYYY-MM-DD, blank = today): ")
    start_t = _prompt_hhmm(_prompt_label("Time window start") + " (HH:MM): ")
    end_t = _prompt_hhmm(_prompt_label("Time window end") + " (HH:MM): ")
    print("", flush=True)

    try:
        venue = _choose_venue_by_name(restaurant)
    except (RuntimeError, ValueError) as e:
        print(_error(str(e)), flush=True)
        sys.exit(1)

    print(_title("Searching"), flush=True)
    print(f"  - Venue: {venue.name} ({venue.city_url_slug}/venues/{venue.url_slug})", flush=True)
    print(f"  - Date:  {day.isoformat()}", flush=True)
    print(f"  - Seats: {seats}", flush=True)
    print(f"  - Window: {start_t.strftime('%H:%M')}–{end_t.strftime('%H:%M')}", flush=True)
    print("", flush=True)

    cfg = AppConfig(
        poll_interval_seconds=30,
        venues=[venue],
        date_range=DateRange(start=day, end=day),
        time_windows=[TimeWindow(start=start_t, end=end_t)],
        notifications=NotificationSettings(console=True, webhook_url=None),
        checker=CheckerSettings(
            user_agent="Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            party_size=seats,
        ),
    )

    log.info(
        "Polling every %ss; you'll be notified of each new matching time. Press Ctrl+C to stop.",
        cfg.poll_interval_seconds,
    )
    monitor = PollMonitor(cfg)
    while True:
        _exit_if_date_range_passed(cfg)
        stop_evt: threading.Event | None = None
        spin_thr: threading.Thread | None = None
        try:
            if sys.stdout.isatty():
                stop_evt, spin_thr = _start_stdout_spinner(f"Searching Resy ({venue.name}) in browser")
            result = monitor.poll()
        except BrowserStartupError as e:
            print(_error(str(e)), flush=True)
            sys.exit(1)
        finally:
            if stop_evt is not None and spin_thr is not None:
                _stop_stdout_spinner(stop_evt, spin_thr)

        _log_poll_result(result)
        _sleep_until_next_poll(monitor.next_wait_seconds(), venue.name)


def _log_poll_result(result: PollResult) -> None:
    if result.new_slots:
        log.info("Match found — still watching for new times.")
    elif result.slots:
        log.info("No new matching slots (still open: %d).", len(result.slots))
    elif result.error is None:
        log.info("No matching slots this run.")


def _exit_if_date_range_passed(cfg: AppConfig) -> None:
    if cfg.date_range_passed():
        log.error(
            "Date range ended %s — nothing left to search. Update the dates to keep monitoring.",
            cfg.resolved_date_range().end.isoformat(),
        )
        sys.exit(CONFIG_ERROR_EXIT_CODE)


def run_once(config_path: Path, *, dry_run: bool = False) -> None:
    cfg = load_config(config_path)
    _exit_if_date_range_passed(cfg)
    date_range = cfg.resolved_date_range()
    if dry_run:
        log.info(
            "Dry run: would poll %d venue(s), date %s..%s, interval=%ss",
            len(cfg.venues),
            date_range.start.isoformat(),
            date_range.end.isoformat(),
            cfg.poll_interval_seconds,
        )
        return

    error: PollError | None = None
    try:
        slots = poll_filtered(cfg)
    except PollError as e:
        log.error("Some pages could not be checked: %s", e)
        slots = filter_slots(cfg, e.slots)
        error = e
    if slots:
        notify(cfg.notifications, slots)
    elif error is None:
        log.info("No matching slots this run.")
    if error is not None:
        sys.exit(1)


def state_path_for(config_path: Path) -> Path:
    """Where ``--loop`` remembers the last poll's slots, so restarts don't re-notify."""
    return config_path.with_name(f".{config_path.stem}.state.json")


def run_loop(config_path: Path) -> None:
    cfg = load_config(config_path)
    monitor = PollMonitor(cfg, state_path=state_path_for(config_path))

    log.info(
        "Loop mode: %d venue(s), poll every %ss",
        len(cfg.venues),
        cfg.poll_interval_seconds,
    )

    while True:
        _exit_if_date_range_passed(cfg)
        try:
            _log_poll_result(monitor.poll())
        except BrowserStartupError as e:
            monitor.record_failure(e)

        time_mod.sleep(monitor.next_wait_seconds())


def main(argv: list[str] | None = None) -> None:
    prepare_tk_environment()
    args = parse_args(argv)
    if args.check_deps:
        sys.exit(check_dependencies())
    if args.gui:
        try:
            from reservation_notifier.gui_mode import run_gui_mode
        except Exception as e:
            print(
                "Could not load GUI mode. Install Tk (e.g. `sudo apt install python3-tk`) "
                "or run without `--gui`.",
                file=sys.stderr,
            )
            print(f"Details: {e!r}", file=sys.stderr)
            sys.exit(1)
        run_gui_mode()
        return

    _configure_logging()
    if args.interactive or not args.config.exists():
        if not args.config.exists() and not args.interactive:
            log.info("Config not found (%s); starting interactive mode.", args.config)
        run_interactive()
        return

    if args.loop:
        run_loop(args.config)
    else:
        run_once(args.config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
