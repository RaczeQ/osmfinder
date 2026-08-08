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
from typing import Optional, Union, cast, overload

import numpy as np
from pooch import HTTPDownloader, retrieve
from pooch import get_logger as get_pooch_logger
from requests.exceptions import RequestException
from rich import get_console
from rich import print as rprint
from shapely import equals_exact, intersects, is_empty, unary_union
from shapely.geometry.base import BaseGeometry, BaseMultipartGeometry
from tqdm.contrib.concurrent import process_map

from osmfinder._constants import OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS
from osmfinder._typing import OsmExtractsIndex
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
from osmfinder._compat import FORCE_TERMINAL
from osmfinder.sources.bbbike import _get_bbbike_index
from osmfinder.extract import (
    OpenStreetMapExtract,
    OsmExtractSource,
    clear_osm_index_cache,
)
from osmfinder.sources.tree import get_available_extracts_as_rich_tree
from osmfinder.sources.geo2day import _get_geo2day_index
from osmfinder.sources.geofabrik import _get_geofabrik_index
from osmfinder.sources.movisda import _get_movisda_admin_index, _get_movisda_grid_index
from osmfinder.sources.osm_fr import _get_openstreetmap_fr_index

__all__ = [
    "download_extracts_pbf_files",
    "find_and_download_extracts_pbf_files",
    "find_smallest_containing_extracts_total",
    "find_smallest_containing_geofabrik_extracts",
    "find_smallest_containing_openstreetmap_fr_extracts",
    "find_smallest_containing_bbbike_extracts",
    "clear_osm_index_cache",
    "get_extract_by_query",
    "download_extract_by_query",
    "display_available_extracts",
    "OsmExtractSource",
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


OSM_EXTRACT_SOURCE_INDEX_FUNCTION = {
    OsmExtractSource.bbbike: _get_bbbike_index,
    OsmExtractSource.geofabrik: _get_geofabrik_index,
    OsmExtractSource.osm_fr: _get_openstreetmap_fr_index,
    OsmExtractSource.geo2day: _get_geo2day_index,
    OsmExtractSource.movisda_admin: _get_movisda_admin_index,
    OsmExtractSource.movisda_grid: _get_movisda_grid_index,
}

# A single source, or multiple sources passed as an iterable or a comma-separated string.
OsmExtractSourceLike = Union[OsmExtractSource, str, Iterable[Union[OsmExtractSource, str]]]


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
        raw_values: list[Union[OsmExtractSource, str]] = [source]
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

    return list(set(resolved))


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
def get_extract_by_query(query: str) -> OpenStreetMapExtract: ...


@overload
def get_extract_by_query(query: str, source: OsmExtractSourceLike) -> OpenStreetMapExtract: ...


@overload
def get_extract_by_query(
    query: str,
    *,
    select_first_match: bool = ...,
    excluded_extracts_ids: Optional[set[str]] = ...,
) -> OpenStreetMapExtract: ...


@overload
def get_extract_by_query(
    query: str,
    source: OsmExtractSourceLike,
    select_first_match: bool = ...,
    excluded_extracts_ids: Optional[set[str]] = ...,
) -> OpenStreetMapExtract: ...


def get_extract_by_query(
    query: str,
    source: OsmExtractSourceLike = "any",
    select_first_match: bool = True,
    excluded_extracts_ids: Optional[set[str]] = None,
) -> OpenStreetMapExtract:
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
        OpenStreetMapExtract: Found extract.
    """
    try:
        index = _get_index_for_sources(source)

        if excluded_extracts_ids:
            index = index.filter_by_mask(~np.isin(index.ids, list(excluded_extracts_ids)))

        query_lower = query.lower().strip()
        query_lower_spaces = query_lower.replace("_", " ")

        file_names_lower = np.char.lower(index.file_names)
        file_names_lower_spaces = np.char.replace(file_names_lower, "_", " ")
        names_lower = np.char.lower(index.names)
        names_lower_spaces = np.char.replace(names_lower, "_", " ")

        file_name_matched_rows = (file_names_lower == query_lower) | (
            file_names_lower_spaces == query_lower_spaces
        )
        extract_name_matched_rows = (names_lower == query_lower) | (
            names_lower_spaces == query_lower_spaces
        )

        matching_index_row: Optional[OpenStreetMapExtract] = None

        # full file name matched
        if np.count_nonzero(file_name_matched_rows) == 1:
            matching_index_row = index.get_extract_by_index(
                int(np.flatnonzero(file_name_matched_rows)[0])
            )
        # single name matched
        elif np.count_nonzero(extract_name_matched_rows) == 1:
            matching_index_row = index.get_extract_by_index(
                int(np.flatnonzero(extract_name_matched_rows)[0])
            )
        # multiple names matched
        elif extract_name_matched_rows.any():
            matching_rows = index.filter_by_mask(extract_name_matched_rows)
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
            matching_full_names: list[str] = []
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

        return cast("OpenStreetMapExtract", matching_index_row)

    except ValueError as ex:
        raise ValueError(f"Unknown OSM extracts source: {source}.") from ex


@overload
def download_extract_by_query(
    query: str,
    *,
    download_directory: Union[str, Path] = "files",
    progressbar: bool = True,
    select_first_match: bool = True,
) -> Path: ...


@overload
def download_extract_by_query(
    query: str,
    source: OsmExtractSourceLike,
    *,
    download_directory: Union[str, Path] = "files",
    progressbar: bool = True,
    select_first_match: bool = True,
) -> Path: ...


def download_extract_by_query(
    query: str,
    source: OsmExtractSourceLike = "any",
    download_directory: Union[str, Path] = "files",
    progressbar: bool = True,
    select_first_match: bool = True,
) -> Path:
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
        Path: Path to the downloaded OSM extract.
    """
    download_directory = Path(download_directory)
    excluded_extracts_ids: set[str] = set()
    unavailable_file_names: list[str] = []

    while True:
        try:
            matching_extract = get_extract_by_query(
                query,
                source,
                select_first_match=select_first_match,
                excluded_extracts_ids=excluded_extracts_ids,
            )
        except OsmExtractZeroMatchesError:
            # If nothing has been excluded yet, the query genuinely matched no extract.
            if not unavailable_file_names:
                raise
            # Otherwise every matching extract was excluded because it failed to download -
            # that's an availability problem, not a zero-match query.
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

        if not unavailable:
            return downloaded[0][1]

        warnings.warn(
            f'Matched extract "{matching_extract.file_name}" is unavailable.'
            " Excluding it and trying the next matching extract.",
            OsmExtractUnavailableWarning,
            stacklevel=0,
        )
        excluded_extracts_ids.add(matching_extract.id)
        unavailable_file_names.append(matching_extract.file_name)


def find_and_download_extracts_pbf_files(
    geometry: Union[BaseGeometry, BaseMultipartGeometry],
    source: OsmExtractSourceLike,
    download_directory: Union[str, Path],
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
    progressbar: bool = True,
) -> list[tuple[OpenStreetMapExtract, Path]]:
    """
    Find the smallest set of extracts covering a geometry and download them as PBF files.

    Searches for the smallest set of extracts covering a given geometry and downloads them.
    If any matching extract turns out to be unavailable (e.g. removed from the provider or
    a temporary server error), it is excluded and the coverage is recalculated using the
    remaining extracts, until a fully downloadable set is found or the geometry can no longer
    be covered.

    Args:
        geometry (Union[BaseGeometry, BaseMultipartGeometry]): Geometry to be covered.
        source (OsmExtractSourceLike): OSM source name. Can be one of: 'any', 'Geofabrik',
            'BBBike', 'OSMfr', or an iterable / comma-separated string of those
            (e.g. ['BBBike', 'OSM_fr'] or 'bbbike,osmfr').
        download_directory (Union[str, Path]): Directory where PBF files should be saved.
        geometry_coverage_iou_threshold (float): Minimal value of the Intersection over Union metric
            for selecting the matching OSM extracts. Has to be in range between 0 and 1.
            Defaults to 0.01.
        allow_uncovered_geometry (bool): Suppress an error if some geometry parts aren't covered
            by any OSM extract. Defaults to `False`.
        progressbar (bool, optional): Show progress bar. Defaults to True.

    Raises:
        GeometryNotCoveredError: If the geometry can't be covered by available extracts.

    Returns:
        list[tuple[OpenStreetMapExtract, Path]]: List of (extract, downloaded path) pairs
            covering the given geometry.
    """
    download_directory = Path(download_directory)
    excluded_extracts_ids: set[str] = set()

    while True:
        matching_extracts = find_smallest_containing_extracts(
            geometry,
            source,
            geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
            allow_uncovered_geometry=allow_uncovered_geometry,
            excluded_extracts_ids=excluded_extracts_ids,
        )

        downloaded, unavailable = _download_extracts_pbf_files(
            matching_extracts,
            download_directory,
            progressbar=progressbar,
            ignore_unavailable=True,
        )

        if not unavailable:
            return downloaded

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
    source: Union[OsmExtractSource, str],
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


def find_smallest_containing_extracts_total(
    geometry: Union[BaseGeometry, BaseMultipartGeometry],
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
    excluded_extracts_ids: Optional[set[str]] = None,
) -> list[OpenStreetMapExtract]:
    """
    Find smallest extracts from all OSM extract indexes that contains given polygon.

    Iterates all indexes and finds smallest extracts that covers a given geometry.

    Extracts are selected based on the highest value of the Intersection over Union metric with
    geometry. Some extracts might be discarded because of low IoU metric value leaving some parts
    of the geometry uncovered.

    Args:
        geometry (Union[BaseGeometry, BaseMultipartGeometry]): Geometry to be covered.
        geometry_coverage_iou_threshold (float): Minimal value of the Intersection over Union metric
            for selecting the matching OSM extracts. Is best matching extract has value lower than
            the threshold, it is discarded (except the first one). Has to be in range between
            0 and 1. Value of 0 will allow every intersected extract, value of 1 will only allow
            extracts that match the geometry exactly. Defaults to 0.01.
        allow_uncovered_geometry (bool): Suppress an error if some geometry parts aren't covered
            by any OSM extract. Defaults to `False`.
        excluded_extracts_ids (Optional[set[str]]): Set of extract ids to exclude from the search.
            Useful for skipping extracts that are unavailable for download. Defaults to `None`.

    Returns:
        List[OpenStreetMapExtract]: List of extracts name, URL to download it and boundary polygon.
    """
    return _find_smallest_containing_extracts(
        geometry=geometry,
        polygons_index=_get_combined_index(),
        geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
        allow_uncovered_geometry=allow_uncovered_geometry,
        excluded_extracts_ids=excluded_extracts_ids,
    )


def find_smallest_containing_geofabrik_extracts(
    geometry: Union[BaseGeometry, BaseMultipartGeometry],
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
    excluded_extracts_ids: Optional[set[str]] = None,
) -> list[OpenStreetMapExtract]:
    """
    Find smallest extracts from Geofabrik that contains given geometry.

    Iterates a geofabrik index and finds smallest extracts that covers a given geometry.

    Extracts are selected based on the highest value of the Intersection over Union metric with
    geometry. Some extracts might be discarded because of low IoU metric value leaving some parts
    of the geometry uncovered.

    Args:
        geometry (Union[BaseGeometry, BaseMultipartGeometry]): Geometry to be covered.
        geometry_coverage_iou_threshold (float): Minimal value of the Intersection over Union metric
            for selecting the matching OSM extracts. Is best matching extract has value lower than
            the threshold, it is discarded (except the first one). Has to be in range between
            0 and 1. Value of 0 will allow every intersected extract, value of 1 will only allow
            extracts that match the geometry exactly. Defaults to 0.01.
        allow_uncovered_geometry (bool): Suppress an error if some geometry parts aren't covered
            by any OSM extract. Defaults to `False`.
        excluded_extracts_ids (Optional[set[str]]): Set of extract ids to exclude from the search.
            Useful for skipping extracts that are unavailable for download. Defaults to `None`.

    Returns:
        List[OpenStreetMapExtract]: List of extracts name, URL to download it and boundary polygon.
    """
    return _find_smallest_containing_extracts(
        geometry=geometry,
        polygons_index=OSM_EXTRACT_SOURCE_INDEX_FUNCTION[OsmExtractSource.geofabrik](),
        geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
        allow_uncovered_geometry=allow_uncovered_geometry,
        excluded_extracts_ids=excluded_extracts_ids,
    )


def find_smallest_containing_openstreetmap_fr_extracts(
    geometry: Union[BaseGeometry, BaseMultipartGeometry],
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
    excluded_extracts_ids: Optional[set[str]] = None,
) -> list[OpenStreetMapExtract]:
    """
    Find smallest extracts from OpenStreetMap.fr that contains given polygon.

    Iterates an osm.fr index and finds smallest extracts that covers a given geometry.

    Extracts are selected based on the highest value of the Intersection over Union metric with
    geometry. Some extracts might be discarded because of low IoU metric value leaving some parts
    of the geometry uncovered.

    Args:
        geometry (Union[BaseGeometry, BaseMultipartGeometry]): Geometry to be covered.
        geometry_coverage_iou_threshold (float): Minimal value of the Intersection over Union metric
            for selecting the matching OSM extracts. Is best matching extract has value lower than
            the threshold, it is discarded (except the first one). Has to be in range between
            0 and 1. Value of 0 will allow every intersected extract, value of 1 will only allow
            extracts that match the geometry exactly. Defaults to 0.01.
        allow_uncovered_geometry (bool): Suppress an error if some geometry parts aren't covered
            by any OSM extract. Defaults to `False`.
        excluded_extracts_ids (Optional[set[str]]): Set of extract ids to exclude from the search.
            Useful for skipping extracts that are unavailable for download. Defaults to `None`.

    Returns:
        List[OpenStreetMapExtract]: List of extracts name, URL to download it and boundary polygon.
    """
    return _find_smallest_containing_extracts(
        geometry=geometry,
        polygons_index=OSM_EXTRACT_SOURCE_INDEX_FUNCTION[OsmExtractSource.osm_fr](),
        geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
        allow_uncovered_geometry=allow_uncovered_geometry,
        excluded_extracts_ids=excluded_extracts_ids,
    )


def find_smallest_containing_bbbike_extracts(
    geometry: Union[BaseGeometry, BaseMultipartGeometry],
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
    excluded_extracts_ids: Optional[set[str]] = None,
) -> list[OpenStreetMapExtract]:
    """
    Find smallest extracts from BBBike that contains given polygon.

    Iterates an BBBike index and finds smallest extracts that covers a given geometry.

    Extracts are selected based on the highest value of the Intersection over Union metric with
    geometry. Some extracts might be discarded because of low IoU metric value leaving some parts
    of the geometry uncovered.

    Args:
        geometry (Union[BaseGeometry, BaseMultipartGeometry]): Geometry to be covered.
        geometry_coverage_iou_threshold (float): Minimal value of the Intersection over Union metric
            for selecting the matching OSM extracts. Is best matching extract has value lower than
            the threshold, it is discarded (except the first one). Has to be in range between
            0 and 1. Value of 0 will allow every intersected extract, value of 1 will only allow
            extracts that match the geometry exactly. Defaults to 0.01.
        allow_uncovered_geometry (bool): Suppress an error if some geometry parts aren't covered
            by any OSM extract. Defaults to `False`.
        excluded_extracts_ids (Optional[set[str]]): Set of extract ids to exclude from the search.
            Useful for skipping extracts that are unavailable for download. Defaults to `None`.

    Returns:
        List[OpenStreetMapExtract]: List of extracts name, URL to download it and boundary polygon.
    """
    return _find_smallest_containing_extracts(
        geometry=geometry,
        polygons_index=OSM_EXTRACT_SOURCE_INDEX_FUNCTION[OsmExtractSource.bbbike](),
        geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
        allow_uncovered_geometry=allow_uncovered_geometry,
        excluded_extracts_ids=excluded_extracts_ids,
    )


def find_smallest_containing_extracts(
    geometry: Union[BaseGeometry, BaseMultipartGeometry],
    source: OsmExtractSourceLike,
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
    excluded_extracts_ids: Optional[set[str]] = None,
) -> list[OpenStreetMapExtract]:
    """
    Find smallest extracts from a given OSM source that contains given polygon.

    Iterates an OSM source index and finds smallest extracts that covers a given geometry.

    Extracts are selected based on the highest value of the Intersection over Union metric with
    geometry. Some extracts might be discarded because of low IoU metric value leaving some parts
    of the geometry uncovered.

    Args:
        geometry (Union[BaseGeometry, BaseMultipartGeometry]): Geometry to be covered.
        source (OsmExtractSourceLike): OSM source name. Can be one of: 'any', 'Geofabrik',
            'BBBike', 'OSMfr', or an iterable / comma-separated string of those
            (e.g. ['BBBike', 'OSM_fr'] or 'bbbike,osmfr').
        geometry_coverage_iou_threshold (float): Minimal value of the Intersection over Union metric
            for selecting the matching OSM extracts. Is best matching extract has value lower than
            the threshold, it is discarded (except the first one). Has to be in range between
            0 and 1. Value of 0 will allow every intersected extract, value of 1 will only allow
            extracts that match the geometry exactly. Defaults to 0.01.
        allow_uncovered_geometry (bool): Suppress an error if some geometry parts aren't covered
            by any OSM extract. Defaults to `False`.
        excluded_extracts_ids (Optional[set[str]]): Set of extract ids to exclude from the search.
            Useful for skipping extracts that are unavailable for download. Defaults to `None`.

    Returns:
        List[OpenStreetMapExtract]: List of extracts name, URL to download it and boundary polygon.
    """
    try:
        index = _get_index_for_sources(source)
    except ValueError as ex:
        raise ValueError(f"Unknown OSM extracts source: {source}.") from ex

    return _find_smallest_containing_extracts(
        geometry=geometry,
        polygons_index=index,
        geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
        allow_uncovered_geometry=allow_uncovered_geometry,
        excluded_extracts_ids=excluded_extracts_ids,
    )


def _find_smallest_containing_extracts(
    geometry: Union[BaseGeometry, BaseMultipartGeometry],
    polygons_index: OsmExtractsIndex,
    num_of_multiprocessing_workers: int = -1,
    multiprocessing_activation_threshold: Optional[int] = None,
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
    excluded_extracts_ids: Optional[set[str]] = None,
) -> list[OpenStreetMapExtract]:
    """
    Find smallest set of extracts covering a given geometry.

    Iterates a provided extracts index and searches for a smallest set that cover a given geometry.
    It's not guaranteed that this set will be the smallest and there will be no overlaps.

    Extracts are selected based on the highest value of the Intersection over Union metric with
    geometry. Some extracts might be discarded because of low IoU metric value leaving some parts
    of the geometry uncovered.

    Geometry will be flattened into singluar geometries if it's `BaseMultipartGeometry`.

    Args:
        geometry (Union[BaseGeometry, BaseMultipartGeometry]): Geometry to be covered.
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

    Returns:
        List[OpenStreetMapExtract]: List of extracts covering a given geometry.
    """
    if excluded_extracts_ids:
        polygons_index = polygons_index.filter_by_mask(
            ~np.isin(polygons_index.ids, list(excluded_extracts_ids))
        )

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

        for extract_ids_list in process_map(
            find_extracts_func,
            geometries,
            desc="Finding matching extracts",
            max_workers=num_of_multiprocessing_workers,
            chunksize=ceil(total_polygons / (4 * num_of_multiprocessing_workers)),
            disable=FORCE_TERMINAL,
        ):
            unique_extracts_ids.update(extract_ids_list)
    else:
        for sub_geometry in geometries:
            unique_extracts_ids.update(
                _find_smallest_containing_extracts_for_single_geometry(
                    geometry=sub_geometry,
                    polygons_index=polygons_index,
                    geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
                    allow_uncovered_geometry=allow_uncovered_geometry,
                )
            )

    extracts_filtered = _filter_extracts(
        geometry,
        unique_extracts_ids,
        polygons_index,
        num_of_multiprocessing_workers,
        multiprocessing_activation_threshold,
    )

    return extracts_filtered


def _find_smallest_containing_extracts_for_single_geometry(
    geometry: BaseGeometry,
    polygons_index: OsmExtractsIndex,
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
) -> set[str]:
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

    selected_extracts_ids: set[str] = set()
    for extract_id, iou_metric_value in zip(checked_extracts_ids, iou_metric_values):
        if iou_metric_value >= geometry_coverage_iou_threshold or not selected_extracts_ids:
            selected_extracts_ids.add(extract_id)
        else:
            skipped_extract = _get_extract_by_id(polygons_index, extract_id)
            warnings.warn(
                (
                    "Skipping extract because of low IoU value "
                    f"({skipped_extract.file_name}, {iou_metric_value:.3g})."
                ),
                GeometryNotCoveredWarning,
                stacklevel=0,
            )

    return selected_extracts_ids


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
        candidate_areas = polygons_index.areas[candidate_indices]
        candidate_geometries = [
            cast("BaseGeometry", polygons_index.geometries[idx]) for idx in candidate_indices
        ]
        intersection_areas = np.array(
            [
                extract_geometry.intersection(geometry_to_cover).area
                for extract_geometry in candidate_geometries
            ]
        )
        iou_values = intersection_areas / (
            candidate_areas + geometry_to_cover.area - intersection_areas
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
        geometry (Union[BaseGeometry, BaseMultipartGeometry]): Geometry to be covered.
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
    # Sort by area descending, then id ascending (mirrors the previous GeoDataFrame behaviour).
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

    simplified_extracts_ids = _simplify_selected_extracts(
        filtered_extracts_ids, sorted_extracts
    )

    for extract_id in simplified_extracts_ids:
        filtered_extracts.append(_get_extract_by_id(sorted_extracts, extract_id))

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
    simplified_extracts_ids: set[str] = filtered_extracts_ids.copy()

    matching_mask = np.isin(sorted_extracts.ids, list(simplified_extracts_ids))
    matching_indices = np.flatnonzero(matching_mask)

    simplify_again = True
    while simplify_again:
        simplify_again = False
        extract_to_remove = None
        for extract_id in simplified_extracts_ids:
            extract_idx = int(np.flatnonzero(sorted_extracts.ids == extract_id)[0])
            extract_geometry = sorted_extracts.geometries[extract_idx]
            other_indices = [idx for idx in matching_indices if idx != extract_idx]
            other_geometries = unary_union(sorted_extracts.geometries[other_indices])
            if extract_geometry.covered_by(other_geometries):
                extract_to_remove = extract_id
                simplify_again = True
                break

        if extract_to_remove is not None:
            simplified_extracts_ids.remove(extract_to_remove)

    return simplified_extracts_ids


def _flatten_geometry(geometry: BaseGeometry) -> list[BaseGeometry]:
    """Flatten all geometries into a list of BaseGeometries."""
    if isinstance(geometry, BaseMultipartGeometry):
        geometries = []
        for sub_geom in geometry.geoms:
            geometries.extend(_flatten_geometry(sub_geom))
        return geometries
    return [geometry]