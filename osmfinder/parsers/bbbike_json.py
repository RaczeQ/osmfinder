"""BBBike region boundary JSON file parser function."""

import warnings
from typing import Any

from requests.exceptions import HTTPError
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from osmfinder._constants import OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS, USER_AGENT
from osmfinder._network import get_with_retries

__all__ = ["parse_bbbike_json_file"]


def parse_bbbike_json_file(json_url: str) -> BaseGeometry | None:  # pragma: no cover
    """
    Parse a BBBike region boundary JSON file from URL into a geometry.

    Args:
        json_url (str): URL to load a boundary JSON file.

    Returns:
        BaseGeometry | None: Parsed geometry, or `None` if the request returns 404 not found.
    """
    result = get_with_retries(
        json_url,
        headers={"User-Agent": USER_AGENT},
        timeout=OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS,
    )
    try:
        result.raise_for_status()
    except HTTPError as exc:
        if exc.response.status_code == 404:
            warnings.warn(f"Resource not found (404): {json_url}", UserWarning, stacklevel=2)
            return None
        raise
    return parse_bbbike_json(result.json())


def parse_bbbike_json(data: list[Any]) -> BaseGeometry:
    """
    Parse a BBBike region boundary (e.g. https://data.bbbike.org/osm/region/europe/europe.json).

    The file is a list of polygons, each polygon is a list of rings (outer ring first,
    then holes) and each ring is a list of `[latitude, longitude]` points.
    Polygons can overlap, so they are merged into a single geometry.

    Args:
        data (list[Any]): Parsed JSON content.

    Returns:
        BaseGeometry: Merged region geometry.
    """
    polygons = [
        Polygon(
            [(lon, lat) for lat, lon in rings[0]],
            [[(lon, lat) for lat, lon in hole] for hole in rings[1:]],
        )
        for rings in data
    ]
    return unary_union(polygons)
