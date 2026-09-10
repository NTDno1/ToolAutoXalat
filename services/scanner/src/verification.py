from __future__ import annotations

import time
from typing import Sequence


class StableSequenceVerifier:
    """Require the same complete history sequence across independent scans."""

    def __init__(self, required_confirmations: int = 3, timeout_seconds: float = 2.0):
        self.required_confirmations = max(2, int(required_confirmations))
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self._candidate: tuple[str, ...] | None = None
        self._confirmations = 0
        self._last_seen = 0.0

    @property
    def confirmations(self) -> int:
        return self._confirmations

    def reset(self) -> None:
        self._candidate = None
        self._confirmations = 0
        self._last_seen = 0.0

    def observe(
        self, sequence: Sequence[str], observed_at: float | None = None
    ) -> bool:
        candidate = tuple(sequence)
        if len(candidate) != 8:
            self.reset()
            return False

        now = time.monotonic() if observed_at is None else observed_at
        is_continuation = (
            candidate == self._candidate
            and self._last_seen > 0
            and 0 <= now - self._last_seen <= self.timeout_seconds
        )
        if is_continuation:
            self._confirmations += 1
        else:
            self._candidate = candidate
            self._confirmations = 1
        self._last_seen = now
        return self._confirmations >= self.required_confirmations
