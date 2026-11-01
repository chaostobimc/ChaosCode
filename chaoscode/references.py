"""Resolve explicit @path file references in user prompts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .tools import ToolError, WorkspaceTools

_FILE_REFERENCE = re.compile(
    r"(?<![\w@])@(?:\{(?P<braced>[^{}\r\n]+)\}|"
    r'"(?P<double>[^"\r\n]+)"|'
    r"'(?P<single>[^'\r\n]+)'|"
    r"(?P<plain>[^\s,;!?()\[\]{}<>\"']+))"
)
_FILE_SUFFIXES = {
    ".py",
    ".txt",
    ".md",
    ".rst",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".js",
    ".ts",
    ".html",
    ".css",
}
MAX_SELECTED_FILES = 4
MAX_FILE_CONTEXT_CHARS = 8_000


@dataclass(frozen=True)
class FileAttachment:
    """A safely-read project file selected in a prompt with @path."""

    path: str
    content: str


def _looks_like_path(value: str) -> bool:
    path = Path(value)
    return (
        "/" in value
        or "\\" in value
        or value.startswith(".")
        or path.suffix.lower() in _FILE_SUFFIXES
    )


def select_file_references(prompt: str, tools: WorkspaceTools) -> list[FileAttachment]:
    """Read existing project files explicitly mentioned with @path.

    Paths with spaces can be written as @"folder/file name.py" or
    @{folder/file name.py}. Plain @mentions are only treated as file paths
    when the target exists or looks path-like, so Python decorators and email
    addresses remain ordinary prompt text.
    """
    attachments: list[FileAttachment] = []
    seen: set[str] = set()

    for match in _FILE_REFERENCE.finditer(prompt):
        raw_path = match.group("braced") or match.group("double") or match.group("single")
        explicit = raw_path is not None
        if raw_path is None:
            raw_path = match.group("plain")
        if raw_path is None:
            continue

        path = raw_path.strip()
        if not explicit:
            path = path.rstrip(".,:")
            # Dotted Python decorators such as @app.route("/") are not files.
            if re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", path) and prompt[
                match.end() :
            ].lstrip().startswith("("):
                continue
        if not path:
            continue

        try:
            relative, content = tools.read_selected_file(path)
        except ToolError as exc:
            if explicit or _looks_like_path(path):
                raise ValueError(
                    f"Die Datei @{path} kann nicht als Projektdatei geladen werden: {exc}"
                ) from exc
            # Leave ordinary @words (for example @dataclass) untouched.
            continue

        if relative in seen:
            continue
        if len(attachments) >= MAX_SELECTED_FILES:
            raise ValueError(
                f"Pro Aufgabe können höchstens {MAX_SELECTED_FILES} Dateien mit @ ausgewählt werden."
            )
        seen.add(relative)
        if len(content) > MAX_FILE_CONTEXT_CHARS:
            content = (
                content[:MAX_FILE_CONTEXT_CHARS]
                + f"\n\n[… Datei-Kontext bei {MAX_FILE_CONTEXT_CHARS} Zeichen gekürzt; "
                "bei Bedarf die restlichen Zeilen mit read_file laden.]"
            )
        attachments.append(FileAttachment(path=relative, content=content))

    return attachments
