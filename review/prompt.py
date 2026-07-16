"""Prompt construction for OpenAI code review."""

from __future__ import annotations

import json

from review.diff_parser import ParsedDiff
from review.models import LLMReviewPayload


def build_system_prompt() -> str:
    """Instructions that constrain the model to schema-valid JSON only."""
    schema = {
        "status": "PASS | FAIL",
        "score": "0-100 integer",
        "summary": "string",
        "issues": [
            {
                "severity": "LOW | MEDIUM | HIGH | CRITICAL",
                "file": "path string from the diff",
                "line": "integer new-file line from the diff, or null",
                "message": "what is wrong",
                "suggestion": "how to fix it",
            }
        ],
    }
    return f"""You are a strict senior engineer performing a code review.

Review dimensions:
- code quality, readability, maintainability
- security (SQL injection, command injection, hardcoded secrets)
- error handling and logging
- Python / FastAPI / SQLAlchemy best practices
- performance and complexity
- duplicate or dead code
- type hints, naming, documentation

Hard rules:
1. Review ONLY the provided git diff. Do not speculate about code not shown.
2. Do not invent file paths or line numbers — every issue must map to a path
   and new-file line that appear in the diff.
3. Return ONLY valid JSON matching this schema (no markdown fences, no prose):
{json.dumps(schema, indent=2)}
4. Prefer HIGH/CRITICAL for security issues (injection, secrets). Use LOW/MEDIUM
   for style and maintainability unless they create real risk.
5. If the change is safe with only minor notes, use low severities and a high score.
"""


def build_user_prompt(parsed: ParsedDiff) -> str:
    """User message containing the filtered unified diff."""
    paths = ", ".join(f.path for f in parsed.files) or "(none)"
    return (
        "Filtered unified diff to review:\n\n"
        f"Files: {paths}\n\n"
        f"```diff\n{parsed.as_unified()}\n```\n\n"
        "Respond with JSON only."
    )


def expected_schema_hint() -> str:
    """Human-readable schema hint for docs/tests."""
    return LLMReviewPayload.model_json_schema().__str__()
