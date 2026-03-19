"""ARCANA AI — Retry decorator for external API calls.

Provides a reusable decorator with exponential backoff and jitter
for all outbound HTTP/API calls. Only retries on transient errors
(5xx, timeouts, connection failures) — never on 4xx client errors.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
from typing import Any, Callable, TypeVar

import httpx

logger = logging.getLogger("arcana.retry")

# Exceptions considered transient and safe to retry
TRANSIENT_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    ConnectionError,
    TimeoutError,
    OSError,
)

F = TypeVar("F", bound=Callable[..., Any])


def _is_transient_http_error(exc: Exception) -> bool:
    """Check if an httpx.HTTPStatusError is a transient (5xx) error."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


def retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_jitter: float = 1.0,
) -> Callable[[F], F]:
    """Decorator that retries async functions on transient failures.

    Args:
        max_retries: Maximum number of attempts (default 3).
        base_delay: Base delay in seconds for exponential backoff (1s, 2s, 4s).
        max_jitter: Maximum random jitter added to each delay (default 0-1s).

    Behaviour:
        - Retries on 5xx HTTP status errors, timeouts, and connection errors.
        - Does NOT retry on 4xx errors (permanent client failures).
        - Logs each retry attempt with the error details.
        - Raises the original exception after all retries are exhausted.
    """

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            for attempt in range(1, max_retries + 1):
                try:
                    return await fn(*args, **kwargs)
                except TRANSIENT_EXCEPTIONS as exc:
                    last_exc = exc
                    if attempt < max_retries:
                        delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, max_jitter)
                        logger.warning(
                            "Retry %d/%d for %s — %s: %s (sleeping %.1fs)",
                            attempt, max_retries, fn.__qualname__,
                            type(exc).__name__, exc, delay,
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            "All %d retries exhausted for %s — %s: %s",
                            max_retries, fn.__qualname__,
                            type(exc).__name__, exc,
                        )
                except httpx.HTTPStatusError as exc:
                    if _is_transient_http_error(exc):
                        last_exc = exc
                        if attempt < max_retries:
                            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, max_jitter)
                            logger.warning(
                                "Retry %d/%d for %s — HTTP %d (sleeping %.1fs)",
                                attempt, max_retries, fn.__qualname__,
                                exc.response.status_code, delay,
                            )
                            await asyncio.sleep(delay)
                        else:
                            logger.error(
                                "All %d retries exhausted for %s — HTTP %d",
                                max_retries, fn.__qualname__,
                                exc.response.status_code,
                            )
                    else:
                        # 4xx — don't retry, raise immediately
                        raise
                except Exception:
                    # Unknown/unexpected errors — don't retry
                    raise

            # All retries exhausted
            if last_exc is not None:
                raise last_exc
            raise RuntimeError(f"retry exhausted for {fn.__qualname__} with no recorded exception")

        return wrapper  # type: ignore[return-value]

    return decorator
