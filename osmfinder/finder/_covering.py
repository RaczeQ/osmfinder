"""
Geometry-based find operations.

This module contains the pure geometry-covering algorithm that, given an index of
OSM extracts, finds the smallest set of extracts covering an input geometry. It also
exposes the public geometry-based entry points (:func:`find_extracts_by_geometry` and
:func:`find_extracts_covering_point`) which resolve patch-sensitive helpers through the
package :mod:`osmfinder.finder` namespace.
"""

import warnings
from collections.abc import Iterable
from functools import partial
from math import ceil
from multiprocessing import cpu_count
from typing import cast

import numpy as np
from shapely import (
    STRtree,
    covers,
    difference,
    equals_exact,
    intersection,
    intersects,
    is_empty,
    length,
    prepare,
    unary_union,
)
from shapely import area as planar_area
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry
from tqdm.contrib.concurrent import process_map

import osmfinder.finder as _finder
from osmfinder._area_helper import calculate_spherical_area
from osmfinder._compat import FORCE_TERMINAL
from osmfinder._results import GeometryCoveringStep, OsmfinderGeometryResult
from osmfinder._typing import (
    OpenStreetMapExtract,
    OsmExtractsIndex,
    OsmExtractSourceLike,
)
from osmfinder.exceptions import (
    GeometryNotCoveredError,
    GeometryNotCoveredWarning,
)

