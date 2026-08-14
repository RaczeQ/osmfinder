<p align="center">
  <img width="300" src="https://raw.githubusercontent.com/raczeq/osmfinder/main/docs/assets/logos/osmfinder_logo.png"><br/>
</p>

# OSM Finder

Find and download publicly available OpenStreetMap `*.osm.pbf` extracts by **name**, **id** or
**geometry**.

`osmfinder` wraps several public extract providers behind a single API:
[Geofabrik](https://download.geofabrik.de/), [BBBike](https://download.bbbike.org/osm/bbbike/),
[OpenStreetMap.fr](https://download.openstreetmap.fr/), [Movisda](https://osm.download.movisda.io/)
and [GEO2day](https://geo2day.com/). It can look up an extract by a text query, or find the
smallest set of extracts covering an arbitrary geometry, and download the matching `*.osm.pbf`
files.

> **Logo attribution:** The osmfinder logo uses icons from the [Lucide](https://lucide.dev/) icon set —
> specifically the **earth** and **square-dashed** icons.

## Installation

```bash
pip install osmfinder
```

## Usage

```python
import osmfinder
from shapely.geometry import box

# --- by name / id ---
# Returns an OsmfinderQueryResult with .extracts list and .extract accessor.
result = osmfinder.find("Monaco")
print(result.extracts[0].id)        # 'Movisda-admin_MC'
print(result.extracts[0].file_name) # 'movisda-admin_monaco'
print(result.extract.id)            # convenience accessor for single-match queries

# Download by name - returns OsmfinderDownloadResult with .download_paths.
dl = osmfinder.download("Monaco", download_directory="files")
print(dl.download_paths)         # [Path('files/movisda-admin_monaco.osm.pbf')]
print(dl.find_result.extract.id)    # 'Movisda-admin_MC'

# --- by geometry ---
geometry = box(2.11, 48.77, 2.54, 48.98)
result = osmfinder.find(geometry)
print(result)                       # multi-line OsmfinderGeometryResult with extracts, coverage, steps
print(len(result.extracts))         # number of extracts covering the geometry
print(result.extracts[0].id)        # 'BBBike_Paris'

# Download by geometry
dl = osmfinder.download(geometry, source="Geofabrik", download_directory="files")
print(len(dl.download_paths))       # number of downloaded files
print(dl.download_paths[0].name)    # 'geofabrik_europe_monaco.osm.pbf'

# --- by point ---
extracts = osmfinder.find_extracts_covering_point((-0.1276, 51.5074), source="Geofabrik")
print(len(extracts))            # number of extracts covering central London
print(extracts[0].id)           # 'Geofabrik_greater-london'

# --- force single extract ---
geometry = box(9.4, 47.2, 9.8, 47.6)
result = osmfinder.find_smallest_containing_extracts(geometry)
print(len(result.extracts))     # 4
print(result.extracts[0].id)    # 'GEO2Day_europe_austria_vorarlberg'
print(result.extracts[1].id)    # 'BBBike_Konstanz'
print(result.extracts[2].id)    # 'GEO2Day_europe_switzerland_saint_gallen'
print(result.extracts[3].id)    # 'Movisda-admin_LI'

result = osmfinder.find_smallest_containing_extracts(
    geometry, force_single_result=True
)
print(len(result.extracts))     # 1
print(result.extracts[0].id)    # 'Movisda-grid_N47W009'

# --- explore what's available ---
osmfinder.display_available_extracts(source="Geofabrik") # source is optional

# Get all extracts as a list for programmatic use
extracts = osmfinder.get_available_extracts(source="Geofabrik") # source is optional
for extract in extracts:
    print(extract.id, extract.file_name)
```

### Sources

The `source` argument accepts a single value, an iterable, or a comma-separated string. Available
values: `any`, `Geofabrik`, `BBBike`, `osmfr`, `GEO2Day`, `Movisda-admin`, `Movisda-grid`.

```python
osmfinder.get_extract_by_query("Berlin", ["Geofabrik", "BBBike"])
osmfinder.get_extract_by_query("Berlin", "geofabrik,bbbike")
```

## Result classes

All find and download operations return typed result objects instead of raw lists.

### `OsmfinderQueryResult`

Returned by `get_extract_by_query()` and `find()` when called with a string query.

| Attribute | Type | Description |
|---|---|---|
| `extracts` | `list[OpenStreetMapExtract]` | All matched extracts |
| `extract` | `OpenStreetMapExtract` | Convenience accessor for the single matched extract |
| `matched_extracts` | `list[OpenStreetMapExtract]` | All extracts matched by the query before selection (may contain more than `extracts` when `select_first_match=True`) |
| `query` | `str` | The original query string |
| `sources_used` | `list[OsmExtractSource]` | Sources that were searched |

### `OsmfinderGeometryResult`

Returned by `find_smallest_containing_extracts()` and `find()` when called with a geometry.

| Attribute | Type | Description |
|---|---|---|
| `extracts` | `list[OpenStreetMapExtract]` | Selected extracts covering the geometry |
| `input_geometry` | `BaseGeometry` | The original input geometry |
| `covered_geometry` | `BaseGeometry` | Union of extract geometries intersecting the input |
| `uncovered_geometry` | `BaseGeometry` | Parts of the input not covered by any extract |
| `steps` | `list[GeometryCoveringStep]` | Record of each extract considered during covering |
| `iou_threshold` | `float` | IoU threshold used for selection |
| `sources_used` | `list[OsmExtractSource]` | Sources that were searched |

### `OsmfinderDownloadResult`

Returned by `download_extract_by_query()`, `find_and_download_extracts_pbf_files()`, and `download()`.

| Attribute | Type | Description |
|---|---|---|
| `find_result` | `OsmfinderQueryResult \| OsmfinderGeometryResult` | The underlying find result |
| `download_paths` | `list[Path]` | Paths to downloaded `.osm.pbf` files |
| `unavailable_extracts` | `list[OpenStreetMapExtract]` | Extracts that could not be downloaded |

### `GeometryCoveringStep`

Record of a single extract considered during geometry covering.

| Attribute | Type | Description |
|---|---|---|
| `extract` | `OpenStreetMapExtract` | The extract considered |
| `iou` | `float` | Intersection over Union with the remaining geometry |
| `selected` | `bool` | Whether the extract was selected |
| `reason` | `str` | Selection reason (`"selected"` or `"low_iou"`) |
| `geometry_to_cover` | `BaseGeometry` | Remaining geometry before this step |
| `intersection_geometry` | `BaseGeometry` | Intersection of the extract with the remaining geometry |

### Example `repr` output

All result objects use a verbose multi-line `repr` for easier debugging:

```python
>>> result = osmfinder.find("Monaco")
>>> print(result)
OsmfinderQueryResult
  query: Monaco
  extract: Movisda-admin_MC — Monaco
  matched extracts: Movisda-admin_MC, Geofabrik_monaco, BBBike_Monaco, OSM_fr_monaco, geofabrik_andorra, +3 more
  sources used: Geofabrik, BBBike, OSM_fr, Movisda-admin, GEO2Day

>>> geometry = box(7.40, 43.71, 7.44, 43.75)
>>> result = osmfinder.find(geometry, source="Geofabrik")
>>> print(result)
OsmfinderGeometryResult
  extracts:
    Geofabrik_europe_monaco — Monaco
  coverage: 100.0%
  iou threshold: 0.01
  steps:
    Geofabrik_europe_monaco — Monaco
      iou: 1.0000, selected, first_extract
  sources used: Geofabrik

>>> dl = osmfinder.download("Monaco", download_directory="files")
>>> print(dl)
OsmfinderDownloadResult
  downloaded:
    files/movisda-admin_monaco.osm.pbf
  unavailable:
    none
  find result:
    OsmfinderQueryResult
      query: Monaco
      extract: Movisda-admin_MC — Monaco
      matched extracts: Movisda-admin_MC, Geofabrik_monaco, BBBike_Monaco, OSM_fr_monaco, geofabrik_andorra, +3 more
      sources used: Geofabrik, BBBike, OSM_fr, Movisda-admin, GEO2Day
```

## Public API

| Function | Search by | Returns |
|---|---|---|
| `get_extract_by_query` | name / id | `OsmfinderQueryResult` |
| `get_available_extracts` | — | `list[OpenStreetMapExtract]` |
| `download_extract_by_query` | name / id | `OsmfinderDownloadResult` |
| `find_smallest_containing_extracts` | geometry | `OsmfinderGeometryResult` |
| `find_and_download_extracts_pbf_files` | geometry | `OsmfinderDownloadResult` |
| `download_extracts_pbf_files` | list of extracts | `list[Path]` |
| `display_available_extracts` | — | prints a tree |
| `clear_osm_index_cache` | — | clears the local index cache |

> **Note:** `find()` and `download()` are dual-purpose helpers. When called with a **string query** they
> return an `OsmfinderQueryResult` / `OsmfinderDownloadResult`. When called with a **geometry** they
> return an `OsmfinderGeometryResult` / `OsmfinderDownloadResult`. Use the explicit
> `get_extract_by_query` / `download_extract_by_query` if you want a single object without the list wrapper.

## Index cache

Provider indexes are cached locally (in the platform cache dir) as GeoParquet (`*.parquet`).
Precalculated indexes are downloaded from this repo's `precalculated_indexes/` folder on first use,
so most sources don't need to be rebuilt from scratch. Use `clear_osm_index_cache()` to force a
refresh.

## License

MIT
