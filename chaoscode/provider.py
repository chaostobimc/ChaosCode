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
APP_VERSION = "20241129.1"
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
    """Minimal, streaming client compatible with xtekky/deepseek4free's API."""

    def __init__(self, auth_token: str, timeout: int = 180) -> None:
        if not isinstance(auth_token, str) or not auth_token.strip():
            raise AuthenticationError("A DeepSeek web-session token is required.")
        self._auth_token = auth_token.strip()
        self._timeout = max(30, min(int(timeout), 600))
        try:
            self._pow = DeepSeekPOW()
            self._session = requests.Session(impersonate="chrome")
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
            "x-app-version": APP_VERSION,
            "x-client-locale": "en_US",
            "x-client-platform": "web",
            "x-client-version": "1.0.0-always",
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
            if data.get("code") not in (None, 0):
                message = data.get("msg") or data.get("message") or "request failed"
                if data.get("code") in (401, 40101):
                    raise AuthenticationError("DeepSeek rejected the web-session token.")
                raise ProviderError(f"DeepSeek API error: {str(message)[:500]}")
            return data
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def _create_chat_session(self) -> str:
        response = self._post_json("/chat_session/create", {"character_id": None})
        try:
            session_id = response["data"]["biz_data"]["id"]
        except (KeyError, TypeError) as exc:
            raise ProviderError("DeepSeek returned an invalid chat-session response.") from exc
        if not isinstance(session_id, str) or not session_id:
            raise ProviderError("DeepSeek returned an empty chat-session ID.")
        return session_id

    def _get_pow_challenge(self) -> dict[str, Any]:
        response = self._post_json(
            "/chat/create_pow_challenge",
            {"target_path": "/api/v0/chat/completion"},
        )
        try:
            challenge = response["data"]["biz_data"]["challenge"]
        except (KeyError, TypeError) as exc:
            raise ProviderError("DeepSeek returned an invalid proof-of-work challenge.") from exc
        if not isinstance(challenge, dict):
            raise ProviderError("DeepSeek returned an invalid proof-of-work challenge.")
        return challenge

    @staticmethod
    def _iter_sse_content(response: Any) -> Iterator[str]:
        total_chars = 0
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
            except json.JSONDecodeError as exc:
                raise ProviderError("DeepSeek sent a malformed streaming event.") from exc
            if not isinstance(payload, dict):
                continue
            if payload.get("error"):
                detail = payload["error"]
                if isinstance(detail, dict):
                    detail = detail.get("message", "request failed")
                raise ProviderError(f"DeepSeek stream error: {str(detail)[:500]}")
            choices = payload.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta", {})
            if not isinstance(delta, dict):
                continue
            content = delta.get("content", "")
            if isinstance(content, str) and content:
                total_chars += len(content)
                if total_chars > MAX_RESPONSE_CHARS:
                    raise ProviderError("DeepSeek response exceeded the 2 MB safety limit.")
                # Reasoning chunks are deliberately not surfaced to the user/agent history.
                kind = delta.get("type")
                if kind not in {"thinking", "reasoning", "reasoning_content"}:
                    yield content

    def complete(self, prompt: str, *, thinking: bool = True, search: bool = False) -> str:
        """Send one prompt and return the model's final text.

        Each request gets a fresh remote chat session. ChaosCode includes its
        bounded local conversation history in the prompt because the legacy
        deepseek4free wrapper does not reliably expose parent message IDs.
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
