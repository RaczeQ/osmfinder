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
    find_smallest_containing_extracts,
    get_extract_by_query,
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
        find_smallest_containing_extracts(
            geometry=geometry,
            source="any",
            allow_uncovered_geometry=allow_uncovered_geometry,
            geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
        )


def test_excluded_extracts_ids() -> None:
    """Test if excluded extracts are skipped and coverage is recalculated."""
    from rq_geo_toolkit.geocode import geocode_to_geometry

    geometry = geocode_to_geometry("Andorra")

    extracts = find_smallest_containing_extracts(geometry, "geofabrik")
    assert [extract.file_name for extract in extracts] == ["geofabrik_europe_andorra"]

    excluded_extracts_ids = {extracts[0].id}
    fallback_extracts = find_smallest_containing_extracts(
        geometry, "geofabrik", excluded_extracts_ids=excluded_extracts_ids
    )

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
        extract = get_extract_by_query("Vatican City")
    assert extract.id == "osmfr_x_vatican_city"

    # False: raises as before.
    with pytest.raises(OsmExtractMultipleMatchesError):
        get_extract_by_query("Vatican City", select_first_match=False)

    # Single match: no warning regardless.
    with warnings.catch_warnings():
        warnings.simplefilter("error", OsmExtractMultipleMatchesWarning)
        assert get_extract_by_query("enfield").id == "Geofabrik_enfield"


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
        extract = get_extract_by_query(query, source, select_first_match=False)
        # if properly found - check id
        assert extract.id == matched_id

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

    mocker.patch("osmfinder.parsers.geojson.requests.get", side_effect=fake_get)
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
            "Movisda-admin_VA",
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
    extracts = find_smallest_containing_extracts(geometry, source)
    assert len(extracts) == 1
    assert extracts[0].id == expected_extract_id, f"{extracts[0].id} vs {expected_extract_id}"


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
    extracts = find_smallest_containing_extracts(
        geometry, source, geometry_coverage_iou_threshold=geometry_coverage_iou_threshold
    )
    assert sorted(extract.file_name for extract in extracts) == sorted(expected_extract_file_names)
