"""Unified public API dispatchers."""

from pathlib import Path
from typing import overload

from shapely.geometry.base import BaseGeometry

from osmfinder._results import (
    OsmfinderDownloadResult,
    OsmfinderGeometryResult,
    OsmfinderQueryResult,
)
from osmfinder.finder._geometry import (
    download_by_geometry,
    find_by_geometry,
)
from osmfinder.finder._query import download_by_query, find_by_query
from osmfinder.finder._sources import OsmExtractSourceLike


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

    Dispatches to :func:`find_by_query` when called with a string query,
    or to :func:`find_by_geometry` when called with a geometry.

    Args:
        query (Union[str, BaseGeometry]): Text query
            or shapely geometry to search for.
        source (OsmExtractSourceLike): OSM source name. Defaults to 'any'.
        select_first_match (bool): Only used for string queries. Defaults to `True`.
        geometry_coverage_iou_threshold (float): Only used for geometry queries. Defaults to 0.01.
        allow_uncovered_geometry (bool): Only used for geometry queries. Defaults to `False`.
        excluded_extracts_ids (Optional[set[str]]): Defaults to `None`.
        force_single_result (bool): Only used for geometry queries. Defaults to `False`.
        single_result_iou_threshold (float): Only used for geometry queries. Defaults to 0.99.

    Returns:
        Union[OsmfinderQueryResult, OsmfinderGeometryResult]: Query result for string queries,
            geometry result for geometry queries.
    """
    if isinstance(query, str):
        return find_by_query(
            query,
            source=source,
            select_first_match=select_first_match,
            excluded_extracts_ids=excluded_extracts_ids,
        )
    return find_by_geometry(
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
    query: str,
    source: OsmExtractSourceLike = "any",
    *,
    download_directory: str | Path = "files",
    select_first_match: bool = True,
    progressbar: bool = True,
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
) -> OsmfinderDownloadResult: ...


def download(
    query: str | BaseGeometry,
    source: OsmExtractSourceLike = "any",
    *,
    download_directory: str | Path = "files",
    select_first_match: bool = True,
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
    force_single_result: bool = False,
    single_result_iou_threshold: float = 0.99,
    progressbar: bool = True,
) -> OsmfinderDownloadResult:
    """
    Download an OSM extract by name or geometry.

    Dispatches to :func:`download_by_query` when called with a string query,
    or to :func:`download_by_geometry` when called with a geometry.

    Args:
        query (Union[str, BaseGeometry]): Text query
            or shapely geometry to search for.
        source (OsmExtractSourceLike): OSM source name. Defaults to 'any'.
        download_directory (Union[str, Path]): Directory where the file should be
            downloaded. Defaults to "files".
        select_first_match (bool): Only used for string queries. Defaults to `True`.
        geometry_coverage_iou_threshold (float): Only used for geometry queries. Defaults to 0.01.
        allow_uncovered_geometry (bool): Only used for geometry queries. Defaults to `False`.
        force_single_result (bool): Only used for geometry queries. Defaults to `False`.
        single_result_iou_threshold (float): Only used for geometry queries. Defaults to 0.99.
        progressbar (bool): Show progress bar. Defaults to True.

    Returns:
        OsmfinderDownloadResult: Result containing ``download_paths`` and ``find_result``
        (the underlying query or geometry result).
    """
    if isinstance(query, str):
        return download_by_query(
            query,
            source=source,
            download_directory=download_directory,
            progressbar=progressbar,
            select_first_match=select_first_match,
        )
    return download_by_geometry(
        query,
        source=source,
        download_directory=download_directory,
        geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
        allow_uncovered_geometry=allow_uncovered_geometry,
        force_single_result=force_single_result,
        single_result_iou_threshold=single_result_iou_threshold,
        progressbar=progressbar,
    )
