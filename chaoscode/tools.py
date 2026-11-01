"""Workspace-scoped tools with explicit approval for mutations and commands."""

from __future__ import annotations

import difflib
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".cache",
    ".tox",
    ".parcel-cache",
    ".output",
    ".nuxt",
    ".svelte-kit",
    "dist",
    "build",
    "coverage",
    "out",
    "target",
    ".next",
    ".turbo",
}
SENSITIVE_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    ".gitconfig",
    "credentials",
    "credentials.json",
    "secrets.json",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}
SENSITIVE_DIRS = {".git", ".ssh", ".gnupg", ".aws", ".azure", ".kube"}
SAFE_ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore"}
MAX_READ_BYTES = 200_000
MAX_WRITE_BYTES = 1_000_000
MAX_TOOL_OUTPUT = 12_000


class ToolError(RuntimeError):
    """Raised when a tool request is invalid or unsafe."""


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


ApprovalCallback = Callable[[str, str], bool]


def _clip(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [output clipped at {limit} characters]"


def _is_sensitive(path: Path) -> bool:
    for part in path.parts:
        lowered = part.lower()
        if lowered in SENSITIVE_DIRS or lowered in SENSITIVE_NAMES:
            return True
        if lowered in SAFE_ENV_TEMPLATES:
            continue
        if lowered.startswith(".env.") or lowered.endswith(tuple(SENSITIVE_SUFFIXES)):
            return True
    return False


def _diff_preview(path: str, old: str, new: str) -> str:
    lines = list(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )
    if not lines:
        return "(no content changes)"
    preview = "\n".join(lines[:80])
    if len(lines) > 80:
        preview += f"\n… [{len(lines) - 80} more diff lines]"
    return _clip(preview, 6_000)


class WorkspaceTools:
    def __init__(self, root: Path) -> None:
        try:
            self.root = root.expanduser().resolve(strict=True)
        except OSError as exc:
            raise ToolError(f"Workspace does not exist: {root}") from exc
        if not self.root.is_dir():
            raise ToolError(f"Workspace is not a directory: {self.root}")

    def _resolve(self, value: Any, *, must_exist: bool) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ToolError("A non-empty project-relative path is required.")
        raw = Path(value).expanduser()
        if raw.is_absolute():
            candidate = raw
        else:
            candidate = self.root / raw
        try:
            resolved = candidate.resolve(strict=must_exist)
        except (OSError, RuntimeError) as exc:
            raise ToolError(f"Path cannot be resolved: {value}: {exc}") from exc
        try:
            relative = resolved.relative_to(self.root)
        except ValueError as exc:
            raise ToolError("Path is outside the project root.") from exc
        if _is_sensitive(relative):
            raise ToolError(
                "Access denied: paths inside secret or repository-internal folders are blocked."
            )
        return resolved

    def _relative(self, path: Path) -> str:
        return "." if path == self.root else path.relative_to(self.root).as_posix()

    def _read_text(self, path: Path) -> str:
        if _is_sensitive(path.relative_to(self.root)):
            raise ToolError("Access denied: this looks like a secret or credential file.")
        if path.is_dir():
            raise ToolError("Expected a file, but the path is a directory.")
        if path.stat().st_size > MAX_READ_BYTES:
            raise ToolError(
                f"File is larger than {MAX_READ_BYTES} bytes; use a narrower/smaller file."
            )
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(
                "File is not UTF-8 text; binary files cannot be read by this tool."
            ) from exc

    def read_selected_file(self, value: str) -> tuple[str, str]:
        """Read a selected project-relative file for an @path prompt reference."""
        if not isinstance(value, str) or not value.strip():
            raise ToolError("A non-empty project-relative file path is required.")
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or value.startswith("\\\\") or re.match(r"^[A-Za-z]:", value):
            raise ToolError("@ file references must use paths relative to the project root.")
        path = self._resolve(normalized, must_exist=True)
        if not path.is_file():
            raise ToolError("The selected path is not a file.")
        return self._relative(path), self._read_text(path)

    def list_files(self, arguments: dict[str, Any]) -> ToolResult:
        base = self._resolve(arguments.get("path", "."), must_exist=True)
        if not base.is_dir():
            raise ToolError("The requested path is not a directory.")
        try:
            limit = int(arguments.get("max_results", 200))
        except (TypeError, ValueError) as exc:
            raise ToolError("max_results must be an integer.") from exc
        limit = max(1, min(limit, 300))
        items: list[str] = []
        for current, dirs, files in os.walk(base, followlinks=False):
            current_path = Path(current)
            dirs[:] = sorted(
                name
                for name in dirs
                if name not in IGNORED_DIRS
                and not _is_sensitive((current_path / name).relative_to(self.root))
                and not (current_path / name).is_symlink()
            )
            for name in sorted(dirs):
                items.append(self._relative(current_path / name) + "/")
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break
            for name in sorted(files):
                path = current_path / name
                if path.is_symlink() or _is_sensitive(path.relative_to(self.root)):
                    continue
                items.append(self._relative(path))
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break
        if not items:
            return ToolResult("No visible files found.")
        suffix = "\n[results limited]" if len(items) >= limit else ""
        return ToolResult("\n".join(items) + suffix)

    def read_file(self, arguments: dict[str, Any]) -> ToolResult:
        path = self._resolve(arguments.get("path"), must_exist=True)
        text = self._read_text(path)
        lines = text.splitlines()
        try:
            start = int(arguments.get("start_line", 1))
            end = int(arguments.get("end_line", len(lines)))
        except (TypeError, ValueError) as exc:
            raise ToolError("start_line and end_line must be integers.") from exc
        start = max(1, start)
        end = max(start, min(end, len(lines)))
        selected = lines[start - 1 : end]
        shown = "\n".join(f"{number:>5} | {line}" for number, line in enumerate(selected, start))
        if start > len(lines):
            shown = f"File has {len(lines)} lines; requested start_line={start}."
        if end < len(lines):
            shown += f"\n… [showing lines {start}-{end} of {len(lines)}]"
        return ToolResult(_clip(shown))

    def search_files(self, arguments: dict[str, Any]) -> ToolResult:
        query = arguments.get("query")
        if not isinstance(query, str) or not query:
            raise ToolError("query must be a non-empty string.")
        try:
            limit = int(arguments.get("max_results", 50))
        except (TypeError, ValueError) as exc:
            raise ToolError("max_results must be an integer.") from exc
        limit = max(1, min(limit, 100))
        regex_enabled = bool(arguments.get("regex", False))
        try:
            pattern = re.compile(query if regex_enabled else re.escape(query))
        except re.error as exc:
            raise ToolError(f"Invalid regular expression: {exc}") from exc
        base = self._resolve(arguments.get("path", "."), must_exist=True)
        files: list[Path] = []
        if base.is_file():
            files = [base]
        elif base.is_dir():
            for current, dirs, names in os.walk(base, followlinks=False):
                current_path = Path(current)
                dirs[:] = sorted(
                    name
                    for name in dirs
                    if name not in IGNORED_DIRS
                    and not _is_sensitive((current_path / name).relative_to(self.root))
                    and not (current_path / name).is_symlink()
                )
                files.extend(
                    current_path / name
                    for name in sorted(names)
                    if not (current_path / name).is_symlink()
                    and not _is_sensitive((current_path / name).relative_to(self.root))
                )
        else:
            raise ToolError("The search path must be a file or directory.")
        matches: list[str] = []
        for path in files:
            try:
                if path.stat().st_size > MAX_READ_BYTES:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    matches.append(f"{self._relative(path)}:{line_no}: {line.strip()}")
                    if len(matches) >= limit:
                        return ToolResult("\n".join(matches) + "\n[results limited]")
        return ToolResult("\n".join(matches) if matches else "No matches found.")

    def _approve(self, approve: ApprovalCallback, title: str, detail: str) -> bool:
        try:
            return bool(approve(title, detail))
        except (EOFError, KeyboardInterrupt):
            return False

    def write_file(self, arguments: dict[str, Any], approve: ApprovalCallback) -> ToolResult:
        relative = arguments.get("path")
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ToolError("content must be a string.")
        if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
            raise ToolError(f"Refusing to write more than {MAX_WRITE_BYTES} bytes at once.")
        path = self._resolve(relative, must_exist=False)
        if path.exists() and path.is_dir():
            raise ToolError("Cannot write a file over a directory.")
        if _is_sensitive(path.relative_to(self.root)):
            raise ToolError("Writes to files that look like secrets are blocked.")
        old = ""
        if path.exists():
            old = self._read_text(path)
        rel = self._relative(path)
        detail = _diff_preview(rel, old, content)
        if not self._approve(approve, f"write file {rel}", detail):
            return ToolResult("User denied this file write.", is_error=True)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="")
        except OSError as exc:
            raise ToolError(f"Could not write {rel}: {exc}") from exc
        return ToolResult(f"Wrote {rel} ({len(content.encode('utf-8'))} bytes).")

    def edit_file(self, arguments: dict[str, Any], approve: ApprovalCallback) -> ToolResult:
        old_text = arguments.get("old_text")
        new_text = arguments.get("new_text")
        if not isinstance(old_text, str) or not old_text:
            raise ToolError("old_text must be a non-empty string.")
        if not isinstance(new_text, str):
            raise ToolError("new_text must be a string.")
        path = self._resolve(arguments.get("path"), must_exist=True)
        if _is_sensitive(path.relative_to(self.root)):
            raise ToolError("Writes to files that look like secrets are blocked.")
        before = self._read_text(path)
        occurrences = before.count(old_text)
        if occurrences == 0:
            raise ToolError("The exact old_text was not found; re-read the file and retry.")
        if occurrences > 1:
            raise ToolError(
                f"old_text matches {occurrences} locations; include more surrounding context."
            )
        after = before.replace(old_text, new_text, 1)
        rel = self._relative(path)
        if len(after.encode("utf-8")) > MAX_WRITE_BYTES:
            raise ToolError(f"Refusing to write more than {MAX_WRITE_BYTES} bytes at once.")
        if not self._approve(approve, f"edit file {rel}", _diff_preview(rel, before, after)):
            return ToolResult("User denied this file edit.", is_error=True)
        try:
            path.write_text(after, encoding="utf-8", newline="")
        except OSError as exc:
            raise ToolError(f"Could not edit {rel}: {exc}") from exc
        return ToolResult(f"Updated {rel}.")

    def run_command(self, arguments: dict[str, Any], approve: ApprovalCallback) -> ToolResult:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ToolError("command must be a non-empty string.")
        working_dir = self._resolve(arguments.get("cwd", "."), must_exist=True)
        if not working_dir.is_dir():
            raise ToolError("The command working directory must be a directory.")
        try:
            timeout = int(arguments.get("timeout", 120))
        except (TypeError, ValueError) as exc:
            raise ToolError("timeout must be an integer.") from exc
        timeout = max(1, min(timeout, 180))
        detail = (
            f"Working directory: {self._relative(working_dir)}\nTimeout: {timeout}s\n\n{command}"
        )
        if not self._approve(approve, "run shell command", detail):
            return ToolResult("User denied this command.", is_error=True)
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=working_dir,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", "replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", "replace")
            return ToolResult(
                _clip(f"Command timed out after {timeout}s.\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"),
                is_error=True,
            )
        except OSError as exc:
            raise ToolError(f"Command could not be started: {exc}") from exc
        stdout = completed.stdout.strip() or "(no stdout)"
        stderr = completed.stderr.strip()
        output = f"Exit code: {completed.returncode}\nSTDOUT:\n{stdout}"
        if stderr:
            output += f"\nSTDERR:\n{stderr}"
        return ToolResult(_clip(output), is_error=completed.returncode != 0)

    def execute(
        self, name: str, arguments: dict[str, Any], approve: ApprovalCallback
    ) -> ToolResult:
        if not isinstance(arguments, dict):
            return ToolResult("Tool arguments must be a JSON object.", is_error=True)
        try:
            if name == "list_files":
                return self.list_files(arguments)
            if name == "read_file":
                return self.read_file(arguments)
            if name == "search_files":
                return self.search_files(arguments)
            if name == "write_file":
                return self.write_file(arguments, approve)
            if name == "edit_file":
                return self.edit_file(arguments, approve)
            if name == "run_command":
                return self.run_command(arguments, approve)
            return ToolResult(f"Unknown tool: {name}", is_error=True)
        except (ToolError, OSError, ValueError) as exc:
            return ToolResult(f"Tool error: {exc}", is_error=True)
