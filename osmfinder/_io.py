import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from arro3.core import Array, Table
from arro3.io import read_parquet, write_parquet
from shapely import from_wkb, to_wkb

from osmfinder._typing import OsmExtractsIndex
from osmfinder.exceptions import OsmExtractIndexCorruptedError

GEOPARQUET_VERSION = "1.1.0"
GEOPARQUET_CRS = {"type": "OGC", "id": 4326}
EXPECTED_COLUMNS = ["id", "name", "file_name", "parent", "geometry", "area", "url"]


def read_parquet_index(file_paths: Path | Sequence[Path]) -> OsmExtractsIndex:
    if isinstance(file_paths, Path):
        file_paths = [file_paths]

    osm_indexes = [
        OsmExtractsIndex.from_numpy_dict(_read_single_index_file(file_path))
        for file_path in file_paths
    ]

    return OsmExtractsIndex.combine_indexes(osm_indexes)


def _read_single_index_file(file_path: Path) -> dict[str, np.ndarray]:
    tbl_raw = read_parquet(file_path).read_all()

    missing_columns = [col for col in EXPECTED_COLUMNS if col not in tbl_raw.column_names]
    if missing_columns:
        raise OsmExtractIndexCorruptedError(
            f"Index file {file_path.name} is missing required columns: {missing_columns}"
        )

    np_tbl = {
        col_name: np.asarray(col_data)
        for col_name, col_data in zip(tbl_raw.column_names, tbl_raw.columns, strict=True)
        if col_name != "geometry"
    }

    np_tbl["geometry"] = from_wkb(tbl_raw["geometry"])

    return np_tbl


def write_parquet_index(index: OsmExtractsIndex, file_path: Path) -> None:
    """Write an extracts index as a GeoParquet file (WKB-encoded, EPSG:4326)."""
    file_path.parent.mkdir(parents=True, exist_ok=True)

    geometry_wkb = np.array([to_wkb(geometry) for geometry in index.geometries], dtype=object)
    table = Table.from_arrays(
        [
            Array.from_numpy(index.ids),
            Array.from_numpy(index.names),
            Array.from_numpy(index.file_names),
            Array.from_numpy(index.parents),
            Array.from_numpy(index.areas),
            Array.from_numpy(index.urls),
            Array.from_numpy(geometry_wkb),
        ],
        names=["id", "name", "file_name", "parent", "area", "url", "geometry"],
    )

    geo_metadata = _build_geo_metadata(index)
    write_parquet(table, file_path, key_value_metadata={"geo": json.dumps(geo_metadata)})


def _build_geo_metadata(index: OsmExtractsIndex) -> dict[str, object]:
    """Build the GeoParquet ``geo`` metadata for the geometry column."""
    geometry_types = sorted({geometry.geom_type for geometry in index.geometries})
    bbox = _calculate_bbox(index.geometries)

    return {
        "version": GEOPARQUET_VERSION,
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "WKB",
                "geometry_types": geometry_types,
                "crs": GEOPARQUET_CRS,
                "edges": "planar",
                "bbox": bbox,
            }
        },
    }


def _calculate_bbox(geometries: np.ndarray) -> list[float]:
    """Calculate the bounding box covering all geometries as [minx, miny, maxx, maxy]."""
    minx = miny = float("inf")
    maxx = maxy = float("-inf")

    for geometry in geometries:
        bounds = geometry.bounds
        minx = min(minx, bounds[0])
        miny = min(miny, bounds[1])
        maxx = max(maxx, bounds[2])
        maxy = max(maxy, bounds[3])

    return [minx, miny, maxx, maxy]
