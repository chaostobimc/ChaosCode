"""Modern desktop chat interface for ChaosCode."""

from __future__ import annotations

import argparse
import copy
import html
import importlib.metadata
import importlib.util
import logging
import re
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, QStringListModel, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QCompleter,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTextBrowser,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .agent import CodingAgent, extract_tool_call
from .config import (
    ConfigError,
    get_auth_token,
    get_preference,
    remove_saved_auth_token,
    save_auth_token,
    set_preference,
)
from .provider import DeepSeek4FreeClient
from .references import select_file_references
from .sessions import Session, SessionError, SessionStore
from .tools import ToolError, WorkspaceTools

LOGGER = logging.getLogger(__name__)

APP_STYLE = """
QWidget {
    background: #101217;
    color: #e7eaf0;
    font-family: "Segoe UI";
    font-size: 10pt;
}
QMainWindow, QDialog { background: #101217; }
QFrame#Sidebar { background: #0c0e12; border-right: 1px solid #242832; }
QFrame#TopBar { background: #101217; border-bottom: 1px solid #242832; }
QFrame#ComposerPanel { background: #101217; border-top: 1px solid #242832; }
QFrame#MessageAssistant { background: #171a21; border: 1px solid #272c36; border-radius: 16px; }
QFrame#MessageUser { background: #1d2635; border: 1px solid #2d3b51; border-radius: 16px; }
QFrame#ActivityCard { background: #15181e; border: 1px solid #2a2e37; border-radius: 10px; }
QPushButton, QToolButton {
    background: #1b1e26; border: 1px solid #2b303b; border-radius: 9px;
    padding: 8px 12px; color: #e7eaf0;
}
QPushButton:hover, QToolButton:hover { background: #252a34; border-color: #414958; }
QPushButton:pressed, QToolButton:pressed { background: #303744; }
QPushButton#PrimaryButton {
    background: #7768f7; border: 1px solid #8a7eff; color: #ffffff; font-weight: 600;
}
QPushButton#PrimaryButton:hover { background: #887bff; }
QPushButton#QuietButton { background: transparent; border-color: transparent; }
QLineEdit, QPlainTextEdit, QSpinBox {
    background: #171a21; border: 1px solid #303541; border-radius: 9px;
    padding: 8px 10px; selection-background-color: #6659c9;
}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus { border-color: #7768f7; }
QPlainTextEdit#Composer { border-radius: 14px; padding: 12px 14px; font-size: 11pt; }
QTreeWidget { background: transparent; border: none; outline: none; }
QTreeWidget::item { padding: 7px 6px; border-radius: 7px; }
QTreeWidget::item:hover { background: #191d25; }
QTreeWidget::item:selected { background: #24293a; color: #ffffff; }
QScrollArea { background: transparent; border: none; }
QTextBrowser { background: transparent; border: none; color: #e7eaf0; }
QCheckBox { spacing: 7px; color: #c6cbd4; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #444b59; border-radius: 4px; background: #171a21; }
QCheckBox::indicator:checked { background: #7768f7; border-color: #8a7eff; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #353a45; min-height: 24px; border-radius: 4px; }
QScrollBar::handle:vertical:hover { background: #4a5060; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QMenu { background: #1a1d25; border: 1px solid #343a46; padding: 5px; }
QMenu::item { padding: 7px 22px 7px 12px; border-radius: 5px; }
QMenu::item:selected { background: #30364a; }
QCompleter QAbstractItemView { background: #1a1e27; border: 1px solid #3a4050; selection-background-color: #5146a5; padding: 5px; }
QStatusBar { background: #0c0e12; color: #9ba3b2; border-top: 1px solid #242832; }
"""

ACTIVE_REFERENCE = re.compile(
    r"(?<![\w@])@(?:(?P<quote>[\"'])(?P<quoted>[^\"'\r\n]*)|"
    r"(?P<brace>\{)(?P<braced>[^{}\r\n]*)|(?P<plain>[^\s\"'{}\r\n]*))$"
)
SESSION_ROLE = int(Qt.ItemDataRole.UserRole)
TEMP_ROLE = SESSION_ROLE + 1
WORKSPACE_ROLE = SESSION_ROLE + 2


@dataclass
class ApprovalRequest:
    title: str
    detail: str
    accepted: bool = False
    answered: threading.Event = field(default_factory=threading.Event)


class TurnWorker(QThread):
    """Run a coding-agent turn outside the GUI thread."""

    approval_requested = Signal(object)
    completed = Signal(object, object)
    failed = Signal(str, object)

    def __init__(
        self,
        *,
        token: str,
        workspace: Path,
        session: Session,
        session_store: SessionStore | None,
        prompt: str,
        selected_files: list[tuple[str, str]],
        search: bool,
        max_steps: int,
        auto_approve: bool,
    ) -> None:
        super().__init__()
        self.token = token
        self.workspace = workspace
        self.session = session
        self.session_store = session_store
        self.prompt_text = prompt
        self.selected_files = selected_files
        self.search = search
        self.max_steps = max_steps
        self.auto_approve = auto_approve

    def _approve(self, title: str, detail: str) -> bool:
        if self.auto_approve:
            return True
        request = ApprovalRequest(title=title, detail=detail)
        self.approval_requested.emit(request)
        if not request.answered.wait(timeout=600):
            return False
        return request.accepted

    def run(self) -> None:
        provider: DeepSeek4FreeClient | None = None
        try:
            provider = DeepSeek4FreeClient(self.token)
            agent = CodingAgent(
                workspace=self.workspace,
                provider=provider,
                session=self.session,
                session_store=self.session_store,
                approve=self._approve,
                search=self.search,
                max_steps=self.max_steps,
            )
            result = agent.run_turn(self.prompt_text, selected_files=self.selected_files)
            self.completed.emit(result, copy.deepcopy(self.session.messages))
        except Exception as exc:  # noqa: BLE001 - show provider/tool errors in the app
            self.failed.emit(str(exc), copy.deepcopy(self.session.messages))
        finally:
            if provider is not None:
                try:
                    provider.close()
                except Exception:  # noqa: BLE001 - cleanup must not strand the UI worker
                    LOGGER.debug("Provider cleanup failed.")


