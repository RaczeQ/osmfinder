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
    OsmExtractUnavailableWarning,
)
from osmfinder.finder import (
    find_extract_by_query,
    find_extracts_by_geometry,
)
from tests._helpers import _index_from_records


def test_find_and_download_excludes_unavailable_extracts(mocker: MockerFixture) -> None:
    """Test if unavailable extracts are excluded and retried with alternatives."""
    from requests.exceptions import HTTPError
    from rq_geo_toolkit.geocode import geocode_to_geometry

    geometry = geocode_to_geometry("Andorra")
    matching_extracts = find_extracts_by_geometry(geometry, "geofabrik")
    failing_extract_id = matching_extracts.extracts[0].id

    def fake_download(
        extract: OpenStreetMapExtract,
        download_directory: Path,
        progressbar: bool = True,
        force_refresh: bool = False,
    ) -> Path:
        if extract.id == failing_extract_id:
            raise HTTPError("Extract unavailable")
        return Path(download_directory) / f"{extract.file_name}.osm.pbf"

    mocker.patch("osmfinder.finder._download_single_extract", side_effect=fake_download)

    with tempfile.TemporaryDirectory() as tmp_dir, pytest.warns(OsmExtractUnavailableWarning):
        result = osmfinder.download(geometry, source="geofabrik", download_directory=tmp_dir)

    result_extracts_ids = {extract.id for extract in result.find_result.extracts}
    assert failing_extract_id not in result_extracts_ids
    assert result.unavailable_extracts
    assert result.unavailable_extracts[0].id == failing_extract_id
    assert len(result.download_paths) >= 1


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


def test_find_and_download_unavailable_extracts_list(mocker: MockerFixture) -> None:
    """Test if download returns unavailable_extracts list for geometry queries."""
    from requests.exceptions import HTTPError
    from rq_geo_toolkit.geocode import geocode_to_geometry

    geometry = geocode_to_geometry("Andorra")
    matching_extracts = find_extracts_by_geometry(geometry, "geofabrik")
    failing_extract_id = matching_extracts.extracts[0].id

    def fake_download(
        extract: OpenStreetMapExtract,
        download_directory: Path,
        progressbar: bool = True,
        force_refresh: bool = False,
    ) -> Path:
        if extract.id == failing_extract_id:
            raise HTTPError("Extract unavailable")
        return Path(download_directory) / f"{extract.file_name}.osm.pbf"

    mocker.patch("osmfinder.finder._download_single_extract", side_effect=fake_download)

    with tempfile.TemporaryDirectory() as tmp_dir, pytest.warns(OsmExtractUnavailableWarning):
        result = osmfinder.download(geometry, source="geofabrik", download_directory=tmp_dir)

    assert len(result.unavailable_extracts) == 1
    assert result.unavailable_extracts[0].id == failing_extract_id


def test_download_query_result_no_retry_raises(mocker: MockerFixture) -> None:
    """Test retry_on_unavailable=False on OsmfinderQueryResult raises on failure."""
    from requests.exceptions import HTTPError

    index = _two_vatican_city_index()
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)

    def fake_download(
        extract: OpenStreetMapExtract,
        download_directory: Path,
        progressbar: bool = True,
        force_refresh: bool = False,
    ) -> Path:
        if extract.id == "osmfr_vc":
            raise HTTPError("Extract unavailable")
        return Path(download_directory) / f"{extract.file_name}.osm.pbf"

    mocker.patch("osmfinder.finder._download_single_extract", side_effect=fake_download)

    with pytest.warns(OsmExtractMultipleMatchesWarning):
        result = find_extract_by_query("Vatican City")

    with tempfile.TemporaryDirectory() as tmp_dir:
        with pytest.raises(HTTPError):
            result.download(download_directory=tmp_dir, retry_on_unavailable=False)


def test_download_geometry_result_no_retry_raises(mocker: MockerFixture) -> None:
    """Test retry_on_unavailable=False on OsmfinderGeometryResult raises on failure."""
    from requests.exceptions import HTTPError
    from rq_geo_toolkit.geocode import geocode_to_geometry

    geometry = geocode_to_geometry("Andorra")
    matching_extracts = find_extracts_by_geometry(geometry, "geofabrik")
    failing_extract_id = matching_extracts.extracts[0].id

    def fake_download(
        extract: OpenStreetMapExtract,
        download_directory: Path,
        progressbar: bool = True,
        force_refresh: bool = False,
    ) -> Path:
        if extract.id == failing_extract_id:
            raise HTTPError("Extract unavailable")
        return Path(download_directory) / f"{extract.file_name}.osm.pbf"

    mocker.patch("osmfinder.finder._download_single_extract", side_effect=fake_download)

    with tempfile.TemporaryDirectory() as tmp_dir:
        with pytest.raises(HTTPError):
            matching_extracts.download(download_directory=tmp_dir, retry_on_unavailable=False)


def test_download_query_result_retry_on_unavailable_true(mocker: MockerFixture) -> None:
    """Test retry_on_unavailable=True (default) still retries with query results."""
    from requests.exceptions import ConnectionError as RequestsConnectionError

    index = _two_vatican_city_index()
    mocker.patch("osmfinder.finder._get_index_for_sources", return_value=index)

    def fake_download(
        extract: OpenStreetMapExtract,
        download_directory: Path,
        progressbar: bool = True,
        force_refresh: bool = False,
    ) -> Path:
        if extract.id == "osmfr_vc":
            raise RequestsConnectionError("offline")
        return Path(download_directory) / f"{extract.file_name}.osm.pbf"

    mocker.patch("osmfinder.finder._download_single_extract", side_effect=fake_download)

    with tempfile.TemporaryDirectory() as tmp_dir:
        with pytest.warns(OsmExtractMultipleMatchesWarning):
            with pytest.warns(OsmExtractUnavailableWarning):
                result = osmfinder.download(
                    "Vatican City", download_directory=tmp_dir, retry_on_unavailable=True
                )

    assert len(result.download_paths) == 1
    assert result.download_paths[0].name == "geo2day_vatican_city.osm.pbf"


def test_download_single_extract_reports_to_custom_progressbar() -> None:
    """Test if a custom progress bar object receives the download progress."""
    import functools
    import http.server
    import threading

    from osmfinder.finder._download import _download_single_extract

    class SpyProgressBar:
        total: int | float | None = None

        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def set_description(self, description: str) -> None:
            self.calls.append(("set_description", description))

        def update(self, n: int) -> None:
            self.calls.append(("update", n))

        def reset(self) -> None:
            self.calls.append(("reset", None))

        def close(self) -> None:
            self.calls.append(("close", None))

    with tempfile.TemporaryDirectory() as tmp_dir_name:
        served_directory = Path(tmp_dir_name) / "served"
        served_directory.mkdir()
        (served_directory / "extract.osm.pbf").write_bytes(b"x" * 10_000)
        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler, directory=str(served_directory)
        )
        with http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            extract = OpenStreetMapExtract(
                id="test_extract",
                name="Test extract",
                parent="test",
                url=f"http://127.0.0.1:{server.server_port}/extract.osm.pbf",
                geometry=box(0, 0, 1, 1),
                file_name="test_extract",
            )
            progressbar = SpyProgressBar()
            downloaded_path = _download_single_extract(
                extract, Path(tmp_dir_name) / "downloads", progressbar=progressbar
            )
            server.shutdown()

        assert downloaded_path.read_bytes() == b"x" * 10_000
        assert progressbar.calls[0] == ("set_description", downloaded_path.name)
        assert progressbar.total == 10_000
        assert progressbar.calls[-3:] == [("reset", None), ("update", 10_000), ("close", None)]
