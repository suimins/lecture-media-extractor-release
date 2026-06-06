"""Cancellation primitives shared by long-running jobs."""

from __future__ import annotations

from threading import Event


class CancellationToken:
    """Thread-safe cancellation flag for workers and subprocess loops."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        from app.core.errors import CancelledError

        if self.cancelled:
            raise CancelledError("작업이 취소됨")

