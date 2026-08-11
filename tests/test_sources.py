"""Tests for the per-provider extract index parsers."""

from typing import Any

import pytest
from pytest_mock import MockerFixture
from shapely import box
from shapely.geometry import mapping
from tqdm import tqdm

from osmfinder._typing import OsmExtractSource
from osmfinder.parsers.geojson import parse_geojson
from osmfinder.sources.bbbike import BBBIKE_EXTRACTS_INDEX_URL, _iterate_bbbike_index
from osmfinder.sources.geo2day import _find_subregion_links
from osmfinder.sources.geofabrik import _parse_geofabrik_index
from osmfinder.sources.movisda import (
    MOVISDA_ADMIN_PBF_BASE_URL,
    MOVISDA_GRID_PBF_BASE_URL,
    _parse_movisda_features,
)
from osmfinder.sources.osm_fr import OPENSTREETMAP_FR_EXTRACTS_INDEX_URL


def test_movisda_admin_parse_features_hierarchy() -> None:
    """Test if Movisda admin extracts are nested via ISO codes with names from name_en/name."""
    geojson_data = {
        "type": "FeatureCollection",
        "features": [
            # Country: name_en preferred over name; parent is the source root.
            {
                "type": "Feature",
                "properties": {"prefix": "RW-", "name": "Rwanda (local)", "name_en": "Rwanda"},
                "geometry": mapping(box(0, 0, 4, 4)),
            },
            # Subdivision: nested under its country code (RW-02 -> RW).
            {
                "type": "Feature",
                "properties": {"prefix": "RW-02-", "name": "Eastern Province"},
                "geometry": mapping(box(1, 1, 2, 2)),
            },
            {
                "type": "Feature",
                "properties": {"prefix": "ZM-", "name_en": "Zambia"},
                "geometry": mapping(box(5, 5, 9, 9)),
            },
            # Same subdivision name in another country -> different parent.
            {
                "type": "Feature",
                "properties": {"prefix": "ZM-03-", "name": "Eastern Province"},
                "geometry": mapping(box(6, 6, 7, 7)),
            },
        ],
    }
    extracts = _parse_movisda_features(
        geojson_data,
        MOVISDA_ADMIN_PBF_BASE_URL,
        OsmExtractSource.movisda_admin.value,
        build_hierarchy=True,
    )
    by_id = {extract.id: extract for extract in extracts}

    # Country -> root, name from name_en.
    assert by_id["Movisda-admin_RW"].parent == "Movisda-admin"
    assert by_id["Movisda-admin_RW"].name == "Rwanda"
    # Subdivision -> nested under its country.
    assert by_id["Movisda-admin_RW-02"].parent == "Movisda-admin_RW"
    assert by_id["Movisda-admin_RW-02"].name == "Eastern Province"
    assert (
        by_id["Movisda-admin_RW-02"].url
        == "https://osm.download.movisda.io/admin/RW-02-latest.osm.pbf"
    )
    # Same subdivision name in another country resolves to a different parent.
    assert by_id["Movisda-admin_ZM-03"].parent == "Movisda-admin_ZM"
    assert by_id["Movisda-admin_ZM-03"].name == "Eastern Province"


def test_movisda_grid_parse_features() -> None:
    """Test if Movisda grid extracts use the tile code as name (resolution encoded in the code)."""
    geojson_data = {
        "type": "FeatureCollection",
        "features": [
            # 1 degree tile.
            {
                "type": "Feature",
                "properties": {"prefix": "N42W001-", "name": "N42W001 (1°)"},
                "geometry": mapping(box(0, 0, 1, 1)),
            },
            # 10 degree tile: resolution encoded in the code via the `-10` suffix.
            {
                "type": "Feature",
                "properties": {"prefix": "N80E000-10-", "name": "N80E000 (10°)"},
                "geometry": mapping(box(2, 2, 3, 3)),
            },
        ],
    }
    extracts = _parse_movisda_features(
        geojson_data,
        MOVISDA_GRID_PBF_BASE_URL,
        OsmExtractSource.movisda_grid.value,
        build_hierarchy=False,
    )
    by_id = {extract.id: extract for extract in extracts}

    assert by_id["Movisda-grid_N42W001"].name == "N42W001"
    assert by_id["Movisda-grid_N42W001"].parent == "Movisda-grid"
    assert (
        by_id["Movisda-grid_N42W001"].url
        == "https://osm.download.movisda.io/grid/N42W001-latest.osm.pbf"
    )
    assert by_id["Movisda-grid_N80E000-10"].name == "N80E000-10"
    assert (
        by_id["Movisda-grid_N80E000-10"].url
        == "https://osm.download.movisda.io/grid/N80E000-10-latest.osm.pbf"
    )


