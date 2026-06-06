"""Application-specific exceptions."""

from __future__ import annotations


class AppError(Exception):
    """Base exception with a user-facing message."""

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class ValidationError(AppError):
    """Raised when user input is invalid."""


class PlaylistError(AppError):
    """Raised when playlist fetching or parsing fails."""


class FfmpegCapabilityError(AppError):
    """Raised when ffmpeg or ffprobe lacks required capabilities."""


class ExternalProcessError(AppError):
    """Raised when an external process exits unsuccessfully."""


class CancelledError(AppError):
    """Raised when a job is cancelled."""

