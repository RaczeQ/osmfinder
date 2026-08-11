"""Exceptions and warnings raised by osmfinder."""


class GeometryNotCoveredWarning(Warning): ...


class GeometryNotCoveredError(Exception): ...


class OsmExtractIndexOutdatedWarning(Warning): ...


class OsmExtractSearchError(Exception):
    """Base class for errors raised while searching for a matching extract."""

    def __init__(self, message: str, matching_full_names: list[str]):
        super().__init__(message)
        self.matching_full_names = matching_full_names


class OsmExtractUnavailableWarning(Warning): ...


class OsmExtractSourceUnavailableWarning(Warning): ...


class OsmExtractsIndexesUnavailableError(Exception): ...


class OsmExtractZeroMatchesError(OsmExtractSearchError): ...


class OsmExtractMultipleMatchesError(OsmExtractSearchError): ...


class OsmExtractsUnavailableError(OsmExtractSearchError): ...


class OsmExtractMultipleMatchesWarning(Warning): ...


class MissingOsmCacheWarning(Warning): ...


class OldOsmCacheWarning(Warning): ...


class OsmExtractIndexCorruptedError(Exception):
    """Raised when a cached index file is corrupted or has an invalid structure."""


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