class DiffHighlighter(QSyntaxHighlighter):
    def highlightBlock(self, text: str) -> None:
        fmt = QTextCharFormat()
        if text.startswith(("+++", "---")):
            fmt.setForeground(QColor("#cbd3df"))
            fmt.setFontWeight(QFont.Weight.Bold)
        elif text.startswith("+"):
            fmt.setForeground(QColor("#72d6a4"))
        elif text.startswith("-"):
            fmt.setForeground(QColor("#ff7b85"))
        elif text.startswith("@@"):
            fmt.setForeground(QColor("#76c7ff"))
        if fmt.foreground().style() != Qt.BrushStyle.NoBrush:
            self.setFormat(0, len(text), fmt)


def _safe_markdown(text: str) -> str:
    """Render a small safe Markdown subset; all source HTML is escaped."""
    output: list[str] = []
    in_code = False
    in_list = False

    def inline(value: str) -> str:
        value = html.escape(value)
        value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
        value = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", value)
        return value

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            output.append("</ul>")
            in_list = False

    for line in text.splitlines():
        if line.strip().startswith("```"):
            close_list()
            if in_code:
                output.append("</code></pre>")
                in_code = False
            else:
                output.append("<pre><code>")
                in_code = True
            continue
        if in_code:
            output.append(html.escape(line) + "\n")
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            close_list()
            level = min(len(heading.group(1)) + 1, 6)
            output.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            continue
        bullet = re.match(r"^\s*[-*+]\s+(.*)$", line)
        if bullet:
            if not in_list:
                output.append("<ul>")
                in_list = True
            output.append(f"<li>{inline(bullet.group(1))}</li>")
            continue
        close_list()
        if line.strip():
            output.append(f"<p>{inline(line)}</p>")
        else:
            output.append("<br>")
    close_list()
    if in_code:
        output.append("</code></pre>")
    return "<html><body>" + "".join(output) + "</body></html>"


class MessageBubble(QFrame):
    def __init__(self, role: str, content: str) -> None:
        super().__init__()
        self.setObjectName("MessageUser" if role == "user" else "MessageAssistant")
        self.setMaximumWidth(900)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(7)
        name = QLabel("Du" if role == "user" else "ChaosCode")
        name.setStyleSheet("color:#aab3c2; font-size:9pt; font-weight:600;")
        layout.addWidget(name)
        browser = QTextBrowser()
        browser.setReadOnly(True)
        browser.setOpenExternalLinks(False)
        browser.setOpenLinks(False)
        browser.setFrameShape(QFrame.Shape.NoFrame)
        browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        browser.setMinimumHeight(34)
        browser.setMaximumHeight(460)
        browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        browser.document().setDefaultFont(QFont("Cascadia Code", 10))
        browser.setHtml(_safe_markdown(content))
        layout.addWidget(browser)


class ActivityCard(QFrame):
    def __init__(self, title: str, detail: str, *, is_error: bool = False) -> None:
        super().__init__()
        self.setObjectName("ActivityCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(5)
        self.toggle = QToolButton()
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setStyleSheet(
            "QToolButton { border:0; background:transparent; text-align:left; color:%s; }"
            % ("#ff9da5" if is_error else "#aab3c2")
        )
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlainText(detail)
        self.details.setMaximumHeight(180)
        self.details.setVisible(False)
        self.toggle.toggled.connect(self._toggle_details)
        layout.addWidget(self.toggle)
        layout.addWidget(self.details)

    def _toggle_details(self, checked: bool) -> None:
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
        self.details.setVisible(checked)


class Composer(QPlainTextEdit):
    send_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Composer")
        self.setPlaceholderText(
            "Beschreibe, was geändert werden soll …  ( @ für Dateien, Enter zum Senden )"
        )
        self.setMinimumHeight(96)
        self.setMaximumHeight(190)
        self.setTabChangesFocus(False)
        self._mention_start: int | None = None
        self._opening = ""
        self.completer = QCompleter([], self)
        self._suggestion_model = QStringListModel([], self)
        self.completer.setModel(self._suggestion_model)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchStartsWith)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.setWidget(self)
        popup = QListView()
        popup.setMinimumWidth(360)
        self.completer.setPopup(popup)
        self.completer.activated[str].connect(self._insert_completion)
        self.textChanged.connect(self._update_completions)

    def set_file_suggestions(self, paths: list[str]) -> None:
        self._suggestion_model.setStringList(paths)

    def insert_file_reference(self, relative_path: str) -> None:
        if " " in relative_path or "\t" in relative_path:
            text = f'@"{relative_path}"'
        else:
            text = f"@{relative_path}"
        cursor = self.textCursor()
        cursor.insertText(text)
        self.setTextCursor(cursor)
        self.setFocus()

    def _active_mention(self) -> tuple[str, int, str] | None:
        cursor_position = self.textCursor().position()
        before_cursor = self.toPlainText()[:cursor_position]
        match = ACTIVE_REFERENCE.search(before_cursor)
        if match is None:
            return None
        opening = match.group("quote") or match.group("brace") or ""
        prefix = match.group("quoted")
        group = "quoted"
        if prefix is None:
            prefix = match.group("braced")
            group = "braced"
        if prefix is None:
            prefix = match.group("plain") or ""
            group = "plain"
        start = match.start(group)
        return prefix, start, opening

    def _update_completions(self) -> None:
        active = self._active_mention()
        if active is None:
            self.completer.popup().hide()
            return
        prefix, start, _opening = active
        self._mention_start = start
        self.completer.setCompletionPrefix(prefix)
        if self.completer.completionCount() == 0:
            self.completer.popup().hide()
            return
        rect = self.cursorRect()
        rect.setWidth(max(360, self.completer.popup().sizeHintForColumn(0) + 36))
        self.completer.complete(rect)

    def _insert_completion(self, suggestion: str) -> None:
        active = self._active_mention()
        if active is None:
            return
        _prefix, start, opening = active
        is_directory = suggestion.endswith("/")
        if opening:
            insertion = suggestion
            if not is_directory:
                insertion += "}" if opening == "{" else opening
        elif " " in suggestion or "\t" in suggestion:
            insertion = f'"{suggestion}' if is_directory else f'"{suggestion}"'
        else:
            insertion = suggestion
        cursor = self.textCursor()
        end = cursor.position()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(insertion)
        self.setTextCursor(cursor)

    def keyPressEvent(self, event) -> None:
        popup = self.completer.popup()
        if popup.isVisible() and event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Tab,
            Qt.Key.Key_Backtab,
        ):
            event.ignore()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.send_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)
        self._update_completions()


