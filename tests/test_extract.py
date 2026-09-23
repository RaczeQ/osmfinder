"""Tests for converting extract metadata to GeoDataFrames."""

import geopandas as gpd
from shapely.geometry import box

from osmfinder._constants import WGS84_CRS
from osmfinder._results import OsmfinderResult
from osmfinder._typing import OpenStreetMapExtract
from osmfinder.extract import extracts_to_geodataframe


def _make_extracts() -> list[OpenStreetMapExtract]:
    return [
        OpenStreetMapExtract(
            id="extract-a",
            name="Extract A",
            parent="parent-a",
            url="url-a",
            geometry=box(0, 0, 1, 1),
            file_name="extract-a",
        ),
        OpenStreetMapExtract(
            id="extract-b",
            name="Extract B",
            parent="parent-b",
            url="url-b",
            geometry=box(1, 1, 2, 2),
            file_name="extract-b",
        ),
    ]


def test_extracts_to_geodataframe_converts_list() -> None:
    """Convert a list of extract metadata objects."""
    extracts = _make_extracts()

    result = extracts_to_geodataframe(extracts)

    assert isinstance(result, gpd.GeoDataFrame)
    assert list(result.columns) == [
        "id",
        "name",
        "parent",
        "url",
        "geometry",
        "file_name",
    ]
    assert result["id"].tolist() == ["extract-a", "extract-b"]
    assert result["name"].tolist() == ["Extract A", "Extract B"]
    assert result["file_name"].tolist() == ["extract-a", "extract-b"]
    assert result.geometry.iloc[0].equals(extracts[0].geometry)
    assert result.geometry.iloc[1].equals(extracts[1].geometry)
    assert str(result.crs) == WGS84_CRS


def test_extracts_to_geodataframe_converts_index(fake_index) -> None:
    """Convert an extract index."""
    result = extracts_to_geodataframe(fake_index)

    assert isinstance(result, gpd.GeoDataFrame)
    assert result["id"].tolist() == fake_index.ids.tolist()
    assert result["file_name"].tolist() == fake_index.file_names.tolist()
    assert result.geometry.iloc[0].equals(fake_index.geometries[0])
    assert result.geometry.iloc[1].equals(fake_index.geometries[1])
    assert str(result.crs) == WGS84_CRS


def test_extracts_to_geodataframe_converts_result() -> None:
    """Convert the extracts attached to an osmfinder result."""
    extracts = _make_extracts()
    result_input = OsmfinderResult(extracts=extracts, sources_used=[])

    result = extracts_to_geodataframe(result_input)

    assert isinstance(result, gpd.GeoDataFrame)
    assert result["id"].tolist() == [extract.id for extract in extracts]
    assert result["url"].tolist() == [extract.url for extract in extracts]
    assert result.geometry.iloc[0].equals(extracts[0].geometry)
    assert result.geometry.iloc[1].equals(extracts[1].geometry)
    assert str(result.crs) == WGS84_CRS


def test_extracts_to_geodataframe_converts_empty_list() -> None:
    """Convert an empty extract list without losing the schema or CRS."""
    result = extracts_to_geodataframe([])

    assert isinstance(result, gpd.GeoDataFrame)
    assert list(result.columns) == [
        "id",
        "name",
        "parent",
        "url",
        "geometry",
        "file_name",
    ]
    assert result.empty
    assert str(result.crs) == WGS84_CRS
