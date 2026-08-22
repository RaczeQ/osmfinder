"""Tests for the osmfinder CLI."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from shapely.geometry import box
from typer.testing import CliRunner

from osmfinder.cli import app
from osmfinder.exceptions import OsmExtractMultipleMatchesError, OsmExtractZeroMatchesError

runner = CliRunner()


def test_version() -> None:
    """Test --version flag prints version and exits."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "osmfinder" in result.output


def test_main_help() -> None:
    """Test main help shows all commands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "list" in result.output
    assert "search" in result.output
    assert "covers" in result.output
    assert "clear" in result.output


def test_list_help() -> None:
    """Test list command help."""
    result = runner.invoke(app, ["list", "--help"])
    assert result.exit_code == 0
    assert "--source" in result.output
    assert "--full-names" in result.output
    assert "--pager" in result.output


def test_search_help() -> None:
    """Test search command help."""
    result = runner.invoke(app, ["search", "--help"])
    assert result.exit_code == 0
    assert "query" in result.output
    assert "--source" in result.output
    assert "--output" in result.output
    assert "--dry-run" in result.output


def test_covers_help() -> None:
    """Test covers command help."""
    result = runner.invoke(app, ["covers", "--help"])
    assert result.exit_code == 0
    assert "--bbox" in result.output
    assert "--wkt" in result.output
    assert "--geojson" in result.output
    assert "--file" in result.output
    assert "--iou-threshold" in result.output
    assert "--single-result" in result.output
    assert "--dry-run" in result.output


def test_clear_help() -> None:
    """Test clear command help."""
    result = runner.invoke(app, ["clear", "--help"])
    assert result.exit_code == 0
    assert "--source" in result.output


def test_search_invokes_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test search command calls find_extract_by_query."""
    mock_result = MagicMock()
    mock_result.matched_extracts = []
    mock_result.extracts = []
    mock_result.sources_used = []
    mock_get = MagicMock(return_value=mock_result)
    monkeypatch.setattr("osmfinder.cli.find_extract_by_query", mock_get)
    mock_download = MagicMock(return_value=MagicMock(download_paths=[], unavailable_extracts=[]))
    monkeypatch.setattr("osmfinder.cli.download", mock_download)

    result = runner.invoke(app, ["search", "Monaco"])
    assert result.exit_code == 0
    mock_get.assert_called_once_with("Monaco", source="any", select_first_match=True)


def test_search_with_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test search command with custom source."""
    mock_result = MagicMock()
    mock_result.matched_extracts = []
    mock_result.extracts = []
    mock_result.sources_used = []
    mock_get = MagicMock(return_value=mock_result)
    monkeypatch.setattr("osmfinder.cli.find_extract_by_query", mock_get)
    mock_download = MagicMock(return_value=MagicMock(download_paths=[], unavailable_extracts=[]))
    monkeypatch.setattr("osmfinder.cli.download", mock_download)

    result = runner.invoke(app, ["search", "Monaco", "--source", "Geofabrik"])
    assert result.exit_code == 0
    mock_get.assert_called_once_with("Monaco", source="Geofabrik", select_first_match=True)


def test_search_with_multiple_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test search command with comma-separated sources."""
    mock_result = MagicMock()
    mock_result.matched_extracts = []
    mock_result.extracts = []
    mock_result.sources_used = []
    mock_get = MagicMock(return_value=mock_result)
    monkeypatch.setattr("osmfinder.cli.find_extract_by_query", mock_get)
    mock_download = MagicMock(return_value=MagicMock(download_paths=[], unavailable_extracts=[]))
    monkeypatch.setattr("osmfinder.cli.download", mock_download)

    result = runner.invoke(app, ["search", "Monaco", "--source", "bbbike,osmfr"])
    assert result.exit_code == 0
    mock_get.assert_called_once_with("Monaco", source="bbbike,osmfr", select_first_match=True)


