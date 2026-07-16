"""Tests for Ollama HTTP client."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from review.ollama_client import OllamaError, call_ollama


def test_call_ollama_returns_message_content() -> None:
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "message": {"role": "assistant", "content": '{"status":"PASS","score":100,"summary":"ok","issues":[]}'}
    }
    mock_response.raise_for_status = MagicMock()

    with patch("review.ollama_client.requests.post", return_value=mock_response) as post:
        content = call_ollama(
            base_url="http://10.28.81.52:11434",
            model="granite3.2:latest",
            system="sys",
            user="usr",
            timeout=30.0,
        )

    assert "PASS" in content
    post.assert_called_once()
    payload = post.call_args.kwargs["json"]
    assert payload["model"] == "granite3.2:latest"
    assert payload["stream"] is False
    assert payload["format"] == "json"


def test_call_ollama_empty_base_url_raises() -> None:
    with pytest.raises(OllamaError, match="OLLAMA_BASE_URL"):
        call_ollama(base_url="  ", model="granite3.2:latest", system="s", user="u", timeout=5.0)


def test_call_ollama_http_error_raises() -> None:
    with patch(
        "review.ollama_client.requests.post",
        side_effect=requests.ConnectionError("refused"),
    ):
        with pytest.raises(OllamaError, match="Ollama request failed"):
            call_ollama(
                base_url="http://10.28.81.52:11434",
                model="granite3.2:latest",
                system="s",
                user="u",
                timeout=5.0,
            )


def test_call_ollama_empty_content_raises() -> None:
    mock_response = MagicMock()
    mock_response.json.return_value = {"message": {"content": ""}}
    mock_response.raise_for_status = MagicMock()

    with patch("review.ollama_client.requests.post", return_value=mock_response):
        with pytest.raises(OllamaError, match="empty content"):
            call_ollama(
                base_url="http://10.28.81.52:11434",
                model="granite3.2:latest",
                system="s",
                user="u",
                timeout=5.0,
            )
