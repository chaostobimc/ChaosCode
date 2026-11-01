import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from PySide6.QtGui import QTextCursor
    from PySide6.QtWidgets import QApplication
except (ImportError, OSError) as exc:
    raise unittest.SkipTest(f"Qt desktop dependencies are unavailable: {exc}") from exc

from chaoscode.desktop import Composer, MainWindow, _safe_markdown
from chaoscode.sessions import Session


class DesktopRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_markdown_escapes_html_and_keeps_basic_formatting(self):
        rendered = _safe_markdown(
            "# Summary\n\n**Safe** text with <script>alert(1)</script>.\n\n"
            "```html\n<img src=x>\n```"
        )
        self.assertIn("<h2>Summary</h2>", rendered)
        self.assertIn("<strong>Safe</strong>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("&lt;img src=x&gt;", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img", rendered)

    def test_composer_matches_project_paths_and_quoted_spaces(self):
        editor = Composer()
        editor.set_file_suggestions(["src/main.py", "src/map.txt", "notes.md"])
        editor.setPlainText("Review @src/main.")
        editor.moveCursor(QTextCursor.MoveOperation.End)
        editor._update_completions()
        self.assertEqual(editor._active_mention()[0], "src/main.")
        self.assertEqual(editor.completer.completionCount(), 1)

        editor.setPlainText('Review @"my folder/fi')
        editor.moveCursor(QTextCursor.MoveOperation.End)
        editor._update_completions()
        active = editor._active_mention()
        self.assertEqual(active[0], "my folder/fi")
        self.assertEqual(active[2], '"')

    def test_main_window_starts_a_new_chat_for_the_selected_project(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("chaoscode.desktop.SessionStore") as store_type,
            patch("chaoscode.desktop.get_preference", return_value=directory),
            patch("chaoscode.desktop.set_preference"),
            patch("chaoscode.desktop.get_auth_token", return_value=(None, None)),
        ):
            store = store_type.return_value
            store.list.return_value = []
            session = Session(id="0123456789ab", cwd=directory)
            store.new.return_value = session
            window = MainWindow(Path(directory))
            self.addCleanup(window.close)
            self.assertEqual(window.current_session.id, session.id)
            self.assertEqual(window.workspace, Path(directory).resolve())
            self.assertEqual(window.project_label.toolTip(), str(Path(directory).resolve()))

    def test_session_title_uses_first_user_message_and_truncates(self):
        session = Session(
            id="0123456789ab",
            cwd="/tmp/project",
            messages=[
                {"role": "assistant", "content": "Hello"},
                {"role": "user", "content": "  Fix   the   parser  "},
            ],
        )
        self.assertEqual(MainWindow._session_title(session), "Fix the parser")

        session.messages[1]["content"] = "x" * 55
        self.assertEqual(MainWindow._session_title(session), "x" * 48 + "…")


if __name__ == "__main__":
    unittest.main()
