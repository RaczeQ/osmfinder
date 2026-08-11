"""Tests for GeoJSON parsing."""

from typing import Any

import pytest
from shapely import box
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from osmfinder.parsers.geojson import parse_geojson


@pytest.mark.parametrize(
    "geojson_data,expected_geometry",
    [
        (
            {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "geometry": mapping(box(0, 0, 1, 1)), "properties": {}}
                ],
            },
            box(0, 0, 1, 1),
        ),
        (
            {"type": "Feature", "geometry": mapping(box(2, 2, 3, 3)), "properties": {}},
            box(2, 2, 3, 3),
        ),
        (mapping(box(4, 4, 5, 5)), box(4, 4, 5, 5)),
    ],
)
def test_parse_geojson(geojson_data: Any, expected_geometry: BaseGeometry) -> None:
    """Test if GeoJSON Feature/FeatureCollection/geometry is parsed into a single geometry."""
    parsed_geometry = parse_geojson(geojson_data)
    assert parsed_geometry is not None
    assert parsed_geometry.equals(expected_geometry)


def test_parse_geojson_empty_feature_collection() -> None:
    """Test if an empty FeatureCollection returns None."""
    assert parse_geojson({"type": "FeatureCollection", "features": []}) is None
