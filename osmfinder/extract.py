"""OpenStreetMap extract class."""

import warnings
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, overload

import platformdirs
from dateutil.relativedelta import relativedelta
from pooch import HTTPDownloader, retrieve
from pooch import get_logger as get_pooch_logger
from requests import HTTPError

from osmfinder._constants import OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS
from osmfinder._io import read_parquet_index, write_parquet_index
from osmfinder._typing import OsmExtractsIndex, OsmExtractSource
from osmfinder.exceptions import (
    MissingOsmCacheWarning,
    OldOsmCacheWarning,
    OsmExtractIndexCorruptedError,
    OsmExtractIndexOutdatedWarning,
)

LFS_DIRECTORY_URL = "https://raw.githubusercontent.com/RaczeQ/osmfinder/main/precalculated_indexes"

_QUICK_REFRESH_SOURCES: set[OsmExtractSource] = set()


def load_index_decorator(
    extract_source: OsmExtractSource,
    fast_build: bool = False,
) -> Callable[[Callable[..., OsmExtractsIndex]], Callable[..., OsmExtractsIndex]]:
    """
    Decorator for loading OSM extracts index.

    Args:
        extract_source (OsmExtractSource): OpenStreetMap extract source.
            Used to save the index to cache.
        fast_build (bool): If True, skip the MissingOsmCacheWarning when the
            index must be built locally and register the source as a quick-refresh
            source for the warning message. Defaults to False.
    """

    def inner(
        function: Callable[..., OsmExtractsIndex],
    ) -> Callable[..., OsmExtractsIndex]:
        if fast_build:
            _QUICK_REFRESH_SOURCES.add(extract_source)

        def wrapper(**kwargs: Any) -> OsmExtractsIndex:
            global_cache_file_path = _get_global_cache_file_path(extract_source)
            global_cache_file_path.parent.mkdir(exist_ok=True, parents=True)

            force_recalculation = kwargs.get("force_recalculation", False)
            if force_recalculation:
                warnings.warn(
                    f"Forcing recalculation of the index for the {extract_source} source",
                    stacklevel=0,
                )

            def _invalidate_outdated_cache() -> OsmExtractsIndex:
                warnings.warn(
                    "Existing cached index has outdated structure. New index will be redownloaded.",
                    OsmExtractIndexOutdatedWarning,
                    stacklevel=0,
                )
                global_cache_file_path.replace(_invalidated_cache_path(global_cache_file_path))
                return wrapper(force_recalculation=force_recalculation)

            def _read_index_from_file() -> OsmExtractsIndex:
                try:
                    index = read_parquet_index(global_cache_file_path)
                except OsmExtractIndexCorruptedError:
                    index = _invalidate_outdated_cache()

                return index

            # Check if index exists in cache
            if not force_recalculation and global_cache_file_path.exists():
                index = _read_index_from_file()
            # Move locally downloaded cache to global directory
            elif (
                not force_recalculation
                and (local_cache_file_path := _get_local_cache_file_path(extract_source)).exists()
            ):
                import shutil

                shutil.copy(local_cache_file_path, global_cache_file_path)
                index = _read_index_from_file()
            # Download index
            elif not force_recalculation and _download_precalculated_index_from_github(
                global_cache_file_path
            ):
                index = _read_index_from_file()
            # Calculate index locally
            else:  # pragma: no cover
                if not fast_build:
                    quick_refresh_names = ", ".join(sorted(s.value for s in _QUICK_REFRESH_SOURCES))
                    warnings.warn(
                        f"Library has to build an index for the {extract_source.value} provider."
                        " This can take multiple minutes. To avoid waiting, use one of the"
                        f" quick-refresh sources that load from a single file:"
                        f" {quick_refresh_names}.",
                        MissingOsmCacheWarning,
                        stacklevel=0,
                    )

                index = function()

            # Save index to cache
            if force_recalculation or not global_cache_file_path.exists():
                write_parquet_index(index, global_cache_file_path)

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


@overload
def clear_osm_index_cache() -> None: ...


@overload
def clear_osm_index_cache(extract_source: OsmExtractSource) -> None: ...


def clear_osm_index_cache(extract_source: OsmExtractSource | None = None) -> None:
    """
    Clear cached osm index.

    Examples:
        >>> import osmfinder
        >>> osmfinder.clear_osm_index_cache()  # doctest: +SKIP
        >>> # Clears all cached indexes.
        >>> osmfinder.clear_osm_index_cache(osmfinder.OsmExtractSource.geofabrik)  # doctest: +SKIP
        >>> # Clears only the Geofabrik cache.
    """
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


def _get_file_creation_date(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_ctime)
