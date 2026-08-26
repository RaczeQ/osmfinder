"""
Download of OSM extracts.

Public ``download`` entry point and its private download helpers. The
``_download_single_extract`` helper is monkeypatched on the
``osmfinder.finder`` package namespace by the test suite (and ``conftest``),
so calls to it are resolved through the package module object ``_finder``
rather than a local import binding. The public find helpers it delegates to
are not patch-sensitive and are imported directly.
"""

import warnings
from pathlib import Path
from typing import overload

from pooch import HTTPDownloader, retrieve
from pooch import get_logger as get_pooch_logger
from requests.exceptions import RequestException
from shapely.geometry.base import BaseGeometry

import osmfinder.finder as _finder
from osmfinder._compat import FORCE_TERMINAL
from osmfinder._constants import OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS
from osmfinder._results import (
    OsmfinderDownloadResult,
    OsmfinderGeometryResult,
    OsmfinderQueryResult,
    OsmfinderResult,
)
from osmfinder._typing import OpenStreetMapExtract, OsmExtractSourceLike
from osmfinder.exceptions import (
    GeometryNotCoveredError,
    OsmExtractsUnavailableError,
    OsmExtractUnavailableWarning,
    OsmExtractZeroMatchesError,
)
from osmfinder.finder._covering import find_extracts_by_geometry
from osmfinder.finder._query import find_extract_by_query


def _download_single_extract(
    extract: OpenStreetMapExtract,
    download_directory: Path,
    progressbar: bool = True,
    force_refresh: bool = False,
) -> Path:
    """Download a single OSM extract as a PBF file."""
    target_path = download_directory / f"{extract.file_name}.osm.pbf"

    if target_path.exists() and not force_refresh:
        return target_path

    import shutil
    import tempfile

    download_directory.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=download_directory) as tmp_dir:
        downloaded = retrieve(
            extract.url,
            fname=f"{extract.file_name}.osm.pbf",
            path=tmp_dir,
            progressbar=progressbar and not FORCE_TERMINAL,
            known_hash=None,
            downloader=HTTPDownloader(timeout=OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS),
        )
        shutil.move(downloaded, target_path)

    return target_path


