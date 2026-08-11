"""Tests for cache saving, loading, validation and index download."""

import datetime
import tempfile
from pathlib import Path

import numpy as np
import pytest
from dateutil.relativedelta import relativedelta
from pytest_mock import MockerFixture
from shapely import box, to_wkb

from osmfinder._io import write_parquet_index
from osmfinder._typing import OpenStreetMapExtract, OsmExtractSource
from osmfinder.exceptions import (
    MissingOsmCacheWarning,
    OldOsmCacheWarning,
    OsmExtractIndexOutdatedWarning,
)
from osmfinder.extract import (
    _download_precalculated_index_from_github,
    _get_global_cache_file_path,
    _get_local_cache_file_path,
    clear_osm_index_cache,
)
from osmfinder.finder import display_available_extracts
from osmfinder.sources.bbbike import BBBIKE_EXTRACTS_INDEX_URL, _load_bbbike_index
from osmfinder.sources.geofabrik import _load_geofabrik_index


def test_proper_cache_saving() -> None:
    """Test if file is saved in cache properly."""
    save_path = _get_global_cache_file_path(OsmExtractSource.geofabrik)
    loaded_index = _load_geofabrik_index()
    assert save_path.exists()
    assert len(loaded_index.ids) > 0


def test_wrong_cached_index() -> None:
    """Test if cached file with missing columns is redownloaded again."""
    save_path = _get_global_cache_file_path(OsmExtractSource.geofabrik)

    first_index = _load_geofabrik_index()
    write_parquet_index(first_index, save_path)

    from arro3.core import Array, Table
    from arro3.io import write_parquet

    geometry_wkb = np.array([to_wkb(g) for g in first_index.geometries], dtype=object)
    bad_table = Table.from_arrays(
        [
            Array.from_numpy(first_index.names),
            Array.from_numpy(first_index.file_names),
            Array.from_numpy(first_index.parents),
            Array.from_numpy(first_index.areas),
            Array.from_numpy(first_index.urls),
            Array.from_numpy(geometry_wkb),
        ],
        names=["name", "file_name", "parent", "area", "url", "geometry"],
    )
    write_parquet(bad_table, save_path, key_value_metadata={"geo": "{}"})

    with pytest.warns(OsmExtractIndexOutdatedWarning):
        second_index = _load_geofabrik_index()

    assert len(second_index.ids) > 0


def test_generate_index_warning(mocker: MockerFixture) -> None:
    """Test if index generation results in warning."""
    extract_source = OsmExtractSource.bbbike
    global_path = _get_global_cache_file_path(extract_source)
    local_path = _get_local_cache_file_path(extract_source)

    global_moved_path = None
    local_moved_path = None
    if global_path.exists():
        global_moved_path = global_path.with_name("bbbike_index_moved.parquet")
        global_path.rename(global_moved_path)

    if local_path.exists():
        local_moved_path = local_path.with_name("bbbike_index_moved.parquet")
        local_path.rename(local_moved_path)

    try:
        mocker.patch(
            "osmfinder.sources.bbbike._iterate_bbbike_index",
            return_value=[
                OpenStreetMapExtract(
                    id="bbbike_test",
                    name="test",
                    parent="bbbike",
                    url="test_url",
                    geometry=box(0, 0, 1, 1),
                )
            ],
        )
        mocker.patch("osmfinder.sources.bbbike.BBBIKE_INDEX", new=None)
        with pytest.warns(MissingOsmCacheWarning):
            _load_bbbike_index(force_recalculation=True)

    finally:
        if global_moved_path is not None:
            global_path.unlink(missing_ok=True)
            global_moved_path.rename(global_path)
            global_moved_path.unlink(missing_ok=True)

        if local_moved_path is not None:
            local_path.unlink(missing_ok=True)
            local_moved_path.rename(local_path)
            local_moved_path.unlink(missing_ok=True)


def test_old_index_warning(mocker: MockerFixture) -> None:
    """Test if old index results in warning."""
    extract_source = OsmExtractSource.bbbike

    mocker.patch(
        "osmfinder.sources.bbbike._iterate_bbbike_index",
        return_value=[
            OpenStreetMapExtract(
                id="bbbike_test",
                name="test",
                parent="bbbike",
                url="test_url",
                geometry=box(0, 0, 1, 1),
            )
        ],
    )
    mocker.patch(
        "osmfinder.extract._get_file_creation_date",
        return_value=datetime.datetime.now() - relativedelta(years=1, days=1),
    )
    mocker.patch("osmfinder.sources.bbbike.BBBIKE_INDEX", new=None)

    with pytest.warns(OldOsmCacheWarning):
        display_available_extracts(source=extract_source)


def test_cache_clearing() -> None:
    """Test if cache clearing works."""
    extract_source = OsmExtractSource.bbbike
    global_path = _get_global_cache_file_path(extract_source)
    local_path = _get_local_cache_file_path(extract_source)

    global_moved_path = None
    local_moved_path = None
    if global_path.exists():
        global_moved_path = global_path.with_name("bbbike_index_moved.parquet")
        global_path.rename(global_moved_path)

    if local_path.exists():
        local_moved_path = local_path.with_name("bbbike_index_moved.parquet")
        local_path.rename(local_moved_path)

    clear_osm_index_cache(extract_source)

    assert not global_path.exists()
    assert not local_path.exists()

    if global_moved_path is not None:
        global_moved_path.rename(global_path)
        global_moved_path.unlink(missing_ok=True)

    if local_moved_path is not None:
        local_moved_path.rename(local_path)
        local_moved_path.unlink(missing_ok=True)


def test_index_download() -> None:
    """Test if downloading precalculated OSM index from Github works."""
    global_bbbike_cache_file_path = _get_global_cache_file_path(OsmExtractSource.bbbike)
    with tempfile.TemporaryDirectory(dir=Path(__file__).parent.resolve()) as tmp_dir_name:
        tmp_file_path = Path(tmp_dir_name) / global_bbbike_cache_file_path.name
        _download_precalculated_index_from_github(tmp_file_path)

        clear_osm_index_cache(OsmExtractSource.bbbike)
        assert not global_bbbike_cache_file_path.exists()
        _load_bbbike_index(force_recalculation=False)

        assert tmp_file_path.read_bytes() == global_bbbike_cache_file_path.read_bytes(), (
            "Mismatch between downloaded and local index files."
        )


def test_written_index_is_valid_geoparquet_1_1(tmp_path: Path) -> None:
    """Test if the written index is a valid GeoParquet 1.1 file."""
    import geoparquet_io as gpio

    from tests._helpers import _index_from_records

    index = _index_from_records([
        {"id": "a", "name": "A", "file_name": "a", "parent": "root", "area": 1.0, "url": "http://x/a", "geometry": box(0, 0, 1, 1)},
        {"id": "b", "name": "B", "file_name": "b", "parent": "root", "area": 4.0, "url": "http://x/b", "geometry": box(10, 10, 12, 12)},
    ])

    cache_path = tmp_path / "index.parquet"
    write_parquet_index(index, cache_path)

    table = gpio.read(str(cache_path))
    result = table.validate()
    assert result.passed(), f"GeoParquet validation failed: {result.failures()}"
