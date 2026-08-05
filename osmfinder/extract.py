"""OpenStreetMap extract class."""

import re
import warnings
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, cast, overload

import platformdirs
from anyascii import anyascii
from dateutil.relativedelta import relativedelta
from pooch import HTTPDownloader, retrieve
from pooch import get_logger as get_pooch_logger
from requests import HTTPError

from osmfinder._constants import OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS, WGS84_CRS
from osmfinder.exceptions import MissingOsmCacheWarning, OldOsmCacheWarning

if TYPE_CHECKING:  # pragma: no cover
    from geopandas import GeoDataFrame
    from pandas import DataFrame
    from shapely.geometry.base import BaseGeometry

LFS_DIRECTORY_URL = (
    "https://raw.githubusercontent.com/RaczeQ/osmfinder/main/precalculated_indexes"
)


@dataclass
class OpenStreetMapExtract:
    """OSM Extract metadata object."""

    id: str
    name: str
    parent: str
    url: str
    geometry: "BaseGeometry"
    file_name: str = ""


class OsmExtractSource(str, Enum):
    """Enum of available OSM extract sources."""

    any = "any"
    geofabrik = "Geofabrik"
    osm_fr = "osmfr"
    bbbike = "BBBike"
    geo2day = "GEO2Day"
    movisda_admin = "Movisda-admin"
    movisda_grid = "Movisda-grid"

    @classmethod
    def _missing_(cls, value):  # type: ignore
        value = value.lower()
        for member in cls:
            if member.lower() == value:
                return member
        return None


def load_index_decorator(
    extract_source: OsmExtractSource,
) -> Callable[[Callable[..., "GeoDataFrame"]], Callable[..., "GeoDataFrame"]]:
    """
    Decorator for loading OSM extracts index.

    Args:
        extract_source (OsmExtractSource): OpenStreetMap extract source.
            Used to save the index to cache.
    """

    def inner(function: Callable[..., "GeoDataFrame"]) -> Callable[..., "GeoDataFrame"]:
        def wrapper(**kwargs: Any) -> "GeoDataFrame":
            global_cache_file_path = _get_global_cache_file_path(extract_source)
            global_cache_file_path.parent.mkdir(exist_ok=True, parents=True)
            expected_columns = ["id", "name", "file_name", "parent", "geometry", "area", "url"]

            force_recalculation = kwargs.get("force_recalculation", False)
            if force_recalculation:
                warnings.warn(
                    f"Forcing recalculation of the index for the {extract_source} source",
                    stacklevel=0,
                )

            # Check if index exists in cache
            if not force_recalculation and global_cache_file_path.exists():
                index_gdf = _read_index(global_cache_file_path)
            # Move locally downloaded cache to global directory
            elif (
                not force_recalculation
                and (local_cache_file_path := _get_local_cache_file_path(extract_source)).exists()
            ):
                import shutil

                shutil.copy(local_cache_file_path, global_cache_file_path)
                index_gdf = _read_index(global_cache_file_path)
            # Download index
            elif not force_recalculation and _download_precalculated_index_from_github(
                global_cache_file_path
            ):
                index_gdf = _read_index(global_cache_file_path)
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

                index_gdf = function()
                # fix invalid geometries before computing metrics and persisting the index
                index_gdf = _ensure_valid_geometries(index_gdf)
                # calculate extracts area
                index_gdf["area"] = index_gdf.geometry.apply(_calculate_geodetic_area)
                index_gdf.sort_values(by=["area", "id"], ignore_index=True, inplace=True)

                # generate full file names
                apply_function = _get_full_file_name_function(index_gdf)
                index_gdf["file_name"] = index_gdf["id"].apply(apply_function)

                index_gdf = index_gdf[expected_columns]

            # Check if columns are right
            if set(expected_columns).symmetric_difference(index_gdf.columns):
                from osmfinder.exceptions import OsmExtractIndexOutdatedWarning

                warnings.warn(
                    "Existing cached index has outdated structure. New index will be redownloaded.",
                    OsmExtractIndexOutdatedWarning,
                    stacklevel=0,
                )
                # Invalidate previous cached index
                global_cache_file_path.replace(_invalidated_cache_path(global_cache_file_path))
                # Download index again
                index_gdf = wrapper(force_recalculation=force_recalculation)

            # Save index to cache
            if force_recalculation or not global_cache_file_path.exists():
                _write_index(index_gdf[expected_columns], global_cache_file_path)

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

            return index_gdf

        return wrapper

    return inner


def _ensure_valid_geometries(index_gdf: "GeoDataFrame") -> "GeoDataFrame":
    """
    Fix topologically invalid geometries in an extracts index.

    Some sources contain invalid geometries (self-intersections, nested shells).
    These would raise ``GEOSException: TopologyException`` during the coverage
    search (intersection / difference / union).
    """
    invalid_geometries_mask = ~index_gdf.geometry.is_valid
    if invalid_geometries_mask.any():
        index_gdf = index_gdf.copy()
        index_gdf.loc[invalid_geometries_mask, "geometry"] = index_gdf.geometry[
            invalid_geometries_mask
        ].make_valid()
    return index_gdf


def extracts_to_geodataframe(extracts: list[OpenStreetMapExtract]) -> "GeoDataFrame":
    """Transforms a list of OpenStreetMapExtracts to a GeoDataFrame."""
    import geopandas as gpd

    return gpd.GeoDataFrame(
        data=[asdict(extract) for extract in extracts], geometry="geometry"
    ).set_crs(WGS84_CRS)


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
        / f"{extract_source.value.lower()}_index.fgb.gz"
    )


def _get_local_cache_file_path(extract_source: OsmExtractSource) -> Path:
    return Path(f"cache/{extract_source.value.lower()}_index.fgb.gz")


def _invalidated_cache_path(path: Path) -> Path:
    """Return the path used to park an invalidated/outdated cache file (appends `.old`)."""
    return path.with_name(path.name + ".old")


def _read_index(path: Path) -> "GeoDataFrame":
    """
    Read a gzipped FlatGeobuf extracts index.

    The file is read straight from the gzip container through GDAL's ``/vsigzip/`` virtual
    filesystem, so it never has to be decompressed to a separate file on disk.
    """
    import geopandas as gpd

    return gpd.read_file(f"/vsigzip/{path}")


def _write_index(index_gdf: "GeoDataFrame", path: Path) -> None:
    """Write an extracts index as a gzip-compressed FlatGeobuf file."""
    import gzip
    import shutil
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_fgb = Path(tmp_dir) / "index.fgb"
        index_gdf.to_file(tmp_fgb, driver="FlatGeobuf")
        with open(tmp_fgb, "rb") as source, gzip.open(path, "wb", compresslevel=9) as destination:
            shutil.copyfileobj(source, destination)


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


def _get_full_file_name_function(index: "DataFrame") -> Callable[[str], str]:
    from pandas import Index

    ids_index = Index(index["id"])

    def inner_function(id: str) -> str:
        current_id = id
        parts = []
        while True:
            if current_id not in ids_index:
                parts.append(_slugify_file_name_part(current_id))
                break
            else:
                matching_row = index.iloc[ids_index.get_loc(current_id)]
                parts.append(_slugify_file_name_part(matching_row["name"]))
                current_id = matching_row["parent"]

        return "_".join(parts[::-1])

    return inner_function


def _get_file_creation_date(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_ctime)
