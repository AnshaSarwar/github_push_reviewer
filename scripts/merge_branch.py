"""
Squash-merge a feature branch into main when AI review returns PASS.

Uses local git (fetch, checkout main, merge --squash, commit, push) so no
pull request is required.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from review.utils import mask_secrets, setup_logging

REPO_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger("review.ci.merge")


class MergeError(RuntimeError):
    """Raised when merge preconditions fail or git rejects the merge."""


def load_review_result(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise MergeError(f"review result file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def should_merge(payload: dict[str, Any]) -> tuple[bool, str]:
    """Return (ok_to_merge, reason_if_not)."""
    decision = str(payload.get("decision", "")).upper()
    if payload.get("infrastructure_failure"):
        return False, "review infrastructure failed — merge blocked"
    if decision != "PASS":
        return False, f"AI review decision is {decision or 'UNKNOWN'} — merge blocked"
    return True, ""


def run_git(args: list[str], *, cwd: Path = REPO_ROOT) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise MergeError(f"git {' '.join(args)} failed: {mask_secrets(detail)}")
    return result.stdout


def squash_merge_branch(
    *,
    branch: str,
    repo_root: Path = REPO_ROOT,
    commit_message: str | None = None,
) -> str:
    """Squash-merge origin/<branch> into main and push. Returns the new commit SHA."""
    run_git(["fetch", "origin", "main", branch], cwd=repo_root)
    run_git(["checkout", "main"], cwd=repo_root)
    run_git(["reset", "--hard", "origin/main"], cwd=repo_root)
    run_git(["merge", "--squash", f"origin/{branch}"], cwd=repo_root)

    status = run_git(["status", "--porcelain"], cwd=repo_root)
    if not status.strip():
        raise MergeError(f"nothing to merge from '{branch}' — branch may already be in main")

    message = commit_message or f"Squash merge '{branch}' [ai-review]"
    run_git(["commit", "-m", message], cwd=repo_root)
    sha = run_git(["rev-parse", "HEAD"], cwd=repo_root).strip()
    run_git(["push", "origin", "main"], cwd=repo_root)
    return sha


def write_merge_summary(merged: bool, message: str) -> None:
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return
    with Path(summary_file).open("a", encoding="utf-8") as handle:
        handle.write("\n## Auto-merge\n\n")
        if merged:
            handle.write(f"**Squash-merged** into `main`.\n\n")
        else:
            handle.write(f"**Not merged:** {message}\n\n")
        handle.write(f"{mask_secrets(message)}\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Squash-merge branch into main after AI PASS")
    parser.add_argument(
        "--result-file",
        type=Path,
        default=Path(os.environ.get("REVIEW_RESULT_FILE", "artifacts/review_result.json")),
    )
    parser.add_argument(
        "--branch",
        default=os.environ.get("GITHUB_HEAD_REF") or os.environ.get("BRANCH_NAME", ""),
        help="Feature branch name (not refs/heads/...)",
    )
    parser.add_argument(
        "--commit-message",
        default=os.environ.get("MERGE_COMMIT_MESSAGE", ""),
        help="Optional squash commit message",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate only; do not run git merge/push",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    args = parse_args(argv)

    try:
        payload = load_review_result(args.result_file)
        ok, reason = should_merge(payload)
        if not ok:
            logger.info("Skipping merge: %s", reason)
            write_merge_summary(False, reason)
            return 1

        branch = args.branch.strip()
        if args.dry_run or not branch:
            msg = f"dry-run: would squash-merge branch '{branch or '(no branch)'}' into main"
            logger.info(msg)
            write_merge_summary(True, msg)
            return 0

        commit_message = args.commit_message.strip() or None
        sha = squash_merge_branch(branch=branch, commit_message=commit_message)
        msg = f"Squash-merged branch '{branch}' into main (commit {sha})"
        logger.info(msg)
        write_merge_summary(True, msg)
        return 0
    except MergeError as exc:
        logger.error("Merge failed: %s", mask_secrets(str(exc)))
        write_merge_summary(False, str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected merge error: %s", mask_secrets(str(exc)))
        write_merge_summary(False, str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
