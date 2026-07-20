"""
Run the Phase-1 Reviewer on a diff file and emit CI artifacts.

Exit codes:
  0 = PASS
  1 = FAIL (code quality or infrastructure)
"""

import argparse
import json
import os
import sys
from pathlib import Path

from review.models import ReviewResult, Status
from review.reviewer import Reviewer
from review.utils import mask_secrets, setup_logging

# Map internal ReviewResult to the project-facing decision schema
def to_decision_payload(result: ReviewResult) -> dict:
    """Map internal ReviewResult to the project-facing decision schema."""
    comments: list[str] = []
    for issue in result.issues:
        loc = f"{issue.file}:{issue.line}" if issue.line is not None else issue.file
        comments.append(f"[{issue.severity.value}] {loc} — {issue.message}")
        if issue.suggestion:
            comments.append(f"  suggestion: {issue.suggestion}")
    if not comments and result.summary:
        comments.append(result.summary)
    return {
        "decision": result.status.value,
        "summary": result.summary,
        "comments": comments,
        "score": result.score,
        "infrastructure_failure": result.infrastructure_failure,
        "issues": [i.model_dump(mode="json") for i in result.issues],
    }

# Write outputs to files
def write_github_outputs(result: ReviewResult, decision: dict) -> None:
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with Path(output_file).open("a", encoding="utf-8") as handle:
            handle.write(f"decision={result.status.value}\n")
            handle.write(f"score={result.score}\n")
            handle.write(f"infrastructure_failure={str(result.infrastructure_failure).lower()}\n")

    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        icon = "PASS" if result.status == Status.PASS else "FAIL"
        with Path(summary_file).open("a", encoding="utf-8") as handle:
            handle.write(f"## AI Review `{icon}` / `{result.status.value}`\n\n")
            handle.write(f"**Score:** {result.score}\n\n")
            handle.write(f"**Summary:** {mask_secrets(result.summary)}\n\n")
            if decision["comments"]:
                handle.write("### Comments\n\n")
                for comment in decision["comments"]:
                    handle.write(f"- {mask_secrets(str(comment))}\n")

# Parse command line arguments
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AI review for CI")
    parser.add_argument("--diff-file", type=Path, required=True)
    parser.add_argument(
        "--result-file",
        type=Path,
        default=Path(os.environ.get("REVIEW_RESULT_FILE", "artifacts/review_result.json")),
    )
    return parser.parse_args(argv)

# Main function to run the AI review
def main(argv: list[str] | None = None) -> int:
    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    args = parse_args(argv)

    from review.models import infrastructure_failure

    if not args.diff_file.is_file():
        result = infrastructure_failure(f"diff file not found: {args.diff_file}")
    else:
        raw = args.diff_file.read_text(encoding="utf-8")
        result = Reviewer().review_diff_text(raw)

    decision = to_decision_payload(result)
    args.result_file.parent.mkdir(parents=True, exist_ok=True)
    args.result_file.write_text(json.dumps(decision, indent=2), encoding="utf-8")

    print(mask_secrets(json.dumps(decision, indent=2)))
    write_github_outputs(result, decision)
    return 0 if result.status == Status.PASS else 1


if __name__ == "__main__":
    sys.exit(main())
