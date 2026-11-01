import json
import unittest

from chaoscode.provider import CloudflareError, DeepSeek4FreeClient


class FakeResponse:
    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200
        self.headers = {"content-type": "text/event-stream"}
        self.text = ""
        self.closed = False

    def iter_lines(self):
        yield from self._lines

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.call = None
        self.closed = False

    def post(self, url, **kwargs):
        self.call = (url, kwargs)
        return self.response

    def close(self):
        self.closed = True


class FakeJsonResponse(FakeResponse):
    def __init__(self, data):
        super().__init__([])
        self.headers = {"content-type": "application/json"}
        self._json = data

    def json(self):
        return self._json


class FakeSequenceSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class FakePow:
    def solve_challenge(self, challenge):
        self.challenge = challenge
        return "proof"


def sse(payload):
    return "data: " + json.dumps(payload)


class ProviderTests(unittest.TestCase):
    def test_sse_reader_ignores_reasoning_and_collects_answer(self):
        response = FakeResponse(
            [
                sse(
                    {"choices": [{"delta": {"content": "private reasoning", "type": "thinking"}}]}
                ).encode(),
                sse({"choices": [{"delta": {"content": "Hello", "type": "text"}}]}).encode(),
                b"data: [DONE]",
            ]
        )
        self.assertEqual("".join(DeepSeek4FreeClient._iter_sse_content(response)), "Hello")

    def test_complete_builds_expected_payload_and_closes_response(self):
        response = FakeResponse(
            [
                sse(
                    {"choices": [{"delta": {"content": "Task complete", "type": "text"}}]}
                ).encode(),
                b"data: [DONE]",
            ]
        )
        client = object.__new__(DeepSeek4FreeClient)
        client._auth_token = "dummy-secret"
        client._timeout = 60
        client._pow = FakePow()
        client._session = FakeSession(response)
        client._create_chat_session = lambda: "remote-session"
        client._get_pow_challenge = lambda: {"challenge": "value"}
        result = client.complete("prompt", thinking=False, search=True)
        self.assertEqual(result, "Task complete")
        url, kwargs = client._session.call
        self.assertTrue(url.endswith("/chat/completion"))
        self.assertEqual(kwargs["json"]["chat_session_id"], "remote-session")
        self.assertFalse(kwargs["json"]["thinking_enabled"])
        self.assertTrue(kwargs["json"]["search_enabled"])
        self.assertEqual(kwargs["headers"]["x-ds-pow-response"], "proof")
        self.assertTrue(response.closed)

    def test_complete_performs_session_challenge_and_stream_requests(self):
        final = FakeResponse(
            [
                sse({"choices": [{"delta": {"content": "Works", "type": "text"}}]}).encode(),
                b"data: [DONE]",
            ]
        )
        client = object.__new__(DeepSeek4FreeClient)
        client._auth_token = "dummy-secret"
        client._timeout = 60
        client._pow = FakePow()
        client._session = FakeSequenceSession(
            [
                FakeJsonResponse({"code": 0, "data": {"biz_data": {"id": "session-1"}}}),
                FakeJsonResponse(
                    {
                        "code": 0,
                        "data": {
                            "biz_data": {
                                "challenge": {
                                    "algorithm": "sha3",
                                    "challenge": "abc",
                                    "salt": "salt",
                                    "difficulty": 1,
                                    "expire_at": 42,
                                    "signature": "sig",
                                    "target_path": "/api/v0/chat/completion",
                                }
                            }
                        },
                    }
                ),
                final,
            ]
        )
        result = client.complete("prompt", thinking=True, search=False)
        self.assertEqual(result, "Works")
        urls = [call[0] for call in client._session.calls]
        self.assertTrue(urls[0].endswith("/chat_session/create"))
        self.assertTrue(urls[1].endswith("/chat/create_pow_challenge"))
        self.assertTrue(urls[2].endswith("/chat/completion"))
        self.assertEqual(client._session.calls[0][1]["json"], {"character_id": None})
        self.assertEqual(
            client._session.calls[1][1]["json"]["target_path"], "/api/v0/chat/completion"
        )
        self.assertEqual(client._session.calls[2][1]["json"]["chat_session_id"], "session-1")

    def test_cloudflare_challenge_is_reported_without_bypass(self):
        response = FakeResponse([])
        response.status_code = 403
        response.headers = {"content-type": "text/html"}
        client = object.__new__(DeepSeek4FreeClient)
        with self.assertRaisesRegex(CloudflareError, "does not bypass"):
            client._raise_for_response(response)

    def test_pow_wasm_is_bundled_and_runs(self):
        from chaoscode._vendor.deepseek4free.pow import DeepSeekPOW

        proof = DeepSeekPOW().solve_challenge(
            {
                "algorithm": "DeepSeekHashV1",
                "challenge": "test",
                "salt": "salt",
                "difficulty": 1,
                "expire_at": 2_000_000_000,
                "signature": "signature",
                "target_path": "/api/v0/chat/completion",
            }
        )
        self.assertTrue(proof)
        self.assertIn('"algorithm"', __import__("base64").b64decode(proof).decode())


if __name__ == "__main__":
    unittest.main()
