from __future__ import annotations

import os
import sys
import sysconfig


def prepare_tk_environment() -> None:
    """Must run before ``import tkinter`` (macOS deprecation + framework hint)."""
    os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")


def macos_framework_python_hint() -> str | None:
    """
    On macOS, CPython must be a *framework* build for Tk windows to appear when
    launched from a terminal. Homebrew ``python@3.x`` in a venv often is not.
    """
    if sys.platform != "darwin":
        return None
    if sysconfig.get_config_var("PYTHONFRAMEWORK"):
        return None
    return (
        "This Python is not a macOS framework build, so Tk windows may not appear.\n\n"
        "Fix options:\n"
        "  • Install Python from https://www.python.org/downloads/ and recreate the venv, or\n"
        "  • brew install python-tk@3.12 && recreate the venv with that interpreter.\n\n"
        "You can still use: python -m reservation_notifier --interactive"
    )
