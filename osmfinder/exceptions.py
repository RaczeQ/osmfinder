"""Exceptions and warnings raised by osmfinder."""


class GeometryNotCoveredWarning(Warning):
    """Emitted when geometry coverage is incomplete and `allow_uncovered_geometry=True`."""


class GeometryNotCoveredError(Exception):
    """Raised when geometry coverage is incomplete and `allow_uncovered_geometry=False`."""


class OsmExtractIndexOutdatedWarning(Warning):
    """Emitted when a cached index has an outdated schema and is being redownloaded."""


class OsmExtractSearchError(Exception):
    """Base class for errors raised while searching for a matching extract."""

    def __init__(self, message: str, matching_full_names: list[str]):
        """Initialize the search error with a message and matching extract names."""
        super().__init__(message)
        self.matching_full_names = matching_full_names


class OsmExtractUnavailableWarning(Warning):
    """Emitted when a matched extract is unavailable for download and the search continues."""


class OsmExtractSourceUnavailableWarning(Warning):
    """Emitted when one or more requested sources fail to load but at least one source succeeds."""


class OsmExtractsIndexesUnavailableError(Exception):
    """Raised when all requested sources fail to load their indexes."""


class OsmExtractZeroMatchesError(OsmExtractSearchError):
    """Raised when the query matches zero extracts by name or file name."""


class OsmExtractMultipleMatchesError(OsmExtractSearchError):
    """Raised when the query matches multiple extracts and `select_first_match=False`."""


class OsmExtractsUnavailableError(OsmExtractSearchError):
    """Raised when all extracts matching the query are unavailable for download."""


class OsmExtractMultipleMatchesWarning(Warning):
    """Emitted when multiple extracts match and the smallest-area match is auto-selected."""


class MissingOsmCacheWarning(Warning):
    """Emitted when no cached index is available and the index must be built locally."""


class OldOsmCacheWarning(Warning):
    """Emitted when the cached index is older than one year and may be outdated."""


class OsmExtractIndexCorruptedError(Exception):
    """Raised when a cached index file is missing required columns or has an invalid structure."""


__all__ = [
    "GeometryNotCoveredError",
    "GeometryNotCoveredWarning",
    "MissingOsmCacheWarning",
    "OldOsmCacheWarning",
    "OsmExtractIndexOutdatedWarning",
    "OsmExtractMultipleMatchesError",
    "OsmExtractMultipleMatchesWarning",
    "OsmExtractSearchError",
    "OsmExtractSourceUnavailableWarning",
    "OsmExtractUnavailableWarning",
    "OsmExtractZeroMatchesError",
    "OsmExtractsIndexesUnavailableError",
    "OsmExtractsUnavailableError",
]
