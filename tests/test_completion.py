import tempfile
import unittest
from pathlib import Path

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import to_formatted_text

from chaoscode.completion import AtFileCompleter
from chaoscode.tools import WorkspaceTools


class AtFileCompleterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        (self.root / "main.py").write_text("print('hello')\n", encoding="utf-8")
        source = self.root / "src"
        source.mkdir()
        (source / "main_window.py").write_text("pass\n", encoding="utf-8")
        docs = self.root / "docs"
        docs.mkdir()
        (docs / "quick start.md").write_text("notes\n", encoding="utf-8")
        self.completer = AtFileCompleter(WorkspaceTools(self.root))

    def complete(self, text):
        return list(self.completer.get_completions(Document(text), CompleteEvent()))

    def test_suggests_project_files_and_folders_after_at(self):
        completions = self.complete("Review @src/ma")
        self.assertEqual([item.text for item in completions], ["src/main_window.py"])
        self.assertEqual(completions[0].start_position, -6)
        metadata = "".join(text for _style, text in to_formatted_text(completions[0].display_meta))
        self.assertEqual(metadata, "Datei")

        directories = self.complete("Review @src")
        self.assertTrue(any(item.text == "src/" for item in directories))

    def test_completion_quotes_paths_with_spaces(self):
        completions = self.complete("Open @")
        by_path = {
            "".join(text for _style, text in to_formatted_text(item.display)): item
            for item in completions
        }
        selected = by_path["docs/quick start.md"]
        self.assertEqual(selected.text, '"docs/quick start.md"')

        quoted = self.complete('Open @"docs/quick ')
        self.assertEqual([item.text for item in quoted], ['docs/quick start.md"'])

    def test_does_not_suggest_for_email_or_regular_text(self):
        self.assertEqual(self.complete("mail me@example.com"), [])
        self.assertEqual(self.complete("Review the main.py file"), [])

    def test_refresh_picks_up_new_project_files(self):
        (self.root / "new_module.py").write_text("pass\n", encoding="utf-8")
        self.completer.refresh()
        self.assertIn("new_module.py", [item.text for item in self.complete("@new_")])


if __name__ == "__main__":
    unittest.main()
