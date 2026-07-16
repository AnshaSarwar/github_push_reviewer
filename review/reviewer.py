"""
Orchestrate filtered-diff → Ollama → validated ReviewResult.

This module has no git / GitHub / CI knowledge. Callers supply raw diff text.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from review.config import Settings, get_settings
from review.diff_parser import ParsedDiff, parse_diff
from review.models import (
    Issue,
    LLMReviewPayload,
    ReviewResult,
    Status,
    derive_score,
    derive_status,
    finalize_from_llm,
    infrastructure_failure,
)
from review.ollama_client import call_ollama
from review.prompt import build_system_prompt, build_user_prompt
from review.utils import mask_secrets, retry_with_timeout, setup_logging


class Reviewer:
    """Ollama-backed code reviewer with fail-closed semantics."""

    def __init__(
        self,
        settings: Settings | None = None,
        llm_generate: Callable[[ParsedDiff], str] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.logger = setup_logging(self.settings.log_level)
        self._llm_generate = llm_generate

    def review_diff_text(self, raw_diff: str) -> ReviewResult:
        """
        Review a raw unified diff string end-to-end.

        Never fails open: infrastructure problems become FAIL with a distinct summary.
        """
        try:
            extra = tuple(
                p.strip()
                for p in self.settings.review_ignore_extra.split(",")
                if p.strip()
            )
            parsed = parse_diff(raw_diff, extra_ignore=extra)
            return self.review_parsed(parsed)
        except Exception as exc:  # noqa: BLE001
            self.logger.error("Unexpected review failure: %s", mask_secrets(str(exc)))
            return infrastructure_failure(str(exc))

    def review_parsed(self, parsed: ParsedDiff) -> ReviewResult:
        """Review an already-parsed/filtered diff."""
        if parsed.is_empty:
            ignored = ", ".join(parsed.ignored_paths) if parsed.ignored_paths else "none"
            self.logger.info(
                "No reviewable files after filtering (ignored: %s); skipping LLM",
                ignored,
            )
            return ReviewResult(
                status=Status.PASS,
                score=100,
                summary=(
                    "No reviewable changes after filtering ignored/binary/generated paths "
                    f"(ignored: {ignored})."
                ),
                issues=[],
                infrastructure_failure=False,
            )

        max_lines = self.settings.max_diff_lines
        chunks = parsed.chunk_by_files(max_lines)

        # Single file larger than the safe cap → refuse (no silent truncate).
        for chunk in chunks:
            if chunk.total_lines > max_lines:
                paths = ", ".join(f.path for f in chunk.files)
                return ReviewResult(
                    status=Status.FAIL,
                    score=0,
                    summary=(
                        f"diff too large to review safely: {chunk.total_lines} lines in "
                        f"[{paths}] exceeds MAX_DIFF_LINES={max_lines}. "
                        "Split the change or raise the cap deliberately."
                    ),
                    issues=[],
                    infrastructure_failure=False,
                )

        results: list[ReviewResult] = []
        for index, chunk in enumerate(chunks, start=1):
            self.logger.info(
                "Reviewing chunk %s/%s (%s lines, %s files)",
                index,
                len(chunks),
                chunk.total_lines,
                len(chunk.files),
            )
            chunk_result = self._review_single_chunk(chunk)
            if chunk_result.infrastructure_failure:
                return chunk_result
            results.append(chunk_result)

        return self._merge_results(results)

    def _review_single_chunk(self, parsed: ParsedDiff) -> ReviewResult:
        try:
            raw_text = self._call_llm(parsed)
            payload = LLMReviewPayload.model_validate_json(raw_text)
            return finalize_from_llm(payload)
        except ValidationError as exc:
            self.logger.error("LLM response failed Pydantic validation: %s", exc)
            return infrastructure_failure(f"LLM response failed schema validation: {exc}")
        except json.JSONDecodeError as exc:
            self.logger.error("LLM returned non-JSON: %s", exc)
            return infrastructure_failure(f"LLM returned non-JSON: {exc}")
        except Exception as exc:  # noqa: BLE001
            self.logger.error("LLM call failed: %s", mask_secrets(str(exc)))
            return infrastructure_failure(str(exc))

    def _call_llm(self, parsed: ParsedDiff) -> str:
        system = build_system_prompt()
        user = build_user_prompt(parsed)

        def _invoke() -> str:
            if self._llm_generate is not None:
                return self._llm_generate(parsed)
            return call_ollama(
                base_url=self.settings.ollama_base_url,
                model=self.settings.ollama_model,
                system=system,
                user=user,
                timeout=self.settings.llm_timeout_seconds,
            )

        return retry_with_timeout(
            _invoke,
            retries=self.settings.llm_max_retries,
            timeout_seconds=self.settings.llm_timeout_seconds,
            logger=self.logger,
            operation="ollama.api.chat",
        )

    @staticmethod
    def _merge_results(results: list[ReviewResult]) -> ReviewResult:
        if len(results) == 1:
            return results[0]

        issues: list[Issue] = []
        for result in results:
            issues.extend(result.issues)

        status = derive_status(issues)
        avg_score = int(sum(r.score for r in results) / len(results))
        score = derive_score(issues, avg_score)
        summaries = [r.summary for r in results if r.summary]
        summary = " | ".join(summaries) if summaries else "Merged multi-chunk review."
        return ReviewResult(
            status=status,
            score=score,
            summary=summary,
            issues=issues,
            infrastructure_failure=False,
        )


def review_diff_file(path: Path, settings: Settings | None = None) -> ReviewResult:
    """Convenience wrapper used by CLI and tests."""
    raw = path.read_text(encoding="utf-8")
    return Reviewer(settings=settings).review_diff_text(raw)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m review.reviewer",
        description="AI code review for a unified git diff file (Ollama)",
    )
    parser.add_argument(
        "--diff-file",
        required=True,
        type=Path,
        help="Path to a unified diff file",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print only JSON ReviewResult",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)

    if not args.diff_file.is_file():
        result = infrastructure_failure(f"diff file not found: {args.diff_file}")
    else:
        result = review_diff_file(args.diff_file, settings=settings)

    payload: dict[str, Any] = result.to_public_dict()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print("=" * 60)
        print(f"STATUS: {result.status.value}")
        print(f"SCORE:  {result.score}")
        if result.infrastructure_failure:
            print("KIND:   INFRASTRUCTURE FAILURE")
        print("=" * 60)
        print(f"\nSummary:\n{mask_secrets(result.summary)}\n")
        if result.issues:
            print("Issues:")
            for i, issue in enumerate(result.issues, 1):
                line = issue.line if issue.line is not None else "-"
                print(f"  {i}. [{issue.severity.value}] {issue.file}:{line}")
                print(f"     {mask_secrets(issue.message)}")
                if issue.suggestion:
                    print(f"     Fix: {mask_secrets(issue.suggestion)}")
        else:
            print("No issues.")
        print()

    return 0 if result.status == Status.PASS else 1


if __name__ == "__main__":
    sys.exit(main())
