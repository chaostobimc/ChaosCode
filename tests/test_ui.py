import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from chaoscode.ui import print_response, styled


class TtyBuffer(io.StringIO):
    def isatty(self):
        return True


class UiTests(unittest.TestCase):
    def test_styled_uses_color_for_tty_and_respects_no_color(self):
        stream = TtyBuffer()
        with patch.dict(os.environ) as environment:
            environment.pop("NO_COLOR", None)
            environment["TERM"] = "xterm-256color"
            self.assertEqual(styled("OK", "32", stream), "\x1b[32mOK\x1b[0m")
            environment["NO_COLOR"] = "1"
            self.assertEqual(styled("OK", "32", stream), "OK")

    def test_print_mode_response_is_plain_for_pipes(self):
        output = io.StringIO()
        with redirect_stdout(output):
            print_response("## Header\n`code`", print_mode=True)
        self.assertEqual(output.getvalue(), "## Header\n`code`\n")

    def test_response_styles_headings_and_code_for_tty(self):
        output = TtyBuffer()
        with patch.dict(os.environ) as environment:
            environment.pop("NO_COLOR", None)
            environment["TERM"] = "xterm-256color"
            with redirect_stdout(output):
                print_response("## Header\n```py\nprint('ok')\n```")
        self.assertIn("\x1b[1;36m## Header\x1b[0m", output.getvalue())
        self.assertIn("\x1b[32mprint('ok')\x1b[0m", output.getvalue())


if __name__ == "__main__":
    unittest.main()
