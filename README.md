# osmfinder

Find and download publicly available OpenStreetMap `*.osm.pbf` extracts by **name**, **id** or
**geometry**.

`osmfinder` wraps several public extract providers behind a single API:
[Geofabrik](https://download.geofabrik.de/), [BBBike](https://download.bbbike.org/osm/bbbike/),
[OpenStreetMap.fr](https://download.openstreetmap.fr/), [Movisda](https://osm.download.movisda.io/)
and [GEO2day](https://geo2day.com/). It can look up an extract by a text query, or find the
smallest set of extracts covering an arbitrary geometry, and download the matching `*.osm.pbf`
files.

## Installation

```bash
pip install osmfinder
```

## Usage

```python
import osmfinder
from shapely.geometry import box

# --- by name / id ---
extract = osmfinder.get_extract_by_query("Monaco")            # searches all sources
extract = osmfinder.get_extract_by_query("Poland", source="Geofabrik")
print(extract.file_name, extract.url)

# short aliases
extract = osmfinder.find("Monaco")
path = osmfinder.download("Monaco", download_directory="files")

# --- by geometry ---
geometry = box(7.40, 43.71, 7.44, 43.75)                      # any shapely geometry
extracts = osmfinder.find_smallest_containing_extracts(geometry, source="Geofabrik")

# find the smallest covering set and download it in one call
downloaded = osmfinder.find_and_download_extracts_pbf_files(
    geometry, source="any", download_directory="files"
)

# --- explore what's available ---
osmfinder.display_available_extracts("Geofabrik")
```

### Sources

The `source` argument accepts a single value, an iterable, or a comma-separated string. Available
values: `any`, `Geofabrik`, `BBBike`, `osmfr`, `GEO2Day`, `Movisda-admin`, `Movisda-grid`.

```python
osmfinder.get_extract_by_query("Berlin", ["Geofabrik", "BBBike"])
osmfinder.get_extract_by_query("Berlin", "geofabrik,bbbike")
```

## Public API

| Function | Search by | Returns |
|---|---|---|
| `get_extract_by_query` / `find` | name / id | single `OpenStreetMapExtract` |
| `download_extract_by_query` / `download` | name / id | downloaded `Path` |
| `find_smallest_containing_extracts` | geometry | list of `OpenStreetMapExtract` |
| `find_smallest_containing_{geofabrik,bbbike,openstreetmap_fr}_extracts` | geometry | per-source list |
| `find_smallest_containing_extracts_total` | geometry | list across all sources |
| `find_and_download_extracts_pbf_files` | geometry | `(extract, Path)` pairs |
| `download_extracts_pbf_files` | list of extracts | list of `Path` |
| `display_available_extracts` | — | prints a tree |
| `clear_osm_index_cache` | — | clears the local index cache |

## Index cache

Provider indexes are cached locally (in the platform cache dir) as gzip-compressed FlatGeobuf
(`*.fgb.gz`), read directly through GDAL's `/vsigzip/` virtual filesystem. Precalculated indexes
are downloaded from this repo's `precalculated_indexes/` folder on first use, so most sources don't
need to be rebuilt from scratch. Use `clear_osm_index_cache()` to force a refresh.

FlatGeobuf + gzip keeps the index files essentially as small as GeoParquet while relying only on
`pyogrio` (already a GeoPandas dependency) instead of the much heavier `pyarrow`.

## License

MIT
