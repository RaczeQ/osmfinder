"""Tests for tree display and structure of available extracts."""

from typing import Any

import pytest
from pytest_mock import MockerFixture
from rich.console import Console
from shapely import box

from osmfinder._typing import OsmExtractSource
from osmfinder.finder import display_available_extracts
from osmfinder.sources.tree import get_available_extracts_as_rich_tree
from tests._helpers import _index_from_records


@pytest.mark.parametrize(
    "osm_source,use_full_names",
    [
        (OsmExtractSource.geofabrik, False),
        (OsmExtractSource.bbbike, False),
        (OsmExtractSource.osm_fr, False),
        (OsmExtractSource.geo2day, False),
        (OsmExtractSource.movisda_admin, False),
        (OsmExtractSource.movisda_grid, False),
        (OsmExtractSource.any, False),
        (OsmExtractSource.any, True),
    ],
)
def test_extracts_tree_printing(
    capfd: Any, mocker: MockerFixture, osm_source: OsmExtractSource, use_full_names: bool
) -> None:
    """Test if displaying available extracts works."""
    mocker.patch("rich.get_console", return_value=Console(width=999))
    display_available_extracts(osm_source, use_full_names)
    output, error_output = capfd.readouterr()

    assert len(output) > 0

    osm_sources_without_any = [src for src in OsmExtractSource if src != OsmExtractSource.any]

    if osm_source == OsmExtractSource.any:
        assert output.startswith("All extracts")
        assert all(src.value in output for src in osm_sources_without_any)
    else:
        assert output.startswith(osm_source.value)

    if use_full_names:
        lines = output.lower().split("\n")

        assert all(
            any(src.value.lower() in line for src in osm_sources_without_any)
            for line in lines
            if len(line.strip()) > 0 and line != "all extracts"
        )

    assert error_output == ""


def _count_tree_nodes(tree: Any) -> int:
    """Count all descendant nodes of a Rich tree."""
    return len(tree.children) + sum(_count_tree_nodes(child) for child in tree.children)


def _render_rich_tree(tree: Any) -> str:
    """Render a Rich tree to plain text."""
    console = Console(width=999)
    with console.capture() as capture:
        console.print(tree)
    return str(capture.get())


def test_extracts_tree_structure_and_loose_parents() -> None:
    """Test if the tree nests children under parents and attaches loose parents."""
    index = _index_from_records(
        [
            {
                "id": "BBBike_a",
                "name": "a",
                "file_name": "bbbike_a",
                "parent": "BBBike",
                "area": 2.0,
                "url": "http://x/a",
                "geometry": box(0, 0, 1, 1),
            },
            {
                "id": "BBBike_a_x",
                "name": "x",
                "file_name": "bbbike_a_x",
                "parent": "BBBike_a",
                "area": 1.0,
                "url": "http://x/x",
                "geometry": box(0, 0, 1, 1),
            },
            {
                "id": "BBBike_b",
                "name": "b",
                "file_name": "bbbike_b",
                "parent": "BBBike",
                "area": 3.0,
                "url": "http://x/b",
                "geometry": box(0, 0, 1, 1),
            },
            {
                "id": "BBBike_orphan",
                "name": "orphan",
                "file_name": "bbbike_orphan",
                "parent": "BBBike_missing",
                "area": 1.0,
                "url": "http://x/o",
                "geometry": box(0, 0, 1, 1),
            },
        ]
    )

    tree = get_available_extracts_as_rich_tree(
        OsmExtractSource.bbbike, {OsmExtractSource.bbbike: lambda: index}, use_full_names=True
    )
    rendered = _render_rich_tree(tree)

    for token in ("bbbike_a", "bbbike_a_x", "bbbike_b", "bbbike_orphan", "BBBike_missing"):
        assert token in rendered, token
    assert rendered.index("bbbike_a") < rendered.index("bbbike_b")
    assert _count_tree_nodes(tree) == 5


def test_extracts_tree_builds_for_large_flat_index() -> None:
    """Test if a large flat index builds quickly (guards against O(N^2) tree building)."""
    number_of_tiles = 10000
    records = [
        {
            "id": f"Movisda-grid_{i}",
            "name": f"N{i:05d}",
            "file_name": f"movisda-grid_n{i}",
            "parent": "Movisda-grid",
            "area": float(i % 1000 + 1),
            "url": f"http://x/{i}",
            "geometry": box(0, 0, 1, 1),
        }
        for i in range(number_of_tiles)
    ]
    index = _index_from_records(records)

    tree = get_available_extracts_as_rich_tree(
        OsmExtractSource.movisda_grid, {OsmExtractSource.movisda_grid: lambda: index}
    )
    assert _count_tree_nodes(tree) == number_of_tiles
