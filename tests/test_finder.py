"""Tests for finding extracts by name, geometry and source resolution."""

from contextlib import nullcontext as does_not_raise
from typing import Any

import pytest
from pytest_mock import MockerFixture
from rq_geo_toolkit.geocode import geocode_to_geometry
from shapely import box, from_wkt
from shapely.geometry import mapping

from osmfinder._typing import OsmExtractSource
from osmfinder.exceptions import (
    GeometryNotCoveredError,
    GeometryNotCoveredWarning,
    OsmExtractMultipleMatchesError,
    OsmExtractMultipleMatchesWarning,
    OsmExtractsIndexesUnavailableError,
    OsmExtractSourceUnavailableWarning,
    OsmExtractZeroMatchesError,
)
from osmfinder.finder import (
    _get_index_for_sources,
    _resolve_extract_sources,
    find_extract_by_query,
    find_extracts_by_geometry,
    find_extracts_covering_point,
)
from tests._helpers import _index_from_extracts, _index_from_records


@pytest.mark.parametrize(
    "source,expected_sources",
    [
        ("geofabrik", [OsmExtractSource.geofabrik]),
        (OsmExtractSource.bbbike, [OsmExtractSource.bbbike]),
        ("GEO2Day", [OsmExtractSource.geo2day]),
        ("movisda-admin", [OsmExtractSource.movisda_admin]),
        (
            "any",
            [
                OsmExtractSource.bbbike,
                OsmExtractSource.geofabrik,
                OsmExtractSource.osm_fr,
                OsmExtractSource.geo2day,
                OsmExtractSource.movisda_admin,
                OsmExtractSource.movisda_grid,
            ],
        ),
        ("GEO2Day,Movisda-grid", [OsmExtractSource.geo2day, OsmExtractSource.movisda_grid]),
        (["bbbike", "osmfr"], [OsmExtractSource.bbbike, OsmExtractSource.osm_fr]),
        (
            [OsmExtractSource.bbbike, OsmExtractSource.osm_fr],
            [OsmExtractSource.bbbike, OsmExtractSource.osm_fr],
        ),
        ("bbbike,osmfr", [OsmExtractSource.bbbike, OsmExtractSource.osm_fr]),
        ("bbbike, osmfr, bbbike", [OsmExtractSource.bbbike, OsmExtractSource.osm_fr]),
        (
            ["geofabrik", "any"],
            [
                OsmExtractSource.geofabrik,
                OsmExtractSource.bbbike,
                OsmExtractSource.osm_fr,
                OsmExtractSource.geo2day,
                OsmExtractSource.movisda_admin,
                OsmExtractSource.movisda_grid,
            ],
        ),
        ("BBBike", [OsmExtractSource.bbbike]),
    ],
)
def test_resolve_extract_sources(source: Any, expected_sources: list[OsmExtractSource]) -> None:
    """Test if source specifications are normalized into concrete sources."""
    resolved_sources = _resolve_extract_sources(source)
    # Order is not significant (the combined index is sorted by area downstream),
    # but the result must be deduplicated.
    assert len(resolved_sources) == len(set(resolved_sources))
    assert set(resolved_sources) == set(expected_sources)


@pytest.mark.parametrize("source", ["", "nonexistent_source"])
def test_resolve_extract_sources_raises_on_invalid(source: str) -> None:
    """Test if invalid or empty source specifications raise ValueError."""
    with pytest.raises(ValueError):
        _resolve_extract_sources(source)


def _single_extract_index(source_name: str) -> "Any":
    """Build a one-extract OsmExtractsIndex for a given source name."""
    return _index_from_records(
        [
            {
                "id": source_name,
                "name": source_name,
                "file_name": source_name,
                "parent": "root",
                "area": 1.0,
                "url": f"http://x/{source_name}.pbf",
                "geometry": box(0, 0, 1, 1),
            }
        ]
    )


def test_get_index_for_multiple_sources(mocker: MockerFixture) -> None:
    """Test if indexes for multiple sources are concatenated."""
    mocker.patch.dict(
        "osmfinder.finder.OSM_EXTRACT_SOURCE_INDEX_FUNCTION",
        {
            OsmExtractSource.bbbike: lambda: _single_extract_index("bbbike"),
            OsmExtractSource.geofabrik: lambda: _single_extract_index("geofabrik"),
            OsmExtractSource.osm_fr: lambda: _single_extract_index("osmfr"),
        },
        clear=True,
    )

    single_index = _get_index_for_sources("bbbike")
    assert list(single_index.ids) == ["bbbike"]

    combined_index = _get_index_for_sources(["bbbike", "osmfr"])
    assert set(combined_index.ids) == {"bbbike", "osmfr"}