class MainWindow(QMainWindow):
    def __init__(self, initial_workspace: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f"ChaosCode Desktop {__version__}")
        self.setMinimumSize(QSize(1050, 680))
        self.resize(1440, 920)
        self.store = SessionStore()
        try:
            last_workspace = Path(get_preference("last_workspace", str(Path.home())))
        except (ConfigError, ValueError, TypeError):
            last_workspace = Path.home()
        start = initial_workspace or last_workspace
        if not start.exists() or not start.is_dir():
            start = Path.home()
        self.workspace = start.resolve()
        self.workspace_tools = WorkspaceTools(self.workspace)
        self.current_session: Session | None = None
        self.temporary_sessions: dict[str, Session] = {}
        self.worker: TurnWorker | None = None
        self._busy = False
        self._build_ui()
        self._refresh_file_suggestions()
        self.refresh_sessions()
        self.new_chat()
        self._refresh_auth_state()

    def _build_ui(self) -> None:
        central = QWidget()
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root_layout.addWidget(splitter)
        self.setCentralWidget(central)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setMinimumWidth(250)
        self.sidebar.setMaximumWidth(360)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(14, 18, 14, 14)
        side.setSpacing(10)

        brand_row = QHBoxLayout()
        brand = QLabel("✦  ChaosCode")
        brand.setStyleSheet("font-size:16pt; font-weight:700; color:#f2f0ff;")
        version = QLabel(f"{__version__}")
        version.setStyleSheet("color:#7f8795; font-size:9pt;")
        brand_row.addWidget(brand)
        brand_row.addStretch(1)
        brand_row.addWidget(version)
        side.addLayout(brand_row)

        self.new_chat_button = QPushButton("＋   Neuer Chat")
        self.new_chat_button.setObjectName("PrimaryButton")
        self.new_chat_button.clicked.connect(self.new_chat)
        side.addWidget(self.new_chat_button)

        project_row = QHBoxLayout()
        self.project_label = QLabel(self.workspace.name or str(self.workspace))
        self.project_label.setStyleSheet("font-weight:600; color:#c9ced8;")
        self.project_label.setToolTip(str(self.workspace))
        self.open_project_button = QToolButton()
        self.open_project_button.setText("Ordner")
        self.open_project_button.clicked.connect(self.choose_workspace)
        project_row.addWidget(self.project_label, 1)
        project_row.addWidget(self.open_project_button)
        side.addLayout(project_row)

        self.chat_filter = QLineEdit()
        self.chat_filter.setPlaceholderText("Chats durchsuchen")
        self.chat_filter.textChanged.connect(self._filter_sessions)
        side.addWidget(self.chat_filter)

        self.chat_tree = QTreeWidget()
        self.chat_tree.setHeaderHidden(True)
        self.chat_tree.setIndentation(14)
        self.chat_tree.itemClicked.connect(self._session_item_clicked)
        self.chat_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chat_tree.customContextMenuRequested.connect(self._show_session_menu)
        side.addWidget(self.chat_tree, 1)

        footer = QHBoxLayout()
        self.account_button = QPushButton("Verbinden")
        self.account_button.clicked.connect(self._show_account_dialog)
        self.diagnostics_button = QToolButton()
        self.diagnostics_button.setText("⋯")
        menu = QMenu(self)
        diagnostics_action = QAction("Diagnose & Info", self)
        diagnostics_action.triggered.connect(self._show_diagnostics)
        menu.addAction(diagnostics_action)
        help_action = QAction("Hilfe", self)
        help_action.triggered.connect(self._show_help)
        menu.addAction(help_action)
        self.diagnostics_button.setMenu(menu)
        self.diagnostics_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        footer.addWidget(self.account_button, 1)
        footer.addWidget(self.diagnostics_button)
        side.addLayout(footer)

        self.main_panel = QWidget()
        main = QVBoxLayout(self.main_panel)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        self.top_bar = QFrame()
        self.top_bar.setObjectName("TopBar")
        top = QHBoxLayout(self.top_bar)
        top.setContentsMargins(22, 12, 22, 12)
        self.chat_title = QLabel("Neuer Chat")
        self.chat_title.setStyleSheet("font-size:13pt; font-weight:650;")
        self.workspace_path_label = QLabel(str(self.workspace))
        self.workspace_path_label.setStyleSheet("color:#8d95a4; font-size:9pt;")
        title_column = QVBoxLayout()
        title_column.setSpacing(2)
        title_column.addWidget(self.chat_title)
        title_column.addWidget(self.workspace_path_label)
        top.addLayout(title_column, 1)

        self.search_toggle = QCheckBox("Websuche")
        self.search_toggle.setToolTip("DeepSeek-Websuche für Antworten aktivieren")
        top.addWidget(self.search_toggle)
        top.addSpacing(8)
        self.auto_approve_toggle = QCheckBox("Auto-Freigabe")
        self.auto_approve_toggle.setToolTip(
            "Dateiänderungen und Shell-Befehle ohne Rückfrage erlauben"
        )
        self.auto_approve_toggle.toggled.connect(self._confirm_auto_approve)
        top.addWidget(self.auto_approve_toggle)
        top.addSpacing(8)
        step_label = QLabel("Schritte")
        step_label.setStyleSheet("color:#9ca4b2;")
        top.addWidget(step_label)
        self.max_steps = QSpinBox()
        self.max_steps.setRange(1, 30)
        self.max_steps.setValue(12)
        self.max_steps.setFixedWidth(68)
        top.addWidget(self.max_steps)
        main.addWidget(self.top_bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.message_container = QWidget()
        self.message_layout = QVBoxLayout(self.message_container)
        self.message_layout.setContentsMargins(32, 24, 32, 26)
        self.message_layout.setSpacing(14)
        self.scroll.setWidget(self.message_container)
        main.addWidget(self.scroll, 1)

        self.composer_panel = QFrame()
        self.composer_panel.setObjectName("ComposerPanel")
        composer_layout = QVBoxLayout(self.composer_panel)
        composer_layout.setContentsMargins(22, 14, 22, 12)
        composer_layout.setSpacing(8)
        self.editor = Composer()
        self.editor.send_requested.connect(self.send_message)
        composer_layout.addWidget(self.editor)
        composer_bottom = QHBoxLayout()
        self.attach_button = QToolButton()
        self.attach_button.setText("＋ Datei")
        self.attach_button.clicked.connect(self.attach_file)
        self.temporary_toggle = QCheckBox("Temporärer Chat")
        self.temporary_toggle.setToolTip(
            "Diese Unterhaltung bleibt nur geöffnet und wird nicht gespeichert."
        )
        self.temporary_toggle.toggled.connect(self._toggle_temporary)
        self.send_button = QPushButton("Senden  ↑")
        self.send_button.setObjectName("PrimaryButton")
        self.send_button.clicked.connect(self.send_message)
        hint = QLabel("Enter senden · Shift+Enter neue Zeile · @ Dateien auswählen")
        hint.setStyleSheet("color:#737b89; font-size:9pt;")
        composer_bottom.addWidget(self.attach_button)
        composer_bottom.addWidget(self.temporary_toggle)
        composer_bottom.addWidget(hint, 1)
        composer_bottom.addWidget(self.send_button)
        composer_layout.addLayout(composer_bottom)
        main.addWidget(self.composer_panel)

        splitter.addWidget(self.sidebar)
        splitter.addWidget(self.main_panel)
        splitter.setSizes([290, 1150])
        self.statusBar().showMessage("Bereit")

    def _refresh_auth_state(self) -> None:
        try:
            token, source = get_auth_token()
        except ConfigError as exc:
            token, source = None, None
            self.statusBar().showMessage(str(exc), 8000)
        if token:
            self.account_button.setText("● Verbunden")
            self.account_button.setToolTip(
                f"DeepSeek ist konfiguriert über: {source}. Token wird nicht angezeigt."
            )
            self.account_button.setStyleSheet("color:#7ed6a4;")
        else:
            self.account_button.setText("DeepSeek verbinden")
            self.account_button.setToolTip("Sicheren DeepSeek-Web-Token einrichten")
            self.account_button.setStyleSheet("color:#e3bd71;")

    def _refresh_file_suggestions(self) -> None:
        result = self.workspace_tools.list_files({"max_results": 300})
        paths = []
        if result.content != "No visible files found.":
            paths = [
                path for path in result.content.splitlines() if path and not path.startswith("[")
            ]
        self.editor.set_file_suggestions(sorted(paths))

    def refresh_sessions(self, selected_id: str | None = None) -> None:
        current_id = selected_id or (self.current_session.id if self.current_session else None)
        self.chat_tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        sessions = self.store.list()
        for session in sessions:
            self._add_session_item(session, groups, temporary=False)
        for session in sorted(
            self.temporary_sessions.values(), key=lambda item: item.updated_at, reverse=True
        ):
            self._add_session_item(session, groups, temporary=True)
        for item in groups.values():
            item.setExpanded(True)
        if current_id:
            match = self.chat_tree.findItems(
                "", Qt.MatchFlag.MatchContains | Qt.MatchFlag.MatchRecursive
            )
            for item in match:
                if item.data(0, SESSION_ROLE) == current_id:
                    self.chat_tree.setCurrentItem(item)
                    break
        self._filter_sessions(self.chat_filter.text())

    def _add_session_item(
        self,
        session: Session,
        groups: dict[str, QTreeWidgetItem],
        *,
        temporary: bool,
    ) -> None:
        project = session.cwd
        key = "__temporary__" if temporary else project
        if key not in groups:
            label = "Temporäre Chats" if temporary else (Path(project).name or project)
            parent = QTreeWidgetItem([label])
            if not temporary:
                parent.setData(0, WORKSPACE_ROLE, project)
                parent.setToolTip(0, project)
            parent.setForeground(0, QColor("#8992a2"))
            groups[key] = parent
            self.chat_tree.addTopLevelItem(parent)
        title = self._session_title(session)
        child = QTreeWidgetItem([title])
        child.setData(0, SESSION_ROLE, session.id)
        child.setData(0, TEMP_ROLE, temporary)
        child.setData(0, WORKSPACE_ROLE, project)
        child.setToolTip(0, f"{title}\n{project}\nAktualisiert: {session.updated_at}")
        if temporary:
            child.setForeground(0, QColor("#b5a5ff"))
        groups[key].addChild(child)

    @staticmethod
    def _session_title(session: Session) -> str:
        first_user = next(
            (
                message.get("content", "")
                for message in session.messages
                if message.get("role") == "user"
            ),
            "Neuer Chat",
        )
        title = " ".join(first_user.split())
        return title[:48] + ("…" if len(title) > 48 else "") if title else "Neuer Chat"

    def _filter_sessions(self, text: str) -> None:
        query = text.strip().casefold()
        for index in range(self.chat_tree.topLevelItemCount()):
            parent = self.chat_tree.topLevelItem(index)
            visible_children = 0
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                visible = (
                    not query
                    or query in child.text(0).casefold()
                    or query in child.toolTip(0).casefold()
                )
                child.setHidden(not visible)
                visible_children += int(visible)
            parent.setHidden(visible_children == 0)

    def _set_busy(self, busy: bool, status: str = "") -> None:
        self._busy = busy
        self.editor.setDisabled(busy)
        self.send_button.setDisabled(busy)
        self.attach_button.setDisabled(busy)
        self.new_chat_button.setDisabled(busy)
        self.open_project_button.setDisabled(busy)
        self.chat_tree.setDisabled(busy)
        self.temporary_toggle.setDisabled(busy)
        self.max_steps.setDisabled(busy)
        self.search_toggle.setDisabled(busy)
        self.auto_approve_toggle.setDisabled(busy)
        self.statusBar().showMessage(status or ("ChaosCode arbeitet …" if busy else "Bereit"))
        self.send_button.setText("Arbeitet …" if busy else "Senden  ↑")

    def _remember_workspace(self) -> None:
        try:
            set_preference("last_workspace", str(self.workspace))
        except (ConfigError, ValueError) as exc:
            self.statusBar().showMessage(
                f"Arbeitsordner konnte nicht gespeichert werden: {exc}", 8000
            )

    def _set_current_session(self, session: Session, *, temporary: bool) -> None:
        self.current_session = session
        self.workspace = Path(session.cwd).resolve()
        self.workspace_tools = WorkspaceTools(self.workspace)
        self._remember_workspace()
        self.project_label.setText(self.workspace.name or str(self.workspace))
        self.project_label.setToolTip(str(self.workspace))
        self.workspace_path_label.setText(str(self.workspace))
        self.chat_title.setText(self._session_title(session))
        self.editor.set_file_suggestions([])
        self._refresh_file_suggestions()
        self._set_checkbox(self.temporary_toggle, temporary)
        self._render_messages()

    @staticmethod
    def _set_checkbox(checkbox: QCheckBox, checked: bool) -> None:
        checkbox.blockSignals(True)
        checkbox.setChecked(checked)
        checkbox.blockSignals(False)

    def new_chat(self) -> None:
        if self._busy:
            return
        if self.temporary_toggle.isChecked():
            session = Session(id=uuid.uuid4().hex[:12], cwd=str(self.workspace))
            self.temporary_sessions[session.id] = session
            temporary = True
        else:
            session = self.store.new(self.workspace)
            temporary = False
        self._set_current_session(session, temporary=temporary)
        self.refresh_sessions(session.id)

    def choose_workspace(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Projektordner öffnen",
            str(self.workspace),
            QFileDialog.Option.ShowDirsOnly,
        )
        if not folder:
            return
        self.workspace = Path(folder).resolve()
        self.workspace_tools = WorkspaceTools(self.workspace)
        self.project_label.setText(self.workspace.name or str(self.workspace))
        self.project_label.setToolTip(str(self.workspace))
        self.workspace_path_label.setText(str(self.workspace))
        self._refresh_file_suggestions()
        self.new_chat()

    def _session_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        session_id = item.data(0, SESSION_ROLE)
        if not session_id:
            root = item.data(0, WORKSPACE_ROLE)
            if root and Path(root).is_dir() and Path(root).resolve() != self.workspace:
                self.workspace = Path(root).resolve()
                self.workspace_tools = WorkspaceTools(self.workspace)
                self.new_chat()
            return
        if self.current_session and session_id == self.current_session.id:
            return
        temporary = bool(item.data(0, TEMP_ROLE))
        if temporary:
            session = self.temporary_sessions.get(session_id)
        else:
            try:
                session = self.store.load(session_id)
            except SessionError as exc:
                QMessageBox.warning(self, "Chat konnte nicht geöffnet werden", str(exc))
                self.refresh_sessions()
                return
        if session is None:
            return
        if not Path(session.cwd).is_dir():
            QMessageBox.warning(
                self,
                "Projektordner fehlt",
                f"Der Projektordner existiert nicht mehr:\n{session.cwd}",
            )
            return
        self._set_current_session(session, temporary=temporary)
        self.refresh_sessions(session.id)

    def _toggle_temporary(self, checked: bool) -> None:
        if self.current_session is None or self._busy:
            return
        current_id = self.current_session.id
        is_temp = current_id in self.temporary_sessions
        if checked and not is_temp:
            old = self.current_session
            clone = copy.deepcopy(old)
            clone.id = uuid.uuid4().hex[:12]
            self.temporary_sessions[clone.id] = clone
            if not old.messages:
                try:
                    self.store.delete(old.id)
                except SessionError:
                    pass
            self._set_current_session(clone, temporary=True)
            self.refresh_sessions(clone.id)
        elif not checked and is_temp:
            session = self.current_session
            try:
                self.store.save(session)
            except SessionError as exc:
                self.statusBar().showMessage(str(exc), 8000)
                self._set_checkbox(self.temporary_toggle, True)
                return
            self.temporary_sessions.pop(session.id, None)
            self._set_current_session(session, temporary=False)
            self.refresh_sessions(session.id)

    def _show_session_menu(self, point) -> None:
        item = self.chat_tree.itemAt(point)
        session_id = item.data(0, SESSION_ROLE) if item else None
        if not session_id:
            return
        menu = QMenu(self)
        delete_action = menu.addAction("Chat löschen")
        chosen = menu.exec(self.chat_tree.viewport().mapToGlobal(point))
        if chosen == delete_action:
            self._delete_session(session_id, bool(item.data(0, TEMP_ROLE)))

    def _delete_session(self, session_id: str, temporary: bool) -> None:
        answer = QMessageBox.question(
            self,
            "Chat löschen",
            "Diese Unterhaltung dauerhaft löschen?"
            if not temporary
            else "Temporären Chat schließen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            if temporary:
                self.temporary_sessions.pop(session_id, None)
            else:
                self.store.delete(session_id)
        except SessionError as exc:
            QMessageBox.warning(self, "Chat konnte nicht gelöscht werden", str(exc))
            return
        if self.current_session and self.current_session.id == session_id:
            self.current_session = None
            self.new_chat()
        else:
            self.refresh_sessions()

    def attach_file(self) -> None:
        file_name, _filter = QFileDialog.getOpenFileName(
            self,
            "Projektdatei auswählen",
            str(self.workspace),
            "Alle Dateien (*.*)",
        )
        if not file_name:
            return
        try:
            relative, _content = self.workspace_tools.read_selected_file(
                Path(file_name).resolve().relative_to(self.workspace).as_posix()
            )
        except (ValueError, ToolError, OSError) as exc:
            QMessageBox.warning(self, "Datei kann nicht angehängt werden", str(exc))
            return
        self.editor.insert_file_reference(relative)
        self.editor.setFocus()

    def _confirm_auto_approve(self, checked: bool) -> None:
        if not checked:
            self.auto_approve_toggle.setStyleSheet("")
            return
        answer = QMessageBox.warning(
            self,
            "Auto-Freigabe aktivieren?",
            "Dateiänderungen und Shell-Befehle werden ohne weitere Rückfrage ausgeführt. "
            "Aktiviere diese Option nur, wenn du der Aufgabe vertraust.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._set_checkbox(self.auto_approve_toggle, False)
        else:
            self.auto_approve_toggle.setStyleSheet("color:#ffbf69; font-weight:700;")

    def send_message(self) -> None:
        if self._busy:
            return
        prompt = self.editor.toPlainText().strip()
        if not prompt:
            self.editor.setFocus()
            return
        if self.current_session is None:
            self.new_chat()
        try:
            token, _source = get_auth_token()
        except ConfigError as exc:
            QMessageBox.critical(self, "Konfigurationsfehler", str(exc))
            return
        if not token:
            self._show_account_dialog()
            try:
                token, _source = get_auth_token()
            except ConfigError as exc:
                QMessageBox.critical(self, "Konfigurationsfehler", str(exc))
                return
            if not token:
                return
        try:
            attachments = select_file_references(prompt, self.workspace_tools)
        except (ValueError, ToolError, OSError) as exc:
            QMessageBox.warning(self, "Dateireferenz kann nicht geladen werden", str(exc))
            return
        session_store = None if self.temporary_toggle.isChecked() else self.store
        self.editor.clear()
        self._render_messages(pending_prompt=prompt)
        worker = TurnWorker(
            token=token,
            workspace=self.workspace,
            session=self.current_session,
            session_store=session_store,
            prompt=prompt,
            selected_files=[(item.path, item.content) for item in attachments],
            search=self.search_toggle.isChecked(),
            max_steps=self.max_steps.value(),
            auto_approve=self.auto_approve_toggle.isChecked(),
        )
        worker.approval_requested.connect(self._approve_action)
        worker.completed.connect(self._turn_completed)
        worker.failed.connect(self._turn_failed)
        worker.finished.connect(self._worker_finished)
        self.worker = worker
        self._set_busy(True, "ChaosCode analysiert deine Aufgabe …")
        worker.start()

    def _approve_action(self, request: ApprovalRequest) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Aktion bestätigen")
        dialog.setMinimumSize(720, 480)
        layout = QVBoxLayout(dialog)
        heading = QLabel(request.title)
        heading.setStyleSheet("font-size:13pt; font-weight:700; color:#f2f0ff;")
        layout.addWidget(heading)
        note = QLabel("Prüfe die Änderung oder den Befehl, bevor du ihn freigibst.")
        note.setStyleSheet("color:#9ca4b2;")
        layout.addWidget(note)
        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setPlainText(request.detail)
        details.setStyleSheet(
            "QPlainTextEdit { font-family:'Cascadia Code','Consolas'; background:#0c0e12; "
            "border:1px solid #303541; border-radius:8px; padding:8px; }"
        )
        details.highlighter = DiffHighlighter(details.document())
        layout.addWidget(details, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        reject = QPushButton("Ablehnen")
        approve = QPushButton("Einmal freigeben")
        approve.setObjectName("PrimaryButton")
        buttons.addWidget(reject)
        buttons.addWidget(approve)
        layout.addLayout(buttons)
        reject.clicked.connect(dialog.reject)
        approve.clicked.connect(dialog.accept)
        dialog.exec()
        request.accepted = dialog.result() == QDialog.DialogCode.Accepted
        request.answered.set()

    def _turn_completed(self, _result: Any, messages: list[dict[str, str]]) -> None:
        if self.current_session is None:
            return
        self.current_session.messages = messages
        self.chat_title.setText(self._session_title(self.current_session))
        self._render_messages()
        if self.current_session.id not in self.temporary_sessions:
            self.refresh_sessions(self.current_session.id)
        self._refresh_file_suggestions()
        self.statusBar().showMessage("Fertig", 4000)

    def _turn_failed(self, message: str, messages: list[dict[str, str]]) -> None:
        if self.current_session is not None:
            self.current_session.messages = messages
            self._render_messages()
        self.statusBar().showMessage(f"Fehler: {message}", 12000)
        QMessageBox.warning(self, "Anfrage fehlgeschlagen", message)

    def _worker_finished(self) -> None:
        self._set_busy(False)
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()
        self._refresh_file_suggestions()
        self.refresh_sessions(self.current_session.id if self.current_session else None)

    def _render_messages(self, pending_prompt: str | None = None) -> None:
        while self.message_layout.count():
            item = self.message_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        messages = self.current_session.messages if self.current_session else []
        if not messages and not pending_prompt:
            self._show_empty_state()
        for message in messages:
            self._add_message(message)
        if pending_prompt:
            self._add_message({"role": "user", "content": pending_prompt})
            self.message_layout.addWidget(
                ActivityCard(
                    "ChaosCode arbeitet …", "Deine Anfrage wird sicher im Projekt ausgeführt."
                )
            )
        self.message_layout.addStretch(1)
        self._scroll_to_bottom()

    def _show_empty_state(self) -> None:
        welcome = QWidget()
        layout = QVBoxLayout(welcome)
        layout.setContentsMargins(30, 100, 30, 100)
        title = QLabel("Woran möchtest du arbeiten?")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size:22pt; font-weight:700; color:#f2f0ff;")
        subtitle = QLabel(
            "ChaosCode kann Dateien lesen, Änderungen vorschlagen und Befehle ausführen.\n"
            "Dateiänderungen und Shell-Befehle werden erst nach deiner Freigabe ausgeführt."
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color:#929aaa; line-height:1.5;")
        layout.addStretch(1)
        layout.addWidget(title)
        layout.addSpacing(10)
        layout.addWidget(subtitle)
        layout.addStretch(1)
        self.message_layout.addWidget(welcome)

    def _add_message(self, message: dict[str, str]) -> None:
        role = message.get("role", "")
        content = message.get("content", "")
        if role == "user":
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(MessageBubble("user", content), 0, Qt.AlignmentFlag.AlignRight)
            self.message_layout.addLayout(row)
        elif role == "assistant":
            if content.startswith("[Tool request not executed:"):
                return
            call, _issue = extract_tool_call(content)
            if call:
                return
            row = QHBoxLayout()
            row.addWidget(MessageBubble("assistant", content), 0, Qt.AlignmentFlag.AlignLeft)
            row.addStretch(1)
            self.message_layout.addLayout(row)
        elif role == "tool":
            name = message.get("name", "tool")
            detail = content
            is_error = (
                content.startswith(("Tool error:", "User denied", "No interactive terminal"))
                or name == "protocol"
            )
            label = {
                "read_file": "Datei gelesen",
                "list_files": "Projektdateien aufgelistet",
                "search_files": "Im Projekt gesucht",
                "write_file": "Datei geschrieben",
                "edit_file": "Datei bearbeitet",
                "run_command": "Befehl ausgeführt",
                "protocol": "Tool-Protokoll",
            }.get(name, f"Werkzeug: {name}")
            card = ActivityCard(("! " if is_error else "✓ ") + label, detail, is_error=is_error)
            row = QHBoxLayout()
            row.addWidget(card, 1)
            self.message_layout.addLayout(row)

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def _show_account_dialog(self) -> None:
        try:
            token, source = get_auth_token()
        except ConfigError as exc:
            QMessageBox.critical(self, "Konfigurationsfehler", str(exc))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("DeepSeek-Konto")
        dialog.setMinimumWidth(520)
        layout = QVBoxLayout(dialog)
        heading = QLabel("DeepSeek Web verbinden" if not token else "DeepSeek ist verbunden")
        heading.setStyleSheet("font-size:16pt; font-weight:700; color:#f2f0ff;")
        layout.addWidget(heading)
        explanation = QLabel(
            "ChaosCode nutzt den kostenlosen Web-Chat, nicht die kostenpflichtige API. "
            "Der Token wird lokal in deiner Benutzerkonfiguration gespeichert und nie angezeigt."
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color:#aab3c2;")
        layout.addWidget(explanation)
        token_input = None
        if token:
            source_label = QLabel(f"Token konfiguriert über: {source}")
            source_label.setStyleSheet("color:#79d6a5;")
            layout.addWidget(source_label)
            if source == "Benutzerkonfiguration":
                remove = QPushButton("Gespeicherten Token entfernen")
                remove.clicked.connect(lambda: self._remove_token_and_close(dialog))
                layout.addWidget(remove)
        else:
            steps = QLabel(
                "1. Melde dich bei chat.deepseek.com an.\n"
                "2. Kopiere in den Browser-Entwicklertools den Wert von userToken.\n"
                "3. Füge ihn hier ein. Behandle ihn wie ein Passwort."
            )
            steps.setStyleSheet("color:#aab3c2;")
            layout.addWidget(steps)
            open_site = QPushButton("DeepSeek im Browser öffnen")
            open_site.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl("https://chat.deepseek.com/"))
            )
            layout.addWidget(open_site)
            token_input = QLineEdit()
            token_input.setEchoMode(QLineEdit.EchoMode.Password)
            token_input.setPlaceholderText("DeepSeek-Web-Token")
            layout.addWidget(token_input)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("Schließen")
        buttons.addWidget(close_button)
        if not token:
            save_button = QPushButton("Sicher speichern")
            save_button.setObjectName("PrimaryButton")
            buttons.addWidget(save_button)
            save_button.clicked.connect(
                lambda: self._save_token_and_close(
                    dialog, token_input.text() if token_input else ""
                )
            )
        close_button.clicked.connect(dialog.reject)
        layout.addLayout(buttons)
        dialog.exec()
        self._refresh_auth_state()

    def _save_token_and_close(self, dialog: QDialog, token: str) -> None:
        try:
            save_auth_token(token)
        except (ValueError, ConfigError) as exc:
            QMessageBox.warning(self, "Token nicht gespeichert", str(exc))
            return
        dialog.accept()

    def _remove_token(self) -> bool:
        try:
            removed = remove_saved_auth_token()
        except ConfigError as exc:
            QMessageBox.warning(self, "Token nicht entfernt", str(exc))
            return False
        return removed

    def _remove_token_and_close(self, dialog: QDialog) -> None:
        if self._remove_token():
            dialog.accept()

    def _show_diagnostics(self) -> None:
        try:
            configured = bool(get_auth_token()[0])
            config_status = "verbunden" if configured else "kein Token"
        except ConfigError as exc:
            config_status = f"Konfigurationsfehler: {exc}"
        try:
            gui_version = importlib.metadata.version("PySide6-Essentials")
        except importlib.metadata.PackageNotFoundError:
            try:
                gui_version = importlib.metadata.version("PySide6")
            except importlib.metadata.PackageNotFoundError:
                gui_version = "gebündelt"
        checks = [
            f"ChaosCode {__version__}",
            f"Python {sys.version.split()[0]} ({sys.platform})",
            f"PySide6 {gui_version}",
            f"Projektordner: {self.workspace}",
            f"Konfiguration: {config_status}",
            f"Aktiver Chat: {self.current_session.id if self.current_session else 'keiner'}",
        ]
        transport_ready = all(
            importlib.util.find_spec(module) is not None for module in ("curl_cffi", "wasmtime")
        )
        checks.append(
            "DeepSeek-Transport und Proof-of-Work: verfügbar"
            if transport_ready
            else "DeepSeek-Abhängigkeiten: unvollständig"
        )
        QMessageBox.information(self, "Diagnose & Info", "\n".join(checks))

    def _show_help(self) -> None:
        QMessageBox.information(
            self,
            "ChaosCode Desktop – Hilfe",
            "• Neue Chats starten oder links gespeicherte Unterhaltungen fortsetzen.\n"
            "• Mit @ Dateien aus dem Projekt als Kontext auswählen.\n"
            "• Änderungen und Shell-Befehle werden erst nach deiner Bestätigung ausgeführt.\n"
            "• Websuche, temporäre Chats und maximale Agent-Schritte findest du in der Leiste.",
        )

    def closeEvent(self, event) -> None:
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(
                self,
                "Anfrage läuft noch",
                "Warte bitte, bis ChaosCode die aktuelle Anfrage abgeschlossen hat.",
            )
            event.ignore()
            return
        event.accept()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chaoscode-desktop")
    parser.add_argument("-C", "--workdir", help="Projektordner beim Start öffnen")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    options = parser.parse_args(argv)
    app = QApplication(sys.argv[:1])
    if options.smoke_test:
        return 0
    app.setApplicationName("ChaosCode Desktop")
    app.setOrganizationName("ChaosCode")
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    workspace = Path(options.workdir).expanduser().resolve() if options.workdir else None
    window = MainWindow(workspace)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
