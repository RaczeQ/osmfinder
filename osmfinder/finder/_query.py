"""
Public find/query operations.

Iterators for finding OSM extracts by a text query. These public functions are the
entry points patched by tests and callers through the package :mod:`osmfinder.finder`
namespace, so any patch-sensitive helper they depend on is resolved through ``_finder``
(the package module object) rather than a local import binding.
"""

import difflib
import warnings
from typing import overload

import numpy as np
from shapely.geometry.base import BaseGeometry

import osmfinder.finder as _finder
from osmfinder._results import (
    OsmfinderGeometryResult,
    OsmfinderQueryResult,
)
from osmfinder._typing import (
    OpenStreetMapExtract,
    OsmExtractSourceLike,
)
from osmfinder.exceptions import (
    OsmExtractMultipleMatchesError,
    OsmExtractMultipleMatchesWarning,
    OsmExtractZeroMatchesError,
)
from osmfinder.finder._covering import find_extracts_by_geometry


@overload
def find_extract_by_query(query: str) -> OsmfinderQueryResult: ...


@overload
def find_extract_by_query(
    query: str,
    source: OsmExtractSourceLike | None,
) -> OsmfinderQueryResult: ...


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
    source: OsmExtractSourceLike | None,
    select_first_match: bool = ...,
    excluded_extracts_ids: set[str] | None = ...,
) -> OsmfinderQueryResult: ...


def find_extract_by_query(
    query: str,
    source: OsmExtractSourceLike | None = None,
    select_first_match: bool = True,
    excluded_extracts_ids: set[str] | None = None,
) -> OsmfinderQueryResult:
    """
    Find an OSM extract by name.

    Args:
        query (str): Query to search for a particular extract.
        source (OsmExtractSourceLike): OSM source name. Can be one of: 'any', 'Geofabrik',
            'BBBike', 'osmfr', or an iterable / comma-separated string of those
            (e.g. ['BBBike', 'osmfr'] or 'bbbike,osmfr'). Defaults to 'any'.
        select_first_match (bool): When multiple extracts match the query by name, select the
            first one (sorted by area ascending, then id) and emit a warning instead of raising
            an error. Set to `False` to raise `OsmExtractMultipleMatchesError` instead.
            Defaults to `True`.
        excluded_extracts_ids (set[str] | None): Set of extract ids to exclude from the search.
            Useful for skipping extracts that are unavailable for download. Defaults to `None`.

    Returns:
        OsmfinderQueryResult: Result containing the matched extract.

    Examples:
        >>> import osmfinder
        >>> result = osmfinder.find_extract_by_query("Monaco")
        >>> result.extracts[0].id
        'Movisda-admin_MC'
        >>> result.extracts[0].file_name
        'movisda-admin_monaco'
        >>> result.sources_used
        [<OsmExtractSource.bbbike: 'BBBike'>, ...]
    """
    try:
        index = _finder._get_index_for_sources(source)

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

        sources_used = _finder._resolve_extract_sources(source)
        if matching_index_row is None:
            raise RuntimeError("Failed to select a matching extract.")
        else:
            return OsmfinderQueryResult(
                query=query,
                extracts=[matching_index_row],
                matched_extracts=matched_extracts,
                sources_used=sources_used,
                config={
                    "select_first_match": select_first_match,
                    "excluded_extracts_ids": (
                        list(excluded_extracts_ids) if excluded_extracts_ids else []
                    ),
                },
            )

    except ValueError as ex:
        raise ValueError(f"Unknown OSM extracts source: {source}.") from ex


@overload
def find(
    query: str,
    source: OsmExtractSourceLike | None = None,
    *,
    select_first_match: bool = True,
    excluded_extracts_ids: set[str] | None = None,
) -> OsmfinderQueryResult: ...


@overload
def find(
    query: BaseGeometry,
    source: OsmExtractSourceLike | None = None,
    *,
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
    excluded_extracts_ids: set[str] | None = None,
    force_single_result: bool = False,
    single_result_iou_threshold: float = 0.99,
) -> OsmfinderGeometryResult: ...


def find(
    query: str | BaseGeometry,
    source: OsmExtractSourceLike | None = None,
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
        query (str | BaseGeometry): Text query
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
        excluded_extracts_ids (set[str] | None): Set of extract ids to exclude from the search.
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
        OsmfinderQueryResult | OsmfinderGeometryResult: Query result for string queries,
            geometry result for geometry queries. Both contain an ``extracts`` list.

    Examples:
        >>> import osmfinder
        >>> from shapely.geometry import box
        >>> # Find by name
        >>> result = osmfinder.find("Monaco")
        >>> len(result.extracts) == 1
        True
        >>> result.extracts[0].id
        'Movisda-admin_MC'
        >>> # Find by geometry
        >>> geom = box(7.40, 43.71, 7.44, 43.75)
        >>> result = osmfinder.find(geom, source="Geofabrik")
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
