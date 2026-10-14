"""Tests for converting extract metadata to GeoDataFrames."""

import warnings
from typing import Any

import geopandas as gpd
import numpy as np
import pytest
from pytest_mock import MockerFixture
from shapely.geometry import box

from osmfinder._constants import WGS84_CRS
from osmfinder._results import OsmfinderResult
from osmfinder._typing import OpenStreetMapExtract, OsmExtractsIndex, OsmExtractSource
from osmfinder.exceptions import (
    OsmExtractsIndexesUnavailableError,
    OsmExtractSourceUnavailableWarning,
)
from osmfinder.extract import _load_all_indexes_with_fallback, extracts_to_geodataframe


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


def _single_extract_index(source_name: str) -> OsmExtractsIndex:
    """Build a one-extract OsmExtractsIndex for a given source name."""
    return OsmExtractsIndex(
        ids=np.array([source_name], dtype=object),
        geometries=np.array([box(0, 0, 1, 1)], dtype=object),
        areas=np.array([1.0]),
        file_names=np.array([source_name], dtype=object),
        names=np.array([source_name.capitalize()], dtype=object),
        parents=np.array(["root"], dtype=object),
        urls=np.array([f"http://x/{source_name}.pbf"], dtype=object),
    )


def test_load_all_indexes_with_fallback_skips_unavailable_sources(
    mocker: MockerFixture,
) -> None:
    """Test if unavailable sources are skipped with a warning when loading all indexes."""
    mocker.patch.dict("osmfinder.extract._index_cache", {}, clear=True)

    def unavailable() -> Any:
        raise ConnectionError("server down")

    mocker.patch.dict(
        "osmfinder.extract._REGISTERED_INDEX_LOADERS",
        {
            OsmExtractSource.geofabrik: lambda **kwargs: _single_extract_index("geofabrik"),
            OsmExtractSource.bbbike: lambda **kwargs: _single_extract_index("bbbike"),
            OsmExtractSource.movisda_grid: unavailable,
        },
        clear=True,
    )

    with pytest.warns(OsmExtractSourceUnavailableWarning) as warning_list:
        combined_index, successful_sources = _load_all_indexes_with_fallback()

    assert set(combined_index.ids) == {"geofabrik", "bbbike"}
    assert set(successful_sources) == {OsmExtractSource.geofabrik, OsmExtractSource.bbbike}
    assert any("movisda" in str(w.message).lower() for w in warning_list)


def test_load_all_indexes_with_fallback_raises_when_all_fail(
    mocker: MockerFixture,
) -> None:
    """Test if an error is raised when all sources fail to load."""
    mocker.patch.dict("osmfinder.extract._index_cache", {}, clear=True)

    def unavailable() -> Any:
        raise ConnectionError("server down")

    mocker.patch.dict(
        "osmfinder.extract._REGISTERED_INDEX_LOADERS",
        {
            OsmExtractSource.geofabrik: unavailable,
            OsmExtractSource.bbbike: unavailable,
        },
        clear=True,
    )

    with pytest.raises(OsmExtractsIndexesUnavailableError):
        _load_all_indexes_with_fallback()


def test_load_all_indexes_with_fallback_succeeds_when_all_available(
    mocker: MockerFixture,
) -> None:
    """Test if all sources are loaded successfully when none fail."""
    mocker.patch.dict("osmfinder.extract._index_cache", {}, clear=True)

    mocker.patch.dict(
        "osmfinder.extract._REGISTERED_INDEX_LOADERS",
        {
            OsmExtractSource.geofabrik: lambda **kwargs: _single_extract_index("geofabrik"),
            OsmExtractSource.bbbike: lambda **kwargs: _single_extract_index("bbbike"),
        },
        clear=True,
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", OsmExtractSourceUnavailableWarning)
        combined_index, successful_sources = _load_all_indexes_with_fallback()

    assert set(combined_index.ids) == {"geofabrik", "bbbike"}
    assert set(successful_sources) == {OsmExtractSource.geofabrik, OsmExtractSource.bbbike}
