"""Public API surface, smoke tests and fuzzy edge-case tests."""

import random

import pytest
from shapely.geometry import box

import osmfinder
from osmfinder import OsmExtractSource
from osmfinder.exceptions import OsmExtractMultipleMatchesError, OsmExtractZeroMatchesError


def test_public_api_exposed() -> None:
    """Test if the public API surface is exposed at the package level."""
    for name in (
        "get_extract_by_query",
        "get_available_extracts",
        "download_extract_by_query",
        "find_smallest_containing_extracts",
        "find_extracts_covering_point",
        "find_and_download_extracts_pbf_files",
        "display_available_extracts",
        "clear_osm_index_cache",
    ):
        assert hasattr(osmfinder, name)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("geofabrik", OsmExtractSource.geofabrik),
        ("Geofabrik", OsmExtractSource.geofabrik),
        ("GEOFABRIK", OsmExtractSource.geofabrik),
        ("BBBike", OsmExtractSource.bbbike),
        ("bbbike", OsmExtractSource.bbbike),
        ("osmfr", OsmExtractSource.osm_fr),
        ("OSMFR", OsmExtractSource.osm_fr),
        ("GEO2Day", OsmExtractSource.geo2day),
        ("Movisda-admin", OsmExtractSource.movisda_admin),
        ("Movisda-grid", OsmExtractSource.movisda_grid),
        ("any", OsmExtractSource.any),
        ("ANY", OsmExtractSource.any),
    ],
)
def test_source_enum_parsing(value: str, expected: OsmExtractSource) -> None:
    """OsmExtractSource parses case-insensitively."""
    assert OsmExtractSource(value) == expected


def test_wrong_osm_extract_source() -> None:
    """An unknown source name raises ValueError."""
    with pytest.raises(ValueError):
        OsmExtractSource("test_source")


def test_smoke_end_to_end_query_flow(monkeypatch: pytest.MonkeyPatch, fake_index) -> None:
    """A quick end-to-end sanity check of the public query API."""
    from osmfinder import finder

    monkeypatch.setattr(finder._sources, "_get_index_for_sources", lambda *args: fake_index)

    # Name query.
    result = osmfinder.get_extract_by_query("Small")
    assert result.extract.file_name == "big_small"

    # Geometry query.
    result = osmfinder.find(fake_index.geometries[1], source="any")
    assert {extract.file_name for extract in result.extracts} == {"big_small"}

    # Point query.
    result = osmfinder.find_extracts_covering_point((1.0, 1.0), source="any")
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0].file_name == "big_small"
    assert result[1].file_name == "big"


def test_fuzzy_source_name_parsing() -> None:
    """Random case/whitespace variations of valid source names always parse."""
    rng = random.Random(42)
    valid_names = [source.value for source in OsmExtractSource]

    for _ in range(50):
        name = rng.choice(valid_names)
        mangled = "".join(char.upper() if rng.random() < 0.5 else char.lower() for char in name)
        if rng.random() < 0.3:
            mangled = f"  {mangled}  "
        assert OsmExtractSource(mangled.strip()) == OsmExtractSource(name)


def test_fuzzy_geometry_queries(monkeypatch: pytest.MonkeyPatch, fake_index) -> None:
    """Random small boxes against a fake index never crash and return valid results."""
    from osmfinder import finder

    rng = random.Random(7)
    monkeypatch.setattr(finder._sources, "_get_index_for_sources", lambda *args: fake_index)

    for _ in range(50):
        x = rng.uniform(0, 8)
        y = rng.uniform(0, 8)
        size = rng.uniform(0.01, 1.0)
        geometry = box(x, y, x + size, y + size)

        results = osmfinder.find(geometry, source="any")
        assert isinstance(results.extracts, list)
        assert all(extract.file_name in {"big", "big_small"} for extract in results.extracts)


def test_fuzzy_extract_name_queries(monkeypatch: pytest.MonkeyPatch, fake_index) -> None:
    """Random name fragments raise the right errors or return a valid match."""
    from osmfinder import finder

    rng = random.Random(11)
    monkeypatch.setattr(finder._sources, "_get_index_for_sources", lambda *args: fake_index)

    for _ in range(50):
        query = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(1, 8)))
        try:
            result = osmfinder.get_extract_by_query(query, select_first_match=False)
        except (OsmExtractZeroMatchesError, OsmExtractMultipleMatchesError):
            continue
        assert result.extract.file_name in {"big", "big_small"}


def test_get_available_extracts_returns_list(monkeypatch: pytest.MonkeyPatch, fake_index) -> None:
    """get_available_extracts returns a list of OpenStreetMapExtract objects."""
    from osmfinder import finder

    monkeypatch.setattr(finder._sources, "_get_index_for_sources", lambda *args: fake_index)

    extracts = osmfinder.get_available_extracts("any")
    assert isinstance(extracts, list)
    assert len(extracts) == 2
    assert all(isinstance(e, osmfinder.OpenStreetMapExtract) for e in extracts)
    ids = [e.id for e in extracts]
    assert set(ids) == {"a", "b"}


def test_get_available_extracts_excludes_ids(monkeypatch: pytest.MonkeyPatch, fake_index) -> None:
    """excluded_extracts_ids filters out the specified extracts."""
    from osmfinder import finder

    monkeypatch.setattr(finder._sources, "_get_index_for_sources", lambda *args: fake_index)

    extracts = osmfinder.get_available_extracts("any", excluded_extracts_ids={"a"})
    assert len(extracts) == 1
    assert extracts[0].id == "b"
