"""Download utilities for OSM extracts."""

from pathlib import Path

from pooch import HTTPDownloader, retrieve
from pooch import get_logger as get_pooch_logger
from requests.exceptions import RequestException

from osmfinder._compat import FORCE_TERMINAL
from osmfinder._constants import OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS
from osmfinder._typing import OpenStreetMapExtract


def download_extracts_pbf_files(
    extracts: list[OpenStreetMapExtract], download_directory: Path, progressbar: bool = True
) -> list[Path]:
    """
    Download OSM extracts as PBF files.

    Args:
        extracts (list[OpenStreetMapExtract]): List of extracts to download.
        download_directory (Path): Directory where PBF files should be saved.
        progressbar (bool, optional): Show progress bar. Defaults to True.

    Returns:
        list[Path]: List of downloaded file paths.

    Examples:
        >>> import osmfinder
        >>> from pathlib import Path
        >>> # Requires a valid extract list; typically obtained from find() first.
        >>> # extracts = osmfinder.find("Monaco")
        >>> # paths = osmfinder.download_extracts_pbf_files(
        >>> #     extracts, download_directory=Path("files")
        >>> # )
        >>> # paths is a list of Path objects pointing to downloaded .osm.pbf files
    """
    downloaded, _ = _download_extracts_pbf_files(
        extracts, download_directory, progressbar=progressbar, ignore_unavailable=False
    )
    return [path for _, path in downloaded]


def _download_single_extract(
    extract: OpenStreetMapExtract, download_directory: Path, progressbar: bool = True
) -> Path:
    """Download a single OSM extract as a PBF file."""
    file_path = retrieve(
        extract.url,
        fname=f"{extract.file_name}.osm.pbf",
        path=download_directory,
        progressbar=progressbar and not FORCE_TERMINAL,
        known_hash=None,
        downloader=HTTPDownloader(timeout=OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS),
    )
    return Path(file_path)


def _download_extracts_pbf_files(
    extracts: list[OpenStreetMapExtract],
    download_directory: Path,
    progressbar: bool = True,
    ignore_unavailable: bool = False,
) -> tuple[list[tuple[OpenStreetMapExtract, Path]], list[OpenStreetMapExtract]]:
    """
    Download OSM extracts as PBF files, optionally tolerating unavailable ones.

    Args:
        extracts (list[OpenStreetMapExtract]): List of extracts to download.
        download_directory (Path): Directory where PBF files should be saved.
        progressbar (bool, optional): Show progress bar. Defaults to True.
        ignore_unavailable (bool, optional): If `True`, network errors for a single extract
            are caught and the extract is reported as unavailable instead of raising.
            Defaults to `False`.

    Returns:
        tuple[list[tuple[OpenStreetMapExtract, Path]], list[OpenStreetMapExtract]]:
            A tuple with a list of (extract, downloaded path) pairs and a list
            of extracts that couldn't be downloaded.
    """
    logger = get_pooch_logger()
    logger.setLevel("WARNING")

    downloaded: list[tuple[OpenStreetMapExtract, Path]] = []
    unavailable: list[OpenStreetMapExtract] = []

    for extract in extracts:
        if not ignore_unavailable:
            downloaded.append(
                (extract, _download_single_extract(extract, download_directory, progressbar))
            )
            continue
        try:
            downloaded.append(
                (extract, _download_single_extract(extract, download_directory, progressbar))
            )
        except RequestException:
            unavailable.append(extract)

    return downloaded, unavailable
