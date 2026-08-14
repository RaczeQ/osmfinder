"""
osmfinder.

Find and download publicly available OpenStreetMap `*.osm.pbf` extracts by name, id or geometry.

The library wraps several public extract providers (Geofabrik, BBBike, OpenStreetMap.fr, Movisda,
GEO2day) behind a single API. It can look up an extract by a text query, or find the smallest set of
extracts covering an arbitrary geometry, and download the matching `*.osm.pbf` files.
"""

from osmfinder._results import (
    GeometryCoveringStep,
    OsmfinderDownloadResult,
    OsmfinderGeometryResult,
    OsmfinderQueryResult,
)
from osmfinder._typing import OpenStreetMapExtract, OsmExtractSource
from osmfinder.extract import clear_osm_index_cache
from osmfinder.finder import (
    OsmExtractSourceLike,
    display_available_extracts,
    download,
    download_extract_by_query,
    download_extracts_pbf_files,
    find,
    find_and_download_extracts_pbf_files,
    find_smallest_containing_extracts,
    get_available_extracts,
    get_extract_by_query,
)

__version__ = "0.1.0"

__all__ = [
    "GeometryCoveringStep",
    "OpenStreetMapExtract",
    "OsmExtractSource",
    "OsmExtractSourceLike",
    "OsmfinderDownloadResult",
    "OsmfinderGeometryResult",
    "OsmfinderQueryResult",
    "clear_osm_index_cache",
    "display_available_extracts",
    "download",
    "download_extract_by_query",
    "download_extracts_pbf_files",
    "find",
    "find_and_download_extracts_pbf_files",
    "find_smallest_containing_extracts",
    "get_available_extracts",
    "get_extract_by_query",
]
