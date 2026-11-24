"""Tests for index building, full name generation and geometry repair."""

from pathlib import Path

import numpy as np
from arro3.io import read_parquet, write_parquet
from shapely import GeometryCollection, LineString, MultiPolygon, box, from_wkt, is_valid

from osmfinder._io import read_parquet_index, write_parquet_index
from tests._helpers import _index_from_extracts


def test_proper_full_name() -> None:
    """Test if full names for extracts are properly generated."""
    test_index = _index_from_extracts(
        [
            {"id": "1", "name": "one", "parent": "x"},
            {"id": "2", "name": "two", "parent": "1"},
        ]
    )
    by_id = dict(zip(test_index.ids, test_index.file_names, strict=True))
    assert by_id["2"] == "x_one_two"

    spaced_index = _index_from_extracts(
        [
            {"id": "RW", "name": "Rwanda", "parent": "root"},
            {"id": "RW-02", "name": "Eastern Province", "parent": "RW"},
        ]
    )
    spaced_by_id = dict(zip(spaced_index.ids, spaced_index.file_names, strict=True))
    full_name = spaced_by_id["RW-02"]
    assert full_name == "root_rwanda_eastern_province"
    assert " " not in full_name


def test_from_extracts_repairs_invalid_geometries() -> None:
    """Test if building an index repairs invalid geometries and overlay ops no longer raise."""
    bowtie = from_wkt("POLYGON ((0 0, 1 1, 1 0, 0 1, 0 0))")
    index = _index_from_extracts(
        [
            {"id": "bowtie", "name": "bowtie", "parent": "root", "geometry": bowtie},
            {"id": "valid", "name": "valid", "parent": "root", "geometry": box(2, 2, 3, 3)},
        ]
    )

    assert is_valid(index.geometries).all()


def test_from_extracts_keeps_only_polygons() -> None:
    """Test if an index contains only polygons and multipolygons."""
    polygons = MultiPolygon([box(0, 0, 1, 1), box(5, 5, 6, 6)])
    line = LineString([(9, 8), (9, 9)])
    geometries = {
        # make_valid returns a MultiPolygon and a LineString in a collection for this spike
        "spike": from_wkt(
            "MULTIPOLYGON (((0 0, 1 0, 1 1, 0 1, 0 0)), ((5 5, 6 5, 6 6, 5 6, 5 5, 5 4, 5 5)))"
        ),
        "valid collection": GeometryCollection([polygons, line]),
        "single polygon": GeometryCollection([box(0, 0, 1, 1), line]),
        "multipolygon": polygons,
        "polygon": box(0, 0, 1, 1),
    }
    index = _index_from_extracts(
        [
            {"id": key, "name": key, "parent": "root", "geometry": geometry}
            for key, geometry in geometries.items()
        ]
    )

    result = dict(zip(index.ids, index.geometries, strict=True))
    for key in ("spike", "valid collection", "multipolygon"):
        assert result[key].geom_type == "MultiPolygon"
        assert result[key].equals(polygons)
    for key in ("single polygon", "polygon"):
        assert result[key].geom_type == "Polygon"
        assert result[key].equals(box(0, 0, 1, 1))


def test_file_sizes_are_saved(tmp_path: Path) -> None:
    """Test if known and unknown file sizes survive saving, loading and filtering an index."""
    index = _index_from_extracts(
        [
            {"id": "large", "name": "x", "parent": "root", "geometry": box(0, 0, 2, 2)},
            {"id": "small", "name": "x", "parent": "root", "file_size": 5 * 10**9 + 1},
        ]
    )
    file_path = tmp_path / "index.parquet"
    write_parquet_index(index, file_path)

    loaded_index = read_parquet_index(file_path)

    assert [extract.file_size for extract in loaded_index] == [5 * 10**9 + 1, None]
    assert [e.file_size for e in loaded_index.filter_by_mask(np.array([False, True]))] == [None]
    assert loaded_index.to_geodataframe()["file_size"].count() == 1


def test_index_without_file_sizes_is_loaded(tmp_path: Path) -> None:
    """Test if an index saved by an older version is loaded with unknown file sizes."""
    file_path = tmp_path / "index.parquet"
    write_parquet_index(_index_from_extracts([{"id": "a", "name": "a", "parent": "x"}]), file_path)
    table = read_parquet(file_path).read_all()
    write_parquet(table.select([c for c in table.column_names if c != "file_size"]), file_path)

    assert [extract.file_size for extract in read_parquet_index(file_path)] == [None]
