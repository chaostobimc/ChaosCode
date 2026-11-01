import json
import tempfile
import unittest
from pathlib import Path

from chaoscode.agent import CodingAgent, extract_tool_call
from chaoscode.prompts import TOOL_TAG_CLOSE, TOOL_TAG_OPEN, build_system_prompt
from chaoscode.sessions import SessionStore


class FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def complete(self, prompt, *, thinking=True, search=False):
        self.prompts.append((prompt, thinking, search))
        return self.responses.pop(0)


def tool_call(name, arguments):
    return TOOL_TAG_OPEN + json.dumps({"name": name, "arguments": arguments}) + TOOL_TAG_CLOSE


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name) / "project"
        self.root.mkdir()
        (self.root / "hello.txt").write_text("hello from project\n", encoding="utf-8")
        self.store = SessionStore(Path(self.temp_dir.name) / "sessions")
        self.session = self.store.new(self.root)

    def test_tool_loop_reads_file_then_returns_answer(self):
        provider = FakeProvider(
            [
                tool_call("read_file", {"path": "hello.txt"}),
                "The project says hello from project.",
            ]
        )
        agent = CodingAgent(
            workspace=self.root,
            provider=provider,
            session=self.session,
            session_store=self.store,
            approve=lambda *_args: True,
            search=True,
        )
        result = agent.run_turn("What is in the greeting file?")
        self.assertEqual(result.tool_calls, 1)
        self.assertIn("hello from project", provider.prompts[1][0])
        self.assertTrue(provider.prompts[0][2])
        self.assertEqual(result.text, "The project says hello from project.")
        self.assertEqual(
            self.store.load(self.session.id, self.root).messages[-1]["role"], "assistant"
        )

    def test_invalid_protocol_is_reported_back_to_model(self):
        provider = FakeProvider(
            ["<chaoscode_tool>{bad json}</chaoscode_tool>", "I will answer without a tool."]
        )
        agent = CodingAgent(
            workspace=self.root,
            provider=provider,
            session=self.session,
            session_store=None,
            approve=lambda *_args: True,
        )
        result = agent.run_turn("Explain the task")
        self.assertEqual(result.text, "I will answer without a tool.")
        self.assertIn("Tool-Protokollfehler", provider.prompts[1][0])
        self.assertNotIn("bad json", provider.prompts[1][0])

    def test_repeated_protocol_failure_stops_without_spamming_model(self):
        provider = FakeProvider(
            [
                "<chaoscode_tool>{bad json}</chaoscode_tool>",
                "<chaoscode_tool>{still bad}</chaoscode_tool>",
                "This response should never be requested.",
            ]
        )
        agent = CodingAgent(
            workspace=self.root,
            provider=provider,
            session=self.session,
            session_store=None,
            approve=lambda *_args: True,
        )
        result = agent.run_turn("Create a file")
        self.assertIn("nicht lesbar", result.text)
        self.assertEqual(len(provider.prompts), 2)

    def test_selected_file_is_loaded_and_can_be_edited(self):
        provider = FakeProvider(
            [
                tool_call(
                    "edit_file",
                    {
                        "path": "hello.txt",
                        "old_text": "hello from project",
                        "new_text": "hello from ChaosCode",
                    },
                ),
                "Die markierte Datei wurde geändert.",
            ]
        )
        agent = CodingAgent(
            workspace=self.root,
            provider=provider,
            session=self.session,
            session_store=None,
            approve=lambda *_args: True,
        )
        result = agent.run_turn(
            "Ändere @hello.txt",
            selected_files=[("hello.txt", "hello from project\n")],
        )
        self.assertIn("File selected explicitly with @hello.txt", provider.prompts[0][0])
        self.assertIn("hello from project", provider.prompts[0][0])
        self.assertEqual(
            (self.root / "hello.txt").read_text(encoding="utf-8"), "hello from ChaosCode\n"
        )
        self.assertEqual(result.tool_calls, 2)

    def test_creates_requested_python_cli_file(self):
        content = """name = input("Wie heißt du? ").strip()
print(f"Hallo {name}")
"""
        provider = FakeProvider(
            [
                tool_call("write_file", {"path": "hallo.py", "content": content}),
                "Ich habe `hallo.py` angelegt.",
            ]
        )
        agent = CodingAgent(
            workspace=self.root,
            provider=provider,
            session=self.session,
            session_store=None,
            approve=lambda *_args: True,
        )
        result = agent.run_turn("Erstelle ein Python-CLI, das meinen Namen abfragt und Hallo sagt.")
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual((self.root / "hallo.py").read_text(encoding="utf-8"), content)

    def test_write_tool_still_requires_approval(self):
        provider = FakeProvider(
            [
                tool_call("write_file", {"path": "new.txt", "content": "new content"}),
                "The write was denied, so I did not change the file.",
            ]
        )
        agent = CodingAgent(
            workspace=self.root,
            provider=provider,
            session=self.session,
            session_store=None,
            approve=lambda *_args: False,
        )
        result = agent.run_turn("Create a new file")
        self.assertEqual(result.tool_calls, 1)
        self.assertFalse((self.root / "new.txt").exists())
        self.assertIn("denied", provider.prompts[1][0])

    def test_extract_tool_call_accepts_fences_and_short_preamble(self):
        block = (
            f"{TOOL_TAG_OPEN}\n"
            '{"name":"read_file","arguments":{"path":"src/main.py"}}\n'
            f"{TOOL_TAG_CLOSE}"
        )
        for response in (f"```xml\n{block}\n```", f"Ich prüfe die Datei.\n{block}\n"):
            with self.subTest(response=response):
                call, issue = extract_tool_call(response)
                self.assertIsNone(issue)
                self.assertEqual(call.name, "read_file")
                self.assertEqual(call.arguments, {"path": "src/main.py"})

    def test_extract_tool_call_validation(self):
        call, issue = extract_tool_call(tool_call("read_file", {"path": "x"}))
        self.assertIsNone(issue)
        self.assertEqual(call.name, "read_file")
        call, issue = extract_tool_call("<chaoscode_tool>{nope}</chaoscode_tool>")
        self.assertIsNone(call)
        self.assertIn("valid JSON", issue)
        self.assertEqual(extract_tool_call("plain answer"), (None, None))

    def test_system_prompt_contains_a_valid_tool_example(self):
        prompt = build_system_prompt(self.root)
        self.assertIn('{"name":"read_file","arguments":{"path":"src/main.py"}}', prompt)
        self.assertNotIn('"arguments":{...}', prompt)


if __name__ == "__main__":
    unittest.main()