def test_get_index_for_sources_skips_unavailable(mocker: MockerFixture) -> None:
    """Test if unavailable sources (offline) are skipped with a warning for multi-source."""
    from requests.exceptions import ConnectionError as RequestsConnectionError

    def unavailable() -> "Any":
        raise RequestsConnectionError("offline")

    mocker.patch.dict(
        "osmfinder.finder.OSM_EXTRACT_SOURCE_INDEX_FUNCTION",
        {
            OsmExtractSource.geofabrik: lambda: _single_extract_index("geofabrik"),
            OsmExtractSource.bbbike: lambda: _single_extract_index("bbbike"),
            OsmExtractSource.movisda_grid: unavailable,
        },
        clear=True,
    )

    # Multi-source: the unavailable source is skipped, the rest is returned with a warning.
    with pytest.warns(OsmExtractSourceUnavailableWarning):
        combined_index = _get_index_for_sources(
            [OsmExtractSource.geofabrik, OsmExtractSource.bbbike, OsmExtractSource.movisda_grid]
        )
    assert set(combined_index.ids) == {"geofabrik", "bbbike"}


def test_get_index_for_sources_raises_when_all_unavailable(mocker: MockerFixture) -> None:
    """Test if an error is raised when no requested source can be loaded."""
    from requests.exceptions import ConnectionError as RequestsConnectionError

    def unavailable() -> "Any":
        raise RequestsConnectionError("offline")

    mocker.patch.dict(
        "osmfinder.finder.OSM_EXTRACT_SOURCE_INDEX_FUNCTION",
        {OsmExtractSource.geofabrik: unavailable, OsmExtractSource.bbbike: unavailable},
        clear=True,
    )

    with (
        pytest.warns(OsmExtractSourceUnavailableWarning),
        pytest.raises(OsmExtractsIndexesUnavailableError),
    ):
        _get_index_for_sources([OsmExtractSource.geofabrik, OsmExtractSource.bbbike])


def test_get_index_for_single_source_propagates_error(mocker: MockerFixture) -> None:
    """Test if a single requested source that can't load raises (no silent degradation)."""
    from requests.exceptions import ConnectionError as RequestsConnectionError

    def unavailable() -> "Any":
        raise RequestsConnectionError("offline")

    mocker.patch.dict(
        "osmfinder.finder.OSM_EXTRACT_SOURCE_INDEX_FUNCTION",
        {OsmExtractSource.geofabrik: unavailable},
        clear=True,
    )

    with pytest.raises(RequestsConnectionError):
        _get_index_for_sources(OsmExtractSource.geofabrik)


@pytest.mark.parametrize(
    "expectation,allow_uncovered_geometry,geometry_coverage_iou_threshold",
    [
        (pytest.raises(GeometryNotCoveredError), False, 0.01),
        (pytest.raises(ValueError), False, -0.1),
        (pytest.raises(ValueError), False, 1.2),
        (pytest.raises(ValueError), True, 1.2),
        (pytest.warns(GeometryNotCoveredWarning), True, 0.01),
    ],
)
def test_uncovered_geometry_extract(
    expectation: Any, allow_uncovered_geometry: bool, geometry_coverage_iou_threshold: float
) -> None:
    """Test if raises errors as expected when geometry can't be covered."""
    with expectation:
        geometry = from_wkt(
            "POLYGON ((-43.064 29.673, -43.064 29.644, -43.017 29.644,"
            " -43.017 29.673, -43.064 29.673))"
        )
        find_extracts_by_geometry(
            geometry=geometry,
            source="any",
            allow_uncovered_geometry=allow_uncovered_geometry,
            geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
        )


def test_excluded_extracts_ids() -> None:
    """Test if excluded extracts are skipped and coverage is recalculated."""
    from rq_geo_toolkit.geocode import geocode_to_geometry

    geometry = geocode_to_geometry("Andorra")

    result = find_extracts_by_geometry(geometry, "geofabrik")
    extracts = result.extracts
    assert [extract.file_name for extract in extracts] == ["geofabrik_europe_andorra"]

    excluded_extracts_ids = {extracts[0].id}
    fallback_result = find_extracts_by_geometry(
        geometry,
        source="geofabrik",
        excluded_extracts_ids=excluded_extracts_ids,
    )
    fallback_extracts = fallback_result.extracts

    fallback_extracts_ids = {extract.id for extract in fallback_extracts}
    assert excluded_extracts_ids.isdisjoint(fallback_extracts_ids)
    assert fallback_extracts


