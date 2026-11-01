"""Optional console entry point for launching the desktop UI."""

from __future__ import annotations

import importlib.util
import sys


def main() -> int:
    if importlib.util.find_spec("PySide6") is None:
        print(
            "Die Desktop-Oberfläche benötigt das optionale Extra: "
            '`pip install "chaoscode-cli[desktop]"`.',
            file=sys.stderr,
        )
        return 2
    try:
        from .desktop import main as desktop_main
    except (ImportError, OSError) as exc:
        print(f"Die Desktop-Oberfläche konnte nicht geladen werden: {exc}", file=sys.stderr)
        return 2
    return desktop_main()
