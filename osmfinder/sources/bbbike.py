"""
BBBike OpenStreetMap extracts.

This module contains wrapper for publically available BBBike download server.
"""

from typing import Any

import requests
from shapely import box
from tqdm import tqdm

from osmfinder._compat import FORCE_TERMINAL
from osmfinder._constants import OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS, USER_AGENT
from osmfinder._typing import OpenStreetMapExtract, OsmExtractsIndex, OsmExtractSource
from osmfinder.extract import load_index_decorator
from osmfinder.parsers.poly import parse_polygon_file

BBBIKE_EXTRACTS_INDEX_URL = "https://download.bbbike.org/osm/bbbike"
BBBIKE_EXTRACTS_CSV_LIST_URL = (
    "https://raw.githubusercontent.com/wosch/bbbike-world/world/etc/cities.csv"
)
BBBIKE_INDEX: OsmExtractsIndex | None = None

__all__ = ["_get_bbbike_index"]


def _get_bbbike_index(**kwargs: Any) -> OsmExtractsIndex:
    global BBBIKE_INDEX  # noqa: PLW0603

    if BBBIKE_INDEX is None:
        BBBIKE_INDEX = _load_bbbike_index(**kwargs)

    return BBBIKE_INDEX


@load_index_decorator(OsmExtractSource.bbbike)
def _load_bbbike_index(**kwargs: Any) -> OsmExtractsIndex:  # pragma: no cover
    """
    Load available extracts from BBBike download service.

    Returns:
        OsmExtractsIndex: Extracts index with metadata.
    """
    extracts = _iterate_bbbike_index()
    return OsmExtractsIndex.from_extracts(extracts)


def _iterate_bbbike_index() -> list[OpenStreetMapExtract]:  # pragma: no cover
    """
    Iterate OpenStreetMap.fr extracts service page.

    Works recursively, by scraping whole available directory.

    Returns:
        list[OpenStreetMapExtract]: List of loaded bbbike extracts objects.
    """
    from bs4 import BeautifulSoup

    extracts = []
    result = requests.get(
        BBBIKE_EXTRACTS_INDEX_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS,
    )
    soup = BeautifulSoup(result.text, "html.parser")
    extract_names = [
        extract_href.text
        for extract_href in soup.select("tr.d > td > a")
        if extract_href.text != ".."
    ]

    csv_regions_result = requests.get(
        BBBIKE_EXTRACTS_CSV_LIST_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS,
    )
    rows = csv_regions_result.text.splitlines()

    bbbike_enum_value = OsmExtractSource.bbbike.value

    with tqdm(disable=FORCE_TERMINAL, desc=bbbike_enum_value, total=len(extract_names)) as pbar:
        for extract_name in extract_names:
            pbar.set_description(f"{bbbike_enum_value}_{extract_name}")
            poly_url = f"{BBBIKE_EXTRACTS_INDEX_URL}/{extract_name}/{extract_name}.poly"
            polygon = parse_polygon_file(poly_url)
            if polygon is None:
                # Fallback to csv regions file
                matching_row = [row for row in rows if row.startswith(extract_name + ":")][0]
                coords = list(map(float, matching_row.split(":")[6].split()))
                if len(coords) != 4:
                    raise ValueError(
                        f"Expecting 4 float values to parse bounding box. Got {len(coords)} values."
                    )
                polygon = box(*coords)
            pbf_url = f"{BBBIKE_EXTRACTS_INDEX_URL}/{extract_name}/{extract_name}.osm.pbf"
            extracts.append(
                OpenStreetMapExtract(
                    id=f"{bbbike_enum_value}_{extract_name}",
                    name=extract_name,
                    parent=bbbike_enum_value,
                    url=pbf_url,
                    geometry=polygon,
                )
            )
            pbar.update()

    return extracts
