"""Tests for GeoJSON parsing."""

from typing import Any

import pytest
from pytest_mock import MockerFixture
from requests.exceptions import HTTPError
from shapely import box
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from osmfinder.parsers.geojson import parse_geojson, parse_geojson_file
from osmfinder.parsers.poly import parse_polygon_file


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


def test_parse_geojson_file_404_returns_none_and_warns(mocker: MockerFixture) -> None:
    """Test if parse_geojson_file returns None and warns on 404."""
    url = "https://example.com/missing.geojson"
    response = mocker.Mock()
    response.status_code = 404
    response.raise_for_status = mocker.Mock(side_effect=HTTPError(response=response))

    mocker.patch("osmfinder._network.requests.get", return_value=response)

    with pytest.warns(UserWarning, match=f"Resource not found \\(404\\): {url}"):
        result = parse_geojson_file(url)
    assert result is None


def test_parse_polygon_file_404_returns_none_and_warns(mocker: MockerFixture) -> None:
    """Test if parse_polygon_file returns None and warns on 404."""
    url = "https://example.com/missing.poly"
    response = mocker.Mock()
    response.status_code = 404
    response.raise_for_status = mocker.Mock(side_effect=HTTPError(response=response))

    mocker.patch("osmfinder._network.requests.get", return_value=response)

    with pytest.warns(UserWarning, match=f"Resource not found \\(404\\): {url}"):
        result = parse_polygon_file(url)
    assert result is None
