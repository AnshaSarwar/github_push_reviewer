"""Generate a unified git diff for CI review (base...HEAD or custom refs)."""

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

from review.diff_parser import parse_diff
from review.utils import setup_logging

REPO_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger("review.ci.diff")

# Run a git command and return the output
def run_git(args: list[str], *, cwd: Path = REPO_ROOT) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout

# Generate a unified git diff for CI review (base...HEAD or custom refs).
def generate_diff(base_ref: str, head_ref: str = "HEAD") -> str:
    """
    Return unified diff for ``base_ref...head_ref`` (merge-base triple-dot).

    Prefer commit SHAs from the push event when running in GitHub Actions.
    """
    logger.info("Generating diff: %s...%s", base_ref, head_ref)
    return run_git(["diff", "--no-ext-diff", f"{base_ref}...{head_ref}"])

# List changed files in a diff
def list_changed_files(raw_diff: str) -> list[str]:
    parsed = parse_diff(raw_diff)
    return [f.path for f in parsed.files]

# Write outputs to files
def write_outputs(
    raw_diff: str,
    *,
    diff_path: Path,
    files_path: Path | None = None,
) -> list[str]:
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff_path.write_text(raw_diff, encoding="utf-8")

    parsed = parse_diff(raw_diff)
    reviewable = [f.path for f in parsed.files]
    ignored = list(parsed.ignored_paths)

    logger.info("Raw diff lines: %s", parsed.raw_line_count)
    logger.info("Reviewable files (%s): %s", len(reviewable), reviewable or "(none)")
    logger.info("Ignored files (%s): %s", len(ignored), ignored or "(none)")

    if files_path is not None:
        files_path.write_text("\n".join(reviewable) + ("\n" if reviewable else ""), encoding="utf-8")

    if os.environ.get("GITHUB_STEP_SUMMARY"):
        summary = Path(os.environ["GITHUB_STEP_SUMMARY"])
        with summary.open("a", encoding="utf-8") as handle:
            handle.write("## Changed files (reviewable)\n\n")
            if reviewable:
                for path in reviewable:
                    handle.write(f"- `{path}`\n")
            else:
                handle.write("_No reviewable files after filtering._\n")
            if ignored:
                handle.write("\n### Ignored\n\n")
                for path in ignored:
                    handle.write(f"- `{path}`\n")

    return reviewable


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate filtered-aware git diff for AI review")
    parser.add_argument(
        "--base",
        default=os.environ.get("REVIEW_BASE_SHA")
        or os.environ.get("GITHUB_BASE_REF", "origin/main"),
        help="Base ref or SHA (default: REVIEW_BASE_SHA / GITHUB_BASE_REF / origin/main)",
    )
    parser.add_argument(
        "--head",
        default=os.environ.get("REVIEW_HEAD_SHA", "HEAD"),
        help="Head ref or SHA (default: REVIEW_HEAD_SHA / HEAD)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(os.environ.get("DIFF_OUTPUT", "artifacts/push.diff")),
        help="Where to write the unified diff",
    )
    parser.add_argument(
        "--files-output",
        type=Path,
        default=Path(os.environ.get("FILES_OUTPUT", "artifacts/changed_files.txt")),
        help="Where to write the reviewable file list",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    args = parse_args(argv)
    base = args.base
    # In Actions, GITHUB_BASE_REF is a branch name without origin/
    if base and "/" not in base and not base.startswith("origin/") and len(base) != 40:
        # branch name from event — prefer origin/<name> after fetch
        candidate = f"origin/{base}"
        try:
            run_git(["rev-parse", "--verify", candidate])
            base = candidate
        except RuntimeError:
            logger.warning("Could not resolve %s; using %s as-is", candidate, args.base)

    try:
        raw = generate_diff(base, args.head)
        write_outputs(raw, diff_path=args.output, files_path=args.files_output)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to generate diff: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
