"""
OpenStreetMap extracts.

This module contains iterators for publically available OpenStreetMap `*.osm.pbf` files
repositories.

The public API (``download``, ``find``, ``find_extract_by_query`` …) and the patch-sensitive
helpers (``_get_index_for_sources``, ``_resolve_extract_sources``,
``OSM_EXTRACT_SOURCE_INDEX_FUNCTION``, ``_download_single_extract``) live in this package
namespace. The remaining logic is split into domain submodules: ``_covering`` (the pure
geometry-covering algorithm), ``_query`` (find by name/geometry/point), ``_listing``
(list/display available extracts) and ``_download`` (downloads). Submodules resolve the
patch-sensitive helpers through this package module at call time, which keeps the
``osmfinder.finder.<name>`` monkeypatching/patching used by the test-suite effective.
"""

import importlib
import warnings
from collections.abc import Callable
from functools import partial

from requests.exceptions import RequestException

from osmfinder._typing import OsmExtractsIndex, OsmExtractSource, OsmExtractSourceLike
from osmfinder.exceptions import (
    OsmExtractsIndexesUnavailableError,
    OsmExtractSourceUnavailableWarning,
)
from osmfinder.extract import (
    _REGISTERED_INDEX_LOADERS,
    _get_registered_index,
    clear_osm_index_cache,
)
from osmfinder.finder._download import (
    _download_single_extract as _download_single_extract,
)
from osmfinder.finder._download import (
    download,
)
from osmfinder.finder._listing import (
    display_available_extracts,
    get_available_extracts,
)
from osmfinder.finder._query import (
    find,
    find_extract_by_query,
    find_extracts_by_geometry,
    find_extracts_covering_point,
)

# Importing the sources package has the side effect of registering every OSM extract
# source index loader into `_REGISTERED_INDEX_LOADERS` (see `osmfinder.sources`).
importlib.import_module("osmfinder.sources")

# A single source, or multiple sources passed as an iterable or a comma-separated string.
OSM_EXTRACT_SOURCE_INDEX_FUNCTION: dict[OsmExtractSource, Callable[..., OsmExtractsIndex]] = {
    source: partial(_get_registered_index, source) for source in _REGISTERED_INDEX_LOADERS
}


def _resolve_extract_sources(source: OsmExtractSourceLike | None) -> list[OsmExtractSource]:
    """
    Normalize a source specification into a list of concrete OSM extract sources.

    Accepts a single `OsmExtractSource`/string, an iterable of them, or a comma-separated
    string (e.g. `"bbbike,osmfr"`). The `any` source is expanded to all available sources.
    Duplicates are removed while preserving order.

    Args:
        source (OsmExtractSourceLike | None): Source specification. Defaults to `any` when
            `None`.

    Raises:
        ValueError: If a provided value can't be parsed to an `OsmExtractSource`,
            or if the specification is empty.

    Returns:
        list[OsmExtractSource]: List of concrete sources (without `any`).
    """
    if source is None:
        raw_values: list[OsmExtractSource | str] = ["any"]
    elif isinstance(source, OsmExtractSource):
        raw_values = [source]
    elif isinstance(source, str):
        raw_values = source.split(",")
    else:
        raw_values = []
        for single_source in source:
            if isinstance(single_source, str):
                raw_values.extend(single_source.split(","))
            else:
                raw_values.append(single_source)

    resolved: list[OsmExtractSource] = []
    for raw_value in raw_values:
        cleaned_value = raw_value.strip() if isinstance(raw_value, str) else raw_value
        if cleaned_value == "":
            continue
        source_enum = OsmExtractSource(cleaned_value)
        if source_enum == OsmExtractSource.any:
            resolved.extend(OSM_EXTRACT_SOURCE_INDEX_FUNCTION.keys())
        else:
            resolved.append(source_enum)

    if not resolved:
        raise ValueError("No OSM extracts source provided.")

    seen: set[OsmExtractSource] = set()
    deduplicated: list[OsmExtractSource] = []
    for item in resolved:
        if item not in seen:
            seen.add(item)
            deduplicated.append(item)
    return sorted(deduplicated, key=lambda s: s.value.lower())


def _get_index_for_sources(source: OsmExtractSourceLike | None) -> OsmExtractsIndex:
    """
    Load and combine extract indexes for one or multiple sources.

    For a single source, loading errors propagate - the request can't be fulfilled otherwise.
    For multiple sources (including ``any``), sources whose index can't be loaded (e.g. when
    offline and not cached locally) are skipped with a warning, as long as at least one source
    loads successfully. If none can be loaded, an error is raised.
    """
    resolved_sources = _resolve_extract_sources(source)

    if len(resolved_sources) == 1:
        return OSM_EXTRACT_SOURCE_INDEX_FUNCTION[resolved_sources[0]]()

    loaded_indexes: list[OsmExtractsIndex] = []
    unavailable_sources: list[OsmExtractSource] = []
    for resolved_source in resolved_sources:
        try:
            loaded_indexes.append(OSM_EXTRACT_SOURCE_INDEX_FUNCTION[resolved_source]())
        except RequestException:
            unavailable_sources.append(resolved_source)

    if unavailable_sources:
        warnings.warn(
            "Couldn't load indexes for some OSM extract sources (offline or unreachable?):"
            f" {[unavailable_source.value for unavailable_source in unavailable_sources]}."
            " Continuing with the available sources.",
            OsmExtractSourceUnavailableWarning,
            stacklevel=0,
        )

    if not loaded_indexes:
        raise OsmExtractsIndexesUnavailableError(
            "Couldn't load any OSM extracts index for the requested sources:"
            f" {[resolved_source.value for resolved_source in resolved_sources]}."
            " Check your internet connection or the local cache."
        )

    return OsmExtractsIndex.combine_indexes(loaded_indexes)


def _get_combined_index() -> OsmExtractsIndex:
    return _get_index_for_sources(OsmExtractSource.any)


__all__ = [
    "download",
    "find",
    "find_extract_by_query",
    "find_extracts_by_geometry",
    "find_extracts_covering_point",
    "get_available_extracts",
    "display_available_extracts",
    "clear_osm_index_cache",
    "OsmExtractSource",
    "OsmExtractSourceLike",
]
