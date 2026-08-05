"""Offline tests for the osmfinder public API (no network access required)."""

import pathlib

import geopandas as gpd
import pytest
from shapely.geometry import box

import osmfinder
from osmfinder import OpenStreetMapExtract, OsmExtractSource, finder


def _fake_index() -> gpd.GeoDataFrame:
    """A tiny two-extract index: a big region containing a small nested one."""
    data = [
        {
            "id": "a",
            "name": "Big",
            "parent": "root",
            "url": "http://example.test/big.osm.pbf",
            "file_name": "big",
            "area": 100.0,
            "geometry": box(0, 0, 10, 10),
        },
        {
            "id": "b",
            "name": "Small",
            "parent": "a",
            "url": "http://example.test/small.osm.pbf",
            "file_name": "big_small",
            "area": 4.0,
            "geometry": box(0, 0, 2, 2),
        },
    ]
    return gpd.GeoDataFrame(data, geometry="geometry", crs="EPSG:4326")


def test_public_api_exposed() -> None:
    for name in (
        "get_extract_by_query",
        "download_extract_by_query",
        "find_smallest_containing_extracts",
        "find_and_download_extracts_pbf_files",
        "display_available_extracts",
        "clear_osm_index_cache",
    ):
        assert hasattr(osmfinder, name)
    assert osmfinder.find is osmfinder.get_extract_by_query
    assert osmfinder.download is osmfinder.download_extract_by_query


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("geofabrik", OsmExtractSource.geofabrik),
        ("Geofabrik", OsmExtractSource.geofabrik),
        ("BBBike", OsmExtractSource.bbbike),
        ("osmfr", OsmExtractSource.osm_fr),
        ("any", OsmExtractSource.any),
    ],
)
def test_source_enum_parsing(value: str, expected: OsmExtractSource) -> None:
    assert OsmExtractSource(value) == expected


def test_get_extract_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(finder, "_get_index_for_sources", lambda source: _fake_index())
    extract = osmfinder.get_extract_by_query("Small")
    assert isinstance(extract, OpenStreetMapExtract)
    assert extract.file_name == "big_small"
    assert extract.url.endswith("small.osm.pbf")


def test_find_by_geometry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(finder, "_get_index_for_sources", lambda source: _fake_index())
    result = osmfinder.find_smallest_containing_extracts(box(0, 0, 1, 1), source="any")
    assert {extract.file_name for extract in result} == {"big_small"}


def test_index_cache_fgb_gz_roundtrip(tmp_path: pathlib.Path) -> None:
    """The gzip-compressed FlatGeobuf cache reads back identically (no pyarrow involved)."""
    from osmfinder.extract import _read_index, _write_index

    gdf = _fake_index()
    cache_path = tmp_path / "geofabrik_index.fgb.gz"
    _write_index(gdf, cache_path)

    assert cache_path.exists()
    restored = _read_index(cache_path)
    assert len(restored) == len(gdf)
    assert set(restored.columns) == set(gdf.columns)
    assert restored.crs is not None and restored.crs.to_epsg() == 4326
    assert set(restored["file_name"]) == {"big", "big_small"}
