from __future__ import annotations

import os
import platform
import shutil
import sys
from typing import List, Optional, Sequence


_LINUX_CHROME_NAMES: Sequence[str] = (
    "google-chrome-stable",
    "google-chrome",
    "chromium-browser",
    "chromium",
)

_LINUX_CHROME_PATHS: Sequence[str] = (
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/snap/bin/chromium",
)

_LINUX_DRIVER_NAMES: Sequence[str] = (
    "chromedriver",
    "chromium-chromedriver",
)

_LINUX_DRIVER_PATHS: Sequence[str] = (
    "/usr/bin/chromedriver",
    "/usr/lib/chromium-browser/chromedriver",
    "/usr/lib/chromium/chromedriver",
)


def _first_executable(paths: Sequence[str]) -> Optional[str]:
    for path in paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def resolve_chrome_binary(explicit: str = "") -> Optional[str]:
    """Return a Chrome/Chromium binary path, or None if none found."""
    explicit = (explicit or "").strip()
    if explicit:
        return explicit if os.path.isfile(explicit) else explicit

    for env in ("CHROME_BINARY", "CHROMIUM_BIN", "GOOGLE_CHROME_BIN"):
        value = (os.environ.get(env) or "").strip()
        if value:
            return value

    for name in _LINUX_CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found

    return _first_executable(_LINUX_CHROME_PATHS)


def resolve_chromedriver_path(explicit: str = "") -> Optional[str]:
    """Return chromedriver path if configured or found on PATH."""
    explicit = (explicit or "").strip()
    if explicit:
        return explicit

    value = (os.environ.get("CHROMEDRIVER_PATH") or "").strip()
    if value:
        return value

    for name in _LINUX_DRIVER_NAMES:
        found = shutil.which(name)
        if found:
            return found

    return _first_executable(_LINUX_DRIVER_PATHS)


def linux_browser_setup_hint() -> str:
    machine = platform.machine().lower()
    lines: List[str] = [
        "Chrome/Chromium or chromedriver was not found or failed to start.",
        "",
        "Debian/Ubuntu (x86_64 or arm64):",
        "  sudo apt-get update",
        "  sudo apt-get install -y chromium chromium-driver",
        "  # older releases may use: chromium-browser chromium-chromedriver",
        "",
        "Then set (adjust paths if needed):",
        "  export CHROME_BINARY=/usr/bin/chromium",
        "  export CHROMEDRIVER_PATH=/usr/bin/chromedriver",
        "",
        "Verify with:",
        "  python -m reservation_notifier --check-deps",
    ]
    if machine in {"aarch64", "arm64", "armv7l"}:
        lines.extend(
            [
                "",
                "ARM/Jetson note: Selenium may not auto-download a driver.",
                "Install a chromedriver that matches your Chromium version,",
                "or set CHROMEDRIVER_PATH explicitly.",
            ]
        )
    return "\n".join(lines)


def format_browser_startup_error(exc: BaseException) -> str:
    return f"{exc}\n\n{linux_browser_setup_hint()}"


def is_linux() -> bool:
    return sys.platform.startswith("linux")
