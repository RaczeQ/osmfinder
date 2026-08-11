"""Tests for index building, full name generation and geometry repair."""

from shapely import box, from_wkt, is_valid

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
