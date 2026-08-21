"""
Finder package - OpenStreetMap extract search and download.

Split into focused submodules:
- ``_sources``: source resolution and index loading
- ``_query``: string-query based search and download
- ``_geometry``: geometry-based search and download
- ``_download``: shared download utilities
- ``_api``: unified public API dispatchers
"""

from osmfinder.finder._api import download, find
from osmfinder.finder._download import (  # noqa: F401
    _download_extracts_pbf_files,
    _download_single_extract,
    download_extracts_pbf_files,
)

# Private helpers re-exported for backward compatibility with tests and internal callers.
from osmfinder.finder._geometry import (  # noqa: F401
    _cover_geometry_with_extracts,
    _filter_extracts,
    _filter_extracts_for_single_geometry,
    _find_single_extract_for_geometry,
    _find_smallest_containing_extracts,
    _find_smallest_containing_extracts_for_single_geometry,
    _flatten_geometry,
    _get_extract_by_id,
    _select_covering_extracts,
    _simplify_selected_extracts,
    download_by_geometry,
    find_and_download_extracts_pbf_files,
    find_by_geometry,
    find_extracts_covering_point,
    find_smallest_containing_extracts,
)
from osmfinder.finder._query import (
    download_by_query,
    download_extract_by_query,
    find_by_query,
    get_extract_by_query,
)
from osmfinder.finder._sources import (  # noqa: F401, attr-defined
    OsmExtractSourceLike,
    _get_combined_index,
    _get_index_for_sources,
    _resolve_extract_sources,
    clear_osm_index_cache,
    display_available_extracts,
    get_available_extracts,
)

__all__ = [
    "download",
    "download_by_geometry",
    "download_by_query",
    "download_extract_by_query",
    "download_extracts_pbf_files",
    "find",
    "find_and_download_extracts_pbf_files",
    "find_by_geometry",
    "find_by_query",
    "find_extracts_covering_point",
    "find_smallest_containing_extracts",
    "clear_osm_index_cache",
    "display_available_extracts",
    "get_available_extracts",
    "get_extract_by_query",
    "OsmExtractSourceLike",
    # Private helpers
    "_cover_geometry_with_extracts",
    "_download_extracts_pbf_files",
    "_download_single_extract",
    "_filter_extracts",
    "_filter_extracts_for_single_geometry",
    "_find_single_extract_for_geometry",
    "_find_smallest_containing_extracts",
    "_find_smallest_containing_extracts_for_single_geometry",
    "_flatten_geometry",
    "_get_combined_index",
    "_get_extract_by_id",
    "_get_index_for_sources",
    "_resolve_extract_sources",
    "_select_covering_extracts",
    "_simplify_selected_extracts",
]
