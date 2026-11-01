import tempfile
import unittest
from pathlib import Path

from chaoscode.references import select_file_references
from chaoscode.tools import WorkspaceTools


class FileReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name) / "project"
        self.root.mkdir()
        (self.root / "hello.py").write_text('print("hello")\n', encoding="utf-8")
        (self.root / ".env").write_text("SECRET=value\n", encoding="utf-8")
        notes = self.root / "docs"
        notes.mkdir()
        (notes / "file with spaces.md").write_text("Project notes\n", encoding="utf-8")
        self.tools = WorkspaceTools(self.root)

    def test_selects_existing_file_and_loads_raw_contents(self):
        selected = select_file_references("Please review @hello.py", self.tools)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].path, "hello.py")
        self.assertEqual(selected[0].content, 'print("hello")\n')

    def test_quoted_and_braced_paths_support_spaces_and_deduplicate(self):
        prompt = 'Review @"docs/file with spaces.md" and also @{docs/file with spaces.md}.'
        selected = select_file_references(prompt, self.tools)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].path, "docs/file with spaces.md")
        self.assertEqual(selected[0].content, "Project notes\n")

    def test_email_addresses_and_python_decorators_are_not_file_references(self):
        prompt = 'Keep @dataclass and @app.route("/"); contact dev@example.com.'
        self.assertEqual(select_file_references(prompt, self.tools), [])

    def test_sensitive_file_reference_is_blocked(self):
        with self.assertRaisesRegex(ValueError, "Access denied"):
            select_file_references("Inspect @.env", self.tools)

    def test_missing_explicit_file_reference_is_reported(self):
        with self.assertRaisesRegex(ValueError, "@missing.py"):
            select_file_references("Inspect @missing.py", self.tools)

    def test_absolute_file_references_are_blocked(self):
        with self.assertRaisesRegex(ValueError, "relative to the project root"):
            select_file_references('Inspect @"/tmp/private.py"', self.tools)

    def test_too_many_files_are_rejected(self):
        for index in range(5):
            (self.root / f"file{index}.txt").write_text(str(index), encoding="utf-8")
        prompt = "Read " + " ".join(f"@file{index}.txt" for index in range(5))
        with self.assertRaisesRegex(ValueError, "höchstens 4"):
            select_file_references(prompt, self.tools)


if __name__ == "__main__":
    unittest.main()