def _download_extracts_pbf_files(
    extracts: list[OpenStreetMapExtract],
    download_directory: Path,
    progressbar: bool = True,
    ignore_unavailable: bool = False,
    force_refresh: bool = False,
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
        force_refresh (bool, optional): When `True`, re-download even if the file already exists.
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
        try:
            downloaded.append(
                (
                    extract,
                    _finder._download_single_extract(
                        extract, download_directory, progressbar, force_refresh=force_refresh
                    ),
                )
            )
        except RequestException:
            if not ignore_unavailable:
                raise
            unavailable.append(extract)

    return downloaded, unavailable


@overload
def download(
    query: OsmfinderResult,
    source: OsmExtractSourceLike | None = None,
    *,
    download_directory: str | Path = "files",
    progressbar: bool = True,
    force_refresh: bool = False,
    retry_on_unavailable: bool = True,
) -> OsmfinderDownloadResult: ...


@overload
def download(
    query: OpenStreetMapExtract,
    source: OsmExtractSourceLike | None = None,
    *,
    download_directory: str | Path = "files",
    progressbar: bool = True,
    force_refresh: bool = False,
    retry_on_unavailable: bool = True,
) -> OsmfinderDownloadResult: ...


@overload
def download(
    query: list[OpenStreetMapExtract],
    source: OsmExtractSourceLike | None = None,
    *,
    download_directory: str | Path = "files",
    progressbar: bool = True,
    force_refresh: bool = False,
    retry_on_unavailable: bool = True,
) -> OsmfinderDownloadResult: ...


@overload
def download(
    query: str,
    source: OsmExtractSourceLike | None = None,
    *,
    download_directory: str | Path = "files",
    select_first_match: bool | None = None,
    progressbar: bool = True,
    force_refresh: bool = False,
) -> OsmfinderDownloadResult: ...


@overload
def download(
    query: BaseGeometry,
    source: OsmExtractSourceLike | None = None,
    *,
    download_directory: str | Path = "files",
    geometry_coverage_iou_threshold: float | None = None,
    allow_uncovered_geometry: bool | None = None,
    force_single_result: bool | None = None,
    single_result_iou_threshold: float | None = None,
    progressbar: bool = True,
    force_refresh: bool = False,
) -> OsmfinderDownloadResult: ...


def download(
    query: str | BaseGeometry | OsmfinderResult | OpenStreetMapExtract | list[OpenStreetMapExtract],
    source: OsmExtractSourceLike | None = None,
    *,
    download_directory: str | Path = "files",
    select_first_match: bool | None = None,
    geometry_coverage_iou_threshold: float | None = None,
    allow_uncovered_geometry: bool | None = None,
    force_single_result: bool | None = None,
    single_result_iou_threshold: float | None = None,
    progressbar: bool = True,
    force_refresh: bool = False,
    retry_on_unavailable: bool = True,
) -> OsmfinderDownloadResult:
    """
    Download OSM extracts.

    When ``retry_on_unavailable`` is ``True`` (the default), unavailable extracts
    are excluded and the search is retried. On success, ``download_paths`` contains
    only the extracts from the final successful attempt; ``unavailable_extracts``
    accumulates all extracts that failed across every attempt.

    Accepts a string query, a geometry, a find result, a single extract, or a list of extracts.

    When ``query`` is an ``OsmfinderResult``, parameters that are not explicitly
    provided, default to the values stored in ``query.config`` from the original
    find operation. Explicit arguments always override those stored defaults.

    Args:
        query: What to download. Accepts a string query, geometry, find result,
            single extract, or list of extracts. A string query or geometry triggers
            a find first; a result or extract list downloads directly.
        source (OsmExtractSourceLike | None): OSM source name. Defaults to `None`.
            When ``query`` is an ``OsmfinderResult`` and ``source`` is not provided,
            the result's ``sources_used`` are used.
        download_directory (str | Path): Directory where files should be downloaded.
            Defaults to "files".
        select_first_match (bool | None): When multiple extracts match the query by name,
            select the first one with a warning. Only used for string queries. Defaults
            to ``None``, in which case the value from ``query.config`` is used when
            ``query`` is an ``OsmfinderResult``, otherwise ``True``.
        geometry_coverage_iou_threshold (float | None): Minimal IoU for selecting extracts.
            Only used for geometry queries. Defaults to ``None``, in which case the value
            from ``query.config`` is used when ``query`` is an ``OsmfinderGeometryResult``,
            otherwise ``0.01``.
        allow_uncovered_geometry (bool | None): Suppress error if geometry parts aren't covered.
            Only used for geometry queries. Defaults to ``None``, in which case the value
            from ``query.config`` is used when ``query`` is an ``OsmfinderGeometryResult``,
            otherwise ``False``.
        force_single_result (bool | None): Return only the single best extract. Only used for
            geometry queries. Defaults to ``None``, in which case the value from
            ``query.config`` is used when ``query`` is an ``OsmfinderGeometryResult``,
            otherwise ``False``.
        single_result_iou_threshold (float | None): Minimal IoU for selecting a single result
            when ``force_single_result`` is ``True`` and ``allow_uncovered_geometry`` is
            ``True``. Only used for geometry queries. Defaults to ``None``, in which case
            the value from ``query.config`` is used when ``query`` is an
            ``OsmfinderGeometryResult``, otherwise ``0.99``.
        progressbar (bool): Show progress bar. Defaults to True.
        force_refresh (bool): When ``True``, re-download even if the file already exists.
            Defaults to False.
        retry_on_unavailable (bool): When ``True``, unavailable extracts are excluded
            and the search is retried. On success, ``download_paths`` contains only
            the extracts from the final successful attempt, while ``unavailable_extracts``
            accumulates all failures across every attempt. When ``False``, the result's
            extracts are downloaded as-is and unavailable extracts raise an exception.
            Defaults to ``True``.

    Returns:
        OsmfinderDownloadResult: Result containing downloaded paths and find result.

    Examples:
        >>> import osmfinder
        >>> from pathlib import Path
        >>> # Download by name
        >>> result = osmfinder.download("Monaco", download_directory="/tmp/osmfinder-doctest")
        >>> len(result.download_paths) == 1
        True
        >>> result.download_paths[0].name
        'movisda-admin_monaco.osm.pbf'
        >>> # Download by geometry
        >>> from shapely.geometry import box
        >>> geom = box(7.40, 43.71, 7.44, 43.75)
        >>> result = osmfinder.download(
        ...     geom, source="Geofabrik", download_directory="/tmp/osmfinder-doctest"
        ... )
        >>> len(result.download_paths) >= 1
        True
        >>> result.download_paths[0].name
        'geofabrik_europe_monaco.osm.pbf'
    """
    download_directory = Path(download_directory)
    find_result: OsmfinderResult
    extracts_to_download: list[OpenStreetMapExtract]
    ignore_unavailable: bool

    if isinstance(query, OsmfinderResult):
        result_config = query.config
    else:
        result_config = {}

    if source is None and isinstance(query, OsmfinderResult):
        source = query.sources_used

    if select_first_match is None:
        select_first_match = result_config.get("select_first_match", True)

    if geometry_coverage_iou_threshold is None:
        geometry_coverage_iou_threshold = result_config.get("geometry_coverage_iou_threshold", 0.01)

    if allow_uncovered_geometry is None:
        allow_uncovered_geometry = result_config.get("allow_uncovered_geometry", False)

    if force_single_result is None:
        force_single_result = result_config.get("force_single_result", False)

    if single_result_iou_threshold is None:
        single_result_iou_threshold = result_config.get("single_result_iou_threshold", 0.99)

    if isinstance(query, str):
        if retry_on_unavailable:
            return _download_with_retry_query(
                query,
                source,
                download_directory,
                select_first_match=select_first_match,
                progressbar=progressbar,
                force_refresh=force_refresh,
            )
        find_result = find_extract_by_query(
            query,
            source=source,
            select_first_match=select_first_match,
        )
        extracts = find_result.extracts
        downloaded, unavailable = _download_extracts_pbf_files(
            extracts,
            download_directory,
            progressbar=progressbar,
            ignore_unavailable=False,
            force_refresh=force_refresh,
        )
        return OsmfinderDownloadResult(
            find_result=find_result,
            download_paths=[path for _, path in downloaded],
            unavailable_extracts=unavailable,
        )
    elif isinstance(query, BaseGeometry):
        if retry_on_unavailable:
            return _download_with_retry_geometry(
                query,
                source,
                download_directory,
                geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
                allow_uncovered_geometry=allow_uncovered_geometry,
                force_single_result=force_single_result,
                single_result_iou_threshold=single_result_iou_threshold,
                progressbar=progressbar,
                force_refresh=force_refresh,
            )
        find_result = find_extracts_by_geometry(
            query,
            source=source,
            geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
            allow_uncovered_geometry=allow_uncovered_geometry,
            force_single_result=force_single_result,
            single_result_iou_threshold=single_result_iou_threshold,
        )
        extracts = find_result.extracts
        downloaded, unavailable = _download_extracts_pbf_files(
            extracts,
            download_directory,
            progressbar=progressbar,
            ignore_unavailable=False,
            force_refresh=force_refresh,
        )
        return OsmfinderDownloadResult(
            find_result=find_result,
            download_paths=[path for _, path in downloaded],
            unavailable_extracts=unavailable,
        )
    elif isinstance(query, OsmfinderResult):
        if retry_on_unavailable:
            if isinstance(query, OsmfinderQueryResult):
                return download(
                    query.query,
                    source=source if source is not None else query.sources_used,
                    download_directory=download_directory,
                    select_first_match=True,
                    progressbar=progressbar,
                    force_refresh=force_refresh,
                )
            elif isinstance(query, OsmfinderGeometryResult):
                return download(
                    query.input_geometry,
                    source=source if source is not None else query.sources_used,
                    download_directory=download_directory,
                    geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
                    allow_uncovered_geometry=allow_uncovered_geometry,
                    force_single_result=force_single_result,
                    single_result_iou_threshold=single_result_iou_threshold,
                    progressbar=progressbar,
                    force_refresh=force_refresh,
                )
        find_result = query
        extracts_to_download = query.extracts
        ignore_unavailable = retry_on_unavailable
    elif isinstance(query, OpenStreetMapExtract):
        find_result = OsmfinderResult(
            extracts=[query],
            sources_used=[],
        )
        extracts_to_download = [query]
        ignore_unavailable = True
    elif isinstance(query, list) and all(isinstance(e, OpenStreetMapExtract) for e in query):
        find_result = OsmfinderResult(
            extracts=query,
            sources_used=[],
        )
        extracts_to_download = query
        ignore_unavailable = True
    else:
        raise TypeError(
            f"Unsupported query type: {type(query).__name__}. "
            "Expected str, BaseGeometry, OsmfinderResult, OpenStreetMapExtract, "
            "or list[OpenStreetMapExtract]."
        )

    if not extracts_to_download:
        return OsmfinderDownloadResult(
            find_result=find_result,
            download_paths=[],
            unavailable_extracts=[],
        )

    downloaded, unavailable = _download_extracts_pbf_files(
        extracts_to_download,
        download_directory,
        progressbar=progressbar,
        ignore_unavailable=ignore_unavailable,
        force_refresh=force_refresh,
    )

    return OsmfinderDownloadResult(
        find_result=find_result,
        download_paths=[path for _, path in downloaded],
        unavailable_extracts=unavailable,
    )


def _download_with_retry_query(
    query: str,
    source: OsmExtractSourceLike | None,
    download_directory: Path,
    *,
    select_first_match: bool = True,
    progressbar: bool = True,
    force_refresh: bool = False,
) -> OsmfinderDownloadResult:
    """
    Download a query string, retrying with alternative extracts if some are unavailable.

    Retries by excluding unavailable extracts from subsequent attempts. On success,
    returns only the extracts downloaded in the final successful attempt. The
    ``unavailable_extracts`` list accumulates all extracts that failed across
    every attempt.

    Args:
        query: Text query to search for.
        source: OSM source name.
        download_directory: Where to save files.
        select_first_match: When multiple extracts match, select the first one with a warning.
        progressbar: Show progress bar.
        force_refresh: Re-download existing files.

    Returns:
        OsmfinderDownloadResult: Result containing downloaded paths and find result.

    Raises:
        OsmExtractZeroMatchesError: If no extracts match the query.
        OsmExtractsUnavailableError: If all matching extracts are unavailable for download.
    """
    excluded_ids: set[str] = set()
    all_unavailable: list[OpenStreetMapExtract] = []

    while True:
        try:
            find_result = find_extract_by_query(
                query,
                source=source,
                select_first_match=select_first_match,
                excluded_extracts_ids=excluded_ids,
            )
        except OsmExtractZeroMatchesError:
            if not all_unavailable:
                raise
            raise OsmExtractsUnavailableError(
                f'All extracts matching query "{query.strip()}" are unavailable for download'
                f" ({', '.join(e.file_name for e in all_unavailable)})."
                " Check your internet connection or try a different source.",
                matching_full_names=sorted(e.file_name for e in all_unavailable),
            ) from None

        extracts = find_result.extracts
        if not extracts:
            break

        downloaded, unavailable = _download_extracts_pbf_files(
            extracts,
            download_directory,
            progressbar=progressbar,
            ignore_unavailable=True,
            force_refresh=force_refresh,
        )
        all_unavailable.extend(unavailable)

        if not unavailable:
            return OsmfinderDownloadResult(
                find_result=find_result,
                download_paths=[path for _, path in downloaded],
                unavailable_extracts=all_unavailable,
            )

        warnings.warn(
            f'Matched extract "{unavailable[0].file_name}" is unavailable.'
            " Excluding it and trying the next matching extract.",
            OsmExtractUnavailableWarning,
            stacklevel=0,
        )
        excluded_ids.update(e.id for e in unavailable)

    return OsmfinderDownloadResult(
        find_result=find_result,
        download_paths=[],
        unavailable_extracts=all_unavailable,
    )


def _download_with_retry_geometry(
    geometry: BaseGeometry,
    source: OsmExtractSourceLike | None,
    download_directory: Path,
    *,
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
    force_single_result: bool = False,
    single_result_iou_threshold: float = 0.99,
    progressbar: bool = True,
    force_refresh: bool = False,
) -> OsmfinderDownloadResult:
    """
    Download geometry coverage, retrying without unavailable extracts.

    Retries by excluding unavailable extracts from subsequent attempts. On success,
    returns only the extracts downloaded in the final successful attempt. The
    ``unavailable_extracts`` list accumulates all extracts that failed across
    every attempt.

    Args:
        geometry: Geometry to cover.
        source: OSM source name.
        download_directory: Where to save files.
        geometry_coverage_iou_threshold: Minimal IoU for selecting extracts.
        allow_uncovered_geometry: Suppress error if geometry parts aren't covered.
        force_single_result: Return only the single best extract.
        single_result_iou_threshold: Minimal IoU for selecting a single result.
        progressbar: Show progress bar.
        force_refresh: Re-download existing files.

    Returns:
        OsmfinderDownloadResult: Result containing downloaded paths and find result.

    Raises:
        GeometryNotCoveredError: If no extracts cover the geometry.
    """
    excluded_ids: set[str] = set()
    all_unavailable: list[OpenStreetMapExtract] = []

    while True:
        try:
            find_result = find_extracts_by_geometry(
                geometry,
                source=source,
                geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
                allow_uncovered_geometry=allow_uncovered_geometry,
                excluded_extracts_ids=excluded_ids,
                force_single_result=force_single_result,
                single_result_iou_threshold=single_result_iou_threshold,
            )
        except GeometryNotCoveredError:
            if not all_unavailable:
                raise
            raise GeometryNotCoveredError(
                "Couldn't find extracts covering given geometry."
                f" Some extracts were unavailable for download"
                f" ({', '.join(e.file_name for e in all_unavailable)})."
                " Check your internet connection or try a different source.",
            ) from None

        extracts = find_result.extracts
        if not extracts:
            break

        downloaded, unavailable = _download_extracts_pbf_files(
            extracts,
            download_directory,
            progressbar=progressbar,
            ignore_unavailable=True,
            force_refresh=force_refresh,
        )
        all_unavailable.extend(unavailable)

        if not unavailable:
            return OsmfinderDownloadResult(
                find_result=find_result,
                download_paths=[path for _, path in downloaded],
                unavailable_extracts=all_unavailable,
            )

        unavailable_names = ", ".join(e.file_name for e in unavailable)
        warnings.warn(
            "Some extracts are unavailable and will be excluded from the search"
            f" ({unavailable_names}). Recalculating coverage without them.",
            OsmExtractUnavailableWarning,
            stacklevel=0,
        )
        excluded_ids.update(e.id for e in unavailable)

    return OsmfinderDownloadResult(
        find_result=find_result,
        download_paths=[],
        unavailable_extracts=all_unavailable,
    )
