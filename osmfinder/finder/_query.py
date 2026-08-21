"""String-query based find and download."""

import difflib
import warnings
from pathlib import Path
from typing import overload

import numpy as np

import osmfinder.finder._download as _download_mod
import osmfinder.finder._sources as _sources_mod
from osmfinder._results import OsmfinderDownloadResult, OsmfinderQueryResult
from osmfinder._typing import (
    OpenStreetMapExtract,
    OsmExtractsIndex,
    OsmExtractSource,
)
from osmfinder.exceptions import (
    OsmExtractMultipleMatchesError,
    OsmExtractMultipleMatchesWarning,
    OsmExtractsUnavailableError,
    OsmExtractUnavailableWarning,
    OsmExtractZeroMatchesError,
)

OsmExtractSourceLike = _sources_mod.OsmExtractSourceLike


def _get_index_for_sources(source: OsmExtractSourceLike) -> OsmExtractsIndex:
    return _sources_mod._get_index_for_sources(source)


def _resolve_extract_sources(source: OsmExtractSourceLike) -> list[OsmExtractSource]:
    return _sources_mod._resolve_extract_sources(source)


def _download_extracts_pbf_files(
    extracts: list[OpenStreetMapExtract],
    download_directory: Path,
    progressbar: bool = True,
    ignore_unavailable: bool = False,
) -> tuple[list[tuple[OpenStreetMapExtract, Path]], list[OpenStreetMapExtract]]:
    return _download_mod._download_extracts_pbf_files(
        extracts, download_directory, progressbar=progressbar, ignore_unavailable=ignore_unavailable
    )


@overload
def get_extract_by_query(query: str) -> OsmfinderQueryResult: ...


@overload
def get_extract_by_query(query: str, source: OsmExtractSourceLike) -> OsmfinderQueryResult: ...


@overload
def get_extract_by_query(
    query: str,
    *,
    select_first_match: bool = ...,
    excluded_extracts_ids: set[str] | None = ...,
) -> OsmfinderQueryResult: ...


@overload
def get_extract_by_query(
    query: str,
    source: OsmExtractSourceLike,
    select_first_match: bool = ...,
    excluded_extracts_ids: set[str] | None = ...,
) -> OsmfinderQueryResult: ...


def get_extract_by_query(
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
        >>> result = osmfinder.get_extract_by_query("Monaco")
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


def find_by_query(
    query: str,
    source: OsmExtractSourceLike = "any",
    *,
    select_first_match: bool = True,
    excluded_extracts_ids: set[str] | None = None,
) -> OsmfinderQueryResult:
    """
    Find an OSM extract by name.

    This is the string-query path entry point.

    Args:
        query (str): Query to search for a particular extract.
        source (OsmExtractSourceLike): OSM source name. Defaults to 'any'.
        select_first_match (bool): When multiple extracts match the query by name, select the
            first one (sorted by area ascending, then id) and emit a warning instead of raising
            an error. Defaults to `True`.
        excluded_extracts_ids (Optional[set[str]]): Set of extract ids to exclude from the search.
            Defaults to `None`.

    Returns:
        OsmfinderQueryResult: Result containing the matched extract.
    """
    return get_extract_by_query(
        query,
        source=source,
        select_first_match=select_first_match,
        excluded_extracts_ids=excluded_extracts_ids,
    )


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
            query_result = get_extract_by_query(
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


def download_by_query(
    query: str,
    source: OsmExtractSourceLike = "any",
    *,
    download_directory: str | Path = "files",
    select_first_match: bool = True,
    progressbar: bool = True,
) -> OsmfinderDownloadResult:
    """
    Download an OSM extract by name.

    This is the string-query path entry point.

    Args:
        query (str): Query to search for a particular extract.
        source (OsmExtractSourceLike): OSM source name. Defaults to 'any'.
        download_directory (Union[str, Path]): Directory where the file should be
            downloaded. Defaults to "files".
        select_first_match (bool): When multiple extracts match the query by name, select the
            first one with a warning instead of raising an error. Defaults to `True`.
        progressbar (bool): Show progress bar. Defaults to True.

    Returns:
        OsmfinderDownloadResult: Result containing the downloaded path and find result.
    """
    return download_extract_by_query(
        query,
        source=source,
        download_directory=download_directory,
        progressbar=progressbar,
        select_first_match=select_first_match,
    )
