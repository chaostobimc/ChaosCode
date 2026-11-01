import shlex
import sys
import tempfile
import unittest
from pathlib import Path

from chaoscode.tools import ToolError, WorkspaceTools


class WorkspaceToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name) / "project"
        self.root.mkdir()
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text(
            "def greet():\n    return 'hello'\n", encoding="utf-8"
        )
        (self.root / ".env").write_text("SECRET=value\n", encoding="utf-8")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("credential=secret\n", encoding="utf-8")
        self.tools = WorkspaceTools(self.root)

    def test_read_and_search_project_text(self):
        read = self.tools.read_file({"path": "src/main.py"})
        self.assertIn("def greet", read.content)
        matches = self.tools.search_files({"query": "greet"})
        self.assertIn("src/main.py:1", matches.content)
        self.assertNotIn(".env", matches.content)

    def test_sensitive_files_and_git_internals_are_blocked(self):
        for path in (".env", ".git/config"):
            with self.subTest(path=path), self.assertRaises(ToolError):
                self.tools.read_file({"path": path})
            with self.subTest(path=path), self.assertRaises(ToolError):
                self.tools.search_files({"query": "secret", "path": path})

    def test_paths_cannot_escape_workspace(self):
        with self.assertRaises(ToolError):
            self.tools.read_file({"path": "../outside.txt"})

    def test_list_files_omits_secrets_and_generated_dirs(self):
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "dep.js").write_text("x", encoding="utf-8")
        result = self.tools.list_files({"path": "."}).content
        self.assertIn("src/main.py", result)
        self.assertNotIn(".env", result)
        self.assertNotIn(".git", result)
        self.assertNotIn("node_modules", result)

    def test_write_requires_approval_and_shows_diff(self):
        approvals = []

        def deny(title, detail):
            approvals.append((title, detail))
            return False

        result = self.tools.write_file({"path": "new.txt", "content": "hello\n"}, deny)
        self.assertTrue(result.is_error)
        self.assertFalse((self.root / "new.txt").exists())
        self.assertIn("write file new.txt", approvals[0][0])
        self.assertIn("hello", approvals[0][1])

    def test_write_and_exact_edit_after_approval(self):
        allow = lambda _title, _detail: True
        self.tools.write_file({"path": "new.txt", "content": "one\ntwo\n"}, allow)
        edited = self.tools.edit_file(
            {"path": "new.txt", "old_text": "two", "new_text": "three"}, allow
        )
        self.assertFalse(edited.is_error)
        self.assertEqual((self.root / "new.txt").read_text(encoding="utf-8"), "one\nthree\n")

    def test_ambiguous_edit_is_rejected(self):
        (self.root / "repeat.txt").write_text("x x x", encoding="utf-8")
        with self.assertRaises(ToolError):
            self.tools.edit_file(
                {"path": "repeat.txt", "old_text": "x", "new_text": "y"},
                lambda *_args: True,
            )

    def test_command_is_not_run_when_denied(self):
        marker = self.root / "marker.txt"
        python_code = f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(python_code)}"
        result = self.tools.run_command({"command": command}, lambda *_args: False)
        self.assertTrue(result.is_error)
        self.assertFalse(marker.exists())

    def test_approved_command_returns_exit_code_and_output(self):
        result = self.tools.run_command(
            {"command": f"{shlex.quote(sys.executable)} -c {shlex.quote('print(42)')}"},
            lambda *_args: True,
        )
        self.assertIn("Exit code: 0", result.content)
        self.assertIn("42", result.content)

    def test_symlink_escape_is_blocked(self):
        outside = Path(self.temp_dir.name) / "outside.txt"
        outside.write_text("private", encoding="utf-8")
        link = self.root / "external.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks are unavailable on this platform")
        with self.assertRaises(ToolError):
            self.tools.read_file({"path": "external.txt"})


if __name__ == "__main__":
    unittest.main()
