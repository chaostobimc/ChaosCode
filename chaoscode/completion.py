"""Interactive @file completion for the terminal prompt."""

from __future__ import annotations

import re

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.shortcuts import CompleteStyle
from prompt_toolkit.styles import Style

from .tools import WorkspaceTools
from .ui import use_color

_ACTIVE_FILE_REFERENCE = re.compile(
    r"(?<![\w@])@(?:(?P<quote>[\"'])(?P<quoted>[^\"'\r\n]*)|"
    r"(?P<brace>\{)(?P<braced>[^{}\r\n]*)|(?P<plain>[^\s\"'{}\r\n]*))$"
)
MAX_VISIBLE_SUGGESTIONS = 40

_COLOR_STYLE = Style.from_dict(
    {
        "completion-menu": "bg:#202633 #d8dee9",
        "completion-menu.completion": "bg:#202633 #d8dee9",
        "completion-menu.completion.current": "bg:#5e81ac #ffffff bold",
        "completion-menu.meta.completion": "bg:#202633 #88c0d0",
        "completion-menu.meta.completion.current": "bg:#5e81ac #ffffff",
        "scrollbar.background": "bg:#202633",
        "scrollbar.button": "bg:#5e81ac",
    }
)
_PLAIN_STYLE = Style.from_dict(
    {
        "completion-menu": "",
        "completion-menu.completion": "",
        "completion-menu.completion.current": "reverse",
        "completion-menu.meta.completion": "",
        "completion-menu.meta.completion.current": "reverse",
        "scrollbar.background": "",
        "scrollbar.button": "reverse",
    }
)


class AtFileCompleter(Completer):
    """Suggest project files and folders only while completing an @ reference."""

    def __init__(self, tools: WorkspaceTools) -> None:
        self.tools = tools
        self.paths: tuple[str, ...] = ()
        self.refresh()

    def refresh(self) -> None:
        """Refresh visible paths before the next interactive prompt."""
        result = self.tools.list_files({"max_results": 300})
        if result.content == "No visible files found.":
            self.paths = ()
            return
        self.paths = tuple(
            sorted(
                path for path in result.content.splitlines() if path and not path.startswith("[")
            )
        )

    def get_completions(self, document: Document, complete_event: CompleteEvent):
        match = _ACTIVE_FILE_REFERENCE.search(document.text_before_cursor)
        if match is None:
            return

        opening = match.group("quote") or match.group("brace") or ""
        prefix = match.group("quoted")
        if prefix is None:
            prefix = match.group("braced")
        if prefix is None:
            prefix = match.group("plain") or ""

        matches = [path for path in self.paths if path.casefold().startswith(prefix.casefold())][
            :MAX_VISIBLE_SUGGESTIONS
        ]
        for path in matches:
            is_directory = path.endswith("/")
            if opening:
                text = path
                if not is_directory:
                    text += "}" if opening == "{" else opening
            elif " " in path or "\t" in path:
                text = f'"{path}' if is_directory else f'"{path}"'
            else:
                text = path
            yield Completion(
                text=text,
                start_position=-len(prefix),
                display=path,
                display_meta="Ordner" if is_directory else "Datei",
            )


def _completion_key_bindings() -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add(Keys.ControlI)
    def complete_current_file_reference(event) -> None:
        buffer = event.current_buffer
        if buffer.complete_state:
            buffer.complete_next()
        else:
            buffer.start_completion(select_first=True)

    return bindings


class AtFilePrompt:
    """Prompt Toolkit session with a colored prompt and @path suggestions."""

    def __init__(self, tools: WorkspaceTools) -> None:
        self.completer = AtFileCompleter(tools)
        self.session = PromptSession(
            completer=self.completer,
            complete_while_typing=True,
            complete_style=CompleteStyle.MULTI_COLUMN,
            reserve_space_for_menu=5,
            history=InMemoryHistory(),
            key_bindings=_completion_key_bindings(),
            style=_COLOR_STYLE if use_color() else _PLAIN_STYLE,
        )

    def prompt(self) -> str:
        self.completer.refresh()
        if use_color():
            message = HTML("<style fg='ansigreen' bold>du</style> <style fg='ansicyan'>❯</style> ")
        else:
            message = "du > "
        return self.session.prompt(message)
