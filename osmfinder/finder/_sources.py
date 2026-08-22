"""Source resolution and extract index loading."""

import warnings
from collections.abc import Iterable

import numpy as np
from requests.exceptions import RequestException

from osmfinder._typing import (
    OpenStreetMapExtract,
    OsmExtractsIndex,
    OsmExtractSource,
)
from osmfinder.exceptions import (
    OsmExtractsIndexesUnavailableError,
    OsmExtractSourceUnavailableWarning,
)
from osmfinder.extract import clear_osm_index_cache
from osmfinder.sources.bbbike import _load_bbbike_index
from osmfinder.sources.geo2day import _load_geo2day_index
from osmfinder.sources.geofabrik import _load_geofabrik_index
from osmfinder.sources.movisda import _load_movisda_admin_index, _load_movisda_grid_index
from osmfinder.sources.osm_fr import _load_openstreetmap_fr_index
from osmfinder.sources.tree import get_available_extracts_as_rich_tree

OSM_EXTRACT_SOURCE_INDEX_FUNCTION = {
    OsmExtractSource.bbbike: _load_bbbike_index,
    OsmExtractSource.geofabrik: _load_geofabrik_index,
    OsmExtractSource.osm_fr: _load_openstreetmap_fr_index,
    OsmExtractSource.geo2day: _load_geo2day_index,
    OsmExtractSource.movisda_admin: _load_movisda_admin_index,
    OsmExtractSource.movisda_grid: _load_movisda_grid_index,
}

OsmExtractSourceLike = OsmExtractSource | str | Iterable[OsmExtractSource | str]

__all__ = ["clear_osm_index_cache"]


def _resolve_extract_sources(source: OsmExtractSourceLike) -> list[OsmExtractSource]:
    """
    Normalize a source specification into a list of concrete OSM extract sources.

    Accepts a single `OsmExtractSource`/string, an iterable of them, or a comma-separated
    string (e.g. `"bbbike,osmfr"`). The `any` source is expanded to all available sources.
    Duplicates are removed while preserving order.

    Args:
        source (OsmExtractSourceLike): Source specification.

    Raises:
        ValueError: If a provided value can't be parsed to an `OsmExtractSource`,
            or if the specification is empty.

    Returns:
        list[OsmExtractSource]: List of concrete sources (without `any`).
    """
    if isinstance(source, OsmExtractSource):
        raw_values: list[OsmExtractSource | str] = [source]
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
    return deduplicated


def _get_index_for_sources(source: OsmExtractSourceLike) -> OsmExtractsIndex:
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


def display_available_extracts(
    source: OsmExtractSource | str,
    use_full_names: bool = True,
    use_pager: bool = False,
) -> None:
    """
    Display all available OSM extracts in the form of a tree.

    Output will be printed to the console.

    Args:
        source (Union[OsmExtractSource, str]): Source for which extracts should be displayed.
        use_full_names (bool, optional): Whether to display full name, or short name of the extract.
            Full name contains all parents of the extract. Defaults to `True`.
        use_pager (bool, optional): Whether to display long output using Rich pager
            or just print to output. Defaults to `False`.

    Raises:
        ValueError: If provided source value cannot be parsed to OsmExtractSource.

    Examples:
        >>> import osmfinder
        >>> # Prints a Rich tree to the console; no return value to assert.
        >>> osmfinder.display_available_extracts("Geofabrik")  # doctest: +SKIP
    """
    from rich import get_console
    from rich import print as rprint

    try:
        source_enum = OsmExtractSource(source)
        tree = get_available_extracts_as_rich_tree(
            source_enum, OSM_EXTRACT_SOURCE_INDEX_FUNCTION, use_full_names
        )
        if not use_pager:
            rprint(tree)
        else:
            console = get_console()
            with console.pager():
                console.print(tree)
    except ValueError as ex:
        raise ValueError(f"Unknown OSM extracts source: {source}.") from ex


def get_available_extracts(
    source: OsmExtractSourceLike = "any",
    excluded_extracts_ids: set[str] | None = None,
) -> list[OpenStreetMapExtract]:
    """
    Return all available OSM extracts for a source as a list.

    Uses the same index loading and caching as the rest of the API. Extracts are returned
    sorted by area ascending (then id), matching the order used by the internal index.

    Args:
        source (OsmExtractSourceLike): OSM source name. Can be one of: 'any', 'Geofabrik',
            'BBBike', 'osmfr', 'GEO2Day', 'Movisda-admin', 'Movisda-grid', or an iterable /
            comma-separated string of those (e.g. ['BBBike', 'OSMfr'] or 'bbbike,osmfr').
            Defaults to 'any'.
        excluded_extracts_ids (Optional[set[str]]): Set of extract ids to exclude from the
            result. Useful for skipping extracts that are unavailable. Defaults to `None`.

    Returns:
        list[OpenStreetMapExtract]: List of all available extracts for the given source(s).

    Examples:
        >>> import osmfinder
        >>> # Get all extracts from a single source
        >>> extracts = osmfinder.get_available_extracts("Geofabrik")
        >>> isinstance(extracts, list)
        True
        >>> len(extracts) >= 1
        True
        >>> # Get all extracts across all sources
        >>> all_extracts = osmfinder.get_available_extracts()
        >>> isinstance(all_extracts, list)
        True
        >>> len(all_extracts) >= 1
        True
    """
    try:
        index = _get_index_for_sources(source)
    except ValueError as ex:
        raise ValueError(f"Unknown OSM extracts source: {source}.") from ex

    if excluded_extracts_ids:
        index = index.filter_by_mask(~np.isin(index.ids, list(excluded_extracts_ids)))

    return list(index)
