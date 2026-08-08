"""OpenStreetMap extract class."""

import re
import warnings
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, cast, overload

import numpy as np
import platformdirs
from anyascii import anyascii
from dateutil.relativedelta import relativedelta
from pooch import HTTPDownloader, retrieve
from pooch import get_logger as get_pooch_logger
from requests import HTTPError

from osmfinder._constants import OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS
from osmfinder._io import read_parquet_index, write_parquet_index
from osmfinder._typing import OpenStreetMapExtract, OsmExtractSource, OsmExtractsIndex
from osmfinder.exceptions import MissingOsmCacheWarning, OldOsmCacheWarning

if TYPE_CHECKING:  # pragma: no cover
    from shapely.geometry.base import BaseGeometry

LFS_DIRECTORY_URL = (
    "https://raw.githubusercontent.com/RaczeQ/osmfinder/main/precalculated_indexes"
)

EXPECTED_COLUMNS = ["id", "name", "file_name", "parent", "geometry", "area", "url"]


def load_index_decorator(
    extract_source: OsmExtractSource,
) -> Callable[[Callable[..., OsmExtractsIndex]], Callable[..., OsmExtractsIndex]]:
    """
    Decorator for loading OSM extracts index.

    Args:
        extract_source (OsmExtractSource): OpenStreetMap extract source.
            Used to save the index to cache.
    """

    def inner(
        function: Callable[..., OsmExtractsIndex],
    ) -> Callable[..., OsmExtractsIndex]:
        def wrapper(**kwargs: Any) -> OsmExtractsIndex:
            global_cache_file_path = _get_global_cache_file_path(extract_source)
            global_cache_file_path.parent.mkdir(exist_ok=True, parents=True)

            force_recalculation = kwargs.get("force_recalculation", False)
            if force_recalculation:
                warnings.warn(
                    f"Forcing recalculation of the index for the {extract_source} source",
                    stacklevel=0,
                )

            # Check if index exists in cache
            if not force_recalculation and global_cache_file_path.exists():
                index = _read_index(global_cache_file_path)
            # Move locally downloaded cache to global directory
            elif (
                not force_recalculation
                and (local_cache_file_path := _get_local_cache_file_path(extract_source)).exists()
            ):
                import shutil

                shutil.copy(local_cache_file_path, global_cache_file_path)
                index = _read_index(global_cache_file_path)
            # Download index
            elif not force_recalculation and _download_precalculated_index_from_github(
                global_cache_file_path
            ):
                index = _read_index(global_cache_file_path)
            # Calculate index locally
            else:  # pragma: no cover
                if extract_source != OsmExtractSource.geofabrik:
                    warnings.warn(
                        f"Library has to build an index for the {extract_source} provider."
                        " This can take multiple minutes. To avoid waiting for building an index,"
                        " the `osm_extract_source` parameter can be changed to `Geofabrik`, since"
                        " the index for it doesn't have to be built.",
                        MissingOsmCacheWarning,
                        stacklevel=0,
                    )

                index = function()
                # fix invalid geometries before computing metrics and persisting the index
                index = _ensure_valid_geometries(index)
                # calculate extracts area
                index = _add_areas_to_index(index)
                # generate full file names
                index = _add_file_names_to_index(index)

            # Check if columns are right
            if set(EXPECTED_COLUMNS).symmetric_difference(_index_columns(index)):
                from osmfinder.exceptions import OsmExtractIndexOutdatedWarning

                warnings.warn(
                    "Existing cached index has outdated structure. New index will be redownloaded.",
                    OsmExtractIndexOutdatedWarning,
                    stacklevel=0,
                )
                # Invalidate previous cached index
                global_cache_file_path.replace(_invalidated_cache_path(global_cache_file_path))
                # Download index again
                index = wrapper(force_recalculation=force_recalculation)

            # Save index to cache
            if force_recalculation or not global_cache_file_path.exists():
                _write_index(index, global_cache_file_path)

            global_cache_file_older_than_year = (
                datetime.now() - relativedelta(years=1)
            ) > _get_file_creation_date(global_cache_file_path)

            if global_cache_file_older_than_year:
                warnings.warn(
                    f"Existing {extract_source} cache index is older than one year"
                    " and it can be outdated. Cache can be cleared using the"
                    " osmfinder.clear_osm_index_cache function.",
                    OldOsmCacheWarning,
                    stacklevel=0,
                )

            return index

        return wrapper

    return inner


def _index_columns(index: OsmExtractsIndex) -> set[str]:
    """Return the set of column names present in an OsmExtractsIndex."""
    return {"id", "name", "file_name", "parent", "geometry", "area", "url"}


def _ensure_valid_geometries(index: OsmExtractsIndex) -> OsmExtractsIndex:
    """
    Fix topologically invalid geometries in an extracts index.

    Some sources contain invalid geometries (self-intersections, nested shells).
    These would raise ``GEOSException: TopologyException`` during the coverage
    search (intersection / difference / union).
    """
    from shapely import is_valid, make_valid

    invalid_geometries_mask = ~is_valid(index.geometries)
    if invalid_geometries_mask.any():
        fixed_geometries = index.geometries.copy()
        fixed_geometries[invalid_geometries_mask] = make_valid(
            index.geometries[invalid_geometries_mask]
        )
        return OsmExtractsIndex(
            ids=index.ids,
            geometries=fixed_geometries,
            areas=index.areas,
            file_names=index.file_names,
            names=index.names,
            parents=index.parents,
            urls=index.urls,
        )
    return index