# Replacing an extract with smaller ones gives more files to download and process.
# Each additional file has to reduce the total size by this part of it.
MIN_SIZE_REDUCTION_PER_FILE = 0.1


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
    of the geometry uncovered. At the end, selected extracts are swapped for other ones if it makes
    the total size of files to download smaller without losing the coverage. Each additional file
    has to reduce it by at least 10%. Areas are used in place of the sizes if those are unknown.

    Args:
        geometry (BaseGeometry): Geometry to be covered.
        polygons_index (OsmExtractsIndex): Index of available extracts.
        num_of_multiprocessing_workers (int): Number of workers used for multiprocessing.
            Defaults to -1 which results in a total number of available cpu threads.
            `0` and `1` values disable multiprocessing.
            Similar to `n_jobs` parameter from `scikit-learn` library.
        multiprocessing_activation_threshold (int): Number of geometries required to start
            processing on multiple processes. Activating multiprocessing for a small
            amount of points might not be feasible. Defaults to 100.
        geometry_coverage_iou_threshold (float): Minimal value of the Intersection over Union metric
            for selecting the matching OSM extracts. Is best matching extract has value lower than
            the threshold, it is discarded (except the first one). Has to be in range between
            0 and 1. Value of 0 will allow every intersected extract, value of 1 will only allow
            extracts that match the geometry exactly. Defaults to 0.01.
        allow_uncovered_geometry (bool): Suppress an error if some geometry parts aren't covered
            by any OSM extract. Defaults to `False`.
        excluded_extracts_ids (set[str] | None): Set of extract ids to exclude from the search.
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
        list[OpenStreetMapExtract]: List of extracts covering a given geometry.
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
        for i, (extract_ids_set, steps) in enumerate(
            process_map(
                find_extracts_func,
                geometries,
                desc="Finding matching extracts",
                max_workers=num_of_multiprocessing_workers,
                chunksize=ceil(total_polygons / (4 * num_of_multiprocessing_workers)),
                disable=FORCE_TERMINAL,
            )
        ):
            unique_extracts_ids.update(extract_ids_set)
            for step in steps:
                step.sub_polygon_index = i
            all_steps.extend(steps)
    else:
        all_steps = []
        for i, sub_geometry in enumerate(geometries):
            extract_ids_set, steps = _find_smallest_containing_extracts_for_single_geometry(
                geometry=sub_geometry,
                polygons_index=polygons_index,
                geometry_coverage_iou_threshold=geometry_coverage_iou_threshold,
                allow_uncovered_geometry=allow_uncovered_geometry,
            )
            unique_extracts_ids.update(extract_ids_set)
            for step in steps:
                step.sub_polygon_index = i
            all_steps.extend(steps)

    extracts = [
        polygons_index.get_extract_by_index(position)
        for position in _minimize_total_size(geometry, unique_extracts_ids, polygons_index)
    ]
    all_steps.extend(
        _get_swapped_in_step(geometry, extract)
        for extract in extracts
        if extract.id not in unique_extracts_ids
    )

    _warn_about_uncovered_geometry(geometry, extracts, all_steps, geometry_coverage_iou_threshold)

    return extracts, all_steps


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
        tuple[set[str], list[GeometryCoveringStep]]: Selected extract index string values
            and covering steps.
    """
    if polygons_index is None:
        raise RuntimeError("Extracts index is empty.")

    if geometry_coverage_iou_threshold < 0 or geometry_coverage_iou_threshold > 1:
        raise ValueError("geometry_coverage_iou_threshold is outside required bounds [0, 1]")

    selected_extracts_ids: set[str] = set()
    checked_extracts_ids: set[str] = set()
    steps: list[GeometryCoveringStep] = []

    geometry_to_cover = _get_geometry_to_cover(geometry)

    exactly_matching_mask = equals_exact(polygons_index.geometries, geometry, tolerance=1e-6)
    if np.count_nonzero(exactly_matching_mask) == 1:
        matching_idx = int(np.flatnonzero(exactly_matching_mask)[0])
        extract_id = str(polygons_index.ids[matching_idx])
        extract = polygons_index.get_extract_by_index(matching_idx)
        selected_extracts_ids.add(extract_id)
        steps.append(
            GeometryCoveringStep(
                extract=extract,
                iou=1.0,
                selected=True,
                reason="first_extract",
                geometry_to_cover=geometry_to_cover,
                intersection_geometry=geometry_to_cover,
            )
        )
        return selected_extracts_ids, steps

    ids = polygons_index.ids
    areas = polygons_index.areas

    while not is_empty(geometry_to_cover):
        candidate_indices = polygons_index.tree.query(geometry_to_cover, predicate="intersects")
        candidate_indices = [
            idx for idx in candidate_indices if str(ids[idx]) not in checked_extracts_ids
        ]

        if not candidate_indices:
            if not allow_uncovered_geometry:
                raise GeometryNotCoveredError(
                    "Couldn't find extracts covering given geometry."
                    " If it's expected behaviour, you can suppress this error by passing"
                    " the `allow_uncovered_geometry=True` argument"
                    " or add `--allow-uncovered-geometry` flag to the CLI command."
                )
            break

        candidate_sort_areas = np.array([float(areas[idx]) for idx in candidate_indices])
        candidate_sort_ids = np.array([str(ids[idx]) for idx in candidate_indices])
        candidate_order = np.lexsort((candidate_sort_ids, candidate_sort_areas))
        candidate_indices = [candidate_indices[i] for i in candidate_order]

        candidate_geometries = np.array(
            [polygons_index.geometries[idx] for idx in candidate_indices], dtype=object
        )
        candidate_areas = calculate_spherical_area(candidate_geometries)
        geometry_to_cover_area = calculate_spherical_area(geometry_to_cover)
        intersection_geometries = intersection(candidate_geometries, geometry_to_cover)
        intersection_areas = calculate_spherical_area(intersection_geometries)
        iou_values = intersection_areas / (
            candidate_areas + geometry_to_cover_area - intersection_areas
        )

        best_pos = int(np.lexsort((candidate_areas, -iou_values))[0])
        best_idx = candidate_indices[best_pos]
        best_iou = float(iou_values[best_pos])

        if best_iou >= geometry_coverage_iou_threshold or not selected_extracts_ids:
            reason = "first_extract" if not selected_extracts_ids else "selected"
            selected_extracts_ids.add(str(ids[best_idx]))
            checked_extracts_ids.add(str(ids[best_idx]))
            extract = polygons_index.get_extract_by_index(best_idx)
            steps.append(
                GeometryCoveringStep(
                    extract=extract,
                    iou=best_iou,
                    selected=True,
                    reason=reason,
                    geometry_to_cover=geometry_to_cover,
                    intersection_geometry=intersection_geometries[best_pos],
                )
            )
            geometry_to_cover = geometry_to_cover.difference(polygons_index.geometries[best_idx])
        else:
            # Sort discarded candidates by IoU (desc), then area (asc), then id (asc)
            discard_order = np.lexsort(
                (
                    np.array([str(ids[idx]) for idx in candidate_indices]),
                    candidate_areas,
                    -iou_values,
                )
            )
            for i in discard_order:
                idx = candidate_indices[i]
                checked_extracts_ids.add(str(ids[idx]))
                extract = polygons_index.get_extract_by_index(idx)
                steps.append(
                    GeometryCoveringStep(
                        extract=extract,
                        iou=float(iou_values[i]),
                        selected=False,
                        reason="low_iou",
                        geometry_to_cover=geometry_to_cover,
                        intersection_geometry=intersection_geometries[i],
                    )
                )
            break

    return selected_extracts_ids, steps


def _get_geometry_to_cover(geometry: BaseGeometry) -> BaseGeometry:
    distance = 0 if geometry.geom_type == "Polygon" else 1e-6
    return cast("BaseGeometry", geometry.buffer(distance))


def _warn_about_uncovered_geometry(
    geometry: BaseGeometry,
    extracts: list[OpenStreetMapExtract],
    steps: list[GeometryCoveringStep],
    geometry_coverage_iou_threshold: float,
) -> None:
    selected_area = unary_union([e.geometry for e in extracts])
    uncovered_geometry = unary_union(
        [
            _get_geometry_to_cover(part).difference(selected_area)
            for part in _flatten_geometry(geometry)
        ]
    )
    if is_empty(uncovered_geometry):
        return

    discarded = sum(step.reason == "low_iou" for step in steps)
    message = (
        f"Discarded {discarded} extracts with IoU below the"
        f" threshold of {geometry_coverage_iou_threshold:.3g}"
        if discarded
        else "Couldn't find extracts covering given geometry"
    )
    total_area = calculate_spherical_area(geometry)
    if total_area > 0:
        covered_area = _format_covered_area(
            total_area, calculate_spherical_area(uncovered_geometry)
        )
        message = f"{message} (covered {covered_area} area)"
    warnings.warn(f"{message}.", GeometryNotCoveredWarning, stacklevel=0)


def _format_covered_area(total_area: float, uncovered_area: float) -> str:
    """Format the covered percentage, never showing 100% for incomplete coverage."""
    covered_percentage = 100 * (1 - uncovered_area / total_area)
    return ">99.99%" if covered_percentage > 99.99 else f"{covered_percentage:.2f}%"


def _find_single_extract_for_geometry(
    geometry: BaseGeometry,
    polygons_index: OsmExtractsIndex,
    iou_threshold: float = 0.99,
    allow_uncovered_geometry: bool = False,
) -> tuple[str, GeometryCoveringStep]:
    """Find a single extract that best covers the given geometry."""
    candidate_indices = polygons_index.tree.query(geometry, predicate="intersects")

    if len(candidate_indices) == 0:
        raise GeometryNotCoveredError("No OSM extracts intersect the query geometry.")

    geometry_area = calculate_spherical_area(geometry)

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
            intersection_area = calculate_spherical_area(intersection_geometry)
            iou = intersection_area / (extract_area + geometry_area - intersection_area)
            partial_candidates.append((int(idx), extract_area, iou))

    costs = _get_extracts_costs([x[0] for x in complete_cover_candidates], polygons_index)

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
                complete_cover_candidates, key=lambda x: (costs[x[0]], x[1])
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
                complete_cover_candidates, key=lambda x: (costs[x[0]], x[1])
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


def _minimize_total_size(
    geometry: BaseGeometry, extracts_ids: Iterable[str], polygons_index: OsmExtractsIndex
) -> list[int]:
    """
    Reduce the total size of a set of extracts, without losing any of its coverage.

    The total size is a sum over extracts, so their overlaps count multiple times, as they are
    downloaded multiple times. The whole geometry is considered at once, so an extract can be
    swapped for the ones selected for different parts of it.

    Args:
        geometry (BaseGeometry): Geometry to be covered.
        extracts_ids (Iterable[str]): Ids of the selected extracts.
        polygons_index (OsmExtractsIndex): Index of available extracts.

    Returns:
        list[int]: Positions of the extracts in the index, sorted by area descending, then id.
    """
    selected: list[int] = np.flatnonzero(np.isin(polygons_index.ids, list(extracts_ids))).tolist()
    parts = np.array(
        [_get_geometry_to_cover(part) for part in _flatten_geometry(geometry)], dtype=object
    )

    if len(selected) < 2:
        return selected

    costs = _get_extracts_costs(selected, polygons_index)
    improved: list[int] | None = _swap_for_single_extracts(parts, selected, polygons_index, costs)
    while improved is not None:
        selected = improved
        improved = _replace_with_smaller_extracts(parts, selected, polygons_index, costs)

    order = np.lexsort((polygons_index.ids[selected], -polygons_index.areas[selected]))
    return [selected[i] for i in order]


def _get_extracts_costs(compared: list[int], polygons_index: OsmExtractsIndex) -> np.ndarray:
    """
    Get file sizes of extracts, or their areas if a size of any of the compared ones is unknown.

    Extracts that can't be compared, since their size or area is unknown, have an infinite cost.
    """
    sizes = polygons_index.file_sizes
    costs = polygons_index.areas if np.isnan(sizes[compared]).any() else sizes
    return np.where(costs > 0, costs, np.inf)


def _replace_with_smaller_extracts(
    parts: np.ndarray, selected: list[int], polygons_index: OsmExtractsIndex, costs: np.ndarray
) -> list[int] | None:
    """
    Replace one of the extracts with a few smaller ones, if it's worth processing more files.

    Returns:
        list[int] | None: Improved set of extracts or None if there isn't any.
    """
    if len(selected) < 2:
        return None

    total_cost = costs[selected].sum()
    file_cost = MIN_SIZE_REDUCTION_PER_FILE * total_cost
    cells, cells_cover = _split_into_cells(parts, polygons_index.geometries[selected])
    is_covered_once = cells_cover.sum(axis=1) == 1

    for i, position in enumerate(selected):
        # sorted by cost, so there is too little to gain from the remaining ones
        if costs[position] <= file_cost:
            break

        # Smaller extracts can cost more than the replaced one, and the additional files count
        # only a half here, since they can make other extracts redundant.
        smaller = _fill_with_smallest_extracts(
            unary_union(cells[cells_cover[:, i] & is_covered_once]),
            polygons_index,
            costs + file_cost / 2,
            excluded=position,
            max_cost=2 * costs[position] + file_cost / 2,
        )
        if smaller is None:
            continue

        replaced = _swap_for_single_extracts(
            parts, [*selected[:i], *selected[i + 1 :], *smaller], polygons_index, costs
        )
        additional_files = max(0, len(replaced) - len(selected))
        cost_limit = total_cost - additional_files * file_cost
        if costs[replaced].sum() < cost_limit * (1 - 1e-9):
            return replaced

    return None


def _fill_with_smallest_extracts(
    geometry: BaseGeometry,
    polygons_index: OsmExtractsIndex,
    costs: np.ndarray,
    excluded: int,
    max_cost: float,
) -> list[int] | None:
    """
    Cover a geometry by taking extracts with the lowest cost per newly covered area.

    Returns:
        list[int] | None: Extracts or None if they can't fit below the maximal total cost.
    """
    candidates = polygons_index.tree.query(geometry, predicate="intersects")
    candidates = candidates[(costs[candidates] < max_cost) & (candidates != excluded)]
    candidates = candidates[np.lexsort((polygons_index.ids[candidates], costs[candidates]))]
    new_parts = intersection(polygons_index.geometries[candidates], geometry)
    filling: list[int] = []

    while not _is_sliver(geometry):
        new_areas = calculate_spherical_area(new_parts)
        is_useful = new_areas > 0
        candidates, new_parts = candidates[is_useful], new_parts[is_useful]
        relative_costs = costs[candidates] / new_areas[is_useful]
        # covering everything that is left at the lowest relative cost is the best possible case
        lower_bound = costs[filling].sum() + (
            relative_costs.min(initial=np.inf) * calculate_spherical_area(geometry)
        )
        if lower_bound >= max_cost:
            return None

        best = int(relative_costs.argmin())
        best_geometry = polygons_index.geometries[candidates[best]]
        filling.append(int(candidates[best]))
        geometry = geometry.difference(best_geometry)
        new_parts = difference(new_parts, best_geometry)

    return filling


def _swap_for_single_extracts(
    parts: np.ndarray, selected: list[int], polygons_index: OsmExtractsIndex, costs: np.ndarray
) -> list[int]:
    """
    Remove redundant extracts and swap the others for single ones with a lower cost.

    Returns:
        list[int]: Positions of the extracts in the index, sorted by cost descending, then id.
    """
    positions = np.lexsort((polygons_index.ids, -costs))
    rank = {int(position): i for i, position in enumerate(positions)}
    selected = sorted(set(selected), key=rank.__getitem__)

    improved = True
    while improved and len(selected) > 1:
        improved = False
        cells, cells_cover = _split_into_cells(parts, polygons_index.geometries[selected])
        covered_cells = {
            position: np.flatnonzero(cells_cover[:, i]) for i, position in enumerate(selected)
        }
        # number of selected extracts covering each cell
        counts = cells_cover.sum(axis=1)

        redundant = _remove_redundant(selected, covered_cells, counts)
        selected = [position for position in selected if position not in redundant]

        candidates = np.unique(polygons_index.tree.query(cells)[1])
        candidates = candidates[costs[candidates] < costs[selected].sum()]
        candidates = candidates[np.lexsort((polygons_index.ids[candidates], costs[candidates]))]
        candidate_indices, cell_indices = STRtree(cells).query(
            polygons_index.geometries[candidates], predicate="covers"
        )
        for i in np.unique(candidate_indices):
            candidate = int(candidates[i])
            if candidate in selected:
                continue

            covered_cells[candidate] = cell_indices[candidate_indices == i]
            counts[covered_cells[candidate]] += 1
            redundant = _remove_redundant(selected, covered_cells, counts)
            if costs[redundant].sum() > costs[candidate] * (1 + 1e-9):
                selected = [position for position in selected if position not in redundant]
                selected = sorted([*selected, candidate], key=rank.__getitem__)
                improved = True
            else:
                counts[covered_cells[candidate]] -= 1
                for position in redundant:
                    counts[covered_cells[position]] += 1

    return selected


def _get_swapped_in_step(
    geometry: BaseGeometry, extract: OpenStreetMapExtract
) -> GeometryCoveringStep:
    """Build a step for an extract swapped in to make the total size smaller."""
    intersection_geometry = extract.geometry.intersection(geometry)
    intersection_area = calculate_spherical_area(intersection_geometry)
    union_area = (
        calculate_spherical_area(extract.geometry)
        + calculate_spherical_area(geometry)
        - intersection_area
    )
    return GeometryCoveringStep(
        extract=extract,
        iou=intersection_area / union_area,
        selected=True,
        reason="smaller_total_size",
        geometry_to_cover=geometry,
        intersection_geometry=intersection_geometry,
    )


def _split_into_cells(
    geometries: np.ndarray, extract_geometries: Iterable[BaseGeometry]
) -> tuple[np.ndarray, np.ndarray]:
    """
    Split geometries into cells lying either fully inside or fully outside of every extract.

    Returns:
        tuple[np.ndarray, np.ndarray]: Cells covered by at least one extract and a boolean matrix
            (cells x extracts) telling which extracts cover them.
    """
    cells = geometries
    columns = list(extract_geometries)
    cells_cover = np.zeros((len(cells), len(columns)), dtype=bool)

    for i, extract_geometry in enumerate(columns):
        prepare(extract_geometry)
        cells_cover[:, i] = covers(extract_geometry, cells)
        crossing = np.flatnonzero(intersects(extract_geometry, cells) & ~cells_cover[:, i])
        inside = intersection(cells[crossing], extract_geometry)
        # skip the ones only touching the extract
        overlapping = planar_area(inside) > 0
        crossing, inside = crossing[overlapping], inside[overlapping]

        outside = difference(cells[crossing], extract_geometry)
        inside_cover = cells_cover[crossing]
        inside_cover[:, i] = True
        cells = np.concatenate([np.delete(cells, crossing), outside, inside])
        cells_cover = np.concatenate(
            [np.delete(cells_cover, crossing, axis=0), cells_cover[crossing], inside_cover]
        )

    is_relevant = cells_cover.any(axis=1) & ~_is_sliver(cells)
    return cells[is_relevant], cells_cover[is_relevant]


def _is_sliver(geometry: BaseGeometry | np.ndarray) -> bool | np.ndarray:
    """Find geometries thinner than a micrometer, being a floating point noise of overlays."""
    return cast("bool | np.ndarray", planar_area(geometry) <= 1e-11 * length(geometry))


def _remove_redundant(
    selected: list[int], covered_cells: dict[int, np.ndarray], counts: np.ndarray
) -> list[int]:
    """Find extracts whose cells are all covered by others and subtract them from the counts."""
    redundant = []
    for position in selected:
        if (counts[covered_cells[position]] > 1).all():
            counts[covered_cells[position]] -= 1
            redundant.append(position)
    return redundant


def _flatten_geometry(geometry: BaseGeometry) -> list[BaseGeometry]:
    """Flatten all geometries into a list of BaseGeometries."""
    from shapely.geometry.base import BaseMultipartGeometry

    if isinstance(geometry, BaseMultipartGeometry):
        geometries = []
        for sub_geom in geometry.geoms:
            geometries.extend(_flatten_geometry(sub_geom))
        return geometries
    return [geometry]


def find_extracts_by_geometry(
    geometry: BaseGeometry,
    source: OsmExtractSourceLike | None = None,
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
    of the geometry uncovered. At the end, selected extracts are swapped for other ones if it makes
    the sum of their areas smaller without losing the coverage. Each additional file has to reduce
    it by at least 10%.

    Args:
        geometry (BaseGeometry): Geometry to be covered.
        source (OsmExtractSourceLike): OSM source name. Can be one of: 'any', 'Geofabrik',
            'BBBike', 'osmfr', or an iterable / comma-separated string of those
            (e.g. ['BBBike', 'osmfr'] or 'bbbike,osmfr'). Defaults to 'any'.
        geometry_coverage_iou_threshold (float): Minimal value of the Intersection over Union metric
            for selecting the matching OSM extracts. Is best matching extract has value lower than
            the threshold, it is discarded (except the first one). Has to be in range between
            0 and 1. Value of 0 will allow every intersected extract, value of 1 will only allow
            extracts that match the geometry exactly. Defaults to 0.01.
        allow_uncovered_geometry (bool): Suppress an error if some geometry parts aren't covered
            by any OSM extract. Defaults to `False`.
        excluded_extracts_ids (set[str] | None): Set of extract ids to exclude from the search.
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
        OsmfinderGeometryResult: Result containing extracts name, URL to download it
        and boundary polygon.

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
        index = _finder._get_index_for_sources(source)
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

    cumulative_union = Polygon()
    input_area = calculate_spherical_area(geometry)
    last_coverage = 0.0
    for step in steps:
        if step.selected:
            cumulative_union = unary_union([cumulative_union, step.extract.geometry])
            covered_area = calculate_spherical_area(cumulative_union.intersection(geometry))
            if input_area > 0:
                last_coverage = min(1.0, covered_area / input_area)
            else:
                last_coverage = 1.0 if covered_area == 0 else 0.0

        step.cumulative_coverage = last_coverage

    covered_geometry = (
        unary_union([e.geometry for e in extracts]).intersection(geometry)
        if extracts
        else Polygon()
    )
    uncovered_geometry = geometry.difference(covered_geometry)

    sources_used = _finder._resolve_extract_sources(source)

    return OsmfinderGeometryResult(
        extracts=extracts,
        sources_used=sources_used,
        input_geometry=geometry,
        covered_geometry=covered_geometry,
        uncovered_geometry=uncovered_geometry,
        steps=steps,
        config={
            "geometry_coverage_iou_threshold": geometry_coverage_iou_threshold,
            "allow_uncovered_geometry": allow_uncovered_geometry,
            "force_single_result": force_single_result,
            "single_result_iou_threshold": single_result_iou_threshold,
            "excluded_extracts_ids": list(excluded_extracts_ids) if excluded_extracts_ids else [],
        },
    )


def find_extracts_covering_point(
    point: tuple[float, float] | Point,
    source: OsmExtractSourceLike | None = None,
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
            'BBBike', 'osmfr', or an iterable / comma-separated string of those
            (e.g. ['BBBike', 'osmfr'] or 'bbbike,osmfr'). Defaults to 'any'.
        excluded_extracts_ids (set[str] | None): Set of extract ids to exclude from the search.
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
        index = _finder._get_index_for_sources(source)
    except ValueError as ex:
        raise ValueError(f"Unknown OSM extracts source: {source}.") from ex

    if excluded_extracts_ids:
        index = index.filter_by_mask(~np.isin(index.ids, list(excluded_extracts_ids)))

    candidate_indices = index.tree.query(point_geom, predicate="intersects")

    matching: list[tuple[float, int]] = []
    for idx in candidate_indices:
        if intersects(index.geometries[idx], point_geom):
            matching.append((index.areas[idx], idx))

    matching.sort(key=lambda item: (item[0], str(index.ids[item[1]])))
    return [index.get_extract_by_index(idx) for _, idx in matching]
