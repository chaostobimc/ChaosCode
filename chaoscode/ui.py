"""Dependency-free, color-aware terminal UI helpers."""

from __future__ import annotations

import os
import re
import sys

_INLINE_CODE = re.compile(r"(`[^`]+`)")
_LIST_ITEM = re.compile(r"^(\s*(?:[-*+]\s+|\d+[.)]\s+))(.*)$")
_BANNER_WIDTH = 46
_BANNER = (
    "+" + "-" * _BANNER_WIDTH + "+",
    "|" + " " * _BANNER_WIDTH + "|",
    "| " + "C H A O S C O D E".center(_BANNER_WIDTH - 2) + " |",
    "| " + "Terminal coding assistant".center(_BANNER_WIDTH - 2) + " |",
    "|" + " " * _BANNER_WIDTH + "|",
    "+" + "-" * _BANNER_WIDTH + "+",
)


def use_color(stream=None) -> bool:
    if stream is None:
        stream = sys.stdout
    return (
        bool(getattr(stream, "isatty", lambda: False)())
        and "NO_COLOR" not in os.environ
        and os.environ.get("TERM", "") != "dumb"
    )


def styled(text: str, code: str, stream=None) -> str:
    if not use_color(stream):
        return text
    return f"\033[{code}m{text}\033[0m"


def print_banner() -> None:
    colors = ("36", "36", "1;35", "2;37", "36", "36")
    for line, color in zip(_BANNER, colors, strict=True):
        print(styled(line, color))


def _style_inline_code(text: str) -> str:
    parts = _INLINE_CODE.split(text)
    return "".join(styled(part, "1;33") if index % 2 else part for index, part in enumerate(parts))


def print_response(text: str, *, print_mode: bool = False) -> None:
    """Render a response with light Markdown styling, keeping pipes plain."""
    if print_mode or not use_color():
        print(text)
        return

    print(styled("ChaosCode", "1;35") + styled("  ·  Antwort", "2;37"))
    in_code_block = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_code_block = not in_code_block
            print(styled("  " + line, "2;36"))
        elif in_code_block:
            print(styled(line, "32"))
        elif re.match(r"^\s*#{1,6}\s", line):
            print(styled(line, "1;36"))
        else:
            item = _LIST_ITEM.match(line)
            if item:
                print(styled(item.group(1), "1;35") + _style_inline_code(item.group(2)))
            else:
                print(_style_inline_code(line))


def info(message: str) -> None:
    print(styled("ℹ", "36", sys.stderr) + f" {message}", file=sys.stderr)


def success(message: str) -> None:
    print(styled("✓", "32") + f" {message}")


def warning(message: str) -> None:
    print(styled("!", "33", sys.stderr) + f" {message}", file=sys.stderr)


def error(message: str) -> None:
    print(styled("✗", "31", sys.stderr) + f" {message}", file=sys.stderr)


def _print_approval_detail(detail: str) -> None:
    if not use_color(sys.stderr):
        print(detail, file=sys.stderr)
        return
    for line in detail.splitlines():
        if line.startswith(("+++", "---")):
            color = "1;37"
        elif line.startswith("+"):
            color = "32"
        elif line.startswith("-"):
            color = "31"
        elif line.startswith("@@"):
            color = "36"
        else:
            color = ""
        print(styled(line, color, sys.stderr) if color else line, file=sys.stderr)


def prompt_approval(title: str, detail: str, auto_approve: bool = False) -> bool:
    print(
        "\n" + styled("Permission required", "1;33", sys.stderr) + f": {title}",
        file=sys.stderr,
    )
    if detail:
        _print_approval_detail(detail)
    if auto_approve:
        print(styled("--yes is enabled; approving this action.", "33", sys.stderr), file=sys.stderr)
        return True
    if not sys.stdin.isatty():
        print(
            "No interactive terminal; denied. Re-run with --yes only if you trust this action.",
            file=sys.stderr,
        )
        return False
    while True:
        try:
            print(
                styled("Approve? [y/N] ", "1;33", sys.stderr),
                end="",
                file=sys.stderr,
                flush=True,
            )
            answer = input().strip().lower()
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
            indicator = styled("◌", "1;36", sys.stderr)
            print(f"\r{indicator} {self.label} …", end="", file=sys.stderr, flush=True)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self.enabled:
            print("\r\033[K", end="", file=sys.stderr, flush=True)
