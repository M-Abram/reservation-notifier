from __future__ import annotations

import logging
import os
from typing import IO, Optional


class TtyColorFormatter(logging.Formatter):
    """Adds readable ANSI highlights for a few important log lines."""

    def __init__(
        self,
        *,
        fmt: Optional[str] = None,
        datefmt: Optional[str] = None,
        stream: Optional[IO[str]] = None,
    ) -> None:
        super().__init__(fmt=fmt or "%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt=datefmt)
        self._stream: IO[str] = stream or getattr(self, "_default_stream", None)  # patched in install_*.

    def bind_stream(self, stream: IO[str]) -> None:
        self._stream = stream

    def _colors_enabled(self) -> bool:
        if os.environ.get("NO_COLOR") is not None:
            return False
        try:
            return bool(self._stream.isatty())
        except Exception:
            return False

    # Note: logging.Formatter already uses attribute name ``_style`` for PercentStyle; never shadow it.

    @staticmethod
    def _ansi(text: str, *codes: int) -> str:
        return f"\033[{';'.join(map(str, codes))}m{text}\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        ct = self.formatTime(record, self.datefmt)
        level = record.levelname
        name = record.name
        msg = record.getMessage()

        if not self._colors_enabled():
            return f"{ct} {level} {name}: {msg}"

        dim = lambda t: self._ansi(t, 2)  # noqa: E731
        bold_cyan_msg = self._ansi(msg, 1, 36)
        bold_yellow_msg = self._ansi(msg, 1, 33)
        red_msg = self._ansi(msg, 1, 31)

        if "Selenium poll:" in msg:
            c_msg = bold_cyan_msg
        elif msg.startswith("No matching slots"):
            c_msg = bold_yellow_msg
        elif record.levelname == "WARNING":
            c_msg = self._ansi(msg, 1, 33)
        elif record.levelname == "ERROR":
            c_msg = red_msg
        else:
            c_msg = msg

        lvl = {
            "DEBUG": lambda t: self._ansi(t, 2, 37),
            "INFO": lambda t: self._ansi(t, 36),
            "WARNING": lambda t: self._ansi(t, 33),
            "ERROR": lambda t: self._ansi(t, 31),
            "CRITICAL": lambda t: self._ansi(t, 1, 31),
        }.get(level, lambda t: t)(level)

        return f"{dim(ct)} {lvl} {dim(name)}: {c_msg}"


def install_colored_stderr_logging(level: int = logging.INFO) -> None:
    """Replace root handlers with one colored stderr handler (best-effort)."""
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)

    handler = logging.StreamHandler()
    formatter = TtyColorFormatter(datefmt="%Y-%m-%dT%H:%M:%S%z")
    formatter.bind_stream(handler.stream)
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(level)