def test_geo2day_find_subregion_links() -> None:
    """Test if only direct (one level deeper) sub-region links are detected."""
    from bs4 import BeautifulSoup

    home_html = (
        '<a href="https://geo2day.com/europe.html">Europe</a>'
        '<a href="#">self</a>'
        '<a href="https://geo2day.com/">Home</a>'
    )
    assert _find_subregion_links(
        "https://geo2day.com/", BeautifulSoup(home_html, "html.parser")
    ) == [("https://geo2day.com/europe.html", "europe")]

    germany_html = (
        '<a href="https://geo2day.com/europe.html">Europe (breadcrumb)</a>'
        '<a href="https://geo2day.com/europe/germany/bayern.html">Bavaria</a>'
    )
    assert _find_subregion_links(
        "https://geo2day.com/europe/germany.html", BeautifulSoup(germany_html, "html.parser")
    ) == [("https://geo2day.com/europe/germany/bayern.html", "bayern")]


def test_geo2day_two_phase_gather_and_parse(mocker: MockerFixture) -> None:
    """Test if regions are first enumerated (with total) and then parsed into extracts."""
    import osmfinder.sources.geo2day as geo2day_module

    pages = {
        "https://geo2day.com/": '<a href="https://geo2day.com/europe.html">Europe</a>',
        "https://geo2day.com/europe.html": (
            '<a href="https://geo2day.com/europe.html">self</a>'
            '<a href="https://geo2day.com/europe/poland.html">Poland</a>'
        ),
        "https://geo2day.com/europe/poland.html": (
            '<a href="https://geo2day.com/europe.html">parent</a>'
        ),
    }

    def fake_get(url: str, headers: Any = None, timeout: Any = None) -> Any:
        response = mocker.Mock()
        response.status_code = 200
        response.raise_for_status = lambda: None
        response.text = pages.get(url, "")
        return response

    mocker.patch("osmfinder.sources.geo2day.requests.get", side_effect=fake_get)
    mocker.patch.object(geo2day_module, "parse_geojson_file", return_value=box(0, 0, 1, 1))

    with tqdm(disable=True) as pbar:
        # Phase 1: enumerate regions and set the progress bar total (no geometry downloaded yet).
        region_objects = geo2day_module._gather_all_geo2day_urls(
            "GEO2Day", "https://geo2day.com/", pbar
        )
        assert pbar.total == 2
        assert {region[0] for region in region_objects} == {
            "GEO2Day_europe",
            "GEO2Day_europe_poland",
        }

        # Phase 2: download geometries and build extracts.
        extracts = geo2day_module._parse_geo2day_urls(pbar=pbar, region_objects=region_objects)

    by_id = {extract.id: extract for extract in extracts}
    assert by_id["GEO2Day_europe"].parent == "GEO2Day"
    assert by_id["GEO2Day_europe"].url == "https://geo2day.com/europe.pbf"
    assert by_id["GEO2Day_europe_poland"].parent == "GEO2Day_europe"
    assert by_id["GEO2Day_europe_poland"].url == "https://geo2day.com/europe/poland.pbf"


def test_geofabrik_parse_index() -> None:
    """Test if a Geofabrik index-v1.json payload is parsed into extracts with proper ids."""
    parsed_data = {
        "features": [
            {
                "type": "Feature",
                "geometry": mapping(box(1, 42, 2, 43)),
                "properties": {
                    "id": "andorra",
                    "parent": "europe",
                    "name": "Andorra",
                    "urls": {"pbf": "https://download.geofabrik.de/europe/andorra-latest.osm.pbf"},
                },
            },
            {
                "type": "Feature",
                "geometry": mapping(box(-10, 35, 40, 70)),
                "properties": {
                    "id": "europe",
                    "name": "Europe",
                    "urls": {"pbf": "https://download.geofabrik.de/europe-latest.osm.pbf"},
                },
            },
            {
                "type": "Feature",
                "geometry": mapping(box(-125, 32, -114, 42)),
                "properties": {
                    "id": "us/california",
                    "parent": "us",
                    "name": "California",
                    "urls": {
                        "pbf": (
                            "https://download.geofabrik.de/north-america/us/"
                            "california-latest.osm.pbf"
                        )
                    },
                },
            },
        ]
    }

    extracts = _parse_geofabrik_index(parsed_data)
    by_id = {extract.id: extract for extract in extracts}

    assert by_id["Geofabrik_andorra"].name == "andorra"
    assert by_id["Geofabrik_andorra"].parent == "Geofabrik_europe"
    assert (
        by_id["Geofabrik_andorra"].url
        == "https://download.geofabrik.de/europe/andorra-latest.osm.pbf"
    )
    # Missing parent resolves to the source root.
    assert by_id["Geofabrik_europe"].parent == "Geofabrik"
    # US sub-extracts have their parent forced to the `us` node.
    assert by_id["Geofabrik_us/california"].parent == "Geofabrik_us"