def test_search_error_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test search command handles exceptions gracefully."""
    mock_get = MagicMock(side_effect=Exception("network error"))
    monkeypatch.setattr("osmfinder.cli.find_extract_by_query", mock_get)

    result = runner.invoke(app, ["search", "Monaco"])
    assert result.exit_code == 1
    assert "Error" in result.output


def test_search_zero_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test search command shows suggestion table on zero matches."""
    from osmfinder._typing import OpenStreetMapExtract

    mock_extract = MagicMock(spec=OpenStreetMapExtract)
    mock_extract.id = "Geofabrik_berlin"
    mock_extract.name = "berlin"
    mock_extract.file_name = "geofabrik_europe_germany_berlin"
    mock_extract.geometry = box(0, 0, 1, 1)

    mock_index = MagicMock()
    mock_index.file_names = ["geofabrik_europe_germany_berlin"]
    mock_index.get_extract_by_index.return_value = mock_extract

    monkeypatch.setattr("osmfinder.cli._get_index_for_sources", lambda *args, **kwargs: mock_index)
    mock_get = MagicMock(
        side_effect=OsmExtractZeroMatchesError(
            'Zero extracts matched by query "xyz".\n'
            'Found full names close to query: "geofabrik_europe_germany_berlin".',
            matching_full_names=["geofabrik_europe_germany_berlin"],
        )
    )
    monkeypatch.setattr("osmfinder.cli.find_extract_by_query", mock_get)

    result = runner.invoke(app, ["search", "xyz"])
    assert result.exit_code == 1
    assert "Zero extracts matched" in result.output
    assert "berlin" in result.output
    assert "Geofabrik" in result.output


def test_search_multiple_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test search command shows table on multiple matches without select-first-match."""
    from osmfinder._typing import OpenStreetMapExtract

    mock_extract1 = MagicMock(spec=OpenStreetMapExtract)
    mock_extract1.id = "Geofabrik_monaco"
    mock_extract1.name = "monaco"
    mock_extract1.file_name = "geofabrik_europe_monaco"
    mock_extract1.geometry = box(0, 0, 1, 1)

    mock_extract2 = MagicMock(spec=OpenStreetMapExtract)
    mock_extract2.id = "BBBike_monaco"
    mock_extract2.name = "monaco"
    mock_extract2.file_name = "bbbike_europe_monaco"
    mock_extract2.geometry = box(0, 0, 1, 1)

    mock_index = MagicMock()
    mock_index.file_names = ["geofabrik_europe_monaco", "bbbike_europe_monaco"]

    def mock_get_extract(idx):
        return [mock_extract1, mock_extract2][idx]

    mock_index.get_extract_by_index.side_effect = mock_get_extract

    monkeypatch.setattr("osmfinder.cli._get_index_for_sources", lambda *args, **kwargs: mock_index)
    mock_get = MagicMock(
        side_effect=OsmExtractMultipleMatchesError(
            'Multiple extracts matched by query "Monaco".\n'
            "Matching extracts full names:"
            ' "bbbike_europe_monaco", "geofabrik_europe_monaco".',
            matching_full_names=["bbbike_europe_monaco", "geofabrik_europe_monaco"],
        )
    )
    monkeypatch.setattr("osmfinder.cli.find_extract_by_query", mock_get)

    result = runner.invoke(app, ["search", "Monaco", "--no-select-first-match"])
    assert result.exit_code == 1
    assert "Multiple extracts matched" in result.output
    assert "BBBike" in result.output
    assert "Geofabrik" in result.output
    assert "monaco" in result.output


def test_search_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test search command with --dry-run does not download."""
    mock_result = MagicMock()
    mock_result.matched_extracts = []
    mock_result.extracts = []
    mock_result.sources_used = []
    mock_result.download = MagicMock()
    mock_get = MagicMock(return_value=mock_result)
    monkeypatch.setattr("osmfinder.cli.find_extract_by_query", mock_get)

    result = runner.invoke(app, ["search", "Monaco", "--dry-run"])
    assert result.exit_code == 0
    mock_result.download.assert_not_called()


