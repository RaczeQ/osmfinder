"""
OpenStreetMap.fr extracts.

This module contains wrapper for publically available OpenStreetMap.fr download server.
"""

import re
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
    soup = BeautifulSoup(result.text, "html.parser")

    extracts_urls = soup.find_all(string=re.compile("-latest\\.osm\\.pbf$"))
    pbar.total = (pbar.total or 0) + len(extracts_urls)
    pbar.refresh()
    extract_soup_objects.extend(
        [(extract_url, id_prefix, directory_url) for extract_url in extracts_urls]
    )

    directories = soup.find_all(src="/icons/folder.gif")
    for directory in directories:
        link = directory.find_parent("tr").find("a")
        name = link.text.replace("/", "")
        extract_soup_objects.extend(
            _gather_all_openstreetmap_fr_urls(
                id_prefix=f"{id_prefix}_{name}",
                directory_url=f"{directory_url}{link['href']}",
                pbar=pbar,
            )
        )

    return extract_soup_objects


def _parse_openstreetmap_fr_urls(
    pbar: tqdm, extract_soup_objects: list[Any]
) -> list[OpenStreetMapExtract]:
    extracts = []

    for soup_object, id_prefix, directory_url in extract_soup_objects:
        link = soup_object.find_parent("tr").find("a")
        name = link.text.replace("-latest.osm.pbf", "")
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
                url=f"{OPENSTREETMAP_FR_EXTRACTS_INDEX_URL}{directory_url}{link['href']}",
                geometry=polygon,
            )
        )
        pbar.set_description_str(id_prefix)
        pbar.update()

    return extracts
