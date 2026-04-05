from __future__ import annotations


class GoogleDriveBaseError(Exception):
    """Base exception for Google Drive connector errors."""

    pass


class GoogleDriveAuthError(GoogleDriveBaseError):
    """Authentication or permissions failure."""

    pass


class GoogleDriveRateLimitError(GoogleDriveBaseError):
    """Quota or rate limit exceeded."""

    pass


class GoogleDriveBusinessError(GoogleDriveBaseError):
    """Business logic failure (e.g. validation, conflict)."""

    pass


class GoogleDriveFatalError(GoogleDriveBaseError):
    """Unhandled or unexpected error."""

    pass
