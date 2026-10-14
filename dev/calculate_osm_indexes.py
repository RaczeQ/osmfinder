"""Recalculate OSM indexes and copy them to dedicated location."""

import warnings
from pathlib import Path

import geoparquet_io as gpio

from osmfinder import OsmExtractSource
from osmfinder._typing import OsmExtractsIndex
from osmfinder.exceptions import (
    OsmExtractsIndexesUnavailableError,
    OsmExtractSourceUnavailableWarning,
)
from osmfinder.extract import (
    _REGISTERED_INDEX_LOADERS,
    _get_global_cache_file_path,
    _get_registered_index,
    clear_osm_index_cache,
)


def _load_all_indexes_with_fallback(
    *,
    force_recalculation: bool = False,
) -> tuple[OsmExtractsIndex, list[OsmExtractSource]]:
    """
    Load indexes for all registered sources, skipping failures with warnings.

    Each source is loaded independently. Sources whose index can't be loaded (e.g. when
    the upstream server is unreachable) are skipped with an `OsmExtractSourceUnavailableWarning`.
    The returned index combines all successfully loaded sources. If every source fails,
    an `OsmExtractsIndexesUnavailableError` is raised.

    Args:
        force_recalculation (bool): When True, force a fresh recalculation of every source
            index instead of using the cache. Defaults to False.

    Returns:
        tuple[OsmExtractsIndex, list[OsmExtractSource]]: Combined index of all successfully
            loaded sources and the list of sources that were successfully loaded.
    """
    from osmfinder.exceptions import OsmExtractsIndexesUnavailableError

    loaded_indexes: list[OsmExtractsIndex] = []
    successful_sources: list[OsmExtractSource] = []

    for extract_source in _REGISTERED_INDEX_LOADERS:
        try:
            index = _get_registered_index(
                extract_source, force_recalculation=force_recalculation
            )
            loaded_indexes.append(index)
            successful_sources.append(extract_source)
        except Exception as ex:
            warnings.warn(
                f"Couldn't load index for {extract_source.value} source"
                f" (server unreachable or otherwise unavailable): {ex}."
                " Skipping this source and continuing with the remaining sources.",
                OsmExtractSourceUnavailableWarning,
                stacklevel=0,
            )

    if not loaded_indexes:
        raise OsmExtractsIndexesUnavailableError(
            "Couldn't load any OSM extracts index for any source."
            " Check your internet connection or the local cache."
        )

    return OsmExtractsIndex.combine_indexes(loaded_indexes), successful_sources

if __name__ == "__main__":
    clear_osm_index_cache()

    try:
        _, successful_sources = _load_all_indexes_with_fallback(force_recalculation=True)
    except OsmExtractsIndexesUnavailableError as ex:
        print(f"ERROR: {ex}")
        raise SystemExit(1) from ex

    for extract_source in successful_sources:
        cache_path = _get_global_cache_file_path(extract_source)

        table = gpio.read(cache_path)

        destination_path = (
            Path(__file__).parent.parent
            / "precalculated_indexes"
            / f"{extract_source.value.lower()}_index.parquet"
        )
        print(f"Copying cache file {cache_path} to {destination_path}.")
        table.write(destination_path, geoparquet_version="2.0")

    # Warn about any sources that were skipped due to being unreachable
    skipped_sources = [
        source
        for source in OsmExtractSource
        if source != OsmExtractSource.any and source not in successful_sources
    ]
    if skipped_sources:
        warnings.warn(
            f"The following sources were skipped due to being unreachable:"
            f" {[s.value for s in skipped_sources]}."
            " Their precalculated indexes were not updated."
            " Run the workflow again later when the servers are back online.",
            UserWarning,
            stacklevel=0,
        )
