import io
import unittest
from contextlib import redirect_stdout

from chaoscode.cli import build_parser, main


class CliTests(unittest.TestCase):
    def test_help_parser_options(self):
        options = build_parser().parse_args(["--resume", "--search", "repair this"])
        self.assertTrue(options.resume)
        self.assertTrue(options.search)
        self.assertEqual(options.prompt, ["repair this"])

    def test_version_command(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(["version"])
        self.assertEqual(result, 0)
        self.assertIn("ChaosCode", output.getvalue())

    def test_print_without_prompt_and_tty_stdin_is_rejected_early(self):
        # Avoid any DeepSeek credentials or network/dependency initialization.
        import sys
        from contextlib import redirect_stderr
        from unittest.mock import patch

        with patch.object(sys.stdin, "isatty", return_value=True), redirect_stderr(io.StringIO()):
            result = main(["--print"])
        self.assertEqual(result, 2)


if __name__ == "__main__":
    unittest.main()