def test_bbbike_iterate_index(mocker: MockerFixture) -> None:
    """Test if the BBBike directory listing and CSV fallback are parsed into extracts."""
    import osmfinder.sources.bbbike as bbbike_module

    index_html = (
        "<table>"
        '<tr class="d"><td><a href="../">..</a></td></tr>'
        '<tr class="d"><td><a href="Aachen/">Aachen</a></td></tr>'
        '<tr class="d"><td><a href="Berlin/">Berlin</a></td></tr>'
        "</table>"
    )
    csv_text = "Berlin:0:1:2:3:4:13.0 52.3 13.8 52.7:rest\n"

    def fake_get(url: str, headers: Any = None, timeout: Any = None) -> Any:
        response = mocker.Mock()
        response.status_code = 200
        response.raise_for_status = lambda: None
        response.text = index_html if url == BBBIKE_EXTRACTS_INDEX_URL else csv_text
        return response

    def fake_poly(url: str) -> Any:
        # Aachen has a poly file; Berlin falls back to the CSV bounding box.
        return box(6.0, 50.7, 6.2, 50.9) if "Aachen" in url else None

    mocker.patch("osmfinder.sources.bbbike.requests.get", side_effect=fake_get)
    mocker.patch.object(bbbike_module, "parse_polygon_file", side_effect=fake_poly)

    extracts = bbbike_module._iterate_bbbike_index()
    by_id = {extract.id: extract for extract in extracts}

    assert set(by_id) == {"BBBike_Aachen", "BBBike_Berlin"}
    assert by_id["BBBike_Aachen"].parent == "BBBike"
    assert (
        by_id["BBBike_Aachen"].url == "https://download.bbbike.org/osm/bbbike/Aachen/Aachen.osm.pbf"
    )
    assert by_id["BBBike_Aachen"].geometry.equals(box(6.0, 50.7, 6.2, 50.9))
    # Berlin uses the CSV bounding box fallback.
    assert by_id["BBBike_Berlin"].geometry.equals(box(13.0, 52.3, 13.8, 52.7))


def test_osm_fr_gather_and_parse(mocker: MockerFixture) -> None:
    """Test if OSM.fr directory pages are enumerated then parsed into extracts."""
    import osmfinder.sources.osm_fr as osm_fr_module

    root_html = (
        "<table>"
        '<tr><td><img src="/icons/folder.gif"></td>'
        '<td><a href="europe/">europe/</a></td></tr>'
        "</table>"
    )
    europe_html = (
        '<table><tr><td><a href="monaco-latest.osm.pbf">monaco-latest.osm.pbf</a></td></tr></table>'
    )

    def fake_get(url: str, headers: Any = None, timeout: Any = None) -> Any:
        response = mocker.Mock()
        response.status_code = 200
        response.raise_for_status = lambda: None
        if url == f"{OPENSTREETMAP_FR_EXTRACTS_INDEX_URL}/":
            response.text = root_html
        elif url == f"{OPENSTREETMAP_FR_EXTRACTS_INDEX_URL}/europe/":
            response.text = europe_html
        else:
            response.text = ""
        return response

    mocker.patch("osmfinder.sources.osm_fr.requests.get", side_effect=fake_get)
    mocker.patch.object(osm_fr_module, "parse_polygon_file", return_value=box(7.4, 43.7, 7.5, 43.8))

    with tqdm(disable=True) as pbar:
        # Phase 1: enumerate directory pages (one PBF discovered) and set the total.
        soup_objects = osm_fr_module._gather_all_openstreetmap_fr_urls("osmfr", "/", pbar)
        assert pbar.total == 1

        # Phase 2: download geometries and build extracts.
        extracts = osm_fr_module._parse_openstreetmap_fr_urls(
            pbar=pbar, extract_soup_objects=soup_objects
        )

    assert len(extracts) == 1
    extract = extracts[0]
    assert extract.id == "osmfr_europe_monaco"
    assert extract.name == "monaco"
    assert extract.parent == "osmfr_europe"
    assert extract.url == "https://download.openstreetmap.fr/extracts/europe/monaco-latest.osm.pbf"
    assert extract.geometry.equals(box(7.4, 43.7, 7.5, 43.8))