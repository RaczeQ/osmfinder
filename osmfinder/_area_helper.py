"""Helper function for vectorized spherical area calculation."""

from collections.abc import Sequence
from typing import overload

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

EARTH_RADIUS_M = 6_371_008.8  # IUGG mean radius — swap for whatever sphere you want


@overload
def calculate_spherical_area(
    geometry: Sequence[BaseGeometry], radius: float = EARTH_RADIUS_M
) -> np.ndarray: ...


@overload
def calculate_spherical_area(geometry: BaseGeometry, radius: float = EARTH_RADIUS_M) -> float: ...


def calculate_spherical_area(
    geometry: BaseGeometry | Sequence[BaseGeometry], radius: float = EARTH_RADIUS_M
) -> float | np.ndarray:
    """
    Vectorized area (km^2) of WGS84 (lon/lat) shapely Polygon(s), spherical approximation.

    Accepts one Polygon or an array-like (list/ndarray/ GeoSeries) of Polygons. Handles holes
    correctly regardless of ring winding order. MultiPolygons are exploded into individual parts
    first.
    """
    is_single = isinstance(geometry, BaseGeometry)
    geometries = np.atleast_1d(np.asarray(geometry, dtype=object))

    if len(geometries) == 0:
        return np.array([], dtype=float)

    parts, part_idx = shapely.get_parts(geometries, return_index=True)

    if len(parts) == 0:
        return np.zeros(len(geometries), dtype=float)

    rings, ring_part_idx = shapely.get_rings(parts, return_index=True)

    if len(rings) == 0:
        return np.zeros(len(geometries), dtype=float)

    is_exterior = np.empty(len(ring_part_idx), dtype=bool)
    is_exterior[0] = True
    is_exterior[1:] = ring_part_idx[1:] != ring_part_idx[:-1]
    sign = np.where(is_exterior, 1.0, -1.0)

    coords, vertex_ring_idx = shapely.get_coordinates(rings, return_index=True)
    lon = np.radians(coords[:, 0])
    lat = np.radians(coords[:, 1])

    lon_next = np.roll(lon, -1)
    lat_next = np.roll(lat, -1)
    edge = (lon_next - lon) * (2.0 + np.sin(lat) + np.sin(lat_next))

    same_ring_next = vertex_ring_idx == np.roll(vertex_ring_idx, -1)
    edge = np.where(same_ring_next, edge, 0.0)

    ring_area = np.abs(np.bincount(vertex_ring_idx, weights=edge, minlength=len(rings))) * (
        radius**2 / 2.0
    )

    part_area = np.bincount(ring_part_idx, weights=sign * ring_area, minlength=len(parts))
    poly_area = np.bincount(part_idx, weights=part_area, minlength=len(geometries))
    poly_area_km2 = poly_area / 1_000_000

    if is_single:
        return float(poly_area_km2[0])
    return poly_area_km2
