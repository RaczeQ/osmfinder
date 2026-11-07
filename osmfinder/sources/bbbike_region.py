"""
BBBike region OpenStreetMap extracts.

This module contains wrapper for the publically available BBBike regions download server (
https://data.bbbike.org/osm/region/).
Each region is described by a boundary JSON file.
"""

from typing import Any

from tqdm import tqdm

from osmfinder._compat import FORCE_TERMINAL
from osmfinder._constants import OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS, USER_AGENT
from osmfinder._network import get_with_retries
from osmfinder._typing import OpenStreetMapExtract, OsmExtractsIndex, OsmExtractSource
from osmfinder.extract import load_index_decorator
from osmfinder.parsers.bbbike_json import parse_bbbike_json_file

BBBIKE_REGION_PBF_INDEX_URL = "https://data.bbbike.org/osm/pbf/region"
BBBIKE_REGION_BOUNDARY_URL = "https://data.bbbike.org/osm/region"


@load_index_decorator(OsmExtractSource.bbbike_region)
def _load_bbbike_region_index(**kwargs: Any) -> OsmExtractsIndex:  # pragma: no cover
    """
    Load available extracts from the BBBike regions download service.

    Returns:
        OsmExtractsIndex: Extracts index with metadata.
    """
    with tqdm(disable=FORCE_TERMINAL) as pbar:
        region_paths = _gather_bbbike_region_paths("", pbar)
        pbar.set_description(OsmExtractSource.bbbike_region.value)
        extracts = _parse_bbbike_region_paths(region_paths, pbar)

    return OsmExtractsIndex.from_extracts(extracts)


def _gather_bbbike_region_paths(directory: str, pbar: tqdm) -> list[str]:
    """
    Recursively list the PBF directory and return region paths (e.g. `europe/germany/berlin`).

    Args:
        directory (str): Directory path relative to the PBF index, ending with `/` or empty.
        pbar (tqdm): Progress bar.

    Returns:
        list[str]: Region paths without the `.osm.pbf` suffix.
    """
    from bs4 import BeautifulSoup

    pbar.set_description_str(f"{OsmExtractSource.bbbike_region.value}/{directory}")
    result = get_with_retries(
        f"{BBBIKE_REGION_PBF_INDEX_URL}/{directory}",
        headers={"User-Agent": USER_AGENT},
        timeout=OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS,
    )
    result.raise_for_status()
    hrefs = [a["href"] for a in BeautifulSoup(result.text, "html.parser").select("td.n > a")]

    region_paths = [
        f"{directory}{href.removesuffix('.osm.pbf')}" for href in hrefs if href.endswith(".osm.pbf")
    ]
    pbar.total = (pbar.total or 0) + len(region_paths)
    pbar.refresh()
    for href in hrefs:
        if href.endswith("/") and href != "../":
            region_paths.extend(_gather_bbbike_region_paths(f"{directory}{href}", pbar))

    return region_paths


def _parse_bbbike_region_paths(region_paths: list[str], pbar: tqdm) -> list[OpenStreetMapExtract]:
    """
    Load boundaries for region paths and build extracts.

    Regions without a boundary file are skipped.

    Args:
        region_paths (list[str]): Region paths (e.g. `europe/germany/berlin`).
        pbar (tqdm): Progress bar.

    Returns:
        list[OpenStreetMapExtract]: List of loaded extracts.
    """
    source_value = OsmExtractSource.bbbike_region.value
    extracts = []
    for region_path in region_paths:
        *parents, name = region_path.split("/")
        parent_id = "_".join([source_value, *parents])
        pbar.set_description_str(parent_id)
        geometry = parse_bbbike_json_file(f"{BBBIKE_REGION_BOUNDARY_URL}/{region_path}/{name}.json")
        pbar.update()
        if geometry is None:
            continue
        extracts.append(
            OpenStreetMapExtract(
                id=f"{parent_id}_{name}",
                name=name,
                parent=parent_id,
                url=f"{BBBIKE_REGION_PBF_INDEX_URL}/{region_path}.osm.pbf",
                geometry=geometry,
            )
        )

    return extracts
