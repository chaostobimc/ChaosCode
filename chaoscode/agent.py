"""ReAct-style agent loop for DeepSeek4Free and the guarded workspace tools."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .prompts import (
    TOOL_TAG_CLOSE,
    TOOL_TAG_OPEN,
    build_system_prompt,
    format_conversation,
)
from .sessions import Session, SessionStore
from .tools import ApprovalCallback, WorkspaceTools

MAX_PROJECT_INSTRUCTIONS = 20_000


@dataclass
class ParsedToolCall:
    name: str
    arguments: dict[str, Any]
    raw: str


class AgentError(RuntimeError):
    """Raised when the agent cannot complete its tool loop."""


def extract_tool_call(text: str) -> tuple[ParsedToolCall | None, str | None]:
    """Parse one ChaosCode call, tolerating common Markdown wrappers.

    Some models place the requested block in a JSON/XML code fence or add a
    short preamble. Accept those formats, but only execute a single tagged
    block whose tags are on their own lines.
    """
    normalized = text.strip()
    if normalized.startswith("```") and normalized.endswith("```"):
        lines = normalized.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            normalized = "\n".join(lines[1:-1]).strip()
    if TOOL_TAG_OPEN not in normalized and TOOL_TAG_CLOSE not in normalized:
        return None, None
    if normalized.count(TOOL_TAG_OPEN) != 1 or normalized.count(TOOL_TAG_CLOSE) != 1:
        return None, "Return exactly one complete ChaosCode tool block, or a normal final answer."

    open_at = normalized.index(TOOL_TAG_OPEN)
    close_at = normalized.index(TOOL_TAG_CLOSE)
    start = open_at + len(TOOL_TAG_OPEN)
    if close_at < start:
        return None, "The tool block closing tag appeared before its opening tag."

    open_line_start = normalized.rfind("\n", 0, open_at) + 1
    close_tag_end = close_at + len(TOOL_TAG_CLOSE)
    close_line_end = normalized.find("\n", close_tag_end)
    if close_line_end < 0:
        close_line_end = len(normalized)
    is_entire_response = not normalized[:open_at].strip() and not normalized[close_tag_end:].strip()
    tags_are_line_delimited = (
        not normalized[open_line_start:open_at].strip()
        and not normalized[close_tag_end:close_line_end].strip()
    )
    if not is_entire_response and not tags_are_line_delimited:
        return (
            None,
            "Put the tool block on its own lines; do not inline it in prose or a code sample.",
        )

    raw = normalized[start:close_at].strip()
    try:
        request = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, (
            f"Tool arguments are not valid JSON ({exc.msg} at character {exc.pos}). "
            "Use quoted strings, no comments or ellipses, and include an arguments object."
        )
    if not isinstance(request, dict):
        return None, "The tool request must be a JSON object."
    name = request.get("name")
    arguments = request.get("arguments")
    if not isinstance(name, str) or not name:
        return None, 'The tool request needs a non-empty "name" string.'
    if not isinstance(arguments, dict):
        return None, 'The tool request "arguments" must be a JSON object.'
    return ParsedToolCall(name=name, arguments=arguments, raw=raw), None


def load_project_instructions(root: Path) -> str:
    """Read conventional root-level agent instructions without following escapes."""
    sections: list[str] = []
    used = 0
    for filename in ("CHAOSCODE.md", "AGENTS.md", "CLAUDE.md"):
        path = root / filename
        if not path.is_file():
            continue
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root.resolve())
            if resolved.stat().st_size > MAX_PROJECT_INSTRUCTIONS:
                continue
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError, RuntimeError):
            continue
        available = MAX_PROJECT_INSTRUCTIONS - used
        if available <= 0:
            break
        content = content[:available]
        sections.append(f"\n### {filename}\n{content}")
        used += len(content)
    return "".join(sections)


@dataclass
class TurnResult:
    text: str
    tool_calls: int


class CodingAgent:
    def __init__(
        self,
        *,
        workspace: Path,
        provider: Any,
        session: Session,
        session_store: SessionStore | None,
        approve: ApprovalCallback,
        search: bool = False,
        max_steps: int = 12,
    ) -> None:
        self.workspace = workspace.resolve()
        self.provider = provider
        self.session = session
        self.session_store = session_store
        self.approve = approve
        self.search = search
        self.max_steps = max(1, min(max_steps, 30))
        self.tools = WorkspaceTools(self.workspace)
        self.system_prompt = build_system_prompt(
            self.workspace, load_project_instructions(self.workspace)
        )

    def _save(self) -> None:
        if self.session_store is not None:
            self.session_store.save(self.session)

    def _append(self, role: str, content: str, name: str | None = None) -> None:
        item = {"role": role, "content": content}
        if name:
            item["name"] = name
        self.session.messages.append(item)
        # Keep session files and future prompts bounded while preserving recent turns.
        if len(self.session.messages) > 100:
            del self.session.messages[: len(self.session.messages) - 100]
        self._save()

    def run_turn(self, user_prompt: str) -> TurnResult:
        request = user_prompt.strip()
        if not request:
            raise ValueError("Bitte gib eine nicht-leere Aufgabe ein.")
        self._append("user", request)
        tool_calls = 0
        protocol_failures = 0

        for step in range(self.max_steps):
            model_prompt = self.system_prompt + "\n\n" + format_conversation(self.session.messages)
            response = self.provider.complete(model_prompt, thinking=True, search=self.search)
            if not isinstance(response, str) or not response.strip():
                raise AgentError("Das Modell hat eine leere Antwort zurückgegeben.")

            call, protocol_error = extract_tool_call(response)
            if protocol_error:
                protocol_failures += 1
                self._append("assistant", "[Tool request not executed: invalid tool-call format.]")
                example = (
                    f"{TOOL_TAG_OPEN}\n"
                    '{"name":"read_file","arguments":{"path":"src/main.py"}}\n'
                    f"{TOOL_TAG_CLOSE}"
                )
                feedback = (
                    f"Tool-Protokollfehler: {protocol_error}\n"
                    "No tool was executed. Correct the request and return one valid block "
                    "with a real tool name and JSON arguments (no comments, ellipses, or Markdown).\n"
                    f"Example:\n{example}"
                )
                self._append("tool", feedback, "protocol")
                if protocol_failures >= 2:
                    message = (
                        "Der Tool-Aufruf war auch nach einer Formatkorrektur nicht lesbar. "
                        "Diese letzte Aktion wurde nicht ausgeführt; bitte wiederhole die Aufgabe."
                    )
                    self._append("assistant", message)
                    return TurnResult(text=message, tool_calls=tool_calls)
                continue
            if call is None:
                self._append("assistant", response.strip())
                return TurnResult(text=response.strip(), tool_calls=tool_calls)

            protocol_failures = 0
            # A structured tool request is retained as data, not executed as prose.
            call_record = json.dumps(
                {"name": call.name, "arguments": call.arguments},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            self._append("assistant", f"{TOOL_TAG_OPEN}{call_record}{TOOL_TAG_CLOSE}")
            tool_calls += 1
            result = self.tools.execute(call.name, call.arguments, self.approve)
            self._append("tool", result.content, call.name)

        last_message = (
            f"Ich habe das Limit von {self.max_steps} Modellschritten erreicht. "
            "Bitte gib mir eine kurze Folgeaufgabe oder erhöhe das Limit."
        )
        self._append("assistant", last_message)
        return TurnResult(text=last_message, tool_calls=tool_calls)
