"""
OpenStreetMap extracts.

This module contains iterators for publically available OpenStreetMap `*.osm.pbf` files
repositories.
"""

import difflib
import warnings
from collections.abc import Iterable
from functools import partial
from math import ceil
from multiprocessing import cpu_count
from pathlib import Path
from typing import cast, overload

import numpy as np
from pooch import HTTPDownloader, retrieve
from pooch import get_logger as get_pooch_logger
from requests.exceptions import RequestException
from rich import get_console
from rich import print as rprint
from shapely import equals_exact, intersects, is_empty, unary_union
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry
from tqdm.contrib.concurrent import process_map

from osmfinder._compat import FORCE_TERMINAL
from osmfinder._constants import OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS
from osmfinder._results import (
    GeometryCoveringStep,
    OsmfinderDownloadResult,
    OsmfinderGeometryResult,
    OsmfinderQueryResult,
    OsmfinderResult,
)
from osmfinder._typing import (
    OpenStreetMapExtract,
    OsmExtractsIndex,
    OsmExtractSource,
    _calculate_geodetic_area,
)
from osmfinder.exceptions import (
    GeometryNotCoveredError,
    GeometryNotCoveredWarning,
    OsmExtractMultipleMatchesError,
    OsmExtractMultipleMatchesWarning,
    OsmExtractsIndexesUnavailableError,
    OsmExtractSourceUnavailableWarning,
    OsmExtractsUnavailableError,
    OsmExtractUnavailableWarning,
    OsmExtractZeroMatchesError,
)
from osmfinder.extract import clear_osm_index_cache
from osmfinder.sources.bbbike import _get_bbbike_index
from osmfinder.sources.geo2day import _get_geo2day_index
from osmfinder.sources.geofabrik import _get_geofabrik_index
from osmfinder.sources.movisda import _get_movisda_admin_index, _get_movisda_grid_index
from osmfinder.sources.osm_fr import _get_openstreetmap_fr_index
from osmfinder.sources.tree import get_available_extracts_as_rich_tree

__all__ = [
    "download",
    "find",
    "find_extract_by_query",
    "find_extracts_by_geometry",
    "find_extracts_covering_point",
    "get_available_extracts",
    "display_available_extracts",
    "clear_osm_index_cache",
    "OsmExtractSource",
    "OsmExtractSourceLike",
]


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


def download_extracts(
    extracts: list[OpenStreetMapExtract] | OpenStreetMapExtract | OsmfinderResult,
    download_directory: str | Path = "files",
    progressbar: bool = True,
) -> list[Path]:
    """
    Download OSM extracts as PBF files.

    Accepts a single extract, a list of extracts, or an :class:`OsmfinderResult` object.
    Unavailable extracts are skipped with a warning instead of raising.

    Args:
        extracts (Union[list[OpenStreetMapExtract], OpenStreetMapExtract, OsmfinderResult]):
            Extracts to download.
        download_directory (Union[str, Path]): Directory where PBF files should be saved.
            Defaults to "files".
        progressbar (bool, optional): Show progress bar. Defaults to True.

    Returns:
        list[Path]: List of downloaded file paths.
    """
    download_directory = Path(download_directory)

    if isinstance(extracts, OsmfinderResult):
        extracts_to_download = extracts.extracts
    elif isinstance(extracts, OpenStreetMapExtract):
        extracts_to_download = [extracts]
    else:
        extracts_to_download = extracts

    if not extracts_to_download:
        return []

    downloaded, unavailable = _download_extracts_pbf_files(
        extracts_to_download,
        download_directory,
        progressbar=progressbar,
        ignore_unavailable=True,
    )

    if unavailable:
        unavailable_names = ", ".join(e.file_name for e in unavailable)
        warnings.warn(
            f"Some extracts are unavailable for download ({unavailable_names}).",
            OsmExtractUnavailableWarning,
            stacklevel=0,
        )

    return [path for _, path in downloaded]


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
                    _download_single_extract(
                        extract, download_directory, progressbar, force_refresh=force_refresh
                    ),
                )
            )
        except RequestException:
            if not ignore_unavailable:
                raise
            unavailable.append(extract)

    return downloaded, unavailable


OSM_EXTRACT_SOURCE_INDEX_FUNCTION = {
    OsmExtractSource.bbbike: _get_bbbike_index,
    OsmExtractSource.geofabrik: _get_geofabrik_index,
    OsmExtractSource.osm_fr: _get_openstreetmap_fr_index,
    OsmExtractSource.geo2day: _get_geo2day_index,
    OsmExtractSource.movisda_admin: _get_movisda_admin_index,
    OsmExtractSource.movisda_grid: _get_movisda_grid_index,
}

# A single source, or multiple sources passed as an iterable or a comma-separated string.
OsmExtractSourceLike = OsmExtractSource | str | Iterable[OsmExtractSource | str]


