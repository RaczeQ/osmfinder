"""Pytest configuration and fixtures for doctests in the osmfinder package."""

from pathlib import Path
from unittest.mock import patch

from osmfinder._typing import OpenStreetMapExtract


def _mock_download_precalculated_index_from_github(destination_path: Path) -> bool:
    """Copy a local frozen test index instead of downloading from GitHub."""
    test_index_dir = Path(__file__).parent.parent / "tests" / "test_indexes"
    src = test_index_dir / destination_path.name
    if src.exists():
        import shutil

        shutil.copy2(src, destination_path)
        return True
    return False


def _mock_download_single_extract(
    extract: OpenStreetMapExtract,
    download_directory: Path,
    progressbar: bool = True,
) -> Path:
    """Create an empty PBF file instead of downloading from the internet."""
    download_directory = Path(download_directory)
    download_directory.mkdir(parents=True, exist_ok=True)
    file_path = download_directory / f"{extract.file_name}.osm.pbf"
    file_path.touch()
    return file_path


patch(
    "osmfinder.extract._download_precalculated_index_from_github",
    side_effect=_mock_download_precalculated_index_from_github,
).start()

patch(
    "osmfinder.finder._download_single_extract",
    side_effect=_mock_download_single_extract,
).start()
