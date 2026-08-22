"""
Immich Media Comparator & Deduplicator
"""

from .exceptions import (
    ImmichAPIError,
    ImmichAuthError,
    ImmichDiffError,
    ImmichNetworkError,
)

__version__ = "1.0.0"
__author__ = "Immich Comparator Contributors"

__all__ = [
    "__version__",
    "__author__",
    "ImmichDiffError",
    "ImmichAuthError",
    "ImmichNetworkError",
    "ImmichAPIError",
]
