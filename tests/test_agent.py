import json
import tempfile
import unittest
from pathlib import Path

from chaoscode.agent import CodingAgent, extract_tool_call
from chaoscode.prompts import TOOL_TAG_CLOSE, TOOL_TAG_OPEN
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

    def test_extract_tool_call_validation(self):
        call, issue = extract_tool_call(tool_call("read_file", {"path": "x"}))
        self.assertIsNone(issue)
        self.assertEqual(call.name, "read_file")
        call, issue = extract_tool_call("<chaoscode_tool>{nope}</chaoscode_tool>")
        self.assertIsNone(call)
        self.assertIn("valid JSON", issue)
        self.assertEqual(extract_tool_call("plain answer"), (None, None))


if __name__ == "__main__":
    unittest.main()
