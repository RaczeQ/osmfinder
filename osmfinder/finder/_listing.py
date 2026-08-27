"""
Listing and display of available OSM extracts.

``get_available_extracts`` and ``display_available_extracts`` both resolve the
requested source(s) through ``_get_index_for_sources`` (and use the
``OSM_EXTRACT_SOURCE_INDEX_FUNCTION`` mapping in the display case). These helper
names are monkeypatched on the ``osmfinder.finder`` package namespace in the test
suite, so they are resolved through the package module object ``_finder`` rather
than a local import binding.
"""

import numpy as np
from rich import get_console
from rich import print as rprint

import osmfinder.finder as _finder
from osmfinder._typing import OpenStreetMapExtract, OsmExtractSource, OsmExtractSourceLike
from osmfinder.sources.tree import get_available_extracts_as_rich_tree


def display_available_extracts(
    source: OsmExtractSource | str,
    use_full_names: bool = True,
    use_pager: bool = False,
) -> None:
    """
    Display all available OSM extracts in the form of a tree.

    Output will be printed to the console.

    Args:
        source (OsmExtractSource | str): Source for which extracts should be displayed.
        use_full_names (bool): Whether to display full name, or short name of the extract.
            Full name contains all parents of the extract. Defaults to `True`.
        use_pager (bool): Whether to display long output using Rich pager
            or just print to output. Defaults to `False`.

    Raises:
        ValueError: If provided source value cannot be parsed to OsmExtractSource.

    Output is printed to the console.
    """
    try:
        source_enum = OsmExtractSource(source)
        tree = get_available_extracts_as_rich_tree(
            source_enum, _finder.OSM_EXTRACT_SOURCE_INDEX_FUNCTION, use_full_names
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
    source: OsmExtractSourceLike | None = None,
    excluded_extracts_ids: set[str] | None = None,
) -> list[OpenStreetMapExtract]:
    """
    Return all available OSM extracts for a source as a list.

    Uses the same index loading and caching as the rest of the API. Extracts are returned
    sorted by area ascending (then id), matching the order used by the internal index.

    Args:
        source (OsmExtractSourceLike): OSM source name. Can be one of: 'any', 'Geofabrik',
            'BBBike', 'osmfr', 'GEO2Day', 'Movisda-admin', 'Movisda-grid', or an iterable /
            comma-separated string of those (e.g. ['BBBike', 'osmfr'] or 'bbbike,osmfr').
            Defaults to 'any'.
        excluded_extracts_ids (set[str] | None): Set of extract ids to exclude from the
            result. Useful for skipping extracts that are unavailable. Defaults to `None`.

    Returns:
        list[OpenStreetMapExtract]: List of all available extracts for the given source(s).

    Examples:
        >>> import osmfinder
        >>> # Get all extracts across all sources
        >>> all_extracts = osmfinder.get_available_extracts()
        >>> len(all_extracts) >= 1
        True
        >>> # Get all extracts from a single source
        >>> extracts = osmfinder.get_available_extracts("Geofabrik")
        >>> len(extracts) >= 1
        True
        >>> [e.id for e in extracts[:3]]
        ['Geofabrik_melilla', 'Geofabrik_ceuta', 'Geofabrik_enfield']
    """
    try:
        index = _finder._get_index_for_sources(source)
    except ValueError as ex:
        raise ValueError(f"Unknown OSM extracts source: {source}.") from ex

    if excluded_extracts_ids:
        index = index.filter_by_mask(~np.isin(index.ids, list(excluded_extracts_ids)))

    return list(index)
