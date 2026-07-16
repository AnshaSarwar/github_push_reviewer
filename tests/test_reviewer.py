"""Tests for reviewer orchestration, fail-closed behavior, and fixture verdicts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from review.config import Settings
from review.diff_parser import ParsedDiff, parse_diff
from review.models import (
    Issue,
    LLMReviewPayload,
    Severity,
    Status,
    derive_status,
    finalize_from_llm,
    infrastructure_failure,
)
from review.reviewer import Reviewer, main

FIXTURES = Path(__file__).parent / "sample_diffs"


def _settings(**overrides: Any) -> Settings:
    base = {
        "openai_api_key": "test-key",
        "openai_model": "gpt-4o-mini",
        "max_diff_lines": 2000,
        "llm_timeout_seconds": 5.0,
        "llm_max_retries": 0,
        "log_level": "WARNING",
    }
    base.update(overrides)
    return Settings(**base)


def _mock_generate(payload: dict[str, Any]):
    def _fn(_parsed: ParsedDiff) -> str:
        return json.dumps(payload)

    return _fn


def _mock_client(payload: dict[str, Any]) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = json.dumps(payload)
    response = MagicMock()
    response.choices = [choice]
    client.chat.completions.create.return_value = response
    return client


def test_derive_status_forces_fail_on_high_or_critical() -> None:
    assert (
        derive_status(
            [Issue(severity=Severity.LOW, file="a.py", line=1, message="nits", suggestion="")]
        )
        == Status.PASS
    )
    assert (
        derive_status(
            [
                Issue(
                    severity=Severity.HIGH,
                    file="a.py",
                    line=1,
                    message="sql injection",
                    suggestion="use bound params",
                )
            ]
        )
        == Status.FAIL
    )


def test_finalize_ignores_model_status_when_severities_conflict() -> None:
    payload = LLMReviewPayload(
        status="PASS",
        score=95,
        summary="looks fine",
        issues=[
            Issue(
                severity=Severity.CRITICAL,
                file="app/config.py",
                line=6,
                message="hardcoded secret",
                suggestion="load from env",
            )
        ],
    )
    result = finalize_from_llm(payload)
    assert result.status == Status.FAIL
    assert result.score <= 49


def test_empty_diff_skips_llm() -> None:
    generate = MagicMock(return_value=json.dumps({"status": "FAIL", "score": 0, "summary": "x", "issues": []}))
    reviewer = Reviewer(settings=_settings(), llm_generate=generate)
    result = reviewer.review_diff_text((FIXTURES / "empty_diff.diff").read_text(encoding="utf-8"))
    assert result.status == Status.PASS
    assert result.score == 100
    generate.assert_not_called()


def test_sql_injection_fixture_fails_via_mocked_openai() -> None:
    payload = {
        "status": "PASS",
        "score": 88,
        "summary": "Introduces string-interpolated SQL",
        "issues": [
            {
                "severity": "CRITICAL",
                "file": "app/users.py",
                "line": 11,
                "message": "SQL injection via f-string interpolation into text()",
                "suggestion": "Use bound parameters",
            }
        ],
    }
    reviewer = Reviewer(settings=_settings(), llm_generate=_mock_generate(payload))
    result = reviewer.review_diff_text((FIXTURES / "sql_injection.diff").read_text(encoding="utf-8"))
    assert result.status == Status.FAIL
    assert result.issues[0].severity == Severity.CRITICAL


def test_hardcoded_secret_fixture_fails_via_mocked_openai() -> None:
    payload = {
        "status": "FAIL",
        "score": 10,
        "summary": "Hardcoded API key in source code",
        "issues": [
            {
                "severity": "CRITICAL",
                "file": "app/config.py",
                "line": 6,
                "message": "Hardcoded API key committed to source",
                "suggestion": "Load API_KEY from environment",
            }
        ],
    }
    reviewer = Reviewer(settings=_settings(), llm_generate=_mock_generate(payload))
    result = reviewer.review_diff_text((FIXTURES / "hardcoded_secret.diff").read_text(encoding="utf-8"))
    assert result.status == Status.FAIL


def test_clean_code_fixture_passes_via_mocked_openai() -> None:
    payload = {
        "status": "FAIL",
        "score": 40,
        "summary": "Healthy docstring",
        "issues": [
            {
                "severity": "LOW",
                "file": "app/health.py",
                "line": 8,
                "message": "Docstring is fine",
                "suggestion": "Optional improvement",
            }
        ],
    }
    reviewer = Reviewer(settings=_settings(), llm_generate=_mock_generate(payload))
    result = reviewer.review_diff_text((FIXTURES / "clean_code.diff").read_text(encoding="utf-8"))
    assert result.status == Status.PASS


def test_malformed_json_is_infrastructure_failure() -> None:
    reviewer = Reviewer(settings=_settings(), llm_generate=lambda _p: "not-json{")
    result = reviewer.review_diff_text((FIXTURES / "clean_code.diff").read_text(encoding="utf-8"))
    assert result.infrastructure_failure is True


def test_missing_api_key_is_infrastructure_failure() -> None:
    reviewer = Reviewer(settings=_settings(openai_api_key=None), llm_generate=None)
    result = reviewer.review_diff_text((FIXTURES / "clean_code.diff").read_text(encoding="utf-8"))
    assert result.status == Status.FAIL
    assert result.infrastructure_failure is True


def test_oversized_single_file_fails_without_llm_call() -> None:
    body = "\n".join(
        [
            "diff --git a/big.py b/big.py",
            "index 1111111..2222222 100644",
            "--- a/big.py",
            "+++ b/big.py",
            "@@ -1,0 +1,50 @@",
            *["+x = 1"] * 50,
            "",
        ]
    )
    generate = MagicMock()
    reviewer = Reviewer(settings=_settings(max_diff_lines=20), llm_generate=generate)
    result = reviewer.review_diff_text(body)
    assert "diff too large" in result.summary
    generate.assert_not_called()


def test_cli_json_exit_code_for_lockfile_only(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--diff-file", str(FIXTURES / "diff_with_lockfile_only.diff"), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
