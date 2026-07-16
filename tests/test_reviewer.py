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
from review.reviewer import Reviewer, main, review_diff_file

FIXTURES = Path(__file__).parent / "sample_diffs"


def _settings(**overrides: Any) -> Settings:
    base = {
        "ollama_base_url": "http://10.28.81.52:11434",
        "ollama_model": "granite3.2:latest",
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
    assert (
        derive_status(
            [
                Issue(
                    severity=Severity.CRITICAL,
                    file="a.py",
                    line=1,
                    message="secret",
                    suggestion="use env",
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
    assert result.infrastructure_failure is False


def test_empty_diff_skips_llm() -> None:
    generate = MagicMock(return_value=json.dumps({"status": "FAIL", "score": 0, "summary": "x", "issues": []}))
    reviewer = Reviewer(settings=_settings(), llm_generate=generate)
    result = reviewer.review_diff_text((FIXTURES / "empty_diff.diff").read_text(encoding="utf-8"))
    assert result.status == Status.PASS
    assert result.score == 100
    assert "No reviewable changes" in result.summary
    generate.assert_not_called()


def test_lockfile_only_skips_llm_and_passes() -> None:
    generate = MagicMock(return_value=json.dumps({"status": "FAIL", "score": 0, "summary": "x", "issues": []}))
    reviewer = Reviewer(settings=_settings(), llm_generate=generate)
    result = reviewer.review_diff_text(
        (FIXTURES / "diff_with_lockfile_only.diff").read_text(encoding="utf-8")
    )
    assert result.status == Status.PASS
    assert result.score == 100
    generate.assert_not_called()


def test_sql_injection_fixture_fails_via_mocked_ollama() -> None:
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
                "suggestion": "Use bound parameters: text('... WHERE name = :q'), {\"q\": q}",
            }
        ],
    }
    reviewer = Reviewer(settings=_settings(), llm_generate=_mock_generate(payload))
    result = reviewer.review_diff_text(
        (FIXTURES / "sql_injection.diff").read_text(encoding="utf-8")
    )
    assert result.status == Status.FAIL
    assert result.issues[0].severity == Severity.CRITICAL
    assert "SQL" in result.issues[0].message or "sql" in result.issues[0].message.lower()


def test_hardcoded_secret_fixture_fails_via_mocked_ollama() -> None:
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
                "suggestion": "Load API_KEY from environment or a secret manager",
            }
        ],
    }
    reviewer = Reviewer(settings=_settings(), llm_generate=_mock_generate(payload))
    result = reviewer.review_diff_text(
        (FIXTURES / "hardcoded_secret.diff").read_text(encoding="utf-8")
    )
    assert result.status == Status.FAIL
    assert any(i.severity in {Severity.HIGH, Severity.CRITICAL} for i in result.issues)


def test_clean_code_fixture_passes_via_mocked_ollama() -> None:
    payload = {
        "status": "FAIL",
        "score": 40,
        "summary": "Healthy docstring and type hints",
        "issues": [
            {
                "severity": "LOW",
                "file": "app/health.py",
                "line": 8,
                "message": "Docstring is fine; no functional risk",
                "suggestion": "Optional: add readiness vs liveness split later",
            }
        ],
    }
    reviewer = Reviewer(settings=_settings(), llm_generate=_mock_generate(payload))
    result = reviewer.review_diff_text((FIXTURES / "clean_code.diff").read_text(encoding="utf-8"))
    assert result.status == Status.PASS
    assert result.score >= 50


def test_malformed_json_is_infrastructure_failure() -> None:
    reviewer = Reviewer(settings=_settings(), llm_generate=lambda _p: "not-json{")
    result = reviewer.review_diff_text((FIXTURES / "clean_code.diff").read_text(encoding="utf-8"))
    assert result.status == Status.FAIL
    assert result.infrastructure_failure is True
    assert "review infrastructure failed" in result.summary


def test_schema_invalid_json_is_infrastructure_failure() -> None:
    reviewer = Reviewer(
        settings=_settings(),
        llm_generate=_mock_generate({"status": "PASS", "score": "hot", "summary": "x", "issues": []}),
    )
    result = reviewer.review_diff_text((FIXTURES / "clean_code.diff").read_text(encoding="utf-8"))
    assert result.status == Status.FAIL
    assert result.infrastructure_failure is True
    assert "review infrastructure failed" in result.summary


def test_missing_ollama_base_url_is_infrastructure_failure() -> None:
    reviewer = Reviewer(settings=_settings(ollama_base_url=""), llm_generate=None)
    result = reviewer.review_diff_text((FIXTURES / "clean_code.diff").read_text(encoding="utf-8"))
    assert result.status == Status.FAIL
    assert result.infrastructure_failure is True
    assert "OLLAMA_BASE_URL" in result.summary or "review infrastructure failed" in result.summary


def test_oversized_single_file_fails_clearly_without_truncation() -> None:
    lines = ["+x = 1"] * 50
    body = "\n".join(
        [
            "diff --git a/big.py b/big.py",
            "index 1111111..2222222 100644",
            "--- a/big.py",
            "+++ b/big.py",
            "@@ -1,0 +1,50 @@",
            *lines,
            "",
        ]
    )
    generate = MagicMock(return_value=json.dumps({"status": "PASS", "score": 100, "summary": "nope", "issues": []}))
    reviewer = Reviewer(settings=_settings(max_diff_lines=20), llm_generate=generate)
    result = reviewer.review_diff_text(body)
    assert result.status == Status.FAIL
    assert "diff too large to review safely" in result.summary
    generate.assert_not_called()


def test_infrastructure_failure_helper_message() -> None:
    result = infrastructure_failure("timeout talking to Ollama")
    assert result.status == Status.FAIL
    assert result.score == 0
    assert result.summary.startswith("review infrastructure failed:")


def test_cli_json_exit_code_for_lockfile_only(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "--diff-file",
            str(FIXTURES / "diff_with_lockfile_only.diff"),
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert payload["score"] == 100


def test_prompt_contains_only_filtered_paths() -> None:
    from review.prompt import build_user_prompt

    parsed = parse_diff((FIXTURES / "mixed_ignores.diff").read_text(encoding="utf-8"))
    prompt = build_user_prompt(parsed)
    assert "src/app.js" in prompt
    assert "node_modules/leftpad" not in prompt
    assert "logo.png" not in prompt
    assert ".venv/" not in prompt
