"""
AI API connector for the Supervisor.

Uses stdlib urllib only — no third-party HTTP client required. The
blocking request runs in a worker thread so ``think()`` stays awaitable.

Provider is read from config so the backend can be swapped without
touching the analyzer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TIMEOUT_S = 120


class AIConnectorError(RuntimeError):
    """Raised when the provider request fails or returns no usable text."""


class AIConnector:
    """Single-turn text completion against a configured AI provider."""

    def __init__(self, config: dict | None = None) -> None:
        config = config or {}
        self.provider = config.get("provider", "anthropic")
        self.model = config.get("model", DEFAULT_MODEL)
        self.max_tokens = int(config.get("max_tokens", DEFAULT_MAX_TOKENS))
        self.timeout_s = int(config.get("timeout_s", DEFAULT_TIMEOUT_S))
        self.base_url = config.get("base_url", "https://api.anthropic.com").rstrip("/")
        # Env var wins when the config file leaves the key blank, so the
        # key never has to be written to disk.
        self.api_key = config.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------

    @classmethod
    def from_file(cls, path: str | None = None) -> AIConnector:
        """Build a connector from supervisor_data/api_config.json."""
        if path is None:
            path = os.path.join(
                os.path.dirname(__file__), "..", "..",
                "supervisor_data", "api_config.json",
            )
        path = os.path.abspath(path)

        config: dict = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    config = json.load(fh)
            except (json.JSONDecodeError, OSError) as exc:
                log.error("api_config read failed for %s: %s", path, exc)
        else:
            log.info("api_config not found at %s — using defaults/env", path)

        return cls(config)

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    async def think(self, system_prompt: str, user_message: str) -> str:
        """Send one system+user turn and return the model's text reply."""
        if self.provider != "anthropic":
            raise AIConnectorError(f"Unsupported provider: {self.provider}")
        if not self.api_key:
            raise AIConnectorError(
                "No API key — set api_key in api_config.json or "
                "the ANTHROPIC_API_KEY environment variable"
            )
        return await asyncio.to_thread(
            self._request_anthropic, system_prompt, user_message,
        )

    def _request_anthropic(self, system_prompt: str, user_message: str) -> str:
        payload = json.dumps({
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }).encode("utf-8")

        url = f"{self.base_url}/v1/messages"
        request = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "content-type": "application/json",
                "anthropic-version": ANTHROPIC_VERSION,
                "x-api-key": self.api_key,
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                body = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise AIConnectorError(f"HTTP {exc.code} from provider: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise AIConnectorError(f"Request to provider failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise AIConnectorError("Provider returned invalid JSON") from exc

        return _extract_text(body)


def _extract_text(body: dict) -> str:
    """Join the text blocks of an Anthropic messages response."""
    blocks = body.get("content")
    if not isinstance(blocks, list):
        raise AIConnectorError("Provider response had no content blocks")

    parts = [
        block["text"] for block in blocks
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    if not parts:
        raise AIConnectorError("Provider response contained no text")
    return "\n".join(parts)
