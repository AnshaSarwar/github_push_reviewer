"""HTTP client for Ollama generate/chat API."""

from __future__ import annotations

import requests


class OllamaError(RuntimeError):
    """Raised when Ollama returns an error or unexpected payload."""


def call_ollama(
    *,
    base_url: str,
    model: str,
    system: str,
    user: str,
    timeout: float,
) -> str:
    """
    Call Ollama ``/api/chat`` and return assistant message content.

    Uses ``format: json`` so the model returns structured review output.
    """
    if not base_url.strip():
        raise OllamaError("OLLAMA_BASE_URL is not set")

    url = f"{base_url.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "format": "json",
    }

    try:
        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise OllamaError(f"Ollama request failed: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise OllamaError("Ollama returned non-JSON response") from exc

    message = data.get("message") or {}
    content = message.get("content")
    if not content or not str(content).strip():
        raise OllamaError("Ollama returned empty content")
    return str(content).strip()
