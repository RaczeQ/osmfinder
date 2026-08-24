

# OSM Finder

Find and download publicly available OpenStreetMap `*.osm.pbf` extracts by **name**, **id** or
**geometry**.

`osmfinder` wraps several public extract providers behind a single API:
[Geofabrik](https://download.geofabrik.de/), [BBBike](https://download.bbbike.org/osm/bbbike/),
[OpenStreetMap.fr](https://download.openstreetmap.fr/), [Movisda](https://osm.download.movisda.io/)
and [GEO2day](https://geo2day.com/). It can look up an extract by a text query, or find the
smallest set of extracts covering an arbitrary geometry, and download the matching `*.osm.pbf`
files.

<p align="center">
  <img width="300" src="https://raw.githubusercontent.com/raczeq/osmfinder/main/docs/assets/logos/osmfinder_logo.png"><br/>
</p>

<p align="center">
    <img alt="GitHub" src="https://img.shields.io/github/license/raczeq/osmfinder?logo=mit&logoColor=%23fff">
    <img src="https://img.shields.io/github/checks-status/raczeq/osmfinder/main?logo=GitHubActions&logoColor=%23fff" alt="Checks">
    <a href="https://github.com/raczeq/osmfinder/actions/workflows/ci-prod.yml" target="_blank"><img alt="GitHub Workflow Status - PROD" src="https://img.shields.io/github/actions/workflow/status/raczeq/osmfinder/ci-prod.yml?label=build-prod&logo=GitHubActions&logoColor=%23fff"></a>
    <a href="https://results.pre-commit.ci/latest/github/raczeq/osmfinder/main" target="_blank"><img src="https://results.pre-commit.ci/badge/github/raczeq/osmfinder/main.svg" alt="pre-commit.ci status"></a>
    <a href="https://www.codefactor.io/repository/github/raczeq/osmfinder"><img alt="CodeFactor Grade" src="https://img.shields.io/codefactor/grade/github/raczeq/osmfinder?logo=codefactor&logoColor=%23fff"></a>
    <a href="https://app.codecov.io/gh/raczeq/osmfinder/tree/main"><img alt="Codecov" src="https://img.shields.io/codecov/c/github/raczeq/osmfinder?logo=codecov&token=PRS4E02ZX0&logoColor=%23fff"></a>
    <a href="https://pypi.org/project/osmfinder" target="_blank"><img src="https://img.shields.io/pypi/v/osmfinder?color=%2334D058&label=pypi%20package&logo=pypi&logoColor=%23fff" alt="Package version"></a>
    <a href="https://anaconda.org/conda-forge/osmfinder" target="_blank"><img src="https://img.shields.io/conda/vn/conda-forge/osmfinder?&logo=anaconda&logoColor=%23fff" alt="Package version"></a>
    <a href="https://pypi.org/project/osmfinder" target="_blank"><img src="https://img.shields.io/pypi/pyversions/osmfinder.svg?color=%2334D058&logo=python&logoColor=%23fff" alt="Supported Python versions"></a>
    <a href="https://pypi.org/project/osmfinder" target="_blank"><img alt="PyPI - Downloads" src="https://img.shields.io/pypi/dm/osmfinder"></a>
</p>

> **Logo attribution:** The osmfinder logo uses icons from the [Lucide](https://lucide.dev/) icon set —
> specifically the **earth** and **square-dashed** icons.

## Installation

```bash
pip install osmfinder
```

## Usage

### Python

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
result = osmfinder.find_extracts_by_geometry(
    geometry, force_single_result=False  # default behaviour
)
print(len(result.extracts))     # 4
print(result.extracts[0].id)    # 'GEO2Day_europe_austria_vorarlberg'
print(result.extracts[1].id)    # 'BBBike_Konstanz'
print(result.extracts[2].id)    # 'GEO2Day_europe_switzerland_saint_gallen'
print(result.extracts[3].id)    # 'Movisda-admin_LI'

result = osmfinder.find_extracts_by_geometry(
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

### CLI

The package also provides a [Typer](https://typer.tiangolo.com/)-based CLI.

```bash
# Search and download by name
osmfinder search Monaco --output files/

# Search without download
osmfinder search Monaco --dry-run
                                        Query: Monaco
┏━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ #    ┃ Selected   ┃ ID                    ┃ Name   ┃ File name               ┃ Area (km²) ┃
┡━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ 1    │ ✔          │ Movisda-admin_MC      │ Monaco │ movisda-admin_monaco    │      80.01 │
│ 2    │            │ GEO2Day_europe_monaco │ monaco │ geo2day_europe_monaco   │     101.88 │
│ 3    │            │ osmfr_europe_monaco   │ monaco │ osmfr_europe_monaco     │     101.88 │
│ 4    │            │ Geofabrik_monaco      │ monaco │ geofabrik_europe_monaco │     184.39 │
└──────┴────────────┴───────────────────────┴────────┴─────────────────────────┴────────────┘

# Find and download extracts covering a bounding box
osmfinder covers --bbox 2.11,48.77,2.54,48.98 --source Geofabrik

# Find extracts without download
osmfinder covers --dry-run --wkt "POLYGON ((9.8 47.2, 9.8 47.6, 9.4 47.6, 9.4 47.2, 9.8 47.2))"
                                            Geometry covering result
┏━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━┓
┃    ┃                                  ┃            ┃            ┃        ┃       Cum. ┃          ┃           ┃
┃ #  ┃ ID                               ┃ Name       ┃ Area (km²) ┃    IoU ┃   Coverage ┃ Status   ┃ Reason    ┃
┡━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━┩
│ 1  │ GEO2Day_europe...tria_vorarlberg │ vorarlberg │    2748.28 │ 0.1820 │      46.9% │ selected │ first_ex… │
│ 2  │ GEO2Day_europe...nd_saint_gallen │ saint_gal… │    2565.17 │ 0.1797 │      84.1% │ selected │ selected  │
│ 3  │ Movisda-admin_LI                 │ Liechtens… │     159.04 │ 0.0624 │      85.7% │ selected │ selected  │
│ 4  │ GEO2Day_europe...zerland_thurgau │ thurgau    │    1092.62 │ 0.0462 │      85.7% │ rejected │ redundant │
│ 5  │ BBBike_Konstanz                  │ Konstanz   │    4471.33 │ 0.0302 │     100.0% │ selected │ selected  │
└────┴──────────────────────────────────┴────────────┴────────────┴────────┴────────────┴──────────┴───────────┘
╭────────────────────────────────────────────────── Summary ───────────────────────────────────────────────────╮
│ Coverage: 100.0%                                                                                             │
│ IoU threshold: 0.01                                                                                          │
│ Sources used: BBBike, GEO2Day, Geofabrik, Movisda-admin, Movisda-grid, osmfr                                 │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────╯

# Find extracts from a GeoJSON file
osmfinder covers --file area.geojson --output downloads/

# List available extracts
osmfinder list --source Geofabrik

# Clear the local index cache
osmfinder clear
```

### Sources

The `source` argument accepts a single value, an iterable, or a comma-separated string. Available
values: `any`, `Geofabrik`, `BBBike`, `osmfr`, `GEO2Day`, `Movisda-admin`, `Movisda-grid`.

```python
osmfinder.find_extract_by_query("Berlin", ["Geofabrik", "BBBike"])
osmfinder.find_extract_by_query("Berlin", "geofabrik,bbbike")
```

## Result classes

All find and download operations return typed result objects instead of raw lists.

### `OpenStreetMapExtract`

Metadata object returned by search and listing operations.

| Attribute | Type | Description |
|---|---|---|
| `id` | `str` | Unique extract identifier (e.g. `Geofabrik_monaco`) |
| `name` | `str` | Human-readable extract name |
| `parent` | `str` | Parent extract identifier in the source hierarchy |
| `url` | `str` | Download URL for the `.osm.pbf` file |
| `geometry` | `BaseGeometry` | Boundary polygon of the extract |
| `file_name` | `str` | Full file name derived from the parent hierarchy |

### `OsmfinderQueryResult`

Returned by `find_extract_by_query()` and `find()` when called with a string query.

| Attribute | Type | Description |
|---|---|---|
| `extracts` | `list[OpenStreetMapExtract]` | All matched extracts |
| `extract` | `OpenStreetMapExtract` | Convenience accessor for the single matched extract |
| `matched_extracts` | `list[OpenStreetMapExtract]` | All extracts matched by the query before selection (may contain more than `extracts` when `select_first_match=True`) |
| `query` | `str` | The original query string |
| `sources_used` | `list[OsmExtractSource]` | Sources that were searched |

### `OsmfinderGeometryResult`

Returned by `find_extracts_by_geometry()` and `find()` when called with a geometry.

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

Returned by `download()`.

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
| `find_extract_by_query` | name / id | `OsmfinderQueryResult` |
| `get_available_extracts` | — | `list[OpenStreetMapExtract]` |
| `find_extracts_by_geometry` | geometry | `OsmfinderGeometryResult` |
| `find_extracts_covering_point` | point | `list[OpenStreetMapExtract]` |
| `download` | name / id / geometry / result / extracts | `OsmfinderDownloadResult` |
| `find` | name / id / geometry | `OsmfinderQueryResult \| OsmfinderGeometryResult` |
| `display_available_extracts` | — | prints a tree |
| `clear_osm_index_cache` | — | clears the local index cache |

> **Note:** `find()` and `download()` are dual-purpose helpers. When called with a **string query** they
> return an `OsmfinderQueryResult` / `OsmfinderDownloadResult`. When called with a **geometry** they
> return an `OsmfinderGeometryResult` / `OsmfinderDownloadResult`. When called with a result or
> extract list they return an `OsmfinderDownloadResult`. Use the explicit
> `find_extract_by_query` / `find_extracts_by_geometry` if you want a single object without the
> download wrapper.

## Index cache

Provider indexes are cached locally (in the platform cache dir) as GeoParquet (`*.parquet`).
Precalculated indexes are downloaded from this repo's `precalculated_indexes/` folder on first use,
so most sources don't need to be rebuilt from scratch. Use `clear_osm_index_cache()` to force a
refresh.

## License

MIT
