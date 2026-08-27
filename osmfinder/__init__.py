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
    find,
    find_extract_by_query,
    find_extracts_by_geometry,
    find_extracts_covering_point,
    get_available_extracts,
)

__version__ = "1.1.0"

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
    "find",
    "find_extract_by_query",
    "find_extracts_by_geometry",
    "find_extracts_covering_point",
    "get_available_extracts",
]