def _add_areas_to_index(index: OsmExtractsIndex) -> OsmExtractsIndex:
    """Calculate and set the geodetic area (in km²) for each extract geometry."""
    areas = np.array([_calculate_geodetic_area(geometry) for geometry in index.geometries])
    # Sort by area and id (ascending), mirroring the previous GeoDataFrame behaviour.
    sort_indices = np.lexsort((index.ids, areas))
    return OsmExtractsIndex(
        ids=index.ids[sort_indices],
        geometries=index.geometries[sort_indices],
        areas=areas[sort_indices],
        file_names=index.file_names[sort_indices],
        names=index.names[sort_indices],
        parents=index.parents[sort_indices],
        urls=index.urls[sort_indices],
    )


def _add_file_names_to_index(index: OsmExtractsIndex) -> OsmExtractsIndex:
    """Generate full file names for each extract and set them on the index."""
    apply_function = _get_full_file_name_function(index)
    file_names = np.array([apply_function(extract_id) for extract_id in index.ids])
    return OsmExtractsIndex(
        ids=index.ids,
        geometries=index.geometries,
        areas=index.areas,
        file_names=file_names,
        names=index.names,
        parents=index.parents,
        urls=index.urls,
    )


def build_index_from_extracts(extracts: list[OpenStreetMapExtract]) -> OsmExtractsIndex:
    """Transforms a list of OpenStreetMapExtracts to an OsmExtractsIndex."""
    return OsmExtractsIndex(
        ids=np.array([extract.id for extract in extracts], dtype=object),
        geometries=np.array([extract.geometry for extract in extracts], dtype=object),
        areas=np.array(
            [_calculate_geodetic_area(extract.geometry) for extract in extracts], dtype=float
        ),
        file_names=np.array([extract.file_name for extract in extracts], dtype=object),
        names=np.array([extract.name for extract in extracts], dtype=object),
        parents=np.array([extract.parent for extract in extracts], dtype=object),
        urls=np.array([extract.url for extract in extracts], dtype=object),
    )


@overload
def clear_osm_index_cache() -> None: ...


@overload
def clear_osm_index_cache(extract_source: OsmExtractSource) -> None: ...


def clear_osm_index_cache(extract_source: Optional[OsmExtractSource] = None) -> None:
    """Clear cached osm index."""
    if extract_source is not None:
        extract_sources = [extract_source]
    else:
        extract_sources = [
            _source for _source in OsmExtractSource if _source != OsmExtractSource.any
        ]

    for _source in extract_sources:
        for cache_path in (
            _get_local_cache_file_path(_source),
            _get_global_cache_file_path(_source),
        ):
            for path in (cache_path, _invalidated_cache_path(cache_path)):
                path.unlink(missing_ok=True)


def _get_global_cache_file_path(extract_source: OsmExtractSource) -> Path:
    return (
        Path(platformdirs.user_cache_dir("osmfinder"))
        / f"{extract_source.value.lower()}_index.parquet"
    )


def _get_local_cache_file_path(extract_source: OsmExtractSource) -> Path:
    return Path(f"cache/{extract_source.value.lower()}_index.parquet")


def _invalidated_cache_path(path: Path) -> Path:
    """Return the path used to park an invalidated/outdated cache file (appends `.old`)."""
    return path.with_name(path.name + ".old")


def _read_index(path: Path) -> OsmExtractsIndex:
    """Read a (Geo)Parquet extracts index."""
    return read_parquet_index(path)


def _write_index(index: OsmExtractsIndex, path: Path) -> None:
    """Write an extracts index as a (Geo)Parquet file."""
    write_parquet_index(index, path)


def _download_precalculated_index_from_github(destination_path: Path) -> bool:
    logger = get_pooch_logger()
    logger.setLevel("WARNING")

    try:
        index_content_file_name = destination_path.name
        index_content_file_url = f"{LFS_DIRECTORY_URL}/{index_content_file_name}"
        retrieve(
            index_content_file_url,
            fname=index_content_file_name,
            path=destination_path.parent,
            progressbar=False,
            known_hash=None,
            downloader=HTTPDownloader(timeout=OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS),
        )
    except HTTPError as ex:
        if ex.response.status_code == 404:
            return False

        raise

    return True


def _calculate_geodetic_area(geometry: "BaseGeometry") -> float:
    from pyproj import Geod
    from shapely.ops import orient

    geod = Geod(ellps="WGS84")
    poly_area_m2, _ = geod.geometry_area_perimeter(orient(geometry, sign=1))
    poly_area_km2 = round(poly_area_m2) / 1_000_000
    return cast("float", poly_area_km2)


def _slugify_file_name_part(value: str) -> str:
    """
    Creates a slug part from file name.

    Makes it lowercase, replaces whitespace with underscores and all diactric characters into ascii.
    """
    ascii_value = re.sub(r"\s+", "_", anyascii(value).strip().lower())
    return re.sub(r"[^a-z0-9_-]+", "", ascii_value)


def _get_full_file_name_function(index: OsmExtractsIndex) -> Callable[[str], str]:
    ids_index = {extract_id: i for i, extract_id in enumerate(index.ids)}

    def inner_function(id: str) -> str:
        current_id = id
        parts = []
        while True:
            if current_id not in ids_index:
                parts.append(_slugify_file_name_part(current_id))
                break
            else:
                matching_row_idx = ids_index[current_id]
                parts.append(_slugify_file_name_part(index.names[matching_row_idx]))
                current_id = index.parents[matching_row_idx]

        return "_".join(parts[::-1])

    return inner_function


def _get_file_creation_date(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_ctime)