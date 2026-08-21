"""Tests for downloading extracts and handling unavailable sources."""

import tempfile
from pathlib import Path

import pytest
from pytest_mock import MockerFixture
from shapely import box

import osmfinder
from osmfinder._typing import OpenStreetMapExtract
from osmfinder.exceptions import (
    OsmExtractMultipleMatchesWarning,
    OsmExtractsUnavailableError,
    OsmExtractUnavailableWarning,
    OsmExtractZeroMatchesError,
)
from osmfinder.finder import (
    download_extract_by_query,
    download_extracts_pbf_files,
    find_and_download_extracts_pbf_files,
    find_smallest_containing_extracts,
)
from tests._helpers import _index_from_records


def test_find_and_download_excludes_unavailable_extracts(mocker: MockerFixture) -> None:
    """Test if unavailable extracts are excluded and the coverage is recalculated."""
    from requests.exceptions import HTTPError
    from rq_geo_toolkit.geocode import geocode_to_geometry

    geometry = geocode_to_geometry("Andorra")
    # Use the finder directly to avoid downloading.
    from osmfinder.finder import find_smallest_containing_extracts

    matching_extracts = find_smallest_containing_extracts(geometry, "geofabrik")
    failing_extract_id = matching_extracts.extracts[0].id

    def fake_download(
        extract: OpenStreetMapExtract, download_directory: Path, progressbar: bool = True
    ) -> Path:
        if extract.id == failing_extract_id:
            raise HTTPError("Extract unavailable")
        return Path(download_directory) / f"{extract.file_name}.osm.pbf"

    mocker.patch("osmfinder.finder._download._download_single_extract", side_effect=fake_download)

    with tempfile.TemporaryDirectory() as tmp_dir, pytest.warns(OsmExtractUnavailableWarning):
        result = find_and_download_extracts_pbf_files(geometry, "geofabrik", tmp_dir)

    result_extracts_ids = {extract.id for extract in result.find_result.extracts}
    assert failing_extract_id not in result_extracts_ids
    assert result.download_paths
    assert all(isinstance(pbf_path, Path) for pbf_path in result.download_paths)


