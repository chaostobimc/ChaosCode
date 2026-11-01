"""Client for the unofficial DeepSeek web-chat API used by deepseek4free.

This intentionally uses DeepSeek's private web interface rather than its paid
OpenAI-compatible API. It does not automate browsers or attempt Cloudflare or
CAPTCHA bypasses.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

try:
    from curl_cffi import requests
except ImportError as exc:  # pragma: no cover - installation error path
    raise RuntimeError(
        "ChaosCode's DeepSeek transport is missing. Reinstall with `pipx install --force .`."
    ) from exc

from ._vendor.deepseek4free.pow import DeepSeekPOW

BASE_URL = "https://chat.deepseek.com/api/v0"
APP_VERSION = "2.0.0"
MAX_RESPONSE_CHARS = 2_000_000


class ProviderError(RuntimeError):
    """Base class for errors returned by the selected AI backend."""


class AuthenticationError(ProviderError):
    """The saved DeepSeek web session is invalid or expired."""


class RateLimitError(ProviderError):
    """DeepSeek is rate limiting the web session."""


class CloudflareError(ProviderError):
    """The service returned an anti-bot challenge that requires user action."""


class DeepSeek4FreeClient:
    """Streaming client for the current DeepSeek4Free web-chat wire format."""

    def __init__(self, auth_token: str, timeout: int = 180) -> None:
        if not isinstance(auth_token, str) or not auth_token.strip():
            raise AuthenticationError("A DeepSeek web-session token is required.")
        self._auth_token = auth_token.strip()
        self._timeout = max(30, min(int(timeout), 600))
        try:
            self._pow = DeepSeekPOW()
            self._session = requests.Session(impersonate="chrome120")
            self._session.headers.update(self._headers())
        except Exception as exc:
            raise ProviderError(f"Could not initialize DeepSeek4Free transport: {exc}") from exc

    def _headers(self, pow_response: str | None = None) -> dict[str, str]:
        headers = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9,de;q=0.8",
            "authorization": f"Bearer {self._auth_token}",
            "content-type": "application/json",
            "origin": "https://chat.deepseek.com",
            "referer": "https://chat.deepseek.com/",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
            ),
            "x-app-version": APP_VERSION,
            "x-client-bundle-id": "com.deepseek.chat",
            "x-client-locale": "en_US",
            "x-client-platform": "web",
            "x-client-version": APP_VERSION,
        }
        if pow_response:
            headers["x-ds-pow-response"] = pow_response
        return headers

    @staticmethod
    def _is_cloudflare(response: Any) -> bool:
        headers = getattr(response, "headers", {}) or {}
        content_type = str(headers.get("content-type", headers.get("Content-Type", ""))).lower()
        if "text/html" in content_type and getattr(response, "status_code", 0) in (200, 403, 503):
            return True
        body = (getattr(response, "text", "") or "")[:4_000].lower()
        return "<!doctype html" in body or "just a moment" in body or "cf-chl-" in body

    def _raise_for_response(self, response: Any) -> None:
        if response.status_code == 401:
            raise AuthenticationError(
                "DeepSeek rejected the web-session token. Run `chaoscode auth login` with a fresh token."
            )
        if response.status_code == 429:
            raise RateLimitError("DeepSeek rate-limited this session. Wait and try again later.")
        if response.status_code in (403, 503) and self._is_cloudflare(response):
            raise CloudflareError(
                "DeepSeek returned a Cloudflare challenge. ChaosCode does not bypass it; "
                "try again later or use the official DeepSeek website."
            )
        if response.status_code >= 500:
            raise ProviderError(
                f"DeepSeek server error (HTTP {response.status_code}). Try again later."
            )
        if response.status_code != 200:
            raise ProviderError(f"DeepSeek request failed (HTTP {response.status_code}).")
        if self._is_cloudflare(response):
            raise CloudflareError(
                "DeepSeek returned a Cloudflare challenge. ChaosCode does not bypass it; "
                "try again later or use the official DeepSeek website."
            )

    @staticmethod
    def _response_json(response: Any) -> dict[str, Any]:
        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ProviderError("DeepSeek returned an invalid JSON response.") from exc
        if not isinstance(data, dict):
            raise ProviderError("DeepSeek returned an unexpected response format.")
        if data.get("error"):
            detail = data["error"]
            if isinstance(detail, dict):
                detail = detail.get("message", "request failed")
            raise ProviderError(f"DeepSeek API error: {str(detail)[:500]}")
        return data

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._session.post(
                f"{BASE_URL}{endpoint}", json=payload, timeout=self._timeout
            )
        except requests.exceptions.RequestException as exc:
            raise ProviderError(f"Could not reach DeepSeek: {exc}") from exc
        try:
            self._raise_for_response(response)
            data = self._response_json(response)
            if data.get("code") not in (None, 0, "0"):
                message = data.get("msg") or data.get("message") or "request failed"
                if data.get("code") in (401, 40101, "401", "40101"):
                    raise AuthenticationError("DeepSeek rejected the web-session token.")
                raise ProviderError(f"DeepSeek API error: {str(message)[:500]}")
            return data
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _biz_data(response: dict[str, Any]) -> dict[str, Any]:
        data = response.get("data")
        if isinstance(data, dict) and isinstance(data.get("biz_data"), dict):
            return data["biz_data"]
        return {}

    def _create_chat_session(self) -> str:
        # The current web API nests the ID under biz_data.chat_session.id.
        # Keep the earlier shapes as fallbacks for older DeepSeek deployments.
        response = self._post_json("/chat_session/create", {})
        biz = self._biz_data(response)
        session = biz.get("chat_session") or biz.get("session") or biz
        if isinstance(session, dict):
            session_id = (
                session.get("id") or session.get("chat_session_id") or session.get("session_id")
            )
        else:
            session_id = session if isinstance(session, (str, int)) else None
        if not session_id:
            data = response.get("data")
            data_keys = sorted(data.keys()) if isinstance(data, dict) else []
            biz_keys = sorted(biz.keys())
            top_keys = sorted(response.keys())
            code = response.get("code", "<missing>")
            raise ProviderError(
                "DeepSeek accepted the request but returned an unrecognized chat-session shape. "
                "Expected data.biz_data.chat_session.id (or the older data.biz_data.id); "
                f"received code={code!r}, top-level keys={top_keys}, "
                f"data keys={data_keys}, biz_data keys={biz_keys}. "
                "The private DeepSeek API may have changed. Update ChaosCode and try again."
            )
        return str(session_id)

    def _get_pow_challenge(self) -> dict[str, Any]:
        response = self._post_json(
            "/chat/create_pow_challenge",
            {"target_path": "/api/v0/chat/completion"},
        )
        biz = self._biz_data(response)
        challenge = biz.get("challenge") or biz
        if not isinstance(challenge, dict) or not challenge.get("challenge"):
            raise ProviderError("DeepSeek returned an invalid proof-of-work challenge.")
        return challenge

    @staticmethod
    def _iter_sse_content(response: Any) -> Iterator[str]:
        """Yield user-facing text from current patch-frame or legacy OpenAI SSE."""
        total_chars = 0
        active_path: str | None = None
        seeded_response = False

        def emit_text(content: Any) -> str | None:
            nonlocal total_chars
            if not isinstance(content, str) or not content:
                return None
            total_chars += len(content)
            if total_chars > MAX_RESPONSE_CHARS:
                raise ProviderError("DeepSeek response exceeded the 2 MB safety limit.")
            return content

        def parse_frame(frame: Any, inherited_path: str | None = None) -> Iterator[str]:
            nonlocal active_path, seeded_response
            if not isinstance(frame, dict):
                return
            if frame.get("type") == "error" or frame.get("error"):
                detail_value = frame.get("content", frame.get("error", "DeepSeek stream error"))
                if isinstance(detail_value, dict):
                    detail_value = detail_value.get("message", detail_value)
                detail = str(detail_value)[:500]
                if frame.get("finish_reason") == "rate_limit_reached":
                    raise RateLimitError(detail)
                raise ProviderError(f"DeepSeek stream error: {detail}")

            # Older xtekky/deepseek4free SSE schema.
            choices = frame.get("choices")
            if isinstance(choices, list) and choices:
                choice = choices[0]
                delta = choice.get("delta", {}) if isinstance(choice, dict) else {}
                if isinstance(delta, dict):
                    kind = delta.get("type", "")
                    content = delta.get("content", "")
                    if kind not in {"thinking", "reasoning", "reasoning_content"}:
                        emitted = emit_text(content)
                        if emitted:
                            yield emitted
                return

            path = frame.get("p", inherited_path or active_path or "")
            operation = frame.get("o")
            value = frame.get("v")
            if operation == "BATCH" and isinstance(value, list):
                for item in value:
                    yield from parse_frame(item, str(path) if path else None)
                return

            # The current API starts with a response snapshot, then streams patches.
            if isinstance(value, dict) and isinstance(value.get("response"), dict):
                fragments = value["response"].get("fragments", [])
                for fragment in fragments if isinstance(fragments, list) else []:
                    if not isinstance(fragment, dict) or fragment.get("type") == "THINK":
                        continue
                    emitted = emit_text(fragment.get("content"))
                    if emitted and not seeded_response:
                        seeded_response = True
                        yield emitted
                return

            if "p" in frame:
                active_path = str(frame["p"])
                path = active_path
            if isinstance(value, str) and path:
                is_thinking = "thinking" in str(path).lower()
                is_content = str(path).endswith("content")
                if (operation == "APPEND" or "p" not in frame) and is_content and not is_thinking:
                    emitted = emit_text(value)
                    if emitted:
                        yield emitted

        for line in response.iter_lines():
            if not line:
                continue
            if isinstance(line, bytes):
                line = line.decode("utf-8", "replace")
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data_line = line[5:].strip()
            if data_line == "[DONE]":
                break
            try:
                payload = json.loads(data_line)
            except json.JSONDecodeError:
                # Some servers send non-JSON keep-alive data frames.
                continue
            yield from parse_frame(payload)

    def complete(self, prompt: str, *, thinking: bool = True, search: bool = False) -> str:
        """Send one prompt and return the model's final text.

        Each agent request gets a fresh remote chat session. ChaosCode includes
        its bounded local conversation history in the prompt, so it does not
        depend on undocumented remote thread/message identifiers.
        """
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        remote_session_id = self._create_chat_session()
        challenge = self._get_pow_challenge()
        try:
            pow_response = self._pow.solve_challenge(challenge)
        except Exception as exc:
            raise ProviderError(
                f"Could not solve DeepSeek's proof-of-work challenge: {exc}"
            ) from exc
        payload = {
            "chat_session_id": remote_session_id,
            "parent_message_id": None,
            "prompt": prompt,
            "ref_file_ids": [],
            "thinking_enabled": bool(thinking),
            "search_enabled": bool(search),
            "action": None,
            "preempt": False,
            "model_type": "default",
        }
        try:
            response = self._session.post(
                f"{BASE_URL}/chat/completion",
                headers=self._headers(pow_response),
                json=payload,
                stream=True,
                timeout=self._timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise ProviderError(f"Could not reach DeepSeek: {exc}") from exc
        try:
            self._raise_for_response(response)
            result = "".join(self._iter_sse_content(response))
        except requests.exceptions.RequestException as exc:
            raise ProviderError(f"DeepSeek stream disconnected: {exc}") from exc
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        if not result.strip():
            raise ProviderError(
                "DeepSeek returned an empty answer. Try again or check the web session."
            )
        return result

    def close(self) -> None:
        close = getattr(self._session, "close", None)
        if callable(close):
            close()