def test_select_first_match(mocker: MockerFixture) -> None:
    """Test if select_first_match picks the smallest-area match with a warning."""
    import warnings

    index = _index_from_extracts(
        [
            {
                "id": "geo2day_x_vatican_city",
                "name": "Vatican City",
                "parent": "a",
                "geometry": box(0, 0, 2, 2),
            },
            {
                "id": "osmfr_x_vatican_city",
                "name": "Vatican City",
                "parent": "b",
                "geometry": box(0, 0, 1, 1),
            },
            {
                "id": "Geofabrik_enfield",
                "name": "enfield",
                "parent": "c",
                "geometry": box(0, 0, 10, 10),
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)

    # Default (True): selects the smallest-area match (osmfr, box 0,0,1,1) and warns.
    with pytest.warns(OsmExtractMultipleMatchesWarning):
        result = find_extract_by_query("Vatican City")
    assert result.extract.id == "osmfr_x_vatican_city"

    # False: raises as before.
    with pytest.raises(OsmExtractMultipleMatchesError):
        find_extract_by_query("Vatican City", select_first_match=False)

    # Single match: no warning regardless.
    with warnings.catch_warnings():
        warnings.simplefilter("error", OsmExtractMultipleMatchesWarning)
        assert find_extract_by_query("enfield").extract.id == "Geofabrik_enfield"


@pytest.mark.parametrize(
    "query,source,expectation,matched_id,exception_values",
    [
        (
            "geofabrik_north-america_us",
            "geofabrik",
            does_not_raise(),
            "Geofabrik_us",
            [],
        ),
        (
            "geofabrik_north-america_us",
            "any",
            does_not_raise(),
            "Geofabrik_us",
            [],
        ),
        (
            "London",
            "BBBike",
            does_not_raise(),
            "BBBike_London",
            [],
        ),
        (
            "LONDON",
            "BBBike",
            does_not_raise(),
            "BBBike_London",
            [],
        ),
        (
            "   tete  ",
            "any",
            does_not_raise(),
            "osmfr_africa_mozambique_tete",
            [],
        ),
        (
            "northeast",
            "any",
            pytest.raises(OsmExtractMultipleMatchesError),
            "",
            [
                "geo2day_south_america_brazil_northeast",
                "osmfr_north-america_us-midwest_illinois_northeast",
                "osmfr_north-america_us-south_florida_northeast",
                "osmfr_north-america_us-south_georgia_northeast",
                "osmfr_north-america_us-south_north-carolina_northeast",
                "osmfr_north-america_us-west_colorado_northeast",
                "osmfr_south-america_brazil_northeast",
            ],
        ),
        (
            "asia",
            "any",
            pytest.raises(OsmExtractMultipleMatchesError),
            "",
            ["geo2day_asia", "geofabrik_asia", "osmfr_asia"],
        ),
        (
            "nrth",
            "any",
            pytest.raises(OsmExtractZeroMatchesError),
            "",
            [
                "osmfr_north-america_us-midwest_illinois_north",
                "movisda-admin_guinea-bissau_north",
                "movisda-admin_burkina_faso_north",
                "movisda-admin_cameroon_north",
                "osmfr_north-america_us-south_texas_north",
                "geo2day_south_america_brazil_north",
                "osmfr_south-america_brazil_north",
            ],
        ),
        (
            "prlnd",
            "any",
            pytest.raises(OsmExtractZeroMatchesError),
            "",
            [
                "movisda-admin_jamaica_portland",
                "bbbike_portland",
                "movisda-admin_poland",
                "geo2day_europe_poland",
                "osmfr_europe_poland",
                "geofabrik_europe_poland",
            ],
        ),
        (
            "empty_extract",
            "any",
            pytest.raises(OsmExtractZeroMatchesError),
            "",
            [],
        ),
    ],
)
def test_extracts_finding(
    query: str,
    source: str,
    expectation: Any,
    matched_id: str,
    exception_values: list[str],
) -> None:
    """Test if extracts finding by name works."""
    with expectation as exception_info:
        # select_first_match=False so multiple matches still raise.
        result = find_extract_by_query(query, source, select_first_match=False)
        # if properly found - check id
        assert result.extract.id == matched_id

    # if threw exception - check resulting arrays
    if exception_info is not None:
        assert exception_info.value.matching_full_names == exception_values


def test_request_timeout_is_passed(mocker: MockerFixture) -> None:
    """Test if HTTP requests are issued with a timeout."""
    import osmfinder.parsers.geojson as geojson_parser_module
    from osmfinder._constants import OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS

    captured_kwargs: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> Any:
        captured_kwargs.update(kwargs)
        response = mocker.Mock()
        response.status_code = 200
        response.raise_for_status = lambda: None
        response.json = lambda: {
            "type": "Feature",
            "geometry": mapping(box(0, 0, 1, 1)),
            "properties": {},
        }
        return response

    mocker.patch("osmfinder._network.requests.get", side_effect=fake_get)
    geojson_parser_module.parse_geojson_file("http://example.com/extent.geojson")

    assert captured_kwargs.get("timeout") == OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    "source,geometry,expected_extract_id",
    [
        (
            "any",
            from_wkt(
                "POLYGON ((12.450637854252449 41.904910802544634,"
                " 12.450637854252449 41.901790362263796,"
                " 12.455878610023916 41.901790362263796, 12.455878610023916 41.904910802544634,"
                " 12.450637854252449 41.904910802544634))"
            ),
            "GEO2Day_europe_vatican_city",
        ),
        (
            "Geofabrik",
            from_wkt(
                "POLYGON ((12.450637854252449 41.904910802544634,"
                " 12.450637854252449 41.901790362263796,"
                " 12.455878610023916 41.901790362263796, 12.455878610023916 41.904910802544634,"
                " 12.450637854252449 41.904910802544634))"
            ),
            "Geofabrik_centro",
        ),
        (
            "any",
            from_wkt(
                "POLYGON ((-0.1514787822171684 51.49843445562462,"
                " -0.1514787822171684 51.48926140694954,"
                " -0.1293785532031677 51.48926140694954, -0.1293785532031677 51.49843445562462,"
                " -0.1514787822171684 51.49843445562462))"
            ),
            "Geofabrik_greater-london",
        ),
        (
            "BBBike",
            from_wkt(
                "POLYGON ((-0.1514787822171684 51.49843445562462,"
                " -0.1514787822171684 51.48926140694954,"
                " -0.1293785532031677 51.48926140694954, -0.1293785532031677 51.49843445562462,"
                " -0.1514787822171684 51.49843445562462))"
            ),
            "BBBike_London",
        ),
        (
            "any",
            from_wkt(
                "POLYGON ((-123.15817514738828 49.29493379142323,"
                " -123.15817514738828 49.23700029433431,"
                " -123.07449492760279 49.23700029433431, -123.07449492760279 49.29493379142323,"
                " -123.15817514738828 49.29493379142323))"
            ),
            "BBBike_Vancouver",
        ),
        (
            "osmfr",
            from_wkt(
                "POLYGON ((-123.15817514738828 49.29493379142323,"
                " -123.15817514738828 49.23700029433431,"
                " -123.07449492760279 49.23700029433431, -123.07449492760279 49.29493379142323,"
                " -123.15817514738828 49.29493379142323))"
            ),
            "osmfr_north-america_canada_british_columbia",
        ),
    ],
)
def test_single_smallest_extract(source: str, geometry: Any, expected_extract_id: str) -> None:
    """Test if extracts matching works correctly for geometries within borders."""
    result = find_extracts_by_geometry(geometry, source)
    assert len(result.extracts) == 1
    assert result.extracts[0].id == expected_extract_id


@pytest.mark.parametrize(
    "source,geometry,geometry_coverage_iou_threshold,expected_extract_file_names",
    [
        (
            "osmfr",
            from_wkt(
                "POLYGON ((1.382599544073372 42.67676873293743,"
                " 1.382599544073372 42.40065303248514,"
                " 1.8092269635579328 42.40065303248514, 1.8092269635579328 42.67676873293743,"
                " 1.382599544073372 42.67676873293743))"
            ),
            0.01,
            [
                "osmfr_europe_andorra",
                "osmfr_europe_france_midi_pyrenees_ariege",
                "osmfr_europe_france_languedoc_roussillon_pyrenees_orientales",
                "osmfr_europe_spain_catalunya_lleida",
            ],
        ),
        (
            "any",
            from_wkt(
                "POLYGON ((1.382599544073372 42.67676873293743,"
                " 1.382599544073372 42.40065303248514,"
                " 1.8092269635579328 42.40065303248514, 1.8092269635579328 42.67676873293743,"
                " 1.382599544073372 42.67676873293743))"
            ),
            0,
            [
                "movisda-grid_n42w001",
            ],
        ),
        (
            "geofabrik",
            geocode_to_geometry("Andorra"),
            0.01,
            ["geofabrik_europe_andorra"],
        ),
        (
            "osmfr",
            geocode_to_geometry("Andorra"),
            0,
            ["osmfr_europe_andorra"],
        ),
        (
            "any",
            box(14.456635, 50.686018, 15.247650, 51.140586),
            0,
            ["movisda-grid_n51w015", "bbbike_goerlitz", "geo2day_europe_czech_republic_liberecky"],
        ),
    ],
)
def test_multiple_smallest_extracts(
    source: str,
    geometry: Any,
    geometry_coverage_iou_threshold: float,
    expected_extract_file_names: list[str],
) -> None:
    """Test if extracts matching works correctly for geometries between borders."""
    result = find_extracts_by_geometry(
        geometry, source, geometry_coverage_iou_threshold=geometry_coverage_iou_threshold
    )
    assert sorted(extract.file_name for extract in result.extracts) == sorted(
        expected_extract_file_names
    )


def test_geometry_covering_step_reasons() -> None:
    """Test if GeometryCoveringStep reasons are set correctly for all states."""
    geometry = from_wkt("POLYGON ((9.8 47.2, 9.8 47.6, 9.4 47.6, 9.4 47.2, 9.8 47.2))")
    result = find_extracts_by_geometry(geometry, "any")
    assert len(result.extracts) == 4
    assert len(result.steps) == 5

    reasons = {step.extract.id: (step.selected, step.reason) for step in result.steps}
    assert reasons["GEO2Day_europe_austria_vorarlberg"] == (True, "first_extract")
    assert reasons["osmfr_europe_switzerland_saint_gallen"] == (True, "selected")
    assert reasons["Movisda-admin_LI"] == (True, "selected")
    assert reasons["osmfr_europe_switzerland_thurgau"] == (False, "redundant")
    assert reasons["BBBike_Konstanz"] == (True, "selected")


def test_geometry_covering_step_low_iou() -> None:
    """Test if low IoU extracts are marked with reason='low_iou'."""
    geometry = box(7.40, 43.71, 7.44, 43.75)
    result = find_extracts_by_geometry(geometry, "Geofabrik")
    low_iou_steps = [step for step in result.steps if step.reason == "low_iou"]
    assert len(low_iou_steps) >= 1
    for step in low_iou_steps:
        assert step.selected is False
        assert step.iou < 0.01


def test_force_single_result_complete_coverage(mocker: MockerFixture) -> None:
    """Test that force_single_result returns one extract that completely covers the geometry."""
    geometry = box(0, 0, 2, 2)
    index = _index_from_extracts(
        [
            {
                "id": "extract_a",
                "name": "Extract A",
                "parent": "root",
                "geometry": box(0, 0, 10, 10),
            },
            {
                "id": "extract_b",
                "name": "Extract B",
                "parent": "root",
                "geometry": box(0, 0, 3, 3),
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)
    result = find_extracts_by_geometry(geometry, force_single_result=True)
    assert len(result.extracts) == 1
    assert result.extracts[0].id == "extract_b"


def test_force_single_result_default_threshold(mocker: MockerFixture) -> None:
    """Test force_single_result with default 0.99 threshold returns one extract."""
    geometry = box(0, 0, 2, 2)
    index = _index_from_extracts(
        [
            {
                "id": "extract_a",
                "name": "Extract A",
                "parent": "root",
                "geometry": box(0, 0, 10, 10),
            },
            {
                "id": "extract_b",
                "name": "Extract B",
                "parent": "root",
                "geometry": box(0, 0, 3, 3),
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)
    result = find_extracts_by_geometry(geometry, force_single_result=True)
    assert len(result.extracts) == 1
    assert result.extracts[0].id == "extract_b"


def test_force_single_result_selects_highest_iou(mocker: MockerFixture) -> None:
    """Test that force_single_result returns the extract with the highest IoU."""
    geometry = box(0, 0, 2, 2)
    index = _index_from_extracts(
        [
            {
                "id": "extract_small",
                "name": "Small extract",
                "parent": "root",
                "geometry": box(0, 0, 3, 3),
            },
            {
                "id": "extract_tight",
                "name": "Tight extract",
                "parent": "root",
                "geometry": box(0, 0, 2.1, 2.1),
            },
            {
                "id": "extract_large",
                "name": "Large extract",
                "parent": "root",
                "geometry": box(0, 0, 10, 10),
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)
    result = find_extracts_by_geometry(geometry, force_single_result=True)
    assert len(result.extracts) == 1
    assert result.extracts[0].id == "extract_tight"
    assert result.steps[0].iou > 0.9


def test_force_single_result_with_095_threshold(mocker: MockerFixture) -> None:
    """Test force_single_result with 0.95 threshold is less strict than 0.99."""
    geometry = box(0, 0, 2, 2)
    index = _index_from_extracts(
        [
            {
                "id": "extract_small",
                "name": "Small extract",
                "parent": "root",
                "geometry": box(0, 0, 3, 3),
            },
            {
                "id": "extract_tight",
                "name": "Tight extract",
                "parent": "root",
                "geometry": box(0, 0, 2.1, 2.1),
            },
            {
                "id": "extract_large",
                "name": "Large extract",
                "parent": "root",
                "geometry": box(0, 0, 10, 10),
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)
    result_099 = find_extracts_by_geometry(
        geometry, force_single_result=True, single_result_iou_threshold=0.99
    )
    result_095 = find_extracts_by_geometry(
        geometry, force_single_result=True, single_result_iou_threshold=0.95
    )
    assert len(result_099.extracts) == 1
    assert len(result_095.extracts) == 1


def test_force_single_result_threshold_rejects_low_iou(mocker: MockerFixture) -> None:
    """Test that force_single_result with strict threshold rejects low IoU extracts."""
    geometry = box(0, 0, 2, 2)
    index = _index_from_extracts(
        [
            {
                "id": "extract_tight",
                "name": "Tight extract",
                "parent": "root",
                "geometry": box(0, 0, 2.1, 2.1),
            },
            {
                "id": "extract_large",
                "name": "Large extract",
                "parent": "root",
                "geometry": box(0, 0, 10, 10),
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)
    result_strict = find_extracts_by_geometry(
        geometry, force_single_result=True, single_result_iou_threshold=0.99
    )
    result_relaxed = find_extracts_by_geometry(
        geometry, force_single_result=True, single_result_iou_threshold=0.40
    )
    assert len(result_strict.extracts) == 1
    assert result_strict.extracts[0].id == "extract_tight"
    assert len(result_relaxed.extracts) == 1
    assert result_relaxed.extracts[0].id == "extract_tight"


def test_force_single_result_no_extract_above_threshold(mocker: MockerFixture) -> None:
    """Test that force_single_result raises when no extract meets the threshold."""
    geometry = box(0, 0, 2, 2)
    index = _index_from_extracts(
        [
            {
                "id": "extract_far",
                "name": "Far extract",
                "parent": "root",
                "geometry": box(10, 10, 12, 12),
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)
    with pytest.raises(GeometryNotCoveredError):
        find_extracts_by_geometry(geometry, force_single_result=True)


def test_force_single_result_invalid_threshold_raises(mocker: MockerFixture) -> None:
    """Test that invalid single_result_iou_threshold raises ValueError."""
    geometry = box(0, 0, 1, 1)
    index = _index_from_extracts(
        [
            {
                "id": "extract_one",
                "name": "Extract One",
                "parent": "root",
                "geometry": box(0, 0, 1, 1),
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)
    with pytest.raises(ValueError):
        find_extracts_by_geometry(
            geometry, force_single_result=True, single_result_iou_threshold=-0.1
        )
    with pytest.raises(ValueError):
        find_extracts_by_geometry(
            geometry, force_single_result=True, single_result_iou_threshold=1.5
        )


def test_force_single_result_false_keeps_multi_result(mocker: MockerFixture) -> None:
    """Test that force_single_result=False preserves existing multi-result behavior."""
    geometry = from_wkt("POLYGON ((9.8 47.2, 9.8 47.6, 9.4 47.6, 9.4 47.2, 9.8 47.2))")
    result_default = find_extracts_by_geometry(geometry, "any")
    result_forced = find_extracts_by_geometry(geometry, "any", force_single_result=False)
    assert len(result_default.extracts) == len(result_forced.extracts)


def test_force_single_result_prefers_non_complete_above_threshold(mocker: MockerFixture) -> None:
    """Test non-complete cover above threshold is preferred when uncovered geometry is allowed."""
    geometry = box(0, 0, 2, 2)
    index = _index_from_extracts(
        [
            {
                "id": "extract_huge",
                "name": "Huge extract",
                "parent": "root",
                "geometry": box(0, 0, 10, 10),
            },
            {
                "id": "extract_tight",
                "name": "Tight extract",
                "parent": "root",
                "geometry": box(0, 0, 2.09, 1.99),
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)
    result = find_extracts_by_geometry(
        geometry,
        force_single_result=True,
        single_result_iou_threshold=0.90,
        allow_uncovered_geometry=True,
    )
    assert len(result.extracts) == 1
    assert result.extracts[0].id == "extract_tight"
    assert result.steps[0].reason == "single_result"


def test_force_single_result_smallest_complete_cover(mocker: MockerFixture) -> None:
    """Test that smallest complete cover is selected when no non-complete above threshold."""
    geometry = box(0, 0, 2, 2)
    index = _index_from_extracts(
        [
            {
                "id": "extract_huge",
                "name": "Huge extract",
                "parent": "root",
                "geometry": box(0, 0, 10, 10),
            },
            {
                "id": "extract_medium",
                "name": "Medium extract",
                "parent": "root",
                "geometry": box(0, 0, 5, 5),
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)
    result = find_extracts_by_geometry(
        geometry, force_single_result=True, single_result_iou_threshold=0.99
    )
    assert len(result.extracts) == 1
    assert result.extracts[0].id == "extract_medium"
    assert result.steps[0].reason == "complete_cover"


def test_force_single_result_warns_on_much_larger_extract(mocker: MockerFixture) -> None:
    """Test that a warning is emitted when selected extract is much larger than query."""
    geometry = box(0, 0, 2, 2)
    index = _index_from_extracts(
        [
            {
                "id": "extract_huge",
                "name": "Huge extract",
                "parent": "root",
                "geometry": box(0, 0, 10, 10),
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)
    with pytest.warns(GeometryNotCoveredWarning, match="is .*x larger than the query geometry"):
        find_extracts_by_geometry(geometry, force_single_result=True)


def test_force_single_result_allow_uncovered_geometry_no_candidates(mocker: MockerFixture) -> None:
    """Test allow_uncovered_geometry=True raises when no candidates exist."""
    geometry = box(0, 0, 2, 2)
    index = _index_from_extracts(
        [
            {
                "id": "extract_far",
                "name": "Far extract",
                "parent": "root",
                "geometry": box(10, 10, 12, 12),
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)
    with pytest.raises(GeometryNotCoveredError, match="No OSM extracts intersect"):
        find_extracts_by_geometry(geometry, force_single_result=True, allow_uncovered_geometry=True)


def test_force_single_result_falls_back_to_complete_cover(mocker: MockerFixture) -> None:
    """
    Test allow_uncovered_geometry=True falls back to complete cover.

    When no partial candidate meets the threshold, the smallest fully containing extract is used.
    """
    geometry = box(0, 0, 2, 2)
    index = _index_from_extracts(
        [
            {
                "id": "extract_huge",
                "name": "Huge extract",
                "parent": "root",
                "geometry": box(0, 0, 10, 10),
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)
    result = find_extracts_by_geometry(
        geometry,
        force_single_result=True,
        allow_uncovered_geometry=True,
        single_result_iou_threshold=0.99,
    )
    assert len(result.extracts) == 1
    assert result.extracts[0].id == "extract_huge"
    assert result.steps[0].reason == "complete_cover"


def test_find_extracts_covering_point_returns_matching_extracts(mocker: MockerFixture) -> None:
    """Test if find_extracts_covering_point returns extracts that contain the point."""
    index = _index_from_extracts(
        [
            {
                "id": "big",
                "name": "Big",
                "parent": "root",
                "geometry": box(0, 0, 10, 10),
            },
            {
                "id": "small",
                "name": "Small",
                "parent": "root",
                "geometry": box(0, 0, 2, 2),
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)

    point_inside_both = (1.0, 1.0)
    result = find_extracts_covering_point(point_inside_both)
    assert len(result) == 2
    assert result[0].id == "small"
    assert result[1].id == "big"


def test_find_extracts_covering_point_returns_smallest_first(mocker: MockerFixture) -> None:
    """Test if results are sorted from smallest to biggest by area."""
    index = _index_from_records(
        [
            {
                "id": "huge",
                "name": "Huge",
                "parent": "root",
                "url": "http://x/huge.pbf",
                "geometry": box(0, 0, 10, 10),
                "area": 100.0,
            },
            {
                "id": "medium",
                "name": "Medium",
                "parent": "root",
                "url": "http://x/medium.pbf",
                "geometry": box(0, 0, 5, 5),
                "area": 25.0,
            },
            {
                "id": "tiny",
                "name": "Tiny",
                "parent": "root",
                "url": "http://x/tiny.pbf",
                "geometry": box(0, 0, 1, 1),
                "area": 1.0,
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)

    result = find_extracts_covering_point((0.5, 0.5))
    assert [e.id for e in result] == ["tiny", "medium", "huge"]


def test_find_extracts_covering_point_returns_empty_list_on_no_match(
    mocker: MockerFixture,
) -> None:
    """Test if an empty list is returned when no extract covers the point."""
    index = _index_from_records(
        [
            {
                "id": "far_away",
                "name": "Far away",
                "parent": "root",
                "url": "http://x/far.pbf",
                "geometry": box(5, 5, 6, 6),
                "area": 1.0,
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)

    result = find_extracts_covering_point((0.0, 0.0))
    assert result == []


def test_find_extracts_covering_point_with_shapely_point(mocker: MockerFixture) -> None:
    """Test if find_extracts_covering_point works with a shapely Point."""
    index = _index_from_records(
        [
            {
                "id": "extract_a",
                "name": "Extract A",
                "parent": "root",
                "url": "http://x/a.pbf",
                "geometry": box(0, 0, 5, 5),
                "area": 25.0,
            },
            {
                "id": "extract_b",
                "name": "Extract B",
                "parent": "root",
                "url": "http://x/b.pbf",
                "geometry": box(3, 3, 8, 8),
                "area": 30.0,
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)

    from shapely.geometry import Point

    point = Point(4.0, 4.0)
    result = find_extracts_covering_point(point)
    assert len(result) == 2
    assert result[0].id == "extract_a"
    assert result[1].id == "extract_b"


def test_find_extracts_covering_point_excluded_ids(mocker: MockerFixture) -> None:
    """Test if excluded_extracts_ids are skipped."""
    index = _index_from_extracts(
        [
            {
                "id": "keep",
                "name": "Keep",
                "parent": "root",
                "geometry": box(0, 0, 10, 10),
            },
            {
                "id": "skip",
                "name": "Skip",
                "parent": "root",
                "geometry": box(0, 0, 2, 2),
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)

    result = find_extracts_covering_point((1.0, 1.0), excluded_extracts_ids={"skip"})
    assert len(result) == 1
    assert result[0].id == "keep"


def test_find_extracts_covering_point_invalid_source_raises(mocker: MockerFixture) -> None:
    """Test if an invalid source raises ValueError."""
    mocker.patch(
        "osmfinder.finder._resolve_extract_sources",
        side_effect=ValueError("No OSM extracts source provided."),
    )

    with pytest.raises(ValueError, match="Unknown OSM extracts source"):
        find_extracts_covering_point((0.0, 0.0), source="nonexistent")


def test_cumulative_coverage_increases_with_selected_steps(mocker: MockerFixture) -> None:
    """Cumulative coverage should increase for each selected extract."""
    index = _index_from_extracts(
        [
            {
                "id": "a",
                "name": "Left",
                "parent": "root",
                "geometry": box(0, 0, 1, 2),
            },
            {
                "id": "b",
                "name": "Right",
                "parent": "root",
                "geometry": box(1, 0, 2, 2),
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)

    result = find_extracts_by_geometry(box(0, 0, 2, 2), "any")
    selected_steps = [s for s in result.steps if s.selected]
    assert len(selected_steps) == 2

    coverages = [s.cumulative_coverage for s in selected_steps]
    assert coverages[0] > 0
    assert coverages[1] >= coverages[0]
    assert abs(coverages[-1] - result.coverage) < 1e-4


def test_cumulative_coverage_multipart_against_total_input(mocker: MockerFixture) -> None:
    """Cumulative coverage is computed against the total multipart input."""
    from shapely.geometry import MultiPolygon

    poly_a = box(0, 0, 1, 1)
    poly_b = box(2, 0, 3, 1)
    multipart = MultiPolygon([poly_a, poly_b])

    index = _index_from_extracts(
        [
            {
                "id": "a",
                "name": "Part A",
                "parent": "root",
                "geometry": poly_a,
            },
            {
                "id": "b",
                "name": "Part B",
                "parent": "root",
                "geometry": poly_b,
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)

    result = find_extracts_by_geometry(multipart, "any")
    selected_steps = [s for s in result.steps if s.selected]
    assert len(selected_steps) == 2
    assert abs(selected_steps[-1].cumulative_coverage - 1.0) < 1e-6


def test_cumulative_coverage_redundant_step_carries_forward(mocker: MockerFixture) -> None:
    """Redundant steps carry forward the last cumulative coverage value."""
    index = _index_from_extracts(
        [
            {
                "id": "a",
                "name": "Big",
                "parent": "root",
                "geometry": box(0, 0, 2, 2),
            },
            {
                "id": "b",
                "name": "Small",
                "parent": "root",
                "geometry": box(0, 0, 1, 1),
            },
        ]
    )
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)

    result = find_extracts_by_geometry(box(0, 0, 1.1, 1.1), "any")
    redundant_steps = [s for s in result.steps if s.reason == "redundant"]
    assert redundant_steps
    last_selected_coverage = 0.0
    for step in result.steps:
        if step.selected:
            last_selected_coverage = step.cumulative_coverage
        elif step.reason == "redundant":
            assert step.cumulative_coverage == last_selected_coverage
