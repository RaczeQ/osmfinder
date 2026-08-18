"""Recalculate OSM indexes and copy them to dedicated location."""

from pathlib import Path

import geoparquet_io as gpio

from osmfinder import OsmExtractSource
from osmfinder.extract import _get_global_cache_file_path, clear_osm_index_cache
from osmfinder.finder import OSM_EXTRACT_SOURCE_INDEX_FUNCTION

if __name__ == "__main__":
    clear_osm_index_cache()
    for get_index_function in OSM_EXTRACT_SOURCE_INDEX_FUNCTION.values():
        get_index_function(force_recalculation=True)

    extract_sources = [_source for _source in OsmExtractSource if _source != OsmExtractSource.any]

    for extract_source in extract_sources:
        cache_path = _get_global_cache_file_path(extract_source)

        table = gpio.read(cache_path)

        destination_path = (
            Path(__file__).parent.parent
            / "precalculated_indexes"
            / f"{extract_source.value.lower()}_index.parquet"
        )
        print(f"Copying cache file {cache_path} to {destination_path}.")
        table.write(destination_path, geoparquet_version="2.0")