def _download_with_retry_query(
    query: str,
    source: OsmExtractSourceLike,
    download_directory: Path,
    *,
    select_first_match: bool = True,
    progressbar: bool = True,
    force_refresh: bool = False,
) -> OsmfinderDownloadResult:
    """
    Download a query string, retrying with alternative extracts if some are unavailable.

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
    source: OsmExtractSourceLike,
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


def _resolve_extract_sources(source: OsmExtractSourceLike) -> list[OsmExtractSource]:
    """
    Normalize a source specification into a list of concrete OSM extract sources.

    Accepts a single `OsmExtractSource`/string, an iterable of them, or a comma-separated
    string (e.g. `"bbbike,osmfr"`). The `any` source is expanded to all available sources.
    Duplicates are removed while preserving order.

    Args:
        source (OsmExtractSourceLike): Source specification.

    Raises:
        ValueError: If a provided value can't be parsed to an `OsmExtractSource`,
            or if the specification is empty.

    Returns:
        list[OsmExtractSource]: List of concrete sources (without `any`).
    """
    if isinstance(source, OsmExtractSource):
        raw_values: list[OsmExtractSource | str] = [source]
    elif isinstance(source, str):
        raw_values = source.split(",")
    else:
        raw_values = []
        for single_source in source:
            if isinstance(single_source, str):
                raw_values.extend(single_source.split(","))
            else:
                raw_values.append(single_source)

    resolved: list[OsmExtractSource] = []
    for raw_value in raw_values:
        cleaned_value = raw_value.strip() if isinstance(raw_value, str) else raw_value
        if cleaned_value == "":
            continue
        source_enum = OsmExtractSource(cleaned_value)
        if source_enum == OsmExtractSource.any:
            resolved.extend(OSM_EXTRACT_SOURCE_INDEX_FUNCTION.keys())
        else:
            resolved.append(source_enum)

    if not resolved:
        raise ValueError("No OSM extracts source provided.")

    seen: set[OsmExtractSource] = set()
    deduplicated: list[OsmExtractSource] = []
    for item in resolved:
        if item not in seen:
            seen.add(item)
            deduplicated.append(item)
    return deduplicated


def _get_index_for_sources(source: OsmExtractSourceLike) -> OsmExtractsIndex:
    """
    Load and combine extract indexes for one or multiple sources.

    For a single source, loading errors propagate - the request can't be fulfilled otherwise.
    For multiple sources (including ``any``), sources whose index can't be loaded (e.g. when
    offline and not cached locally) are skipped with a warning, as long as at least one source
    loads successfully. If none can be loaded, an error is raised.
    """
    resolved_sources = _resolve_extract_sources(source)

    if len(resolved_sources) == 1:
        return OSM_EXTRACT_SOURCE_INDEX_FUNCTION[resolved_sources[0]]()

    loaded_indexes: list[OsmExtractsIndex] = []
    unavailable_sources: list[OsmExtractSource] = []
    for resolved_source in resolved_sources:
        try:
            loaded_indexes.append(OSM_EXTRACT_SOURCE_INDEX_FUNCTION[resolved_source]())
        except RequestException:
            unavailable_sources.append(resolved_source)

    if unavailable_sources:
        warnings.warn(
            "Couldn't load indexes for some OSM extract sources (offline or unreachable?):"
            f" {[unavailable_source.value for unavailable_source in unavailable_sources]}."
            " Continuing with the available sources.",
            OsmExtractSourceUnavailableWarning,
            stacklevel=0,
        )

    if not loaded_indexes:
        raise OsmExtractsIndexesUnavailableError(
            "Couldn't load any OSM extracts index for the requested sources:"
            f" {[resolved_source.value for resolved_source in resolved_sources]}."
            " Check your internet connection or the local cache."
        )

    return OsmExtractsIndex.combine_indexes(loaded_indexes)


def _get_combined_index() -> OsmExtractsIndex:
    return _get_index_for_sources(OsmExtractSource.any)


@overload
def find_extract_by_query(query: str) -> OsmfinderQueryResult: ...


@overload
def find_extract_by_query(query: str, source: OsmExtractSourceLike) -> OsmfinderQueryResult: ...


@overload
def find_extract_by_query(
    query: str,
    *,
    select_first_match: bool = ...,
    excluded_extracts_ids: set[str] | None = ...,
) -> OsmfinderQueryResult: ...


@overload
def find_extract_by_query(
    query: str,
    source: OsmExtractSourceLike,
    select_first_match: bool = ...,
    excluded_extracts_ids: set[str] | None = ...,
) -> OsmfinderQueryResult: ...


def find_extract_by_query(
    query: str,
    source: OsmExtractSourceLike = "any",
    select_first_match: bool = True,
    excluded_extracts_ids: set[str] | None = None,
) -> OsmfinderQueryResult:
    """
    Find an OSM extract by name.

    Args:
        query (str): Query to search for a particular extract.
        source (OsmExtractSourceLike): OSM source name. Can be one of: 'any', 'Geofabrik',
            'BBBike', 'OSM_fr', or an iterable / comma-separated string of those
            (e.g. ['BBBike', 'OSM_fr'] or 'bbbike,osmfr'). Defaults to 'any'.
        select_first_match (bool): When multiple extracts match the query by name, select the
            first one (sorted by area ascending, then id) and emit a warning instead of raising
            an error. Set to `False` to raise `OsmExtractMultipleMatchesError` instead.
            Defaults to `True`.
        excluded_extracts_ids (Optional[set[str]]): Set of extract ids to exclude from the search.
            Useful for skipping extracts that are unavailable for download. Defaults to `None`.

    Returns:
        OsmfinderQueryResult: Result containing the matched extract.

    Examples:
        >>> import osmfinder
        >>> result = osmfinder.find_extract_by_query("Monaco")
        >>> isinstance(result, osmfinder.OsmfinderQueryResult)
        True
        >>> result.extracts[0].id
        'Movisda-admin_MC'
        >>> result.extracts[0].file_name
        'movisda-admin_monaco'
        >>> result.sources_used
        [<OsmExtractSource.bbbike: 'BBBike'>, ...]
    """
    try:
        index = _get_index_for_sources(source)

        if excluded_extracts_ids:
            index = index.filter_by_mask(~np.isin(index.ids, list(excluded_extracts_ids)))

        query_lower = query.lower().strip()
        query_lower_spaces = query_lower.replace("_", " ")

        file_names = index.file_names.astype(str)
        names = index.names.astype(str)
        file_names_lower = np.char.lower(file_names)
        names_lower = np.char.lower(names)
        if file_names.size > 0:
            file_names_lower_spaces = np.char.replace(file_names_lower, "_", " ")
            names_lower_spaces = np.char.replace(names_lower, "_", " ")
        else:
            file_names_lower_spaces = file_names_lower
            names_lower_spaces = names_lower

        file_name_matched_rows = (file_names_lower == query_lower) | (
            file_names_lower_spaces == query_lower_spaces
        )
        extract_name_matched_rows = (names_lower == query_lower) | (
            names_lower_spaces == query_lower_spaces
        )

        matching_index_row: OpenStreetMapExtract | None = None
        matched_extracts: list[OpenStreetMapExtract] = []

        # full file name matched
        if np.count_nonzero(file_name_matched_rows) == 1:
            matching_index_row = index.get_extract_by_index(
                int(np.flatnonzero(file_name_matched_rows)[0])
            )
            matched_extracts = [matching_index_row]
        # single name matched
        elif np.count_nonzero(extract_name_matched_rows) == 1:
            matching_index_row = index.get_extract_by_index(
                int(np.flatnonzero(extract_name_matched_rows)[0])
            )
            matched_extracts = [matching_index_row]
        # multiple names matched
        elif extract_name_matched_rows.any():
            matching_rows = index.filter_by_mask(extract_name_matched_rows)
            matched_extracts = [
                matching_rows.get_extract_by_index(i) for i in range(len(matching_rows.ids))
            ]
            matching_full_names = sorted(matching_rows.file_names)
            full_names = ", ".join(f'"{full_name}"' for full_name in matching_full_names)

            if not select_first_match:
                raise OsmExtractMultipleMatchesError(
                    f'Multiple extracts matched by query "{query.strip()}".\n'
                    f"Matching extracts full names: {full_names}.",
                    matching_full_names=matching_full_names,
                )

            # Select the smallest-area match (index is already sorted by area, then id).
            matching_index_row = matching_rows.get_extract_by_index(0)
            warnings.warn(
                f'Multiple extracts matched by query "{query.strip()}"'
                f" (matching full names: {full_names})."
                f' Selected "{matching_index_row.file_name}".'
                " Use the full name as a query or set `select_first_match=False`"
                " to control this behaviour.",
                OsmExtractMultipleMatchesWarning,
                stacklevel=0,
            )
        # zero names matched
        elif not extract_name_matched_rows.any():
            matching_full_names = []
            unique_names_lower = np.unique(names_lower)
            suggested_query_names = difflib.get_close_matches(
                query_lower, unique_names_lower, n=5, cutoff=0.7
            )

            if suggested_query_names:
                for suggested_query_name in suggested_query_names:
                    found_extracts = index.filter_by_mask(names_lower == suggested_query_name)
                    matching_full_names.extend(found_extracts.file_names)
                full_names = ", ".join(f'"{full_name}"' for full_name in matching_full_names)
                exception_message = (
                    f'Zero extracts matched by query "{query}".\n'
                    f"Found full names close to query: {full_names}."
                )
            else:
                exception_message = (
                    f'Zero extracts matched by query "{query}".\n'
                    "Zero close matches have been found."
                )

            raise OsmExtractZeroMatchesError(
                exception_message,
                matching_full_names=matching_full_names,
            )

        sources_used = _resolve_extract_sources(source)
        if matching_index_row is None:
            raise RuntimeError("Failed to select a matching extract.")
        else:
            return OsmfinderQueryResult(
                query=query,
                extracts=[matching_index_row],
                matched_extracts=matched_extracts,
                sources_used=sources_used,
            )

    except ValueError as ex:
        raise ValueError(f"Unknown OSM extracts source: {source}.") from ex


@overload
def download_extract_by_query(
    query: str,
    *,
    download_directory: str | Path = "files",
    progressbar: bool = True,
    select_first_match: bool = True,
) -> OsmfinderDownloadResult: ...


@overload
def download_extract_by_query(
    query: str,
    source: OsmExtractSourceLike,
    *,
    download_directory: str | Path = "files",
    progressbar: bool = True,
    select_first_match: bool = True,
) -> OsmfinderDownloadResult: ...


def download_extract_by_query(
    query: str,
    source: OsmExtractSourceLike = "any",
    download_directory: str | Path = "files",
    progressbar: bool = True,
    select_first_match: bool = True,
) -> OsmfinderDownloadResult:
    """
    Download an OSM extract by name.

    Args:
        query (str): Query to search for a particular extract.
        source (OsmExtractSourceLike): OSM source name. Can be one of: 'any', 'Geofabrik',
            'BBBike', 'OSM_fr', or an iterable / comma-separated string of those
            (e.g. ['BBBike', 'OSM_fr'] or 'bbbike,osmfr'). Defaults to 'any'.
        download_directory (Union[str, Path], optional): Directory where the file should be
            downloaded. Defaults to "files".
        progressbar (bool, optional): Show progress bar. Defaults to True.
        select_first_match (bool, optional): When multiple extracts match the query by name,
            select the first one (sorted by area ascending, then id) with a warning instead of
            raising an error. Defaults to `True`.

    Returns:
        OsmfinderDownloadResult: Result containing the downloaded path and find result.

    Examples:
        >>> import osmfinder
        >>> from pathlib import Path
        >>> result = osmfinder.download_extract_by_query(
        ...     "Monaco", download_directory="/tmp/osmfinder-doctest"
        ... )
        >>> isinstance(result, osmfinder.OsmfinderDownloadResult)
        True
        >>> isinstance(result.download_paths[0], Path)
        True
        >>> result.download_paths[0].name
        'movisda-admin_monaco.osm.pbf'
        >>> result.find_result.extracts[0].id
        'Movisda-admin_MC'
    """
    download_directory = Path(download_directory)
    excluded_extracts_ids: set[str] = set()
    unavailable_file_names: list[str] = []
    all_unavailable: list[OpenStreetMapExtract] = []

    while True:
        try:
            query_result = find_extract_by_query(
                query,
                source,
                select_first_match=select_first_match,
                excluded_extracts_ids=excluded_extracts_ids,
            )
            matching_extract = query_result.extracts[0]
        except OsmExtractZeroMatchesError:
            if not unavailable_file_names:
                raise
            raise OsmExtractsUnavailableError(
                f'All extracts matching query "{query.strip()}" are unavailable for download'
                f" ({', '.join(unavailable_file_names)})."
                " Check your internet connection or try a different source.",
                matching_full_names=sorted(unavailable_file_names),
            ) from None

        downloaded, unavailable = _download_extracts_pbf_files(
            [matching_extract],
            download_directory,
            progressbar=progressbar,
            ignore_unavailable=True,
        )
        all_unavailable.extend(unavailable)

        if not unavailable:
            return OsmfinderDownloadResult(
                find_result=query_result,
                download_paths=[downloaded[0][1]],
                unavailable_extracts=all_unavailable,
            )

        warnings.warn(
            f'Matched extract "{matching_extract.file_name}" is unavailable.'
            " Excluding it and trying the next matching extract.",
            OsmExtractUnavailableWarning,
            stacklevel=0,
        )
        excluded_extracts_ids.add(matching_extract.id)
        unavailable_file_names.append(matching_extract.file_name)


def find_and_download_extracts_pbf_files(
    geometry: BaseGeometry,
    source: OsmExtractSourceLike = "any",
    download_directory: str | Path = "files",
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
    force_single_result: bool = False,
    single_result_iou_threshold: float = 0.99,
    progressbar: bool = True,
) -> OsmfinderDownloadResult:
    """
    Find the smallest set of extracts covering a geometry and download them as PBF files.

    Searches for the smallest set of extracts covering a given geometry and downloads them.
    If any matching extract turns out to be unavailable (e.g. removed from the provider or
    a temporary server error), it is excluded and the coverage is recalculated using the
    remaining extracts, until a fully downloadable set is found or the geometry can no longer
    be covered.

    Args:
        geometry (BaseGeometry): Geometry to be covered.
        source (OsmExtractSourceLike): OSM source name. Can be one of: 'any', 'Geofabrik',
            'BBBike', 'OSMfr', or an iterable / comma-separated string of those
            (e.g. ['BBBike', 'OSM_fr'] or 'bbbike,osmfr'). Defaults to 'any'.
        download_directory (Union[str, Path]): Directory where PBF files should be saved.
            Defaults to "files".
        geometry_coverage_iou_threshold (float): Minimal value of the Intersection over Union metric
            for selecting the matching OSM extracts. Has to be in range between 0 and 1.
            Defaults to 0.01.
        allow_uncovered_geometry (bool): Suppress an error if some geometry parts aren't covered
            by any OSM extract. Defaults to `False`.
        force_single_result (bool): When ``True``, return only the smallest extract that best covers
            the geometry. If ``allow_uncovered_geometry`` is ``False``, the smallest fully
            containing extract is returned. If ``allow_uncovered_geometry`` is ``True``, the
            extract with the highest IoU above ``single_result_iou_threshold`` is returned.
            If no candidate meets the threshold, the smallest fully containing extract is used as
            a fallback.
            Defaults to ``False``.
        single_result_iou_threshold (float): Minimal IoU value for selecting a single result when
            ``force_single_result`` is ``True`` and ``allow_uncovered_geometry`` is ``True``.
            Defaults to 0.99.
        progressbar (bool, optional): Show progress bar. Defaults to True.

    Raises:
        GeometryNotCoveredError: If the geometry can't be covered by available extracts.

    Returns:
        OsmfinderDownloadResult: Result containing downloaded paths and find result.

    Examples:
        >>> import osmfinder
        >>> from shapely.geometry import box
        >>> geom = box(7.40, 43.71, 7.44, 43.75)
        >>> result = osmfinder.find_and_download_extracts_pbf_files(
        ...     geom, source="Geofabrik", download_directory="/tmp/osmfinder-doctest"
        ... )
        >>> isinstance(result, osmfinder.OsmfinderDownloadResult)
        True
        >>> len(result.download_paths) >= 1
        True
        >>> result.download_paths[0].name
        'geofabrik_europe_monaco.osm.pbf'
    """
    download_directory = Path(download_directory)
    excluded_extracts_ids: set[str] = set()
    all_unavailable: list[OpenStreetMapExtract] = []

    while True:
        matching_extracts = find_extracts_by_geometry(
            geometry,
            source,
            geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
            allow_uncovered_geometry=allow_uncovered_geometry,
            excluded_extracts_ids=excluded_extracts_ids,
            force_single_result=force_single_result,
            single_result_iou_threshold=single_result_iou_threshold,
        )

        downloaded, unavailable = _download_extracts_pbf_files(
            matching_extracts.extracts,
            download_directory,
            progressbar=progressbar,
            ignore_unavailable=True,
        )
        all_unavailable.extend(unavailable)

        if not unavailable:
            return OsmfinderDownloadResult(
                find_result=matching_extracts,
                download_paths=[path for _, path in downloaded],
                unavailable_extracts=all_unavailable,
            )

        unavailable_file_names = ", ".join(extract.file_name for extract in unavailable)
        warnings.warn(
            (
                "Some matching extracts are unavailable and will be excluded from the search"
                f" ({unavailable_file_names}). Recalculating the coverage without them."
            ),
            OsmExtractUnavailableWarning,
            stacklevel=0,
        )
        excluded_extracts_ids.update(extract.id for extract in unavailable)


def display_available_extracts(
    source: OsmExtractSource | str,
    use_full_names: bool = True,
    use_pager: bool = False,
) -> None:
    """
    Display all available OSM extracts in the form of a tree.

    Output will be printed to the console.

    Args:
        source (Union[OsmExtractSource, str]): Source for which extracts should be displayed.
        use_full_names (bool, optional): Whether to display full name, or short name of the extract.
            Full name contains all parents of the extract. Defaults to `True`.
        use_pager (bool, optional): Whether to display long output using Rich pager
            or just print to output. Defaults to `False`.

    Raises:
        ValueError: If provided source value cannot be parsed to OsmExtractSource.

    Examples:
        >>> import osmfinder
        >>> # Prints a Rich tree to the console; no return value to assert.
        >>> osmfinder.display_available_extracts("Geofabrik")  # doctest: +SKIP
    """
    try:
        source_enum = OsmExtractSource(source)
        tree = get_available_extracts_as_rich_tree(
            source_enum, OSM_EXTRACT_SOURCE_INDEX_FUNCTION, use_full_names
        )
        if not use_pager:
            rprint(tree)
        else:
            console = get_console()
            with console.pager():
                console.print(tree)
    except ValueError as ex:
        raise ValueError(f"Unknown OSM extracts source: {source}.") from ex


def get_available_extracts(
    source: OsmExtractSourceLike = "any",
    excluded_extracts_ids: set[str] | None = None,
) -> list[OpenStreetMapExtract]:
    """
    Return all available OSM extracts for a source as a list.

    Uses the same index loading and caching as the rest of the API. Extracts are returned
    sorted by area ascending (then id), matching the order used by the internal index.

    Args:
        source (OsmExtractSourceLike): OSM source name. Can be one of: 'any', 'Geofabrik',
            'BBBike', 'osmfr', 'GEO2Day', 'Movisda-admin', 'Movisda-grid', or an iterable /
            comma-separated string of those (e.g. ['BBBike', 'OSMfr'] or 'bbbike,osmfr').
            Defaults to 'any'.
        excluded_extracts_ids (Optional[set[str]]): Set of extract ids to exclude from the
            result. Useful for skipping extracts that are unavailable. Defaults to `None`.

    Returns:
        list[OpenStreetMapExtract]: List of all available extracts for the given source(s).

    Examples:
        >>> import osmfinder
        >>> # Get all extracts from a single source
        >>> extracts = osmfinder.get_available_extracts("Geofabrik")
        >>> isinstance(extracts, list)
        True
        >>> len(extracts) >= 1
        True
        >>> # Get all extracts across all sources
        >>> all_extracts = osmfinder.get_available_extracts()
        >>> isinstance(all_extracts, list)
        True
        >>> len(all_extracts) >= 1
        True
    """
    try:
        index = _get_index_for_sources(source)
    except ValueError as ex:
        raise ValueError(f"Unknown OSM extracts source: {source}.") from ex

    if excluded_extracts_ids:
        index = index.filter_by_mask(~np.isin(index.ids, list(excluded_extracts_ids)))

    return list(index)


def find_extracts_by_geometry(
    geometry: BaseGeometry,
    source: OsmExtractSourceLike = "any",
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
    excluded_extracts_ids: set[str] | None = None,
    force_single_result: bool = False,
    single_result_iou_threshold: float = 0.99,
) -> OsmfinderGeometryResult:
    """
    Find smallest extracts from a given OSM source that contains given polygon.

    Iterates an OSM source index and finds smallest extracts that covers a given geometry.

    Extracts are selected based on the highest value of the Intersection over Union metric with
    geometry. Some extracts might be discarded because of low IoU metric value leaving some parts
    of the geometry uncovered.

    Args:
        geometry (BaseGeometry): Geometry to be covered.
        source (OsmExtractSourceLike): OSM source name. Can be one of: 'any', 'Geofabrik',
            'BBBike', 'OSMfr', or an iterable / comma-separated string of those
            (e.g. ['BBBike', 'OSM_fr'] or 'bbbike,osmfr'). Defaults to 'any'.
        geometry_coverage_iou_threshold (float): Minimal value of the Intersection over Union metric
            for selecting the matching OSM extracts. Is best matching extract has value lower than
            the threshold, it is discarded (except the first one). Has to be in range between
            0 and 1. Value of 0 will allow every intersected extract, value of 1 will only allow
            extracts that match the geometry exactly. Defaults to 0.01.
        allow_uncovered_geometry (bool): Suppress an error if some geometry parts aren't covered
            by any OSM extract. Defaults to `False`.
        excluded_extracts_ids (Optional[set[str]]): Set of extract ids to exclude from the search.
            Useful for skipping extracts that are unavailable for download. Defaults to `None`.
        force_single_result (bool): When ``True``, return only the smallest extract that best covers
            the geometry. If ``allow_uncovered_geometry`` is ``False``, the smallest fully
            containing extract is returned. If ``allow_uncovered_geometry`` is ``True``, the
            extract with the highest IoU above ``single_result_iou_threshold`` is returned.
            If no candidate meets the threshold, the smallest fully containing extract is used as
            a fallback.
            Defaults to ``False``.
        single_result_iou_threshold (float): Minimal IoU value for selecting a single result when
            ``force_single_result`` is ``True`` and ``allow_uncovered_geometry`` is ``True``.
            Defaults to 0.99.

    Returns:
        List[OpenStreetMapExtract]: List of extracts name, URL to download it and boundary polygon.

    Examples:
        >>> import osmfinder
        >>> from shapely.geometry import box
        >>> geom = box(7.40, 43.71, 7.44, 43.75)
        >>> results = osmfinder.find_extracts_by_geometry(geom, source="Geofabrik")
        >>> len(results.extracts) >= 1
        True
        >>> results.extracts[0].id
        'Geofabrik_monaco'
    """
    try:
        index = _get_index_for_sources(source)
    except ValueError as ex:
        raise ValueError(f"Unknown OSM extracts source: {source}.") from ex

    extracts, steps = _find_smallest_containing_extracts(
        geometry=geometry,
        polygons_index=index,
        geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
        allow_uncovered_geometry=allow_uncovered_geometry,
        excluded_extracts_ids=excluded_extracts_ids,
        force_single_result=force_single_result,
        single_result_iou_threshold=single_result_iou_threshold,
    )

    final_ids = {e.id for e in extracts}
    for step in steps:
        if step.selected and step.extract.id not in final_ids:
            step.selected = False
            step.reason = "redundant"

    covered_geometry = (
        unary_union([e.geometry for e in extracts]).intersection(geometry)
        if extracts
        else Polygon()
    )
    uncovered_geometry = geometry.difference(covered_geometry)

    sources_used = _resolve_extract_sources(source)

    return OsmfinderGeometryResult(
        extracts=extracts,
        sources_used=sources_used,
        input_geometry=geometry,
        covered_geometry=covered_geometry,
        uncovered_geometry=uncovered_geometry,
        steps=steps,
        iou_threshold=geometry_coverage_iou_threshold,
    )


@overload
def find_extracts_covering_point(
    point: tuple[float, float],
    source: OsmExtractSourceLike = "any",
    *,
    excluded_extracts_ids: set[str] | None = None,
) -> list[OpenStreetMapExtract]: ...


@overload
def find_extracts_covering_point(
    point: Point,
    source: OsmExtractSourceLike = "any",
    *,
    excluded_extracts_ids: set[str] | None = None,
) -> list[OpenStreetMapExtract]: ...


def find_extracts_covering_point(
    point: tuple[float, float] | Point,
    source: OsmExtractSourceLike = "any",
    *,
    excluded_extracts_ids: set[str] | None = None,
) -> list[OpenStreetMapExtract]:
    """
    Find all extracts that contain a specific point.

    Args:
        point (tuple[float, float] | Point): A ``(lon, lat)`` coordinate tuple
            or a shapely ``Point`` geometry. The tuple follows the ``(x, y)`` convention
            used by GeoJSON and shapely, i.e. longitude first, latitude second.
        source (OsmExtractSourceLike): OSM source name. Can be one of: 'any', 'Geofabrik',
            'BBBike', 'OSMfr', or an iterable / comma-separated string of those
            (e.g. ['BBBike', 'OSM_fr'] or 'bbbike,osmfr'). Defaults to 'any'.
        excluded_extracts_ids (Optional[set[str]]): Set of extract ids to exclude from the search.
            Useful for skipping extracts that are unavailable for download. Defaults to `None`.

    Returns:
        list[OpenStreetMapExtract]: List of extracts covering the point, sorted by area
            from smallest to biggest. Returns an empty list if no extract covers the point.

    Examples:
        >>> import osmfinder
        >>> # Query by (lon, lat) tuple
        >>> extracts = osmfinder.find_extracts_covering_point(
        ...     (7.42, 43.73), source="Geofabrik"
        ... )
        >>> len(extracts) >= 1
        True
        >>> extracts[0].id
        'Geofabrik_monaco'
        >>> # Query by shapely Point
        >>> from shapely.geometry import Point
        >>> extracts = osmfinder.find_extracts_covering_point(
        ...     Point(7.42, 43.73), source="Geofabrik"
        ... )
        >>> len(extracts) >= 1
        True
    """
    if isinstance(point, tuple):
        lon, lat = point
        point_geom = Point(lon, lat)
    else:
        point_geom = point

    try:
        index = _get_index_for_sources(source)
    except ValueError as ex:
        raise ValueError(f"Unknown OSM extracts source: {source}.") from ex

    if excluded_extracts_ids:
        index = index.filter_by_mask(~np.isin(index.ids, list(excluded_extracts_ids)))

    candidate_indices = index.tree.query(point_geom)

    matching: list[tuple[float, int]] = []
    for idx in candidate_indices:
        if intersects(index.geometries[idx], point_geom):
            matching.append((index.areas[idx], idx))

    matching.sort(key=lambda item: (item[0], str(index.ids[item[1]])))
    return [index.get_extract_by_index(idx) for _, idx in matching]


def _find_smallest_containing_extracts(
    geometry: BaseGeometry,
    polygons_index: OsmExtractsIndex,
    num_of_multiprocessing_workers: int = -1,
    multiprocessing_activation_threshold: int | None = None,
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
    excluded_extracts_ids: set[str] | None = None,
    force_single_result: bool = False,
    single_result_iou_threshold: float = 0.99,
) -> tuple[list[OpenStreetMapExtract], list[GeometryCoveringStep]]:
    """
    Find smallest set of extracts covering a given geometry.

    Iterates a provided extracts index and searches for a smallest set that cover a given geometry.
    It's not guaranteed that this set will be the smallest and there will be no overlaps.

    Extracts are selected based on the highest value of the Intersection over Union metric with
    geometry. Some extracts might be discarded because of low IoU metric value leaving some parts
    of the geometry uncovered.

    Args:
        geometry (BaseGeometry): Geometry to be covered.
        polygons_index (OsmExtractsIndex): Index of available extracts.
        num_of_multiprocessing_workers (int, optional): Number of workers used for multiprocessing.
            Defaults to -1 which results in a total number of available cpu threads.
            `0` and `1` values disable multiprocessing.
            Similar to `n_jobs` parameter from `scikit-learn` library.
        multiprocessing_activation_threshold (int, optional): Number of gometries required to start
            processing on multiple processes. Activating multiprocessing for a small
            amount of points might not be feasible. Defaults to 100.
        geometry_coverage_iou_threshold (float): Minimal value of the Intersection over Union metric
            for selecting the matching OSM extracts. Is best matching extract has value lower than
            the threshold, it is discarded (except the first one). Has to be in range between
            0 and 1. Value of 0 will allow every intersected extract, value of 1 will only allow
            extracts that match the geometry exactly. Defaults to 0.01.
        allow_uncovered_geometry (bool): Suppress an error if some geometry parts aren't covered
            by any OSM extract. Defaults to `False`.
        excluded_extracts_ids (Optional[set[str]]): Set of extract ids to exclude from the search.
            Useful for skipping extracts that are unavailable for download. Defaults to `None`.
        force_single_result (bool): When ``True``, return only the smallest extract that best covers
            the geometry. If ``allow_uncovered_geometry`` is ``False``, the smallest fully
            containing extract is returned. If ``allow_uncovered_geometry`` is ``True``, the
            extract with the highest IoU above ``single_result_iou_threshold`` is returned.
            If no candidate meets the threshold, the smallest fully containing extract is used as
            a fallback.
            Defaults to ``False``.
        single_result_iou_threshold (float): Minimal IoU value for selecting a single result when
            ``force_single_result`` is ``True`` and ``allow_uncovered_geometry`` is ``True``.
            Defaults to 0.99.

    Returns:
        List[OpenStreetMapExtract]: List of extracts covering a given geometry.
    """
    if excluded_extracts_ids:
        polygons_index = polygons_index.filter_by_mask(
            ~np.isin(polygons_index.ids, list(excluded_extracts_ids))
        )

    if force_single_result and (single_result_iou_threshold < 0 or single_result_iou_threshold > 1):
        raise ValueError("single_result_iou_threshold is outside required bounds [0, 1]")

    if force_single_result:
        selected_id, step = _find_single_extract_for_geometry(
            geometry=geometry,
            polygons_index=polygons_index,
            iou_threshold=single_result_iou_threshold,
            allow_uncovered_geometry=allow_uncovered_geometry,
        )
        extract = _get_extract_by_id(polygons_index, selected_id)
        return [extract], [step]

    if num_of_multiprocessing_workers == 0:
        num_of_multiprocessing_workers = 1
    elif num_of_multiprocessing_workers < 0:
        num_of_multiprocessing_workers = cpu_count()

    if not multiprocessing_activation_threshold:
        multiprocessing_activation_threshold = 100

    unique_extracts_ids: set[str] = set()

    geometries = _flatten_geometry(geometry)

    total_polygons = len(geometries)

    if (
        num_of_multiprocessing_workers > 1
        and total_polygons >= multiprocessing_activation_threshold
    ):
        find_extracts_func = partial(
            _find_smallest_containing_extracts_for_single_geometry,
            polygons_index=polygons_index,
            geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
            allow_uncovered_geometry=allow_uncovered_geometry,
        )

        all_steps: list[GeometryCoveringStep] = []
        for extract_ids_set, steps in process_map(
            find_extracts_func,
            geometries,
            desc="Finding matching extracts",
            max_workers=num_of_multiprocessing_workers,
            chunksize=ceil(total_polygons / (4 * num_of_multiprocessing_workers)),
            disable=FORCE_TERMINAL,
        ):
            unique_extracts_ids.update(extract_ids_set)
            all_steps.extend(steps)
    else:
        all_steps = []
        for sub_geometry in geometries:
            extract_ids_set, steps = _find_smallest_containing_extracts_for_single_geometry(
                geometry=sub_geometry,
                polygons_index=polygons_index,
                geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
                allow_uncovered_geometry=allow_uncovered_geometry,
            )
            unique_extracts_ids.update(extract_ids_set)
            all_steps.extend(steps)

    extracts_filtered = _filter_extracts(
        geometry,
        unique_extracts_ids,
        polygons_index,
        num_of_multiprocessing_workers,
        multiprocessing_activation_threshold,
    )

    return extracts_filtered, all_steps


def _find_smallest_containing_extracts_for_single_geometry(
    geometry: BaseGeometry,
    polygons_index: OsmExtractsIndex,
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
) -> tuple[set[str], list[GeometryCoveringStep]]:
    """
    Find smallest set of extracts covering a given singular geometry.

    Extracts are selected based on the highest value of the Intersection over Union metric with
    geometry. Some extracts might be discarded because of low IoU metric value leaving some parts
    of the geometry uncovered.

    Args:
        geometry (BaseGeometry): Geometry to be covered.
        polygons_index (OsmExtractsIndex): Index of available extracts.
        geometry_coverage_iou_threshold (float): Minimal value of the Intersection over Union metric
            for selecting the matching OSM extracts. Is best matching extract has value lower than
            the threshold, it is discarded (except the first one). Has to be in range between
            0 and 1. Value of 0 will allow every intersected extract, value of 1 will only allow
            extracts that match the geometry exactly. Defaults to 0.01.
        allow_uncovered_geometry (bool): Suppress an error if some geometry parts aren't covered
            by any OSM extract. Defaults to `False`.

    Raises:
        RuntimeError: If provided extracts index is empty.
        RuntimeError: If there is no extracts covering a given geometry (singularly or in group).
        ValueError: If geometry_coverage_iou_threshold is outside bounds [0, 1].

    Returns:
        Set[str]: Selected extract index string values.
    """
    if polygons_index is None:
        raise RuntimeError("Extracts index is empty.")

    if geometry_coverage_iou_threshold < 0 or geometry_coverage_iou_threshold > 1:
        raise ValueError("geometry_coverage_iou_threshold is outside required bounds [0, 1]")

    checked_extracts_ids, iou_metric_values = _cover_geometry_with_extracts(
        geometry=geometry,
        polygons_index=polygons_index,
        allow_uncovered_geometry=allow_uncovered_geometry,
    )

    selected_extracts_ids, steps = _select_covering_extracts(
        checked_extracts_ids=checked_extracts_ids,
        iou_metric_values=iou_metric_values,
        polygons_index=polygons_index,
        input_geometry=geometry,
        geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
    )
    return selected_extracts_ids, steps


def _select_covering_extracts(
    checked_extracts_ids: list[str],
    iou_metric_values: list[float],
    polygons_index: OsmExtractsIndex,
    input_geometry: BaseGeometry,
    geometry_coverage_iou_threshold: float = 0.01,
) -> tuple[set[str], list[GeometryCoveringStep]]:
    """Select extracts based on IoU threshold and return selection steps."""
    selected_extracts_ids: set[str] = set()
    steps: list[GeometryCoveringStep] = []
    geometry_to_cover = input_geometry

    for extract_id, iou_metric_value in zip(checked_extracts_ids, iou_metric_values, strict=True):
        extract = _get_extract_by_id(polygons_index, extract_id)
        intersection_geometry = extract.geometry.intersection(geometry_to_cover)

        if iou_metric_value >= geometry_coverage_iou_threshold or not selected_extracts_ids:
            reason = "first_extract" if not selected_extracts_ids else "selected"
            selected_extracts_ids.add(extract_id)
            steps.append(
                GeometryCoveringStep(
                    extract=extract,
                    iou=iou_metric_value,
                    selected=True,
                    reason=reason,
                    geometry_to_cover=geometry_to_cover,
                    intersection_geometry=intersection_geometry,
                )
            )
            geometry_to_cover = geometry_to_cover.difference(extract.geometry)
        else:
            warnings.warn(
                (
                    "Skipping extract because of low IoU value "
                    f"({extract.file_name}, {iou_metric_value:.3g})."
                ),
                GeometryNotCoveredWarning,
                stacklevel=0,
            )
            steps.append(
                GeometryCoveringStep(
                    extract=extract,
                    iou=iou_metric_value,
                    selected=False,
                    reason="low_iou",
                    geometry_to_cover=geometry_to_cover,
                    intersection_geometry=intersection_geometry,
                )
            )

    return selected_extracts_ids, steps


def _find_single_extract_for_geometry(
    geometry: BaseGeometry,
    polygons_index: OsmExtractsIndex,
    iou_threshold: float = 0.99,
    allow_uncovered_geometry: bool = False,
) -> tuple[str, GeometryCoveringStep]:
    """Find a single extract that best covers the given geometry."""
    candidate_indices = polygons_index.tree.query(geometry)
    candidate_indices = [
        idx
        for idx in candidate_indices
        if intersects(cast("BaseGeometry", polygons_index.geometries[idx]), geometry)
    ]

    if not candidate_indices:
        raise GeometryNotCoveredError("No OSM extracts intersect the query geometry.")

    geometry_area = _calculate_geodetic_area(geometry)

    complete_cover_candidates: list[tuple[int, float, float]] = []
    partial_candidates: list[tuple[int, float, float]] = []

    for idx in candidate_indices:
        extract_geometry = cast("BaseGeometry", polygons_index.geometries[idx])
        extract_area = float(polygons_index.areas[idx])

        if extract_geometry.covers(geometry):
            iou = geometry_area / extract_area if extract_area > 0 else 1.0
            complete_cover_candidates.append((int(idx), extract_area, iou))
        else:
            intersection_geometry = extract_geometry.intersection(geometry)
            intersection_area = _calculate_geodetic_area(intersection_geometry)
            iou = intersection_area / (extract_area + geometry_area - intersection_area)
            partial_candidates.append((int(idx), extract_area, iou))

    selected_idx: int | None = None
    selected_area = 0.0
    selected_iou = 0.0
    selected_reason = ""

    if allow_uncovered_geometry:
        above_threshold = [
            (idx, area, iou) for idx, area, iou in partial_candidates if iou >= iou_threshold
        ]
        if above_threshold:
            selected_idx, selected_area, selected_iou = min(
                above_threshold, key=lambda x: (-x[2], x[1])
            )
            selected_reason = "single_result"
        elif complete_cover_candidates:
            selected_idx, selected_area, selected_iou = min(
                complete_cover_candidates, key=lambda x: x[1]
            )
            selected_reason = "complete_cover"
        else:
            raise GeometryNotCoveredError(
                f"No extract meets the IoU threshold of {iou_threshold}"
                " of fully contains the query geometry."
            )
    else:
        if complete_cover_candidates:
            selected_idx, selected_area, selected_iou = min(
                complete_cover_candidates, key=lambda x: x[1]
            )
            selected_reason = "complete_cover"
        else:
            raise GeometryNotCoveredError("No extract fully contains the query geometry.")

    selected_extract = polygons_index.get_extract_by_index(selected_idx)
    selected_intersection = selected_extract.geometry.intersection(geometry)

    if selected_iou < 0.05:
        area_ratio = selected_area / geometry_area if geometry_area > 0 else float("inf")
        if area_ratio > 2:
            warnings.warn(
                f"Selected extract '{selected_extract.file_name}' is {area_ratio:.1f}x "
                f"larger than the query geometry (IoU={selected_iou:.4f}).",
                GeometryNotCoveredWarning,
                stacklevel=0,
            )

    step = GeometryCoveringStep(
        extract=selected_extract,
        iou=selected_iou,
        selected=True,
        reason=selected_reason,
        geometry_to_cover=geometry,
        intersection_geometry=selected_intersection,
    )
    return str(polygons_index.ids[selected_idx]), step


def _get_extract_by_id(index: OsmExtractsIndex, extract_id: str) -> OpenStreetMapExtract:
    """Return the extract with the given id from the index."""
    matching_indices = np.flatnonzero(index.ids == extract_id)
    return index.get_extract_by_index(int(matching_indices[0]))


def _cover_geometry_with_extracts(
    geometry: BaseGeometry,
    polygons_index: OsmExtractsIndex,
    allow_uncovered_geometry: bool = False,
) -> tuple[list[str], list[float]]:
    """
    Intersect a geometry with extracts and return the IoU coverage.

    Args:
        geometry (BaseGeometry): Geometry to be covered.
        polygons_index (OsmExtractsIndex): Index of available extracts.
        allow_uncovered_geometry (bool): Suppress an error if some geometry parts aren't covered
            by any OSM extract. Defaults to `False`.

    Raises:
        RuntimeError: If provided extracts index is empty.
        RuntimeError: If there is no extracts covering a given geometry (singularly or in group).

    Returns:
        tuple[list[str], list[float]]: List of extracts index string values with a list
            of IoU metric values.
    """
    if polygons_index is None:
        raise RuntimeError("Extracts index is empty.")

    checked_extracts_ids: list[str] = []
    iou_metric_values: list[float] = []

    if geometry.geom_type == "Polygon":
        geometry_to_cover = cast("BaseGeometry", geometry.buffer(0))
    else:
        geometry_to_cover = cast("BaseGeometry", geometry.buffer(1e-6))

    exactly_matching_mask = np.array(
        [
            equals_exact(extract_geometry, geometry, tolerance=1e-6)
            for extract_geometry in polygons_index.geometries
        ]
    )
    if np.count_nonzero(exactly_matching_mask) == 1:
        matching_idx = int(np.flatnonzero(exactly_matching_mask)[0])
        checked_extracts_ids.append(str(polygons_index.ids[matching_idx]))
        iou_metric_values.append(1.0)
        return checked_extracts_ids, iou_metric_values

    while not is_empty(geometry_to_cover):
        # Find candidate extracts that intersect the remaining geometry.
        candidate_indices = polygons_index.tree.query(geometry_to_cover)
        candidate_indices = [
            idx
            for idx in candidate_indices
            if str(polygons_index.ids[idx]) not in checked_extracts_ids
            and intersects(cast("BaseGeometry", polygons_index.geometries[idx]), geometry_to_cover)
        ]

        # Sort candidates deterministically by area then id so that
        # np.lexsort tiebreaking is stable across platforms/processes.
        candidate_sort_areas = np.array(
            [float(polygons_index.areas[idx]) for idx in candidate_indices]
        )
        candidate_sort_ids = np.array([str(polygons_index.ids[idx]) for idx in candidate_indices])
        candidate_order = np.lexsort((candidate_sort_ids, candidate_sort_areas))
        candidate_indices = [candidate_indices[i] for i in candidate_order]

        if not candidate_indices:
            if not allow_uncovered_geometry:
                raise GeometryNotCoveredError(
                    "Couldn't find extracts covering given geometry."
                    " If it's expected behaviour, you can suppress this error by passing"
                    " the `allow_uncovered_geometry=True` argument"
                    " or add `--allow-uncovered-geometry` flag to the CLI command."
                )
            warnings.warn(
                "Couldn't find extracts covering given geometry.",
                GeometryNotCoveredWarning,
                stacklevel=0,
            )
            break

        # Compute IoU for each candidate.
        candidate_geometries = [
            cast("BaseGeometry", polygons_index.geometries[idx]) for idx in candidate_indices
        ]
        candidate_areas = np.array(
            [_calculate_geodetic_area(geometry) for geometry in candidate_geometries]
        )
        geometry_to_cover_area = _calculate_geodetic_area(geometry_to_cover)
        intersection_areas = np.array(
            [
                _calculate_geodetic_area(extract_geometry.intersection(geometry_to_cover))
                for extract_geometry in candidate_geometries
            ]
        )
        iou_values = intersection_areas / (
            candidate_areas + geometry_to_cover_area - intersection_areas
        )

        # Select the best matching extract (highest IoU, then smallest area).
        best_candidate_pos = int(np.lexsort((candidate_areas, -iou_values))[0])
        best_idx = candidate_indices[best_candidate_pos]
        best_iou = iou_values[best_candidate_pos]

        geometry_to_cover = geometry_to_cover.difference(candidate_geometries[best_candidate_pos])
        checked_extracts_ids.append(str(polygons_index.ids[best_idx]))
        iou_metric_values.append(float(best_iou))

    return checked_extracts_ids, iou_metric_values


def _filter_extracts(
    geometry: BaseGeometry,
    extracts_ids: Iterable[str],
    polygons_index: OsmExtractsIndex,
    num_of_multiprocessing_workers: int,
    multiprocessing_activation_threshold: int,
) -> list[OpenStreetMapExtract]:
    """
    Filter a set of extracts to include least overlaps in it.

    Args:
        geometry (BaseGeometry): Geometry to be covered.
        extracts_ids (Iterable[str]): Group of selected extracts indexes.
        polygons_index (OsmExtractsIndex): Index of available extracts.
        num_of_multiprocessing_workers (int): Number of workers used for multiprocessing.
            Similar to `n_jobs` parameter from `scikit-learn` library.
        multiprocessing_activation_threshold (int): Number of gometries required to start
            processing on multiple processes.

    Raises:
        RuntimeError: If provided extracts index is empty.

    Returns:
        List[OpenStreetMapExtract]: Filtered list of extracts.
    """
    if polygons_index is None:
        raise RuntimeError("Extracts index is empty.")

    matching_mask = np.isin(polygons_index.ids, list(extracts_ids))
    sorted_extracts = polygons_index.filter_by_mask(matching_mask)
    # Sort by area descending, then id ascending.
    sort_indices = np.lexsort((sorted_extracts.ids, -sorted_extracts.areas))
    sorted_extracts = OsmExtractsIndex(
        ids=sorted_extracts.ids[sort_indices],
        geometries=sorted_extracts.geometries[sort_indices],
        areas=sorted_extracts.areas[sort_indices],
        file_names=sorted_extracts.file_names[sort_indices],
        names=sorted_extracts.names[sort_indices],
        parents=sorted_extracts.parents[sort_indices],
        urls=sorted_extracts.urls[sort_indices],
    )

    filtered_extracts: list[OpenStreetMapExtract] = []
    filtered_extracts_ids: set[str] = set()

    geometries = _flatten_geometry(geometry)

    total_geometries = len(geometries)

    if (
        num_of_multiprocessing_workers > 1
        and total_geometries >= multiprocessing_activation_threshold
    ):
        filter_extracts_func = partial(
            _filter_extracts_for_single_geometry,
            sorted_extracts=sorted_extracts,
        )

        for extract_ids_list in process_map(
            filter_extracts_func,
            geometries,
            desc="Filtering extracts",
            max_workers=num_of_multiprocessing_workers,
            chunksize=ceil(total_geometries / (4 * num_of_multiprocessing_workers)),
            disable=FORCE_TERMINAL,
        ):
            filtered_extracts_ids.update(extract_ids_list)
    else:
        for sub_geometry in geometries:
            filtered_extracts_ids.update(
                _filter_extracts_for_single_geometry(sub_geometry, sorted_extracts)
            )

    simplified_extracts_ids = _simplify_selected_extracts(filtered_extracts_ids, sorted_extracts)

    for idx in range(len(sorted_extracts.ids)):
        if sorted_extracts.ids[idx] in simplified_extracts_ids:
            filtered_extracts.append(sorted_extracts.get_extract_by_index(int(idx)))

    return filtered_extracts


def _filter_extracts_for_single_geometry(
    geometry: BaseGeometry, sorted_extracts: OsmExtractsIndex
) -> set[str]:
    """
    Filter a set of extracts to include least overlaps in it for a single geometry.

    Works by selecting biggest extracts (by area) and not including smaller ones if they don't
    increase a coverage.

    Args:
        geometry (BaseGeometry): Geometry to be covered.
        sorted_extracts (OsmExtractsIndex): Sorted index of available extracts.

    Returns:
        Set[str]: Selected extract index string values.
    """
    filtered_extracts_ids: set[str] = set()

    if geometry.geom_type == "Polygon":
        geometry_to_cover = geometry.buffer(0)
    else:
        geometry_to_cover = geometry.buffer(1e-6)

    for idx in range(len(sorted_extracts.ids)):
        if is_empty(geometry_to_cover):
            break

        extract_geometry = sorted_extracts.geometries[idx]
        if extract_geometry.disjoint(geometry_to_cover):
            continue

        geometry_to_cover = geometry_to_cover.difference(extract_geometry)
        filtered_extracts_ids.add(str(sorted_extracts.ids[idx]))

    return filtered_extracts_ids


def _simplify_selected_extracts(
    filtered_extracts_ids: set[str], sorted_extracts: OsmExtractsIndex
) -> set[str]:
    simplified_extracts_ids = filtered_extracts_ids.copy()

    simplify_again = True
    while simplify_again:
        simplify_again = False
        extract_to_remove = None
        for extract_id in sorted(simplified_extracts_ids):
            extract_idx = int(np.flatnonzero(sorted_extracts.ids == extract_id)[0])
            extract_geometry = sorted_extracts.geometries[extract_idx]

            other_ids = [eid for eid in simplified_extracts_ids if eid != extract_id]
            if not other_ids:
                continue

            other_geometries = unary_union(
                [
                    sorted_extracts.geometries[int(np.flatnonzero(sorted_extracts.ids == oid)[0])]
                    for oid in other_ids
                ]
            )
            if extract_geometry.covered_by(other_geometries):
                # Prefer removing the larger extract when a smaller one is completely
                # covered by the union of the remaining extracts.
                extract_to_remove = extract_id
                for other_id in other_ids:
                    other_idx = int(np.flatnonzero(sorted_extracts.ids == other_id)[0])
                    if sorted_extracts.geometries[other_idx].covers(extract_geometry):
                        if sorted_extracts.areas[other_idx] > sorted_extracts.areas[extract_idx]:
                            extract_to_remove = other_id
                simplify_again = True
                break

        if extract_to_remove is not None:
            simplified_extracts_ids.remove(extract_to_remove)

    return simplified_extracts_ids


def _flatten_geometry(geometry: BaseGeometry) -> list[BaseGeometry]:
    """Flatten all geometries into a list of BaseGeometries."""
    from shapely.geometry.base import BaseMultipartGeometry

    if isinstance(geometry, BaseMultipartGeometry):
        geometries = []
        for sub_geom in geometry.geoms:
            geometries.extend(_flatten_geometry(sub_geom))
        return geometries
    return [geometry]


@overload
def find(
    query: str,
    source: OsmExtractSourceLike = "any",
    *,
    select_first_match: bool = True,
    excluded_extracts_ids: set[str] | None = None,
) -> OsmfinderQueryResult: ...


@overload
def find(
    query: BaseGeometry,
    source: OsmExtractSourceLike = "any",
    *,
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
    excluded_extracts_ids: set[str] | None = None,
    force_single_result: bool = False,
    single_result_iou_threshold: float = 0.99,
) -> OsmfinderGeometryResult: ...


def find(
    query: str | BaseGeometry,
    source: OsmExtractSourceLike = "any",
    *,
    select_first_match: bool = True,
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
    excluded_extracts_ids: set[str] | None = None,
    force_single_result: bool = False,
    single_result_iou_threshold: float = 0.99,
) -> OsmfinderQueryResult | OsmfinderGeometryResult:
    """
    Find an OSM extract by name or geometry.

    Dispatches to :func:`find_extract_by_query` when called with a string query,
    or to :func:`find_extracts_by_geometry` when called with a geometry.

    Args:
        query (Union[str, BaseGeometry]): Text query
            or shapely geometry to search for.
        source (OsmExtractSourceLike): OSM source name. Defaults to 'any'.
        select_first_match (bool): When multiple extracts match the query by name, select the
            first one (sorted by area ascending, then id) and emit a warning instead of raising
            an error. Only used for string queries. Defaults to `True`.
        geometry_coverage_iou_threshold (float): Minimal value of the Intersection over Union
            metric for selecting the matching OSM extracts. Only used for geometry queries.
            Defaults to 0.01.
        allow_uncovered_geometry (bool): Suppress an error if some geometry parts aren't covered
            by any OSM extract. Only used for geometry queries. Defaults to `False`.
        excluded_extracts_ids (Optional[set[str]]): Set of extract ids to exclude from the search.
            Useful for skipping extracts that are unavailable for download. Defaults to `None`.
        force_single_result (bool): When ``True``, return only the smallest extract that best covers
            the geometry. If ``allow_uncovered_geometry`` is ``False``, the smallest fully
            containing extract is returned. If ``allow_uncovered_geometry`` is ``True``, the
            extract with the highest IoU above ``single_result_iou_threshold`` is returned.
            If no candidate meets the threshold, the smallest fully containing extract is used as
            a fallback.
            Only used for geometry queries. Defaults to ``False``.
        single_result_iou_threshold (float): Minimal IoU value for selecting a single result when
            ``force_single_result`` is ``True`` and ``allow_uncovered_geometry`` is ``True``.
            Only used for geometry queries. Defaults to 0.99.

    Returns:
        Union[OsmfinderQueryResult, OsmfinderGeometryResult]: Query result for string queries,
            geometry result for geometry queries. Both contain an ``extracts`` list.

    Examples:
        >>> import osmfinder
        >>> from shapely.geometry import box
        >>> # Find by name
        >>> result = osmfinder.find("Monaco")
        >>> isinstance(result, osmfinder.OsmfinderQueryResult)
        True
        >>> len(result.extracts) == 1
        True
        >>> result.extracts[0].id
        'Movisda-admin_MC'
        >>> # Find by geometry
        >>> geom = box(7.40, 43.71, 7.44, 43.75)
        >>> result = osmfinder.find(geom, source="Geofabrik")
        >>> isinstance(result, osmfinder.OsmfinderGeometryResult)
        True
        >>> len(result.extracts) >= 1
        True
        >>> result.extracts[0].id
        'Geofabrik_monaco'
    """
    if isinstance(query, str):
        return find_extract_by_query(
            query,
            source=source,
            select_first_match=select_first_match,
            excluded_extracts_ids=excluded_extracts_ids,
        )
    return find_extracts_by_geometry(
        query,
        source=source,
        geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
        allow_uncovered_geometry=allow_uncovered_geometry,
        excluded_extracts_ids=excluded_extracts_ids,
        force_single_result=force_single_result,
        single_result_iou_threshold=single_result_iou_threshold,
    )


@overload
def download(
    query: OsmfinderResult,
    source: OsmExtractSourceLike = "any",
    *,
    download_directory: str | Path = "files",
    progressbar: bool = True,
    force_refresh: bool = False,
    retry_on_unavailable: bool = True,
) -> OsmfinderDownloadResult: ...


@overload
def download(
    query: OpenStreetMapExtract,
    source: OsmExtractSourceLike = "any",
    *,
    download_directory: str | Path = "files",
    progressbar: bool = True,
    force_refresh: bool = False,
    retry_on_unavailable: bool = True,
) -> OsmfinderDownloadResult: ...


@overload
def download(
    query: list[OpenStreetMapExtract],
    source: OsmExtractSourceLike = "any",
    *,
    download_directory: str | Path = "files",
    progressbar: bool = True,
    force_refresh: bool = False,
    retry_on_unavailable: bool = True,
) -> OsmfinderDownloadResult: ...


@overload
def download(
    query: str,
    source: OsmExtractSourceLike = "any",
    *,
    download_directory: str | Path = "files",
    select_first_match: bool = True,
    progressbar: bool = True,
    force_refresh: bool = False,
) -> OsmfinderDownloadResult: ...


@overload
def download(
    query: BaseGeometry,
    source: OsmExtractSourceLike = "any",
    *,
    download_directory: str | Path = "files",
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
    force_single_result: bool = False,
    single_result_iou_threshold: float = 0.99,
    progressbar: bool = True,
    force_refresh: bool = False,
) -> OsmfinderDownloadResult: ...


def download(
    query: str | BaseGeometry | OsmfinderResult | OpenStreetMapExtract | list[OpenStreetMapExtract],
    source: OsmExtractSourceLike = "any",
    *,
    download_directory: str | Path = "files",
    select_first_match: bool = True,
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
    force_single_result: bool = False,
    single_result_iou_threshold: float = 0.99,
    progressbar: bool = True,
    force_refresh: bool = False,
    retry_on_unavailable: bool = True,
) -> OsmfinderDownloadResult:
    """
    Download OSM extracts.

    Accepts a string query, a geometry, a find result, a single extract, or a list of extracts.

    Args:
        query: What to download. Accepts a string query, geometry, find result,
            single extract, or list of extracts. A string query or geometry triggers
            a find first; a result or extract list downloads directly.
        source (OsmExtractSourceLike): OSM source name. Defaults to 'any'.
        download_directory (Union[str, Path]): Directory where files should be downloaded.
            Defaults to "files".
        select_first_match (bool): When multiple extracts match the query by name, select the
            first one with a warning. Only used for string queries. Defaults to `True`.
        geometry_coverage_iou_threshold (float): Minimal IoU for selecting extracts.
            Only used for geometry queries. Defaults to 0.01.
        allow_uncovered_geometry (bool): Suppress error if geometry parts aren't covered.
            Only used for geometry queries. Defaults to `False`.
        force_single_result (bool): Return only the single best extract. Only used for geometry
            queries. Defaults to ``False``.
        single_result_iou_threshold (float): Minimal IoU for selecting a single result when
            ``force_single_result`` is ``True`` and ``allow_uncovered_geometry`` is ``True``.
            Only used for geometry queries. Defaults to 0.99.
        progressbar (bool): Show progress bar. Defaults to True.
        force_refresh (bool): When ``True``, re-download even if the file already exists.
            Defaults to False.
        retry_on_unavailable (bool): When ``True`` and the query is an ``OsmfinderResult``,
            unavailable extracts are excluded and the search is retried. When ``False``,
            the result's extracts are downloaded as-is and unavailable extracts raise
            an exception. Defaults to ``True``.

    Returns:
        OsmfinderDownloadResult: Result containing downloaded paths and find result.

    Examples:
        >>> import osmfinder
        >>> from pathlib import Path
        >>> # Download by name
        >>> result = osmfinder.download("Monaco", download_directory="/tmp/osmfinder-doctest")
        >>> isinstance(result, osmfinder.OsmfinderDownloadResult)
        True
        >>> len(result.download_paths) == 1
        True
        >>> isinstance(result.download_paths[0], Path)
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

    if isinstance(query, str):
        return _download_with_retry_query(
            query,
            source,
            download_directory,
            select_first_match=select_first_match,
            progressbar=progressbar,
            force_refresh=force_refresh,
        )
    elif isinstance(query, BaseGeometry):
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
    elif isinstance(query, OsmfinderResult):
        if retry_on_unavailable:
            if isinstance(query, OsmfinderQueryResult):
                return download(
                    query.query,
                    source=source or query.sources_used,
                    download_directory=download_directory,
                    select_first_match=True,
                    progressbar=progressbar,
                    force_refresh=force_refresh,
                )
            elif isinstance(query, OsmfinderGeometryResult):
                return download(
                    query.input_geometry,
                    source=source or query.sources_used,
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
    elif isinstance(query, list) and query and isinstance(query[0], OpenStreetMapExtract):
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


_get_extract_by_query = find_extract_by_query
_legacy_find_smallest_containing_extracts = find_extracts_by_geometry
_download_extract_by_query = download_extract_by_query
_download_extracts = download_extracts
_download_extracts_pbf_files_public = download_extracts_pbf_files
_find_and_download_extracts_pbf_files = find_and_download_extracts_pbf_files
