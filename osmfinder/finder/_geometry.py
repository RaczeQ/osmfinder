"""Geometry-based find and download."""

import warnings
from collections.abc import Iterable
from functools import partial
from math import ceil
from multiprocessing import cpu_count
from pathlib import Path
from typing import cast, overload

import numpy as np
from shapely import equals_exact, intersects, is_empty, unary_union
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry
from tqdm.contrib.concurrent import process_map

import osmfinder.finder._download as _download_mod
import osmfinder.finder._sources as _sources_mod
from osmfinder._compat import FORCE_TERMINAL
from osmfinder._results import (
    GeometryCoveringStep,
    OsmfinderDownloadResult,
    OsmfinderGeometryResult,
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
    OsmExtractUnavailableWarning,
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


def find_smallest_containing_extracts(
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
        >>> results = osmfinder.find_smallest_containing_extracts(geom, source="Geofabrik")
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


def find_by_geometry(
    geometry: BaseGeometry,
    source: OsmExtractSourceLike = "any",
    *,
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
    excluded_extracts_ids: set[str] | None = None,
    force_single_result: bool = False,
    single_result_iou_threshold: float = 0.99,
) -> OsmfinderGeometryResult:
    """
    Find the smallest set of OSM extracts covering a geometry.

    This is the geometry-based path entry point.

    Args:
        geometry (BaseGeometry): Shapely geometry to cover.
        source (OsmExtractSourceLike): OSM source name. Defaults to 'any'.
        geometry_coverage_iou_threshold (float): Minimal Intersection over Union
            metric for selecting extracts. Defaults to 0.01.
        allow_uncovered_geometry (bool): Suppress an error if some geometry parts aren't covered.
            Defaults to `False`.
        excluded_extracts_ids (Optional[set[str]]): Set of extract ids to exclude.
            Defaults to `None`.
        force_single_result (bool): Return only the smallest extract that best covers the geometry.
            Defaults to `False`.
        single_result_iou_threshold (float): Minimal IoU for selecting a single result when
            ``force_single_result`` is ``True``. Defaults to 0.99.

    Returns:
        OsmfinderGeometryResult: Result containing the covering extracts and covering steps.
    """
    return find_smallest_containing_extracts(
        geometry,
        source=source,
        geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
        allow_uncovered_geometry=allow_uncovered_geometry,
        excluded_extracts_ids=excluded_extracts_ids,
        force_single_result=force_single_result,
        single_result_iou_threshold=single_result_iou_threshold,
    )


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
    from pathlib import Path

    download_directory = Path(download_directory)
    excluded_extracts_ids: set[str] = set()
    all_unavailable: list[OpenStreetMapExtract] = []

    while True:
        matching_extracts = find_smallest_containing_extracts(
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


def download_by_geometry(
    geometry: BaseGeometry,
    source: OsmExtractSourceLike = "any",
    *,
    download_directory: str | Path = "files",
    geometry_coverage_iou_threshold: float = 0.01,
    allow_uncovered_geometry: bool = False,
    force_single_result: bool = False,
    single_result_iou_threshold: float = 0.99,
    progressbar: bool = True,
) -> OsmfinderDownloadResult:
    """
    Download OSM extracts covering a geometry.

    This is the geometry-based path entry point.

    Args:
        geometry (BaseGeometry): Shapely geometry to cover.
        source (OsmExtractSourceLike): OSM source name. Defaults to 'any'.
        download_directory (Union[str, Path]): Directory where files should be downloaded.
            Defaults to "files".
        geometry_coverage_iou_threshold (float): Minimal Intersection over Union
            metric for selecting extracts. Defaults to 0.01.
        allow_uncovered_geometry (bool): Suppress an error if some geometry parts aren't covered.
            Defaults to `False`.
        force_single_result (bool): Return only the smallest extract that best covers the geometry.
            Defaults to `False`.
        single_result_iou_threshold (float): Minimal IoU for selecting a single result when
            ``force_single_result`` is ``True``. Defaults to 0.99.
        progressbar (bool): Show progress bar. Defaults to True.

    Returns:
        OsmfinderDownloadResult: Result containing download paths and find result.
    """
    return find_and_download_extracts_pbf_files(
        geometry,
        source=source,
        download_directory=download_directory,
        geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
        allow_uncovered_geometry=allow_uncovered_geometry,
        force_single_result=force_single_result,
        single_result_iou_threshold=single_result_iou_threshold,
        progressbar=progressbar,
    )


# ---------------------------------------------------------------------------
# Internal geometry helpers
# ---------------------------------------------------------------------------


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
