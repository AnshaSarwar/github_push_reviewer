"""Parse and filter raw unified git diffs before any LLM call."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

# Explicit ignore list — filtering happens here, not in the model.
DEFAULT_IGNORE_PATTERNS: tuple[str, ...] = (
    # lock / package manager
    "**/package-lock.json",
    "**/yarn.lock",
    "**/pnpm-lock.yaml",
    "**/Pipfile.lock",
    "**/poetry.lock",
    "**/uv.lock",
    "**/Cargo.lock",
    "**/composer.lock",
    "**/Gemfile.lock",
    "**/go.sum",
    # dependencies / venvs
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/__pycache__/**",
    "**/.tox/**",
    # generated / build
    "**/dist/**",
    "**/build/**",
    "**/.next/**",
    "**/coverage/**",
    "**/*.min.js",
    "**/*.min.css",
    "**/*.map",
    "**/generated/**",
    # binaries / media
    "**/*.png",
    "**/*.jpg",
    "**/*.jpeg",
    "**/*.gif",
    "**/*.webp",
    "**/*.ico",
    "**/*.pdf",
    "**/*.zip",
    "**/*.gz",
    "**/*.tar",
    "**/*.whl",
    "**/*.so",
    "**/*.dll",
    "**/*.exe",
    "**/*.bin",
    # secrets / local env (never send to LLM)
    "**/.env",
    "**/.env.*",
    "**/*.pem",
    "**/*.p12",
    "**/*.keystore",
)

_DIFF_FILE_RE = re.compile(
    r"^diff --git a/(?P<a_path>.+?) b/(?P<b_path>.+)$",
    re.MULTILINE,
)
_BINARY_RE = re.compile(r"^Binary files .* differ$", re.MULTILINE)
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")

# Path segments that mean "ignore this whole tree"
_IGNORED_DIR_NAMES: frozenset[str] = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".tox",
        "dist",
        "build",
        ".next",
        "coverage",
        "generated",
    }
)


@dataclass(slots=True)
class FileDiff:
    """One file's portion of a unified diff after filtering."""

    path: str
    old_path: str
    hunks_text: str
    line_count: int
    is_binary: bool = False

    def as_unified(self) -> str:
        header = f"diff --git a/{self.old_path} b/{self.path}\n"
        return header + self.hunks_text


@dataclass(slots=True)
class ParsedDiff:
    """Filtered, reviewable representation of a git diff."""

    files: list[FileDiff] = field(default_factory=list)
    ignored_paths: list[str] = field(default_factory=list)
    raw_line_count: int = 0

    @property
    def total_lines(self) -> int:
        return sum(f.line_count for f in self.files)

    @property
    def is_empty(self) -> bool:
        return len(self.files) == 0

    def as_unified(self) -> str:
        return "\n".join(f.as_unified() for f in self.files)

    def chunk_by_files(self, max_lines: int) -> list[ParsedDiff]:
        """
        Split into chunks that each stay under ``max_lines``.

        Oversized single files become their own chunk (caller decides
        whether that chunk is still too large to review).
        """
        if self.total_lines <= max_lines:
            return [self]

        chunks: list[ParsedDiff] = []
        current: list[FileDiff] = []
        current_lines = 0

        for file_diff in self.files:
            if current and current_lines + file_diff.line_count > max_lines:
                chunks.append(ParsedDiff(files=list(current), raw_line_count=current_lines))
                current = []
                current_lines = 0
            current.append(file_diff)
            current_lines += file_diff.line_count

        if current:
            chunks.append(ParsedDiff(files=list(current), raw_line_count=current_lines))
        return chunks


def _path_matches(path: str, patterns: tuple[str, ...]) -> bool:
    """
    Match path against glob patterns.

    Uses ``PurePosixPath.match`` so ``**`` works, and also matches root-level
    names against ``**/filename`` patterns.
    """
    # Keep leading dotfiles (do not strip "." from ".env")
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    parts = normalized.split("/")
    if any(part in _IGNORED_DIR_NAMES for part in parts):
        return True

    candidate = PurePosixPath(normalized)
    basename = candidate.name

    for pattern in patterns:
        pat = pattern.replace("\\", "/")
        variants = {pat}
        if pat.startswith("**/"):
            variants.add(pat[3:])
        if pat.endswith("/**"):
            variants.add(pat[:-3])

        for variant in variants:
            if not variant:
                continue
            # Exact basename / full-path equality (dotfiles, lockfile names)
            if basename == variant or normalized == variant:
                return True
            try:
                if candidate.match(variant):
                    return True
                if PurePosixPath(basename).match(variant):
                    return True
            except ValueError:
                continue
    return False


def _split_file_blocks(raw: str) -> list[tuple[str, str, str]]:
    """Return list of (a_path, b_path, block_body including headers after diff line)."""
    matches = list(_DIFF_FILE_RE.finditer(raw))
    if not matches:
        return []

    blocks: list[tuple[str, str, str]] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        body = raw[start:end].lstrip("\n")
        blocks.append((match.group("a_path"), match.group("b_path"), body))
    return blocks


def parse_diff(
    raw: str,
    *,
    ignore_patterns: tuple[str, ...] | None = None,
    extra_ignore: tuple[str, ...] = (),
) -> ParsedDiff:
    """
    Parse a unified git diff and drop ignored / binary paths.

    Args:
        raw: Full ``git diff`` text.
        ignore_patterns: Override default ignore list when provided.
        extra_ignore: Additional patterns appended to the active list.
    """
    patterns = tuple(ignore_patterns if ignore_patterns is not None else DEFAULT_IGNORE_PATTERNS)
    patterns = patterns + tuple(p for p in extra_ignore if p)

    raw_line_count = len(raw.splitlines()) if raw else 0
    if not raw.strip():
        return ParsedDiff(files=[], ignored_paths=[], raw_line_count=0)

    files: list[FileDiff] = []
    ignored: list[str] = []

    for a_path, b_path, body in _split_file_blocks(raw):
        path = b_path if b_path != "/dev/null" else a_path
        is_binary = bool(_BINARY_RE.search(body)) or "GIT binary patch" in body

        if is_binary or _path_matches(path, patterns) or _path_matches(a_path, patterns):
            ignored.append(path)
            continue

        line_count = len(body.splitlines())
        files.append(
            FileDiff(
                path=path,
                old_path=a_path,
                hunks_text=body if body.endswith("\n") else body + "\n",
                line_count=line_count,
                is_binary=False,
            )
        )

    return ParsedDiff(files=files, ignored_paths=ignored, raw_line_count=raw_line_count)


def new_file_line_numbers(file_diff: FileDiff) -> set[int]:
    """Collect new-file line numbers present in the diff (for validation helpers/tests)."""
    lines: set[int] = set()
    current: int | None = None
    for row in file_diff.hunks_text.splitlines():
        hunk = _HUNK_HEADER_RE.match(row)
        if hunk:
            current = int(hunk.group(2))
            continue
        if current is None:
            continue
        if row.startswith("+") and not row.startswith("+++"):
            lines.add(current)
            current += 1
        elif row.startswith("-") and not row.startswith("---"):
            continue
        elif row.startswith("\\"):
            continue
        else:
            current += 1
    return lines
