"""Tests for verbose __repr__ output of result classes."""

from pathlib import Path

from shapely.geometry import box

from osmfinder._results import (
    GeometryCoveringStep,
    OsmfinderDownloadResult,
    OsmfinderGeometryResult,
    OsmfinderQueryResult,
    OsmfinderResult,
)
from osmfinder._typing import OpenStreetMapExtract, OsmExtractSource
from osmfinder.extract import OsmExtractsIndex


def _make_extract(idx: str, name: str, file_name: str = "") -> OpenStreetMapExtract:
    """Build a minimal OpenStreetMapExtract for testing."""
    geom = box(0, 0, 1, 1)
    return OpenStreetMapExtract(
        id=idx,
        name=name,
        parent="",
        url="",
        geometry=geom,
        file_name=file_name if file_name else idx,
    )


def _make_index() -> OsmExtractsIndex:
    """Build a tiny two-extract index for testing."""
    extracts = [
        _make_extract("a", "Alpha", "alpha"),
        _make_extract("b", "Beta", "beta"),
    ]
    return OsmExtractsIndex.from_extracts(extracts)


def test_base_repr_single_extract() -> None:
    """A single extract and one source render correctly."""
    result = OsmfinderResult(
        extracts=[_make_extract("a", "Alpha")],
        sources_used=[OsmExtractSource.geofabrik],
    )
    repr_str = repr(result)
    assert "OsmfinderResult" in repr_str
    assert "extracts:" in repr_str
    assert "Alpha" in repr_str
    assert "sources used: Geofabrik" in repr_str


def test_base_repr_no_extracts() -> None:
    """Empty extracts and sources render as none."""
    result = OsmfinderResult(extracts=[], sources_used=[])
    repr_str = repr(result)
    assert "OsmfinderResult" in repr_str
    assert "extracts: none" in repr_str
    assert "sources used: none" in repr_str


def test_base_repr_multiple_sources() -> None:
    """Multiple sources are joined with commas."""
    result = OsmfinderResult(
        extracts=[_make_extract("a", "Alpha")],
        sources_used=[OsmExtractSource.geofabrik, OsmExtractSource.bbbike],
    )
    repr_str = repr(result)
    assert "sources used: Geofabrik, BBBike" in repr_str


def test_query_repr_single_match() -> None:
    """A single matched extract shows the extract line without the multiple-match note."""
    index = _make_index()
    result = OsmfinderQueryResult(
        query="alpha",
        extracts=[index.get_extract_by_index(0)],
        matched_extracts=[index.get_extract_by_index(0)],
        sources_used=[OsmExtractSource.geofabrik],
    )
    repr_str = repr(result)
    assert "OsmfinderQueryResult" in repr_str
    assert "query: alpha" in repr_str
    assert "extract: a — Alpha" in repr_str
    assert "matched extracts: a" in repr_str
    assert "(selected from multiple matches)" not in repr_str


def test_query_repr_multiple_matches() -> None:
    """Multiple matched extracts show the selection note."""
    index = _make_index()
    result = OsmfinderQueryResult(
        query="alpha",
        extracts=[index.get_extract_by_index(0)],
        matched_extracts=[
            index.get_extract_by_index(0),
            index.get_extract_by_index(1),
        ],
        sources_used=[OsmExtractSource.geofabrik],
    )
    repr_str = repr(result)
    assert "OsmfinderQueryResult" in repr_str
    assert "query: alpha" in repr_str
    assert "extract: a — Alpha (selected from multiple matches)" in repr_str
    assert "matched extracts: a, b" in repr_str


def test_query_repr_long_matched_list_ellipsis() -> None:
    """Long matched extract lists are truncated with +N more."""
    many_extracts = [_make_extract(str(i), f"Extract {i}") for i in range(10)]
    result = OsmfinderQueryResult(
        query="test",
        extracts=[many_extracts[0]],
        matched_extracts=many_extracts,
        sources_used=[OsmExtractSource.geofabrik],
    )
    repr_str = repr(result)
    assert "matched extracts: 0, 1, 2, 3, 4, +5 more" in repr_str


def test_geometry_repr_basic() -> None:
    """A geometry result shows extracts, coverage, threshold, steps and sources."""
    index = _make_index()
    step = GeometryCoveringStep(
        extract=index.get_extract_by_index(0),
        iou=0.85,
        selected=True,
        reason="selected",
        geometry_to_cover=box(0, 0, 1, 1),
        intersection_geometry=box(0, 0, 1, 1),
    )
    result = OsmfinderGeometryResult(
        extracts=[index.get_extract_by_index(0)],
        sources_used=[OsmExtractSource.geofabrik],
        input_geometry=box(0, 0, 1, 1),
        covered_geometry=box(0, 0, 1, 1),
        uncovered_geometry=box(0, 0, 0, 0),
        steps=[step],
        iou_threshold=0.01,
    )
    repr_str = repr(result)
    assert "OsmfinderGeometryResult" in repr_str
    assert "extracts:" in repr_str
    assert "a — Alpha" in repr_str
    assert "coverage: 100.0%" in repr_str
    assert "iou threshold: 0.01" in repr_str
    assert "steps:" in repr_str
    assert "iou: 0.8500, selected, selected" in repr_str
    assert "sources used: Geofabrik" in repr_str


