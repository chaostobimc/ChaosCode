"""Small dependency-free terminal UI helpers."""

from __future__ import annotations

import os
import sys


def use_color(stream=sys.stdout) -> bool:
    return bool(getattr(stream, "isatty", lambda: False)()) and os.environ.get("NO_COLOR") is None


def styled(text: str, code: str, stream=sys.stdout) -> str:
    if not use_color(stream):
        return text
    return f"\033[{code}m{text}\033[0m"


def info(message: str) -> None:
    print(styled("ℹ", "36") + f" {message}", file=sys.stderr)


def success(message: str) -> None:
    print(styled("✓", "32") + f" {message}")


def warning(message: str) -> None:
    print(styled("!", "33") + f" {message}", file=sys.stderr)


def error(message: str) -> None:
    print(styled("✗", "31") + f" {message}", file=sys.stderr)


def prompt_approval(title: str, detail: str, auto_approve: bool = False) -> bool:
    print(f"\nPermission required: {title}", file=sys.stderr)
    if detail:
        print(detail, file=sys.stderr)
    if auto_approve:
        print("--yes is enabled; approving this action.", file=sys.stderr)
        return True
    if not sys.stdin.isatty():
        print(
            "No interactive terminal; denied. Re-run with --yes only if you trust this action.",
            file=sys.stderr,
        )
        return False
    while True:
        try:
            answer = input("Approve? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nDenied.", file=sys.stderr)
            return False
        if answer in {"y", "yes", "j", "ja"}:
            return True
        if answer in {"", "n", "no", "nein"}:
            return False
        print("Please answer y or n.", file=sys.stderr)


class ThinkingIndicator:
    """A minimal, TTY-only indicator for network/model latency."""

    def __init__(self, label: str = "DeepSeek arbeitet") -> None:
        self.label = label
        self.enabled = bool(getattr(sys.stderr, "isatty", lambda: False)())

    def __enter__(self):
        if self.enabled:
            print(f"\r{self.label} …", end="", file=sys.stderr, flush=True)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self.enabled:
            print("\r\033[K", end="", file=sys.stderr, flush=True)
