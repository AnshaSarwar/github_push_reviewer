"""Tests for Phase 2 CI helper scripts (no live GitHub/Ollama required)."""

from __future__ import annotations

import json
from pathlib import Path

from review.models import Issue, ReviewResult, Severity, Status
from scripts.publish_review import format_pr_comment
from scripts.run_ai_review import to_decision_payload

FIXTURES = Path(__file__).parent / "sample_diffs"


def test_to_decision_payload_maps_status_and_comments() -> None:
    result = ReviewResult(
        status=Status.FAIL,
        score=20,
        summary="Critical issues detected.",
        issues=[
            Issue(
                severity=Severity.CRITICAL,
                file="app/users.py",
                line=11,
                message="Possible SQL Injection.",
                suggestion="Use bound parameters.",
            )
        ],
    )
    payload = to_decision_payload(result)
    assert payload["decision"] == "FAIL"
    assert payload["summary"] == "Critical issues detected."
    assert any("SQL Injection" in c for c in payload["comments"])


def test_format_pr_comment_includes_decision() -> None:
    body = format_pr_comment(
        {
            "decision": "PASS",
            "score": 95,
            "summary": "Looks good.",
            "comments": ["Good input validation."],
            "infrastructure_failure": False,
        }
    )
    assert "`PASS`" in body
    assert "Good input validation." in body


def test_run_ai_review_cli_on_lockfile_only(tmp_path: Path) -> None:
    from scripts.run_ai_review import main

    diff = FIXTURES / "diff_with_lockfile_only.diff"
    out = tmp_path / "result.json"
    code = main(["--diff-file", str(diff), "--result-file", str(out)])
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["decision"] == "PASS"
    assert payload["score"] == 100


def test_publish_dry_run(tmp_path: Path, capsys) -> None:
    from scripts.publish_review import main

    result = tmp_path / "review_result.json"
    result.write_text(
        json.dumps(
            {
                "decision": "FAIL",
                "score": 10,
                "summary": "Critical issues detected.",
                "comments": ["Possible SQL Injection."],
                "infrastructure_failure": False,
            }
        ),
        encoding="utf-8",
    )
    code = main(["--result-file", str(result), "--dry-run"])
    assert code == 0
    assert "SQL Injection" in capsys.readouterr().out
