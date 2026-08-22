"""
Custom exceptions for immich-diff.
"""


class ImmichDiffError(Exception):
    """Base exception for all immich-diff errors."""
    pass


class ImmichAuthError(ImmichDiffError):
    """Raised when Immich API authentication fails (HTTP 401 / 403)."""
    pass


class ImmichNetworkError(ImmichDiffError):
    """Raised when network connectivity fails after retries (timeout, connection refused, DNS error)."""
    pass


class ImmichAPIError(ImmichDiffError):
    """Raised when the Immich API returns an unexpected response or server error (HTTP 5xx / malformed data)."""
    pass
