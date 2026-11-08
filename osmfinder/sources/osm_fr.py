"""
OpenStreetMap.fr extracts.

This module contains wrapper for publically available OpenStreetMap.fr download server.
"""

import warnings
from typing import Any

from requests.exceptions import HTTPError
from tqdm import tqdm

from osmfinder._compat import FORCE_TERMINAL
from osmfinder._constants import OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS, USER_AGENT
from osmfinder._network import get_with_retries
from osmfinder._typing import OpenStreetMapExtract, OsmExtractsIndex, OsmExtractSource
from osmfinder.extract import load_index_decorator
from osmfinder.parsers.poly import parse_polygon_file

OPENSTREETMAP_FR_POLYGONS_INDEX_URL = "https://download.openstreetmap.fr/polygons"
OPENSTREETMAP_FR_EXTRACTS_INDEX_URL = "https://download.openstreetmap.fr/extracts"


@load_index_decorator(OsmExtractSource.osm_fr)
def _load_openstreetmap_fr_index(**kwargs: Any) -> OsmExtractsIndex:  # pragma: no cover
    """
    Load available extracts from OpenStreetMap.fr download service.

    Returns:
        OsmExtractsIndex: Extracts index with metadata.
    """
    extracts = []
    with tqdm(disable=FORCE_TERMINAL) as pbar:
        extract_soup_objects = _gather_all_openstreetmap_fr_urls(
            OsmExtractSource.osm_fr.value, "/", pbar
        )
        pbar.set_description(OsmExtractSource.osm_fr.value)
        extracts = _parse_openstreetmap_fr_urls(
            pbar=pbar, extract_soup_objects=extract_soup_objects
        )

    return OsmExtractsIndex.from_extracts(extracts)


def _gather_all_openstreetmap_fr_urls(
    id_prefix: str, directory_url: str, pbar: tqdm
) -> list[Any]:  # pragma: no cover
    """
    Iterate OpenStreetMap.fr extracts service page.

    Works recursively, by scraping whole available directory.

    Args:
        id_prefix (str): Prefix to be applies to extracts names.
        directory_url (str): Directory URL to load.
        pbar (tqdm): Progress bar.

    Returns:
        list[Any]: List of osm.fr extracts urls objects for further processing.
    """
    from bs4 import BeautifulSoup

    pbar.set_description_str(id_prefix)
    extract_soup_objects = []

    result = get_with_retries(
        f"{OPENSTREETMAP_FR_EXTRACTS_INDEX_URL}{directory_url}",
        headers={"User-Agent": USER_AGENT},
        timeout=OSM_EXTRACTS_REQUEST_TIMEOUT_SECONDS,
    )
    try:
        result.raise_for_status()
    except HTTPError as exc:
        if exc.response.status_code == 404:
            warnings.warn(
                f"Resource not found (404): {OPENSTREETMAP_FR_EXTRACTS_INDEX_URL}{directory_url}",
                UserWarning,
                stacklevel=2,
            )
            return []
        raise
    # Works with both table (Apache) and plain `<pre>` (nginx) directory listings.
    hrefs = [
        link["href"] for link in BeautifulSoup(result.text, "html.parser").find_all("a", href=True)
    ]

    extracts_hrefs = [href for href in hrefs if href.endswith("-latest.osm.pbf")]
    pbar.total = (pbar.total or 0) + len(extracts_hrefs)
    pbar.refresh()
    extract_soup_objects.extend([(href, id_prefix, directory_url) for href in extracts_hrefs])

    for href in hrefs:
        if href.endswith("/") and not href.startswith(("..", "/", "?")):
            extract_soup_objects.extend(
                _gather_all_openstreetmap_fr_urls(
                    id_prefix=f"{id_prefix}_{href.removesuffix('/')}",
                    directory_url=f"{directory_url}{href}",
                    pbar=pbar,
                )
            )

    return extract_soup_objects


def _parse_openstreetmap_fr_urls(
    pbar: tqdm, extract_soup_objects: list[Any]
) -> list[OpenStreetMapExtract]:
    extracts = []

    for href, id_prefix, directory_url in extract_soup_objects:
        name = href.removesuffix("-latest.osm.pbf")
        polygon = parse_polygon_file(
            f"{OPENSTREETMAP_FR_POLYGONS_INDEX_URL}/{directory_url}{name}.poly"
        )
        if polygon is None:
            continue
        extracts.append(
            OpenStreetMapExtract(
                id=f"{id_prefix}_{name}",
                name=name,
                parent=id_prefix,
                url=f"{OPENSTREETMAP_FR_EXTRACTS_INDEX_URL}{directory_url}{href}",
                geometry=polygon,
            )
        )
        pbar.set_description_str(id_prefix)
        pbar.update()

    return extracts
