"""Logging and retry helpers for the review package."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+[a-z0-9._\-]+"),
    re.compile(r"sk-[a-zA-Z0-9]{10,}"),
    re.compile(r"gsk_[a-zA-Z0-9]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]*?-----END [A-Z ]+PRIVATE KEY-----"),
)


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure root logger for the review package once."""
    logger = logging.getLogger("review")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logger


def mask_secrets(text: str) -> str:
    """Redact secret-looking values before writing to logs."""
    masked = text
    for pattern in _SECRET_PATTERNS:
        masked = pattern.sub("[REDACTED]", masked)
    return masked


def retry_with_timeout(
    fn: Callable[[], T],
    *,
    retries: int,
    timeout_seconds: float,
    logger: logging.Logger | None = None,
    operation: str = "operation",
) -> T:
    """
    Run ``fn`` with a soft timeout budget and limited retries.

    Groq SDK calls are blocking; we enforce wall-clock budget across
    attempts and re-raise the last error if all attempts fail.
    """
    log = logger or logging.getLogger("review")
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    attempts = max(1, retries + 1)

    for attempt in range(1, attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — callers decide fail-closed policy
            last_error = exc
            log.warning(
                "%s failed (attempt %s/%s): %s",
                operation,
                attempt,
                attempts,
                mask_secrets(str(exc)),
            )
            if attempt < attempts and (deadline - time.monotonic()) > 0:
                time.sleep(min(1.5 * attempt, max(0.0, deadline - time.monotonic())))

    assert last_error is not None
    raise TimeoutError(
        f"{operation} exceeded timeout ({timeout_seconds}s) or exhausted retries"
    ) from last_error