def test_geometry_repr_empty_extracts_and_steps() -> None:
    """Empty extracts and steps render as none."""
    result = OsmfinderGeometryResult(
        extracts=[],
        sources_used=[],
        input_geometry=box(0, 0, 1, 1),
        covered_geometry=box(0, 0, 0, 0),
        uncovered_geometry=box(0, 0, 1, 1),
        steps=[],
        iou_threshold=0.01,
    )
    repr_str = repr(result)
    assert "extracts:\n    none" in repr_str
    assert "steps:\n    none" in repr_str
    assert "coverage: 0.0%" in repr_str


def test_step_repr_selected() -> None:
    """A selected step shows a compact one-liner."""
    extract = _make_extract("x", "Xray")
    step = GeometryCoveringStep(
        extract=extract,
        iou=0.9234,
        selected=True,
        reason="first_extract",
        geometry_to_cover=box(0, 0, 1, 1),
        intersection_geometry=box(0, 0, 1, 1),
    )
    repr_str = repr(step)
    assert repr_str == "GeometryCoveringStep(x, Xray)"


def test_step_repr_skipped() -> None:
    """A skipped step shows a compact one-liner."""
    extract = _make_extract("y", "Yankee")
    step = GeometryCoveringStep(
        extract=extract,
        iou=0.0012,
        selected=False,
        reason="low_iou",
        geometry_to_cover=box(0, 0, 1, 1),
        intersection_geometry=box(0, 0, 1, 1),
    )
    repr_str = repr(step)
    assert repr_str == "GeometryCoveringStep(y, Yankee)"


def test_step_repr_indented() -> None:
    """Geometry result indents each line of the step details by 4 spaces."""
    extract = _make_extract("z", "Zulu")
    step = GeometryCoveringStep(
        extract=extract,
        iou=0.5,
        selected=True,
        reason="selected",
        geometry_to_cover=box(0, 0, 1, 1),
        intersection_geometry=box(0, 0, 1, 1),
    )
    result = OsmfinderGeometryResult(
        extracts=[],
        sources_used=[],
        input_geometry=box(0, 0, 1, 1),
        covered_geometry=box(0, 0, 0, 0),
        uncovered_geometry=box(0, 0, 1, 1),
        steps=[step],
        iou_threshold=0.01,
    )
    repr_str = repr(result)
    assert "    z — Zulu" in repr_str
    assert "      iou: 0.5000, selected, selected" in repr_str


def test_download_repr_basic() -> None:
    """A download result shows downloaded paths, unavailable and find result."""
    index = _make_index()
    query_result = OsmfinderQueryResult(
        query="test",
        extracts=[index.get_extract_by_index(0)],
        matched_extracts=[index.get_extract_by_index(0)],
        sources_used=[OsmExtractSource.geofabrik],
    )
    dl_result = OsmfinderDownloadResult(
        find_result=query_result,
        download_paths=[Path("/tmp/alpha.osm.pbf")],
        unavailable_extracts=[],
    )
    repr_str = repr(dl_result)
    assert "OsmfinderDownloadResult" in repr_str
    assert "downloaded:" in repr_str
    assert "/tmp/alpha.osm.pbf" in repr_str
    assert "unavailable:\n    none" in repr_str
    assert "find result:" in repr_str
    assert "OsmfinderQueryResult" in repr_str


def test_download_repr_unavailable_extracts_ellipsis() -> None:
    """Long unavailable extract lists are truncated with +N more."""
    index = _make_index()
    query_result = OsmfinderQueryResult(
        query="test",
        extracts=[index.get_extract_by_index(0)],
        matched_extracts=[index.get_extract_by_index(0)],
        sources_used=[OsmExtractSource.geofabrik],
    )
    unavailable = [_make_extract(str(i), f"Extract {i}") for i in range(10)]
    dl_result = OsmfinderDownloadResult(
        find_result=query_result,
        download_paths=[Path("/tmp/alpha.osm.pbf")],
        unavailable_extracts=unavailable,
    )
    repr_str = repr(dl_result)
    assert "unavailable:\n    0, 1, 2, 3, 4, +5 more" in repr_str


def test_download_repr_no_downloads() -> None:
    """Empty download paths render as none."""
    index = _make_index()
    query_result = OsmfinderQueryResult(
        query="test",
        extracts=[index.get_extract_by_index(0)],
        matched_extracts=[index.get_extract_by_index(0)],
        sources_used=[OsmExtractSource.geofabrik],
    )
    dl_result = OsmfinderDownloadResult(
        find_result=query_result,
        download_paths=[],
        unavailable_extracts=[],
    )
    repr_str = repr(dl_result)
    assert "downloaded:\n    none" in repr_str