def test_download_extracts_pbf_files_raises_on_unavailable(mocker: MockerFixture) -> None:
    """Test if the public download function keeps raising on errors (back-compat)."""
    from requests.exceptions import HTTPError

    extract = OpenStreetMapExtract(
        id="test_extract",
        name="test_extract",
        parent="",
        url="http://example.com/test_extract.osm.pbf",
        geometry=box(0, 0, 1, 1),
        file_name="test_extract",
    )
    mocker.patch(
        "osmfinder.finder._download._download_single_extract",
        side_effect=HTTPError("Extract unavailable"),
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        with pytest.raises(HTTPError):
            download_extracts_pbf_files([extract], Path(tmp_dir))


def _two_vatican_city_index() -> "object":
    return _index_from_records(
        [
            {
                "id": "geo2day_vc",
                "name": "Vatican City",
                "file_name": "geo2day_vatican_city",
                "parent": "a",
                "area": 0.5,
                "url": "http://x/geo2day.pbf",
                "geometry": box(0, 0, 1, 1),
            },
            {
                "id": "osmfr_vc",
                "name": "Vatican City",
                "file_name": "osmfr_vatican_city",
                "parent": "b",
                "area": 0.4,
                "url": "http://x/osmfr.pbf",
                "geometry": box(0, 0, 1, 1),
            },
        ]
    )


def test_download_extract_by_query_redundancy(mocker: MockerFixture) -> None:
    """Test if a failed download falls back to the next matching extract."""
    from requests.exceptions import ConnectionError as RequestsConnectionError

    index = _two_vatican_city_index()
    mocker.patch("osmfinder.finder._sources._get_index_for_sources", return_value=index)

    def fake_download(
        extract: OpenStreetMapExtract, download_directory: Path, progressbar: bool = True
    ) -> Path:
        # The smallest-area match (osmfr, 0.4) is selected first and must fail.
        if extract.id == "osmfr_vc":
            raise RequestsConnectionError("offline")
        return Path(download_directory) / f"{extract.file_name}.osm.pbf"

    mocker.patch("osmfinder.finder._download._download_single_extract", side_effect=fake_download)

    with tempfile.TemporaryDirectory() as tmp_dir:
        with pytest.warns(OsmExtractMultipleMatchesWarning):
            with pytest.warns(OsmExtractUnavailableWarning):
                result = osmfinder.download("Vatican City", download_directory=tmp_dir)
    # Fell back to the second match.
    assert len(result.download_paths) == 1
    assert result.download_paths[0].name == "geo2day_vatican_city.osm.pbf"
    # After fallback, find_result contains the final selected extract (geo2day_vc)
    assert result.find_result.extracts[0].id == "geo2day_vc"
    assert result.download_paths[0].name == "geo2day_vatican_city.osm.pbf"


def test_download_extract_by_query_all_unavailable(mocker: MockerFixture) -> None:
    """Test if exhausting all matches (all unavailable) raises an availability error."""
    from requests.exceptions import ConnectionError as RequestsConnectionError

    index = _two_vatican_city_index()
    mocker.patch("osmfinder.finder._sources._get_index_for_sources", return_value=index)
    mocker.patch(
        "osmfinder.finder._download._download_single_extract",
        side_effect=RequestsConnectionError("offline"),
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        # All matches fail to download -> availability error (not a zero-match query).
        with (
            pytest.warns(OsmExtractUnavailableWarning),
            pytest.raises(OsmExtractsUnavailableError) as exc_info,
        ):
            osmfinder.download("Vatican City", download_directory=tmp_dir)
    assert set(exc_info.value.matching_full_names) == {
        "geo2day_vatican_city",
        "osmfr_vatican_city",
    }


def test_download_extract_by_query_zero_match(mocker: MockerFixture) -> None:
    """Test if a genuinely unmatched query still raises a zero-match error (not availability)."""
    index = _two_vatican_city_index()
    mocker.patch("osmfinder.finder._sources._get_index_for_sources", return_value=index)

    with tempfile.TemporaryDirectory() as tmp_dir:
        with pytest.raises(OsmExtractZeroMatchesError):
            download_extract_by_query("totally_nonexistent_extract", download_directory=tmp_dir)


def test_find_and_download_unavailable_extracts_list(mocker: MockerFixture) -> None:
    """Test if find_and_download returns unavailable_extracts list."""
    from requests.exceptions import HTTPError
    from rq_geo_toolkit.geocode import geocode_to_geometry

    geometry = geocode_to_geometry("Andorra")
    matching_extracts = find_smallest_containing_extracts(geometry, "geofabrik")
    failing_extract_id = matching_extracts.extracts[0].id

    def fake_download(
        extract: OpenStreetMapExtract, download_directory: Path, progressbar: bool = True
    ) -> Path:
        if extract.id == failing_extract_id:
            raise HTTPError("Extract unavailable")
        return Path(download_directory) / f"{extract.file_name}.osm.pbf"

    mocker.patch("osmfinder.finder._download._download_single_extract", side_effect=fake_download)

    with tempfile.TemporaryDirectory() as tmp_dir, pytest.warns(OsmExtractUnavailableWarning):
        result = find_and_download_extracts_pbf_files(geometry, "geofabrik", tmp_dir)

    assert len(result.unavailable_extracts) == 1
    assert result.unavailable_extracts[0].id == failing_extract_id


def test_download_extract_by_query_unavailable_list(mocker: MockerFixture) -> None:
    """Test if download_extract_by_query returns unavailable_extracts list."""
    from requests.exceptions import ConnectionError as RequestsConnectionError

    index = _two_vatican_city_index()
    mocker.patch("osmfinder.finder._sources._get_index_for_sources", return_value=index)

    def fake_download(
        extract: OpenStreetMapExtract, download_directory: Path, progressbar: bool = True
    ) -> Path:
        if extract.id == "osmfr_vc":
            raise RequestsConnectionError("offline")
        return Path(download_directory) / f"{extract.file_name}.osm.pbf"

    mocker.patch("osmfinder.finder._download._download_single_extract", side_effect=fake_download)

    with tempfile.TemporaryDirectory() as tmp_dir:
        with pytest.warns(OsmExtractMultipleMatchesWarning):
            with pytest.warns(OsmExtractUnavailableWarning):
                result = osmfinder.download("Vatican City", download_directory=tmp_dir)

    assert len(result.unavailable_extracts) == 1
    assert result.unavailable_extracts[0].id == "osmfr_vc"
