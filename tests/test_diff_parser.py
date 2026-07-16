"""Tests for diff parsing and ignore-list filtering."""

from __future__ import annotations

from pathlib import Path

import pytest

from review.diff_parser import DEFAULT_IGNORE_PATTERNS, parse_diff

FIXTURES = Path(__file__).parent / "sample_diffs"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_empty_diff_is_empty() -> None:
    parsed = parse_diff(_read("empty_diff.diff"))
    assert parsed.is_empty
    assert parsed.files == []
    assert parsed.total_lines == 0


def test_lockfiles_are_ignored() -> None:
    parsed = parse_diff(_read("diff_with_lockfile_only.diff"))
    assert parsed.is_empty
    assert "package-lock.json" in parsed.ignored_paths
    assert "poetry.lock" in parsed.ignored_paths


def test_mixed_ignores_keep_only_reviewable_source() -> None:
    parsed = parse_diff(_read("mixed_ignores.diff"))
    paths = [f.path for f in parsed.files]
    assert paths == ["src/app.js"]
    assert "node_modules/leftpad/index.js" in parsed.ignored_paths
    assert "app.min.js" in parsed.ignored_paths
    assert "assets/logo.png" in parsed.ignored_paths
    assert ".venv/lib/python3.12/site-packages/x.py" in parsed.ignored_paths


def test_sql_injection_fixture_is_reviewable() -> None:
    parsed = parse_diff(_read("sql_injection.diff"))
    assert not parsed.is_empty
    assert parsed.files[0].path == "app/users.py"
    assert "SELECT * FROM users" in parsed.as_unified()


def test_hardcoded_secret_fixture_is_reviewable() -> None:
    parsed = parse_diff(_read("hardcoded_secret.diff"))
    assert not parsed.is_empty
    assert "FIXTURE_HARDCODED_SECRET" in parsed.as_unified()


def test_clean_code_fixture_is_reviewable() -> None:
    parsed = parse_diff(_read("clean_code.diff"))
    assert [f.path for f in parsed.files] == ["app/health.py"]


def test_extra_ignore_patterns() -> None:
    raw = _read("clean_code.diff")
    parsed = parse_diff(raw, extra_ignore=("**/health.py",))
    assert parsed.is_empty
    assert "app/health.py" in parsed.ignored_paths


def test_default_ignore_patterns_cover_expected_families() -> None:
    joined = " ".join(DEFAULT_IGNORE_PATTERNS)
    assert "node_modules" in joined
    assert "package-lock.json" in joined
    assert ".venv" in joined
    assert "*.min.js" in joined


@pytest.mark.parametrize(
    "path,should_ignore",
    [
        ("foo/package-lock.json", True),
        ("node_modules/x/index.js", True),
        ("src/main.py", False),
        ("dist/bundle.js", True),
        (".env", True),
    ],
)
def test_path_filter_via_parse(path: str, should_ignore: bool) -> None:
    raw = f"""diff --git a/{path} b/{path}
index 1111111..2222222 100644
--- a/{path}
+++ b/{path}
@@ -1 +1 @@
-old
+new
"""
    parsed = parse_diff(raw)
    if should_ignore:
        assert parsed.is_empty
        assert path in parsed.ignored_paths
    else:
        assert not parsed.is_empty
        assert parsed.files[0].path == path


def test_chunk_by_files_splits_when_over_cap() -> None:
    raw = ""
    for i in range(3):
        # each file ~4 lines of body
        raw += f"""diff --git a/f{i}.py b/f{i}.py
index 1111111..2222222 100644
--- a/f{i}.py
+++ b/f{i}.py
@@ -1 +1 @@
-old{i}
+new{i}
"""
    parsed = parse_diff(raw)
    chunks = parsed.chunk_by_files(max_lines=6)
    assert len(chunks) >= 2
    assert sum(len(c.files) for c in chunks) == 3