def test_search_download_info(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test search command shows download info before downloading."""
    from osmfinder._typing import OpenStreetMapExtract

    mock_extract = MagicMock(spec=OpenStreetMapExtract)
    mock_extract.id = "test"
    mock_extract.name = "test"
    mock_extract.file_name = "test"
    mock_extract.geometry = box(0, 0, 1, 1)

    mock_result = MagicMock()
    mock_result.extracts = [mock_extract]
    mock_result.matched_extracts = [mock_extract]
    mock_result.sources_used = []
    mock_get = MagicMock(return_value=mock_result)
    monkeypatch.setattr("osmfinder.cli.find_extract_by_query", mock_get)
    mock_dl = MagicMock()
    mock_dl.download_paths = [Path("/tmp/test.osm.pbf")]
    mock_download = MagicMock(return_value=mock_dl)
    monkeypatch.setattr("osmfinder.cli.download", mock_download)

    result = runner.invoke(app, ["search", "Monaco", "--output", str(tmp_path)])
    assert result.exit_code == 0
    assert "Downloading" in result.output
    assert "Downloaded" in result.output
    mock_download.assert_called_once()


def test_search_results_sorted_by_area_then_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test search command displays matched extracts sorted by area then id."""
    from osmfinder._typing import OpenStreetMapExtract

    def make_extract(extract_id, name, geometry):
        mock = MagicMock(spec=OpenStreetMapExtract)
        mock.id = extract_id
        mock.name = name
        mock.file_name = name
        mock.geometry = geometry
        return mock

    geom_large = box(-10, -10, 10, 10)
    geom_small = box(0, 0, 1, 1)
    geom_medium = box(0, 0, 5, 5)

    extract_large = make_extract("A_large", "large", geom_large)
    extract_small = make_extract("B_small", "small", geom_small)
    extract_medium = make_extract("C_medium", "medium", geom_medium)

    mock_result = MagicMock()
    mock_result.matched_extracts = [extract_large, extract_small, extract_medium]
    mock_result.extracts = []
    mock_result.sources_used = []
    mock_get = MagicMock(return_value=mock_result)
    monkeypatch.setattr("osmfinder.cli.find_extract_by_query", mock_get)
    mock_download = MagicMock(return_value=MagicMock(download_paths=[], unavailable_extracts=[]))
    monkeypatch.setattr("osmfinder.cli.download", mock_download)

    result = runner.invoke(app, ["search", "test"])
    assert result.exit_code == 0

    output = result.output
    pos_large = output.index("large")
    pos_small = output.index("small")
    pos_medium = output.index("medium")
    assert pos_small < pos_medium < pos_large


def test_covers_results_follow_steps_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test covers command displays selected extracts in steps order."""
    from shapely.geometry import box

    mock_extract1 = MagicMock()
    mock_extract1.id = "extract_a"
    mock_extract1.name = "A"
    mock_extract1.file_name = "a"
    mock_extract1.geometry = box(0, 0, 1, 1)

    mock_extract2 = MagicMock()
    mock_extract2.id = "extract_b"
    mock_extract2.name = "B"
    mock_extract2.file_name = "b"
    mock_extract2.geometry = box(0, 0, 1, 1)

    mock_step1 = MagicMock()
    mock_step1.selected = True
    mock_step1.extract = mock_extract1
    mock_step1.iou = 0.5
    mock_step1.reason = "first_extract"

    mock_step2 = MagicMock()
    mock_step2.selected = True
    mock_step2.extract = mock_extract2
    mock_step2.iou = 0.3
    mock_step2.reason = "selected"

    mock_result = MagicMock()
    mock_result.extracts = [mock_extract2, mock_extract1]
    mock_result.steps = [mock_step1, mock_step2]
    mock_result.input_geometry = box(0, 0, 1, 1)
    mock_result.covered_geometry = box(0, 0, 1, 1)
    mock_result.iou_threshold = 0.01
    mock_result.sources_used = []
    mock_find = MagicMock(return_value=mock_result)
    monkeypatch.setattr("osmfinder.cli.find_extracts_by_geometry", mock_find)

    result = runner.invoke(app, ["covers", "--bbox", "0,0,1,1", "--dry-run"])
    assert result.exit_code == 0

    output = result.output
    pos_a = output.index("A")
    pos_b = output.index("B")
    assert pos_a < pos_b


def test_list_invokes_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test list command calls display_available_extracts."""
    mock_display = MagicMock()
    monkeypatch.setattr("osmfinder.cli.display_available_extracts", mock_display)

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    mock_display.assert_called_once_with(source="any", use_full_names=True, use_pager=True)


def test_list_with_multiple_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test list command with multiple sources falls back to any."""
    mock_display = MagicMock()
    monkeypatch.setattr("osmfinder.cli.display_available_extracts", mock_display)

    result = runner.invoke(app, ["list", "--source", "geofabrik,bbbike"])
    assert result.exit_code == 0
    mock_display.assert_called_once_with(source="any", use_full_names=True, use_pager=True)


def test_covers_invokes_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test covers command calls find_extracts_by_geometry."""
    from shapely.geometry import box

    mock_result = MagicMock()
    mock_result.extracts = []
    mock_result.steps = []
    mock_result.input_geometry = box(0, 0, 1, 1)
    mock_result.covered_geometry = box(0, 0, 1, 1)
    mock_result.iou_threshold = 0.01
    mock_result.sources_used = []
    mock_result.download = MagicMock()
    mock_find = MagicMock(return_value=mock_result)
    monkeypatch.setattr("osmfinder.cli.find_extracts_by_geometry", mock_find)

    result = runner.invoke(app, ["covers", "--bbox", "0,0,1,1"])
    assert result.exit_code == 0
    called_geometry = mock_find.call_args[0][0]
    assert called_geometry.equals(box(0, 0, 1, 1))
    mock_result.download.assert_not_called()


def test_covers_no_geometry_error() -> None:
    """Test covers command errors when no geometry is provided."""
    result = runner.invoke(app, ["covers"])
    assert result.exit_code == 1
    assert "No geometry provided" in result.output


def test_covers_multiple_geometry_error() -> None:
    """Test covers command errors when multiple geometries are provided."""
    result = runner.invoke(app, ["covers", "--bbox", "0,0,1,1", "--wkt", "POINT(0 0)"])
    assert result.exit_code == 1
    assert "only one geometry input" in result.output


def test_covers_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test covers command with --dry-run does not download."""
    from shapely.geometry import box

    mock_result = MagicMock()
    mock_result.extracts = []
    mock_result.steps = []
    mock_result.input_geometry = box(0, 0, 1, 1)
    mock_result.covered_geometry = box(0, 0, 1, 1)
    mock_result.iou_threshold = 0.01
    mock_result.sources_used = []
    mock_result.download = MagicMock()
    mock_find = MagicMock(return_value=mock_result)
    monkeypatch.setattr("osmfinder.cli.find_extracts_by_geometry", mock_find)

    result = runner.invoke(app, ["covers", "--bbox", "0,0,1,1", "--dry-run"])
    assert result.exit_code == 0
    mock_result.download.assert_not_called()


def test_covers_download_info(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test covers command shows download info before downloading."""
    from shapely.geometry import box

    mock_extract = MagicMock()
    mock_extract.id = "test"
    mock_extract.name = "test"
    mock_extract.file_name = "test"
    mock_extract.geometry = box(0, 0, 1, 1)

    mock_result = MagicMock()
    mock_result.extracts = [mock_extract]
    mock_result.steps = []
    mock_result.input_geometry = box(0, 0, 1, 1)
    mock_result.covered_geometry = box(0, 0, 1, 1)
    mock_result.iou_threshold = 0.01
    mock_result.sources_used = []
    mock_find = MagicMock(return_value=mock_result)
    monkeypatch.setattr("osmfinder.cli.find_extracts_by_geometry", mock_find)
    mock_dl = MagicMock()
    mock_dl.download_paths = [Path("/tmp/test.osm.pbf")]
    mock_download = MagicMock(return_value=mock_dl)
    monkeypatch.setattr("osmfinder.cli.download", mock_download)

    result = runner.invoke(app, ["covers", "--bbox", "0,0,1,1", "--output", str(tmp_path)])
    assert result.exit_code == 0
    assert "Downloading" in result.output
    assert "Downloaded" in result.output
    mock_download.assert_called_once()


def test_clear_invokes_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test clear command calls clear_osm_index_cache without arguments."""
    mock_clear = MagicMock()
    monkeypatch.setattr("osmfinder.cli.clear_osm_index_cache", mock_clear)

    result = runner.invoke(app, ["clear"])
    assert result.exit_code == 0
    mock_clear.assert_called_once_with()


def test_clear_with_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test clear command with --source flag."""
    mock_clear = MagicMock()
    monkeypatch.setattr("osmfinder.cli.clear_osm_index_cache", mock_clear)

    result = runner.invoke(app, ["clear", "--source", "Geofabrik"])
    assert result.exit_code == 0
    from osmfinder._typing import OsmExtractSource

    mock_clear.assert_called_once_with(extract_source=OsmExtractSource.geofabrik)


def test_clear_with_invalid_source() -> None:
    """Test clear command with invalid source prints error."""
    result = runner.invoke(app, ["clear", "--source", "invalid-source"])
    assert result.exit_code == 1
    assert "Unknown OSM extracts source" in result.output
